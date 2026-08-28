from __future__ import annotations

import logging
import os
import threading
from pathlib import Path
from typing import TYPE_CHECKING

import yaml
from pydantic import ValidationError

from app import paths
from app.config import ConfigStore
from app.persistence import RoomStore, validate_room_name
from app.schemas import Room

if TYPE_CHECKING:
    from app.personas import PersonaStore

logger = logging.getLogger(__name__)


class RoomExistsError(Exception):
    pass


class RoomNameReservedError(Exception):
    pass


RESERVED_NAMES = frozenset({"main", "default"})


class RoomConfigStore:
    def __init__(
        self,
        config: ConfigStore,
        chatrooms_yaml: Path | None = None,
        chatrooms_root: Path | None = None,
        personas: PersonaStore | None = None,
    ) -> None:
        self.config = config
        self.yaml_path = Path(chatrooms_yaml) if chatrooms_yaml is not None else paths.CHATROOMS_YAML
        self.chatrooms_root = (
            Path(chatrooms_root) if chatrooms_root is not None else paths.CHATROOMS_DIR
        )
        self.personas: PersonaStore | None = personas
        self._rooms: list[dict] = []
        self._lock = threading.Lock()
        self._load()

    def _load(self) -> None:
        with self._lock:
            self._rooms = []
            if not self.yaml_path.exists():
                self._persist_locked()
                return
            try:
                raw = yaml.safe_load(self.yaml_path.read_text(encoding="utf-8"))
            except (OSError, yaml.YAMLError) as exc:
                logger.warning("chatrooms.yaml unreadable (%s); starting with no rooms", exc)
                return
            data = raw.get("chat_rooms") if isinstance(raw, dict) else None
            if isinstance(data, list):
                self._rooms = [dict(room) for room in data if isinstance(room, dict)]

    def _persist_locked(self) -> None:
        self.yaml_path.parent.mkdir(parents=True, exist_ok=True)
        payload = yaml.safe_dump(
            {"chat_rooms": self._rooms},
            sort_keys=False,
            allow_unicode=True,
            default_flow_style=False,
        )
        tmp = self.yaml_path.with_name(self.yaml_path.name + ".tmp")
        tmp.write_text(payload, encoding="utf-8")
        os.replace(tmp, self.yaml_path)

    def list(self) -> list[dict]:
        with self._lock:
            return [dict(room) for room in self._rooms]

    def get(self, name: str) -> dict | None:
        with self._lock:
            for room in self._rooms:
                if room.get("name") == name:
                    return dict(room)
            return None

    def create(self, room: dict) -> dict:
        validated = self._validate(room)
        with self._lock:
            if any(r.get("name") == validated["name"] for r in self._rooms):
                raise RoomExistsError(f"room already exists: {validated['name']}")
            self._rooms.append(validated)
            self._persist_locked()
            return dict(validated)

    def update(self, name: str, room: dict) -> dict:
        validated = self._validate(room)
        if validated["name"] != name:
            raise ValueError(f"room name mismatch: url {name!r} != body {validated['name']!r}")
        with self._lock:
            for i, r in enumerate(self._rooms):
                if r.get("name") == name:
                    self._rooms[i] = validated
                    break
            else:
                raise KeyError(name)
            self._persist_locked()
            return dict(validated)

    def delete(self, name: str) -> None:
        with self._lock:
            index = next(
                (i for i, r in enumerate(self._rooms) if r.get("name") == name), None
            )
            if index is None:
                raise KeyError(name)
            self._rooms.pop(index)
            self._persist_locked()
        RoomStore(name, self.config, root=self.chatrooms_root).delete()

    def remove_persona(self, persona_name: str) -> bool:
        with self._lock:
            changed = False
            for room in self._rooms:
                names = room.get("persona_names")
                if isinstance(names, list) and persona_name in names:
                    room["persona_names"] = [n for n in names if n != persona_name]
                    changed = True
            if changed:
                self._persist_locked()
            return changed

    def _validate(self, room: dict) -> dict:
        try:
            data = Room.model_validate(room).model_dump()
        except ValidationError as exc:
            err = exc.errors()[0]
            loc = ".".join(str(part) for part in err["loc"])
            label = loc if loc else "room"
            raise ValueError(f"invalid {label}: {err['msg']}") from exc
        validate_room_name(data["name"])
        if data["name"].lower() in RESERVED_NAMES:
            raise RoomNameReservedError(f"room name is reserved: {data['name']}")
        if self.personas is not None:
            missing = [n for n in data["persona_names"] if self.personas.get(n) is None]
            if missing:
                raise ValueError(
                    "unknown persona(s) in persona_names: " + ", ".join(missing)
                )
        return data
