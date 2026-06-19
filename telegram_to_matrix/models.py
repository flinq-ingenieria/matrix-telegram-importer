from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class TelegramMessageNormalized:
    chat_id: str
    chat_title: str
    chat_type: str
    message_id: int
    date_unix_ms: int
    from_id: str
    from_name: str
    text_plain: str
    media_path: Optional[Path]
    media_kind: Optional[str]
    media_mime: Optional[str]
    media_width: Optional[int]
    media_height: Optional[int]
    media_duration_seconds: Optional[int]


@dataclass(frozen=True)
class PreparedMedia:
    file_path: Path
    mime_type: str
    msgtype: str
    body: str
    info: dict


@dataclass
class CheckpointRecord:
    telegram_chat_id: str
    telegram_message_id: int
    status: str
    matrix_event_id: Optional[str]
    content_hash: str
    attempts: int
    last_error: Optional[str]
    updated_at: str
