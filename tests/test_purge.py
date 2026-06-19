from __future__ import annotations

import sqlite3
from pathlib import Path

import yaml

from telegram_to_matrix.purge import (
    load_chat_stats,
    purge_checkpoint_rows,
    purge_room_map,
    select_chat_ids,
)


def _init_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            """
            CREATE TABLE checkpoint (
                telegram_chat_id TEXT NOT NULL,
                telegram_message_id INTEGER NOT NULL,
                status TEXT NOT NULL,
                matrix_event_id TEXT,
                content_hash TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0,
                last_error TEXT,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (telegram_chat_id, telegram_message_id)
            )
            """
        )
        conn.executemany(
            """
            INSERT INTO checkpoint (
              telegram_chat_id, telegram_message_id, status, matrix_event_id,
              content_hash, attempts, last_error, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                ("1", 1, "sent", "$e1", "h1", 1, None, "t"),
                ("1", 2, "failed", None, "h2", 2, "err", "t"),
                ("2", 1, "sent", "$e2", "h3", 1, None, "t"),
                ("2", 2, "sent", "$e3", "h4", 1, None, "t"),
            ],
        )
        conn.commit()
    finally:
        conn.close()


def test_select_auto_partial(tmp_path: Path) -> None:
    db = tmp_path / "state.sqlite"
    _init_db(db)

    stats = load_chat_stats(db)
    selected = select_chat_ids(stats, selected=[], auto_partial=True)

    assert selected == ["1"]


def test_purge_checkpoint_and_room_map(tmp_path: Path) -> None:
    db = tmp_path / "state.sqlite"
    _init_db(db)

    room_map = tmp_path / "room_map.yaml"
    room_map.write_text(
        yaml.safe_dump({"1": "!room1:test", "2": "!room2:test"}, sort_keys=True),
        encoding="utf-8",
    )

    deleted = purge_checkpoint_rows(db, ["1"])
    assert deleted == 2

    removed = purge_room_map(room_map, ["1"])
    assert removed == 1

    data = yaml.safe_load(room_map.read_text(encoding="utf-8"))
    assert data == {"2": "!room2:test"}
