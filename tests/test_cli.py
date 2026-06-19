from __future__ import annotations

import yaml

from pathlib import Path

from telegram_to_matrix.cli import _resolve_user_mxid
from telegram_to_matrix.mapping import MappingStore


def test_resolve_user_mxid_keeps_explicit_mapping(tmp_path: Path) -> None:
    user_map_path = tmp_path / "user_map.yaml"
    room_map_path = tmp_path / "room_map.yaml"
    user_map_path.write_text(
        yaml.safe_dump(
            {
                "1001": {
                    "mxid": "@alice.example:matrix.example.com",
                    "displayname": "Alice Example",
                }
            }
        ),
        encoding="utf-8",
    )
    room_map_path.write_text(yaml.safe_dump({}), encoding="utf-8")

    store = MappingStore.load(user_map_path, room_map_path)

    mxid, displayname = _resolve_user_mxid(
        store,
        domain="matrix.example.com",
        telegram_user_id="1001",
        display_name="Alice Example",
    )

    assert mxid == "@alice.example:matrix.example.com"
    assert displayname == "Alice Example"
    assert store.get_user("1001") == {
        "mxid": "@alice.example:matrix.example.com",
        "displayname": "Alice Example",
    }


def test_resolve_user_mxid_generates_when_missing(tmp_path: Path) -> None:
    user_map_path = tmp_path / "user_map.yaml"
    room_map_path = tmp_path / "room_map.yaml"
    user_map_path.write_text(yaml.safe_dump({}), encoding="utf-8")
    room_map_path.write_text(yaml.safe_dump({}), encoding="utf-8")

    store = MappingStore.load(user_map_path, room_map_path)

    mxid, displayname = _resolve_user_mxid(
        store,
        domain="matrix.example.com",
        telegram_user_id="1001",
        display_name="Alice Example",
    )

    assert mxid == "@tg_alice_example_1001:matrix.example.com"
    assert displayname == "Alice Example"
    assert store.get_user("1001") == {
        "mxid": "@tg_alice_example_1001:matrix.example.com",
        "displayname": "Alice Example",
    }
