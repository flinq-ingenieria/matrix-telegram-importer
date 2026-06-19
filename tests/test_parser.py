from __future__ import annotations

import json
from pathlib import Path

from telegram_to_matrix.parser import parse_telegram_export


def test_parse_telegram_export_text_and_media(tmp_path: Path) -> None:
    media = tmp_path / "photos" / "img.jpg"
    media.parent.mkdir(parents=True)
    media.write_bytes(b"x")

    export = {
        "chats": {
            "list": [
                {
                    "id": 123,
                    "name": "Grupo",
                    "type": "private_group",
                    "messages": [
                        {
                            "id": 1,
                            "type": "message",
                            "date_unixtime": "1700000000",
                            "from": "Alice",
                            "from_id": "user1",
                            "text": ["hola", {"type": "bold", "text": " mundo"}],
                        },
                        {
                            "id": 2,
                            "type": "message",
                            "date_unixtime": "1700000001",
                            "from": "Bob",
                            "from_id": "user2",
                            "text": "",
                            "photo": "photos/img.jpg",
                        },
                    ],
                },
                {
                    "id": 999,
                    "name": "Canal",
                    "type": "broadcast_channel",
                    "messages": [
                        {
                            "id": 10,
                            "type": "message",
                            "date_unixtime": "1700000002",
                            "text": "ignored",
                        }
                    ],
                },
            ]
        }
    }

    export_path = tmp_path / "result.json"
    export_path.write_text(json.dumps(export), encoding="utf-8")

    messages = parse_telegram_export(export_path, tmp_path)

    assert len(messages) == 2
    assert messages[0].text_plain == "hola mundo"
    assert messages[1].media_kind == "image"
    assert messages[1].media_path is not None
    assert messages[1].media_path.name == "img.jpg"


def test_parse_skips_file_not_included_placeholder(tmp_path: Path) -> None:
    export = {
        "id": 1,
        "name": "Chat",
        "type": "private_group",
        "messages": [
            {
                "id": 3,
                "type": "message",
                "date_unixtime": "1700000001",
                "from": "Bob",
                "from_id": "user2",
                "text": "",
                "file": "(File not included. Change data exporting settings to download.)",
                "mime_type": "application/pdf",
            }
        ],
    }
    export_path = tmp_path / "result.json"
    export_path.write_text(json.dumps(export), encoding="utf-8")

    messages = parse_telegram_export(export_path, tmp_path)
    assert len(messages) == 1
    assert messages[0].media_path is None
    assert messages[0].media_kind is None


def test_parse_single_chat_export_format(tmp_path: Path) -> None:
    export = {
        "id": 777,
        "name": "Chat único",
        "type": "personal_chat",
        "messages": [
            {
                "id": 10,
                "type": "message",
                "date_unixtime": "1700001234",
                "from": "Carlos",
                "from_id": "user123",
                "text": "hola desde single export",
            }
        ],
    }

    export_path = tmp_path / "result.json"
    export_path.write_text(json.dumps(export), encoding="utf-8")

    messages = parse_telegram_export(export_path, tmp_path)
    assert len(messages) == 1
    assert messages[0].chat_id == "777"
    assert messages[0].chat_type == "personal_chat"
    assert messages[0].from_id == "user123"
    assert messages[0].text_plain == "hola desde single export"
