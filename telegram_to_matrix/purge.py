from __future__ import annotations

import argparse
import shutil
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import yaml


@dataclass(frozen=True)
class ChatStats:
    chat_id: str
    sent: int
    failed: int
    skipped: int
    total: int

    @property
    def partial(self) -> bool:
        return self.sent > 0 and self.failed > 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Purge checkpoint rows for half-imported Telegram chats"
    )
    parser.add_argument("--state-db", type=Path, required=True, help="checkpoint.sqlite path")
    parser.add_argument("--room-map", type=Path, help="room_map.yaml path")
    parser.add_argument("--chat-id", action="append", default=[], help="Telegram chat id to purge")
    parser.add_argument(
        "--auto-partial",
        action="store_true",
        help="Select chats with sent>0 and failed>0",
    )
    parser.add_argument(
        "--remove-room-map",
        action="store_true",
        help="Also remove selected chat ids from room_map.yaml",
    )
    parser.add_argument("--list", action="store_true", help="List chat stats and exit")
    parser.add_argument("--apply", action="store_true", help="Actually apply changes")
    return parser.parse_args()


def load_chat_stats(db_path: Path) -> list[ChatStats]:
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            """
            SELECT
                telegram_chat_id,
                SUM(CASE WHEN status = 'sent' THEN 1 ELSE 0 END) AS sent,
                SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed,
                SUM(CASE WHEN status = 'skipped' THEN 1 ELSE 0 END) AS skipped,
                COUNT(*) AS total
            FROM checkpoint
            GROUP BY telegram_chat_id
            ORDER BY telegram_chat_id
            """
        ).fetchall()
    finally:
        conn.close()

    return [
        ChatStats(
            chat_id=str(row[0]),
            sent=int(row[1] or 0),
            failed=int(row[2] or 0),
            skipped=int(row[3] or 0),
            total=int(row[4] or 0),
        )
        for row in rows
    ]


def select_chat_ids(stats: list[ChatStats], selected: list[str], auto_partial: bool) -> list[str]:
    selected_ids = {str(v) for v in selected}
    if auto_partial:
        selected_ids.update(item.chat_id for item in stats if item.partial)
    return sorted(selected_ids)


def backup_file(path: Path) -> Path:
    timestamp = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_path = path.with_suffix(path.suffix + f".{timestamp}.bak")
    shutil.copy2(path, backup_path)
    return backup_path


def purge_checkpoint_rows(db_path: Path, chat_ids: list[str]) -> int:
    if not chat_ids:
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


def purge_room_map(room_map_path: Path, chat_ids: list[str]) -> int:
    if not room_map_path.exists() or not chat_ids:
        return 0

    with room_map_path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        raise ValueError(f"room_map is not a YAML dict: {room_map_path}")

    removed = 0
    for chat_id in chat_ids:
        if str(chat_id) in data:
            del data[str(chat_id)]
            removed += 1

    room_map_path.write_text(yaml.safe_dump(data, sort_keys=True), encoding="utf-8")
    return removed


def print_stats(stats: list[ChatStats]) -> None:
    if not stats:
        print("No checkpoint rows found.")
        return

    print("chat_id\tsent\tfailed\tskipped\ttotal\tpartial")
    for item in stats:
        print(
            f"{item.chat_id}\t{item.sent}\t{item.failed}\t{item.skipped}\t{item.total}\t"
            f"{'yes' if item.partial else 'no'}"
        )


def main() -> int:
    args = parse_args()

    if not args.state_db.exists():
        raise FileNotFoundError(f"state db not found: {args.state_db}")

    stats = load_chat_stats(args.state_db)

    if args.list:
        print_stats(stats)
        if not args.auto_partial and not args.chat_id:
            return 0

    chat_ids = select_chat_ids(stats, args.chat_id, args.auto_partial)
    if not chat_ids:
        print("No chats selected. Use --chat-id or --auto-partial.")
        return 0

    print(f"Selected chat ids: {', '.join(chat_ids)}")

    if not args.apply:
        print("Dry-run mode. Add --apply to execute purge.")
        return 0

    db_backup = backup_file(args.state_db)
    deleted_rows = purge_checkpoint_rows(args.state_db, chat_ids)
    print(f"Backup created: {db_backup}")
    print(f"Deleted checkpoint rows: {deleted_rows}")

    if args.remove_room_map:
        if not args.room_map:
            raise ValueError("--remove-room-map requires --room-map")
        map_backup = backup_file(args.room_map) if args.room_map.exists() else None
        removed = purge_room_map(args.room_map, chat_ids)
        if map_backup:
            print(f"Room map backup created: {map_backup}")
        print(f"Removed room_map entries: {removed}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
