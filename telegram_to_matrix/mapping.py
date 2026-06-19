from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import unicodedata

import yaml


@dataclass
class MappingStore:
    user_map_path: Path
    room_map_path: Path
    user_map: dict[str, dict[str, str]]
    room_map: dict[str, str]

    @classmethod
    def load(cls, user_map_path: Path, room_map_path: Path) -> "MappingStore":
        user_map = _load_yaml(user_map_path)
        room_map = _load_yaml(room_map_path)
        if not isinstance(user_map, dict):
            user_map = {}
        if not isinstance(room_map, dict):
            room_map = {}

        return cls(
            user_map_path=user_map_path,
            room_map_path=room_map_path,
            user_map=user_map,
            room_map=room_map,
        )

    def get_user(self, telegram_user_id: str) -> dict[str, str] | None:
        value = self.user_map.get(str(telegram_user_id))
        if isinstance(value, dict):
            return value
        return None

    def put_user(self, telegram_user_id: str, mxid: str, displayname: str) -> None:
        self.user_map[str(telegram_user_id)] = {
            "mxid": mxid,
            "displayname": displayname,
        }

    def get_room(self, telegram_chat_id: str) -> str | None:
        value = self.room_map.get(str(telegram_chat_id))
        return str(value) if value else None

    def put_room(self, telegram_chat_id: str, room_id: str) -> None:
        self.room_map[str(telegram_chat_id)] = room_id

    def save(self) -> None:
        """Persist room mappings only.

        User mappings are intentionally left untouched so manual entries in
        user_map.yaml are not overwritten by the importer.
        """
        self.user_map_path.parent.mkdir(parents=True, exist_ok=True)
        self.room_map_path.parent.mkdir(parents=True, exist_ok=True)
        self.room_map_path.write_text(
            yaml.safe_dump(self.room_map, sort_keys=True, allow_unicode=False),
            encoding="utf-8",
        )


def _load_yaml(path: Path):
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def build_localpart(display_name: str, telegram_user_id: str) -> str:
    normalized = unicodedata.normalize("NFKD", display_name)
    ascii_name = normalized.encode("ascii", "ignore").decode("ascii")
    base = "".join(ch.lower() if ch.isalnum() else "_" for ch in ascii_name).strip("_")
    if not base:
        base = "telegram_user"
    while "__" in base:
        base = base.replace("__", "_")
    return f"tg_{base}_{telegram_user_id}"[:200]
