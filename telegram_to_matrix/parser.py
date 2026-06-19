from __future__ import annotations

import json
import mimetypes
from pathlib import Path
from typing import Any

from .models import TelegramMessageNormalized

SUPPORTED_CHAT_TYPES = {"private_group", "private_supergroup", "personal_chat", "saved_messages"}
UNSUPPORTED_CHAT_TYPES = {"broadcast_channel", "public_channel", "channel"}


def _normalize_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[str] = []
        for part in value:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict):
                parts.append(str(part.get("text", "")))
        return "".join(parts)
    return ""


def _resolve_media_path(media_dir: Path, message: dict[str, Any]) -> tuple[Path | None, str | None, str | None]:
    photo_ref = message.get("photo")
    file_ref = message.get("file")

    # Telegram export may include this literal placeholder when media was not downloaded.
    if isinstance(file_ref, str) and file_ref.startswith("(File not included"):
        return None, None, None

    if photo_ref:
        media_path = (media_dir / photo_ref).resolve()
        return media_path, "image", "image/jpeg"

    if not file_ref:
        return None, None, None

    media_path = (media_dir / file_ref).resolve()
    media_type = message.get("media_type")
    mime_type = message.get("mime_type")

    kind_map = {
        "photo": "image",
        "voice_message": "audio",
        "audio_file": "audio",
        "video_file": "video",
        "video_message": "video",
        "animation": "video",
        "sticker": "sticker",
    }
    media_kind = kind_map.get(media_type)
    if media_kind is None:
        detected_mime = mime_type or mimetypes.guess_type(media_path.name)[0] or ""
        if detected_mime.startswith("image/"):
            media_kind = "image"
        elif detected_mime.startswith("audio/"):
            media_kind = "audio"
        elif detected_mime.startswith("video/"):
            media_kind = "video"
        else:
            media_kind = "document"

    return media_path, media_kind, mime_type


def _iter_chats(payload: dict[str, Any]) -> list[dict[str, Any]]:
    chats_list = payload.get("chats", {}).get("list")
    if isinstance(chats_list, list):
        return chats_list

    # Telegram Desktop "single chat export" format:
    # root object has id/name/type/messages directly.
    if isinstance(payload.get("messages"), list):
        return [payload]

    return []


def parse_telegram_export(export_path: Path, media_dir: Path) -> list[TelegramMessageNormalized]:
    with export_path.open("r", encoding="utf-8") as fh:
        payload = json.load(fh)

    chats = _iter_chats(payload)
    normalized: list[TelegramMessageNormalized] = []

    for chat in chats:
        chat_id = str(chat.get("id", ""))
        chat_title = str(chat.get("name", chat_id))
        chat_type = str(chat.get("type", ""))

        if chat_type in UNSUPPORTED_CHAT_TYPES:
            continue
        if SUPPORTED_CHAT_TYPES and chat_type not in SUPPORTED_CHAT_TYPES:
            # Unknown chat types are skipped in v1.
            continue

        for raw_message in chat.get("messages", []):
            if raw_message.get("type") != "message":
                continue

            date_unix = raw_message.get("date_unixtime")
            if date_unix is None:
                continue

            try:
                message_id = int(raw_message["id"])
                date_unix_ms = int(date_unix) * 1000
            except (KeyError, TypeError, ValueError):
                continue

            from_id = str(raw_message.get("from_id") or raw_message.get("actor_id") or "unknown")
            from_name = str(raw_message.get("from") or raw_message.get("actor") or "Unknown")
            text_plain = _normalize_text(raw_message.get("text", "")).strip()
            media_path, media_kind, media_mime = _resolve_media_path(media_dir, raw_message)
            try:
                media_width = int(raw_message["width"]) if raw_message.get("width") is not None else None
            except (TypeError, ValueError):
                media_width = None
            try:
                media_height = int(raw_message["height"]) if raw_message.get("height") is not None else None
            except (TypeError, ValueError):
                media_height = None
            try:
                media_duration_seconds = (
                    int(raw_message["duration_seconds"])
                    if raw_message.get("duration_seconds") is not None
                    else None
                )
            except (TypeError, ValueError):
                media_duration_seconds = None

            normalized.append(
                TelegramMessageNormalized(
                    chat_id=chat_id,
                    chat_title=chat_title,
                    chat_type=chat_type,
                    message_id=message_id,
                    date_unix_ms=date_unix_ms,
                    from_id=from_id,
                    from_name=from_name,
                    text_plain=text_plain,
                    media_path=media_path,
                    media_kind=media_kind,
                    media_mime=media_mime,
                    media_width=media_width,
                    media_height=media_height,
                    media_duration_seconds=media_duration_seconds,
                )
            )

    normalized.sort(key=lambda m: (m.chat_id, m.date_unix_ms, m.message_id))
    return normalized
