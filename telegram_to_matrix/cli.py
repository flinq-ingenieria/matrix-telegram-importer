from __future__ import annotations

import argparse
import hashlib
import logging
import tempfile
from collections import defaultdict
from pathlib import Path

import requests

from .checkpoint import CheckpointStore
from .logging_conf import configure_logging
from .mapping import MappingStore, build_localpart
from .matrix_client import MatrixClient
from .media import MediaConversionError, prepare_media
from .models import TelegramMessageNormalized
from .parser import parse_telegram_export
from .retrying import retry_call

LOG = logging.getLogger("telegram_to_matrix")


SUPPORTED_MEDIA_KINDS = {"image", "audio", "video", "document"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backfill Telegram export into Matrix")
    parser.add_argument("--telegram-export", type=Path, required=True)
    parser.add_argument("--media-dir", type=Path, required=True)
    parser.add_argument("--homeserver", required=True)
    parser.add_argument("--domain", required=True)
    parser.add_argument("--as-token", required=True)
    parser.add_argument("--hs-token", required=True)
    parser.add_argument("--synapse-admin-token", required=True)
    parser.add_argument(
        "--bot-mxid",
        default="@telegram-backfill-bot:example.com",
        help="Matrix bot user that must be in each target room before backfilling",
    )
    parser.add_argument("--state-db", type=Path, required=True)
    parser.add_argument("--room-map", type=Path, required=True)
    parser.add_argument("--user-map", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--since-message-id", type=int)
    parser.add_argument("--max-messages", type=int)
    parser.add_argument("--retry-max-attempts", type=int, default=5)
    parser.add_argument("--retry-base-delay", type=float, default=0.5)
    parser.add_argument("--fail-fast", action="store_true")
    parser.add_argument("--skip-videos", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    return parser.parse_args()


def _content_hash(message: TelegramMessageNormalized) -> str:
    payload = "|".join(
        [
            message.chat_id,
            str(message.message_id),
            str(message.date_unix_ms),
            message.from_id,
            message.from_name,
            message.text_plain,
            str(message.media_kind),
            str(message.media_path),
        ]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _txn_id(chat_id: str, message_id: int) -> str:
    sanitized = "".join(ch if ch.isalnum() else "_" for ch in chat_id)
    return f"tg-{sanitized}-{message_id}"


def _build_mxid(domain: str, from_name: str, from_id: str) -> str:
    localpart = build_localpart(from_name, from_id)
    return f"@{localpart}:{domain}"


def _resolve_user_mxid(
    mappings: MappingStore,
    *,
    domain: str,
    telegram_user_id: str,
    display_name: str,
) -> tuple[str, str]:
    user_cfg = mappings.get_user(telegram_user_id)
    if user_cfg:
        configured_mxid = user_cfg["mxid"]
        configured_displayname = user_cfg.get("displayname", display_name)
        return configured_mxid, configured_displayname

    fallback_mxid = _build_mxid(domain, display_name, telegram_user_id)
    mappings.put_user(telegram_user_id, fallback_mxid, display_name)
    return fallback_mxid, display_name


def _build_text_content(text: str) -> dict:
    return {
        "msgtype": "m.text",
        "body": text,
    }


def _build_media_content(prepared_media, mxc_uri: str) -> dict:
    return {
        "msgtype": prepared_media.msgtype,
        "body": prepared_media.body,
        "url": mxc_uri,
        "info": prepared_media.info,
    }


def _ensure_bot_in_room(
    matrix: MatrixClient,
    *,
    room_id: str,
    creator_mxid: str,
    bot_mxid: str,
    chat_id: str,
    retry_max_attempts: int,
    retry_base_delay: float,
) -> None:
    if not bot_mxid:
        return

    try:
        retry_call(
            lambda: matrix.join_room(room_id, bot_mxid),
            max_attempts=retry_max_attempts,
            base_delay=retry_base_delay,
        )
    except requests.HTTPError as exc:
        if exc.response is None or exc.response.status_code not in (403, 404):
            raise
        try:
            retry_call(
                lambda: matrix.invite_to_room(room_id, creator_mxid, bot_mxid),
                max_attempts=retry_max_attempts,
                base_delay=retry_base_delay,
            )
            retry_call(
                lambda: matrix.join_room(room_id, bot_mxid),
                max_attempts=retry_max_attempts,
                base_delay=retry_base_delay,
            )
        except requests.HTTPError:
            retry_call(
                lambda: matrix.admin_join_room(room_id, bot_mxid),
                max_attempts=retry_max_attempts,
                base_delay=retry_base_delay,
            )

    LOG.info("bot-joined chat=%s room=%s bot=%s", chat_id, room_id, bot_mxid)


def _ensure_user_in_room(
    matrix: MatrixClient,
    *,
    room_id: str,
    user_mxid: str,
    inviter_mxid: str,
    retry_max_attempts: int,
    retry_base_delay: float,
) -> None:
    try:
        retry_call(
            lambda: matrix.join_room(room_id, user_mxid),
            max_attempts=retry_max_attempts,
            base_delay=retry_base_delay,
        )
    except requests.HTTPError as exc:
        if exc.response is None or exc.response.status_code != 403:
            raise
        retry_call(
            lambda: matrix.invite_to_room(room_id, inviter_mxid, user_mxid),
            max_attempts=retry_max_attempts,
            base_delay=retry_base_delay,
        )
        retry_call(
            lambda: matrix.join_room(room_id, user_mxid),
            max_attempts=retry_max_attempts,
            base_delay=retry_base_delay,
        )


def _validate_input(args: argparse.Namespace) -> None:
    if not args.telegram_export.exists():
        raise FileNotFoundError(f"Telegram export JSON not found: {args.telegram_export}")
    if not args.media_dir.exists():
        raise FileNotFoundError(f"Media directory not found: {args.media_dir}")


def main() -> int:
    args = parse_args()
    configure_logging(args.verbose)
    _validate_input(args)

    checkpoint = CheckpointStore(args.state_db)
    mappings = MappingStore.load(args.user_map, args.room_map)

    messages = parse_telegram_export(args.telegram_export, args.media_dir)
    if args.since_message_id is not None:
        messages = [m for m in messages if m.message_id >= args.since_message_id]
    if args.max_messages is not None:
        messages = messages[: args.max_messages]

    LOG.info("Loaded %d messages from telegram export", len(messages))

    messages_by_chat: dict[str, list[TelegramMessageNormalized]] = defaultdict(list)
    for msg in messages:
        messages_by_chat[msg.chat_id].append(msg)

    for chat_id, chat_messages in messages_by_chat.items():
        room_id = mappings.get_room(chat_id)
        if room_id:
            LOG.info(
                "target-chat chat=%s title=%s room_id=%s bot=%s mode=mapped",
                chat_id,
                chat_messages[0].chat_title,
                room_id,
                args.bot_mxid,
            )
        else:
            LOG.info(
                "target-chat chat=%s title=%s room_id=<new> bot=%s mode=will-create",
                chat_id,
                chat_messages[0].chat_title,
                args.bot_mxid,
            )

    if args.dry_run:
        for message in messages[: min(10, len(messages))]:
            LOG.info(
                "dry-run sample chat=%s msg_id=%s author=%s text_len=%d media_kind=%s",
                message.chat_id,
                message.message_id,
                message.from_name,
                len(message.text_plain),
                message.media_kind,
            )
        LOG.info("Dry run completed")
        return 0

    matrix = MatrixClient(
        homeserver=args.homeserver,
        as_token=args.as_token,
        hs_token=args.hs_token,
        synapse_admin_token=args.synapse_admin_token,
    )

    stats = {
        "sent": 0,
        "skipped": 0,
        "failed": 0,
    }

    with tempfile.TemporaryDirectory(prefix="tg2mx_") as tmp_dir:
        tmp_path = Path(tmp_dir)

        for chat_id, chat_messages in messages_by_chat.items():
            creator = chat_messages[0]
            creator_mxid, creator_displayname = _resolve_user_mxid(
                mappings,
                domain=args.domain,
                telegram_user_id=creator.from_id,
                display_name=creator.from_name,
            )
            retry_call(
                lambda: matrix.ensure_user(creator_mxid, creator_displayname),
                max_attempts=args.retry_max_attempts,
                base_delay=args.retry_base_delay,
            )

            room_id = mappings.get_room(chat_id)
            if room_id and not matrix.admin_get_room(room_id):
                LOG.warning("Mapped room %s no longer exists, recreating", room_id)
                mappings.room_map.pop(str(chat_id), None)
                room_id = None
            if not room_id:
                room_id = retry_call(
                    lambda: matrix.create_room(
                        chat_messages[0].chat_title,
                        creator_mxid,
                        actor_mxid=args.bot_mxid,
                    ),
                    max_attempts=args.retry_max_attempts,
                    base_delay=args.retry_base_delay,
                )
                mappings.put_room(chat_id, room_id)
                LOG.info("Created room chat=%s room_id=%s", chat_id, room_id)

            _ensure_bot_in_room(
                matrix,
                room_id=room_id,
                creator_mxid=creator_mxid,
                bot_mxid=args.bot_mxid,
                chat_id=chat_id,
                retry_max_attempts=args.retry_max_attempts,
                retry_base_delay=args.retry_base_delay,
            )

            _ensure_user_in_room(
                matrix,
                room_id=room_id,
                user_mxid=creator_mxid,
                inviter_mxid=args.bot_mxid,
                retry_max_attempts=args.retry_max_attempts,
                retry_base_delay=args.retry_base_delay,
            )
            _ensure_user_in_room(
                matrix,
                room_id=room_id,
                user_mxid=args.bot_mxid,
                inviter_mxid=creator_mxid,
                retry_max_attempts=args.retry_max_attempts,
                retry_base_delay=args.retry_base_delay,
            )
            retry_call(
                lambda: matrix.make_room_admin(room_id, creator_mxid),
                max_attempts=args.retry_max_attempts,
                base_delay=args.retry_base_delay,
            )
            retry_call(
                lambda: matrix.set_room_history_visibility(room_id, creator_mxid, "shared"),
                max_attempts=args.retry_max_attempts,
                base_delay=args.retry_base_delay,
            )
            LOG.info(
                "history-visibility-set chat=%s room=%s visibility=shared by=%s",
                chat_id,
                room_id,
                creator_mxid,
            )

            users_in_chat = {(m.from_id, m.from_name) for m in chat_messages if m.from_id != creator.from_id}
            for from_id, from_name in users_in_chat:
                mxid, displayname = _resolve_user_mxid(
                    mappings,
                    domain=args.domain,
                    telegram_user_id=from_id,
                    display_name=from_name,
                )
                retry_call(
                    lambda mxid=mxid, displayname=displayname: matrix.ensure_user(mxid, displayname),
                    max_attempts=args.retry_max_attempts,
                    base_delay=args.retry_base_delay,
                )
                try:
                    retry_call(
                        lambda mxid=mxid: matrix.join_room(room_id, mxid),
                        max_attempts=args.retry_max_attempts,
                        base_delay=args.retry_base_delay,
                    )
                except requests.HTTPError as exc:
                    if exc.response is None or exc.response.status_code != 403:
                        raise
                    try:
                        retry_call(
                            lambda mxid=mxid: matrix.invite_to_room(room_id, args.bot_mxid, mxid),
                            max_attempts=args.retry_max_attempts,
                            base_delay=args.retry_base_delay,
                        )
                        retry_call(
                            lambda mxid=mxid: matrix.join_room(room_id, mxid),
                            max_attempts=args.retry_max_attempts,
                            base_delay=args.retry_base_delay,
                        )
                    except requests.HTTPError:
                        raise

            for index, message in enumerate(chat_messages, start=1):
                content_hash = _content_hash(message)
                previous = checkpoint.get(message.chat_id, message.message_id)
                if previous and previous.status == "sent":
                    stats["skipped"] += 1
                    continue

                attempt_number = (previous.attempts if previous else 0) + 1
                sender_mxid, _sender_displayname = _resolve_user_mxid(
                    mappings,
                    domain=args.domain,
                    telegram_user_id=message.from_id,
                    display_name=message.from_name,
                )
                txn_id = _txn_id(message.chat_id, message.message_id)

                try:
                    supported_media_kinds = set(SUPPORTED_MEDIA_KINDS)
                    if args.skip_videos:
                        supported_media_kinds.discard("video")

                    if message.media_kind and message.media_kind not in supported_media_kinds:
                        raise MediaConversionError(f"Unsupported media kind: {message.media_kind}")

                    if message.media_path and message.media_kind in SUPPORTED_MEDIA_KINDS:
                        prepared = prepare_media(message, tmp_path / message.chat_id)
                        mxc_uri = retry_call(
                            lambda: matrix.upload_media(prepared.file_path, prepared.mime_type, sender_mxid),
                            max_attempts=args.retry_max_attempts,
                            base_delay=args.retry_base_delay,
                        )
                        LOG.debug(
                            "media-uploaded chat=%s msg_id=%s user=%s mxc=%s",
                            message.chat_id,
                            message.message_id,
                            message.from_name,
                            mxc_uri,
                        )
                        content = _build_media_content(prepared, mxc_uri)
                    elif message.text_plain:
                        content = _build_text_content(message.text_plain)
                    else:
                        raise MediaConversionError("Message without supported payload")

                    event_id = retry_call(
                        lambda: matrix.send_message(
                            room_id,
                            txn_id,
                            sender_mxid,
                            message.date_unix_ms,
                            content,
                        ),
                        max_attempts=args.retry_max_attempts,
                        base_delay=args.retry_base_delay,
                    )

                    checkpoint.upsert(
                        message.chat_id,
                        message.message_id,
                        status="sent",
                        content_hash=content_hash,
                        matrix_event_id=event_id,
                        attempts=attempt_number,
                        last_error=None,
                    )
                    stats["sent"] += 1
                    LOG.info(
                        "sent chat=%s msg_id=%s user=%s kind=%s event_id=%s",
                        message.chat_id,
                        message.message_id,
                        message.from_name,
                        message.media_kind or "text",
                        event_id,
                    )
                except MediaConversionError as exc:
                    checkpoint.upsert(
                        message.chat_id,
                        message.message_id,
                        status="skipped",
                        content_hash=content_hash,
                        matrix_event_id=None,
                        attempts=attempt_number,
                        last_error=str(exc),
                    )
                    stats["skipped"] += 1
                    LOG.info(
                        "skipped chat=%s msg_id=%s user=%s reason=%s",
                        message.chat_id,
                        message.message_id,
                        message.from_name,
                        str(exc),
                    )
                except Exception as exc:  # noqa: BLE001
                    checkpoint.upsert(
                        message.chat_id,
                        message.message_id,
                        status="failed",
                        content_hash=content_hash,
                        matrix_event_id=None,
                        attempts=attempt_number,
                        last_error=str(exc),
                    )
                    stats["failed"] += 1
                    LOG.exception(
                        "failed chat=%s msg_id=%s user=%s",
                        message.chat_id,
                        message.message_id,
                        message.from_name,
                    )
                    if args.fail_fast:
                        return 1

                if index % 100 == 0:
                    LOG.info(
                        "progress chat=%s processed=%s sent=%s skipped=%s failed=%s",
                        chat_id,
                        index,
                        stats["sent"],
                        stats["skipped"],
                        stats["failed"],
                    )

    mappings.save()
    LOG.info(
        "Finished backfill sent=%s skipped=%s failed=%s",
        stats["sent"],
        stats["skipped"],
        stats["failed"],
    )
    return 0 if stats["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
