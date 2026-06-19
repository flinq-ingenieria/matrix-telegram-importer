from __future__ import annotations

import yaml

from pathlib import Path

from telegram_to_matrix.mapping import build_localpart
from telegram_to_matrix.mapping import MappingStore


def test_build_localpart_normalizes_accents_to_ascii() -> None:
    localpart = build_localpart("Álvaro Núñez Example", "user123")

    assert localpart == "tg_alvaro_nunez_example_user123"
    assert localpart.isascii()


def test_save_only_persists_room_map(tmp_path: Path) -> None:
    user_map_path = tmp_path / "user_map.yaml"
    room_map_path = tmp_path / "room_map.yaml"
    user_map_path.write_text(
        yaml.safe_dump({"1": {"mxid": "@alice:test", "displayname": "Alice"}}),
        encoding="utf-8",
    )
    room_map_path.write_text(
        yaml.safe_dump({"10": "!room:test"}),
        encoding="utf-8",
    )

    store = MappingStore.load(user_map_path, room_map_path)
    store.put_user("2", "@bob:test", "Bob")
    store.put_room("11", "!room2:test")
    store.save()

    assert yaml.safe_load(user_map_path.read_text(encoding="utf-8")) == {
        "1": {"mxid": "@alice:test", "displayname": "Alice"}
    }
    assert yaml.safe_load(room_map_path.read_text(encoding="utf-8")) == {
        "10": "!room:test",
        "11": "!room2:test",
    }
