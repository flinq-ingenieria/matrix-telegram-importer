from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional

from .models import CheckpointRecord


class CheckpointStore:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._init_db()

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path)
        try:
            conn.row_factory = sqlite3.Row
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_db(self) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS checkpoint (
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

    def get(self, chat_id: str, message_id: int) -> Optional[CheckpointRecord]:
        with self._conn() as conn:
            row = conn.execute(
                """
                SELECT telegram_chat_id, telegram_message_id, status, matrix_event_id,
                       content_hash, attempts, last_error, updated_at
                FROM checkpoint
                WHERE telegram_chat_id = ? AND telegram_message_id = ?
                """,
                (chat_id, message_id),
            ).fetchone()

        if row is None:
            return None
        return CheckpointRecord(
            telegram_chat_id=row["telegram_chat_id"],
            telegram_message_id=row["telegram_message_id"],
            status=row["status"],
            matrix_event_id=row["matrix_event_id"],
            content_hash=row["content_hash"],
            attempts=row["attempts"],
            last_error=row["last_error"],
            updated_at=row["updated_at"],
        )

    def upsert(
        self,
        chat_id: str,
        message_id: int,
        *,
        status: str,
        content_hash: str,
        matrix_event_id: str | None,
        attempts: int,
        last_error: str | None,
    ) -> None:
        updated_at = datetime.now(tz=timezone.utc).isoformat()
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO checkpoint (
                    telegram_chat_id,
                    telegram_message_id,
                    status,
                    matrix_event_id,
                    content_hash,
                    attempts,
                    last_error,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (telegram_chat_id, telegram_message_id)
                DO UPDATE SET
                    status = excluded.status,
                    matrix_event_id = excluded.matrix_event_id,
                    content_hash = excluded.content_hash,
                    attempts = excluded.attempts,
                    last_error = excluded.last_error,
                    updated_at = excluded.updated_at
                """,
                (
                    chat_id,
                    message_id,
                    status,
                    matrix_event_id,
                    content_hash,
                    attempts,
                    last_error,
                    updated_at,
                ),
            )
