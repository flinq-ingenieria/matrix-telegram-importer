from __future__ import annotations

import argparse
import time
from urllib.parse import quote

import requests


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Mini tool: delete Matrix rooms (Synapse admin)")
    p.add_argument("--homeserver", required=True)
    p.add_argument("--admin-token", required=True)
    p.add_argument("--room-id", action="append", default=[], help="Room ID like !abc:example.com")
    p.add_argument("--room-file", help="Text file with one room_id per line")
    p.add_argument("--wait", action="store_true", help="Wait for delete task completion")
    p.add_argument("--timeout", type=int, default=600)
    p.add_argument("--poll", type=float, default=2.0)
    p.add_argument("--apply", action="store_true", help="Execute deletion (otherwise dry-run)")
    return p.parse_args()


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def _read_room_ids(args: argparse.Namespace) -> list[str]:
    ids = [r.strip() for r in args.room_id if r and r.strip()]
    if args.room_file:
        with open(args.room_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    ids.append(line)
    # de-dup preserving order
    seen: set[str] = set()
    out: list[str] = []
    for rid in ids:
        if rid not in seen:
            seen.add(rid)
            out.append(rid)
    return out


def _request(session: requests.Session, method: str, url: str, token: str, payload: dict | None = None) -> requests.Response:
    r = session.request(method, url, headers=_headers(token), json=payload, timeout=30)
    if r.status_code >= 400:
        raise requests.HTTPError(f"{r.status_code}: {r.text}", response=r)
    return r


def delete_room(session: requests.Session, hs: str, token: str, room_id: str) -> str | None:
    enc = quote(room_id, safe="")
    payload = {"block": True, "purge": True, "force_purge": True}

    attempts = [
        ("POST", f"{hs}/_synapse/admin/v2/rooms/{enc}/delete", payload),
        ("POST", f"{hs}/_synapse/admin/v1/rooms/{enc}/delete", payload),
        ("DELETE", f"{hs}/_synapse/admin/v2/rooms/{enc}", payload),
        ("DELETE", f"{hs}/_synapse/admin/v1/rooms/{enc}", payload),
    ]

    last_err: Exception | None = None
    for method, url, body in attempts:
        try:
            r = _request(session, method, url, token, body)
            if not r.text:
                return None
            data = r.json()
            return data.get("delete_id")
        except requests.HTTPError as exc:
            last_err = exc
            txt = exc.response.text.lower() if exc.response is not None else ""
            if exc.response is not None and exc.response.status_code in (400, 404, 405) and (
                "unrecognized" in txt or "unknown" in txt or "not found" in txt
            ):
                continue
            raise

    if last_err:
        raise last_err
    return None


def wait_delete(session: requests.Session, hs: str, token: str, delete_id: str, timeout: int, poll: float) -> None:
    end = time.time() + timeout
    enc = quote(delete_id, safe="")
    urls = [
        f"{hs}/_synapse/admin/v2/rooms/delete_status/{enc}",
        f"{hs}/_synapse/admin/v1/rooms/delete_status/{enc}",
    ]
    while True:
        last_err: Exception | None = None
        for url in urls:
            try:
                r = _request(session, "GET", url, token)
                data = r.json() if r.text else {}
                status = str(data.get("status", "")).lower()
                if status in {"complete", "completed", "done", "success"}:
                    return
                if status in {"failed", "error"}:
                    raise RuntimeError(f"Delete task failed: {data}")
                print(f"pending delete_id={delete_id} status={status or 'unknown'}")
                break
            except Exception as exc:  # noqa: BLE001
                last_err = exc
                continue

        if time.time() >= end:
            raise TimeoutError(f"Timeout waiting delete_id={delete_id}")
        if last_err and all(isinstance(last_err, requests.HTTPError) for _ in [0]):
            # best effort retry loop for mixed Synapse versions
            pass
        time.sleep(poll)


def main() -> int:
    args = parse_args()
    hs = args.homeserver.rstrip("/")
    room_ids = _read_room_ids(args)

    if not room_ids:
        print("No room IDs. Use --room-id or --room-file")
        return 1

    print("Rooms selected:")
    for rid in room_ids:
        print(f"- {rid}")

    if not args.apply:
        print("Dry-run. Add --apply to execute")
        return 0

    session = requests.Session()
    ok = 0
    fail = 0

    for rid in room_ids:
        try:
            delete_id = delete_room(session, hs, args.admin_token, rid)
            if delete_id:
                print(f"started room={rid} delete_id={delete_id}")
                if args.wait:
                    wait_delete(session, hs, args.admin_token, delete_id, args.timeout, args.poll)
                    print(f"done room={rid} delete_id={delete_id}")
            else:
                print(f"deleted room={rid}")
            ok += 1
        except Exception as exc:  # noqa: BLE001
            print(f"FAILED room={rid} err={exc}")
            fail += 1

    print(f"Summary ok={ok} fail={fail}")
    return 0 if fail == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
