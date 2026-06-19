from __future__ import annotations

import argparse
import shutil
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

import yaml

from .matrix_client import MatrixClient


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Delete mapped Matrix rooms for clean Telegram re-import"
    )
    parser.add_argument("--homeserver", required=True)
    parser.add_argument("--synapse-admin-token", required=True)
    parser.add_argument("--as-token", default="")
    parser.add_argument("--hs-token", default="")
    parser.add_argument("--room-map", type=Path, required=True)
    parser.add_argument("--state-db", type=Path)
    parser.add_argument("--chat-id", action="append", default=[])
    parser.add_argument("--all-mapped", action="store_true")
    parser.add_argument("--remove-room-map", action="store_true")
    parser.add_argument("--remove-checkpoint", action="store_true")
    parser.add_argument("--wait-delete", action="store_true")
    parser.add_argument("--wait-timeout", type=int, default=600)
    parser.add_argument("--poll-interval", type=float, default=2.0)
    parser.add_argument(
        "--cleanup-tg-users",
        action="store_true",
        help="Deactivate local Matrix users whose MXID starts with @tg_ after room deletion.",
    )
    parser.add_argument(
        "--fallback-shutdown",
        action="store_true",
        help="If delete is unsupported, fallback to admin shutdown_room (blocks room but may not purge DB).",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--apply", action="store_true")
    return parser.parse_args()


def backup_file(path: Path) -> Path:
    timestamp = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_path = path.with_suffix(path.suffix + f".{timestamp}.bak")
    shutil.copy2(path, backup_path)
    return backup_path


def load_room_map(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        raise ValueError(f"room_map is not a YAML dict: {path}")
    return {str(k): str(v) for k, v in data.items()}


def save_room_map(path: Path, data: dict[str, str]) -> None:
    path.write_text(yaml.safe_dump(data, sort_keys=True), encoding="utf-8")


def purge_checkpoint_rows(db_path: Path, chat_ids: list[str]) -> int:
    if not db_path.exists() or not chat_ids:
        return 0
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.executemany(
            "DELETE FROM checkpoint WHERE telegram_chat_id = ?",
            [(chat_id,) for chat_id in chat_ids],
        )
        conn.commit()
        return int(cur.rowcount or 0)
    finally:
        conn.close()


def deactivate_tg_users(client: MatrixClient, *, prefix: str = "@tg_", page_size: int = 100) -> list[str]:
    """Deactivate local users created by older generated MXID mappings."""
    removed: list[str] = []
    from_offset = 0

    while True:
        payload = client.list_users(from_offset=from_offset, limit=page_size, guests=False)
        users = payload.get("users", [])
        if not isinstance(users, list) or not users:
            break

        for user in users:
            if not isinstance(user, dict):
                continue
            name = str(user.get("name", ""))
            if not name.startswith(prefix):
                continue
            client.deactivate_user(name, erase=True)
            removed.append(name)
            print(f"deactivated user={name}")

        next_token = payload.get("next_token")
        if next_token is None:
            break
        try:
            from_offset = int(next_token)
        except (TypeError, ValueError):
            break

    return removed


def main() -> int:
    args = parse_args()

    if not args.apply and not args.dry_run:
        print("Safety: use --dry-run to preview or --apply to execute.")
        return 1

    room_map = load_room_map(args.room_map)
    if not room_map:
        print(f"No rooms in map: {args.room_map}")
        return 0

    selected = sorted({str(chat_id) for chat_id in args.chat_id})
    if args.all_mapped:
        selected = sorted(room_map.keys())

    if not selected:
        print("No chats selected. Use --chat-id ... or --all-mapped.")
        return 1

    missing = [chat_id for chat_id in selected if chat_id not in room_map]
    if missing:
        print(f"chat_id not found in room_map: {', '.join(missing)}")

    targets = [(chat_id, room_map[chat_id]) for chat_id in selected if chat_id in room_map]
    if not targets:
        print("No valid chat_id targets to process.")
        return 1

    print("Selected rooms:")
    for chat_id, room_id in targets:
        print(f"- chat_id={chat_id} room_id={room_id}")

    if args.dry_run and not args.apply:
        print("Dry-run complete. No changes made.")
        return 0

    client = MatrixClient(
        homeserver=args.homeserver,
        as_token=args.as_token,
        hs_token=args.hs_token,
        synapse_admin_token=args.synapse_admin_token,
    )

    deleted = 0
    failed = 0
    deleted_chat_ids: list[str] = []
    for chat_id, room_id in targets:
        try:
            result = client.admin_delete_room(room_id, block=True, purge=True)
            delete_id = result.get("delete_id") if isinstance(result, dict) else None
            if delete_id:
                print(f"deleted room chat_id={chat_id} room_id={room_id} delete_id={delete_id}")
                if args.wait_delete:
                    _wait_delete_complete(
                        client,
                        chat_id=chat_id,
                        room_id=room_id,
                        delete_id=str(delete_id),
                        timeout_seconds=args.wait_timeout,
                        poll_interval=args.poll_interval,
                    )
            else:
                print(f"deleted room chat_id={chat_id} room_id={room_id}")

            room_state = client.admin_get_room(room_id)
            if room_state:
                if args.fallback_shutdown:
                    print(
                        f"room still exists after delete, trying shutdown fallback "
                        f"chat_id={chat_id} room_id={room_id}"
                    )
                    client.admin_shutdown_room(room_id)
                    room_state_after_shutdown = client.admin_get_room(room_id)
                    if room_state_after_shutdown:
                        raise RuntimeError(
                            f"room still exists after shutdown fallback: {room_id}"
                        )
                else:
                    raise RuntimeError(
                        f"room still exists after delete attempt: {room_id}. "
                        "Use --wait-delete and/or --fallback-shutdown."
                    )

            deleted += 1
            deleted_chat_ids.append(chat_id)
        except Exception as exc:  # noqa: BLE001
            print(f"FAILED delete chat_id={chat_id} room_id={room_id} err={exc}")
            failed += 1

    tg_users_removed: list[str] = []
    if args.cleanup_tg_users:
        try:
            tg_users_removed = deactivate_tg_users(client)
        except Exception as exc:  # noqa: BLE001
            print(f"FAILED tg-user cleanup err={exc}")
            failed += 1

    if args.remove_room_map:
        backup = backup_file(args.room_map)
        for chat_id in deleted_chat_ids:
            room_map.pop(chat_id, None)
        save_room_map(args.room_map, room_map)
        print(f"room_map updated (backup: {backup})")

    if args.state_db and args.remove_checkpoint:
        if args.state_db.exists():
            backup = backup_file(args.state_db)
            removed = purge_checkpoint_rows(args.state_db, deleted_chat_ids)
            print(f"checkpoint rows removed: {removed} (backup: {backup})")
        else:
            print(f"state db not found, skipping: {args.state_db}")

    if args.cleanup_tg_users:
        print(f"tg users deactivated: {len(tg_users_removed)}")

    print(f"Done. deleted={deleted} failed={failed}")
    return 0 if failed == 0 else 2


def _wait_delete_complete(
    client: MatrixClient,
    *,
    chat_id: str,
    room_id: str,
    delete_id: str,
    timeout_seconds: int,
    poll_interval: float,
) -> None:
    deadline = time.time() + timeout_seconds
    while True:
        status_payload = client.admin_get_delete_status(delete_id)
        status = str(status_payload.get("status", "")).lower()
        if status in {"complete", "completed", "done", "success"}:
            print(
                f"delete-complete chat_id={chat_id} room_id={room_id} "
                f"delete_id={delete_id} status={status}"
            )
            return
        if status in {"failed", "error"}:
            raise RuntimeError(
                f"Delete failed for room {room_id} (delete_id={delete_id}): {status_payload}"
            )
        if time.time() >= deadline:
            raise TimeoutError(
                f"Timeout waiting delete completion for room {room_id} "
                f"(delete_id={delete_id}). Last status: {status_payload}"
            )
        print(
            f"delete-pending chat_id={chat_id} room_id={room_id} "
            f"delete_id={delete_id} status={status or 'unknown'}"
        )
        time.sleep(poll_interval)


if __name__ == "__main__":
    raise SystemExit(main())
