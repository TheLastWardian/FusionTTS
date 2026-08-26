from __future__ import annotations

import logging
import os
import threading
from pathlib import Path
from typing import TYPE_CHECKING

import yaml
from pydantic import ValidationError

from app import paths
from app.schemas import Persona

if TYPE_CHECKING:
    from app.rooms import RoomConfigStore

logger = logging.getLogger(__name__)


class PersonaExistsError(Exception):
    pass


class PersonaStore:
    def __init__(
        self,
        personas_yaml: Path | None = None,
        audio_dir: Path | None = None,
        rooms: RoomConfigStore | None = None,
    ) -> None:
        self.yaml_path = Path(personas_yaml) if personas_yaml is not None else paths.PERSONAS_YAML
        self.audio_dir = Path(audio_dir) if audio_dir is not None else paths.PERSONAS_AUDIO_DIR
        self.rooms: RoomConfigStore | None = rooms
        self._personas: list[dict] = []
        self._lock = threading.Lock()
        self._load()

    def _load(self) -> None:
        with self._lock:
            self._personas = []
            if not self.yaml_path.exists():
                self._persist_locked()
                return
            try:
                raw = yaml.safe_load(self.yaml_path.read_text(encoding="utf-8"))
            except (OSError, yaml.YAMLError) as exc:
                logger.warning("personas.yaml unreadable (%s); starting with no personas", exc)
                return
            data = raw.get("personas") if isinstance(raw, dict) else None
            if isinstance(data, list):
                self._personas = [
                    self._normalize(dict(persona)) for persona in data if isinstance(persona, dict)
                ]

    @staticmethod
    def _normalize(persona: dict) -> dict:
        hints = persona.get("router_hints")
        if isinstance(hints, str):
            persona["router_hints"] = [hint.strip() for hint in hints.split(",") if hint.strip()]
        elif not isinstance(hints, list):
            persona["router_hints"] = []
        return persona

    def _persist_locked(self) -> None:
        self.yaml_path.parent.mkdir(parents=True, exist_ok=True)
        payload = yaml.safe_dump(
            {"personas": self._personas},
            sort_keys=False,
            allow_unicode=True,
            default_flow_style=False,
        )
        tmp = self.yaml_path.with_name(self.yaml_path.name + ".tmp")
        tmp.write_text(payload, encoding="utf-8")
        os.replace(tmp, self.yaml_path)

    def list(self) -> list[dict]:
        with self._lock:
            return [dict(persona) for persona in self._personas]

    def get(self, name: str) -> dict | None:
        with self._lock:
            for persona in self._personas:
                if persona.get("name") == name:
                    return dict(persona)
            return None

    def create(self, persona: dict) -> dict:
        validated = self._validate(persona)
        with self._lock:
            if any(p.get("name") == validated["name"] for p in self._personas):
                raise PersonaExistsError(f"persona already exists: {validated['name']}")
            self._personas.append(validated)
            self._persist_locked()
            return dict(validated)

    def update(self, name: str, persona: dict) -> dict:
        validated = self._validate(persona)
        if validated["name"] != name:
            raise ValueError(f"persona name mismatch: url {name!r} != body {validated['name']!r}")
        with self._lock:
            for i, p in enumerate(self._personas):
                if p.get("name") == name:
                    self._personas[i] = validated
                    break
            else:
                raise KeyError(name)
            self._persist_locked()
            return dict(validated)

    def delete(self, name: str) -> None:
        with self._lock:
            index = next(
                (i for i, p in enumerate(self._personas) if p.get("name") == name), None
            )
            if index is None:
                raise KeyError(name)
            persona = self._personas[index]
            self._personas.pop(index)
            self._persist_locked()
        if self.rooms is not None:
            self.rooms.remove_persona(name)
        self._delete_audio_files(persona)

    def _delete_audio_files(self, persona: dict) -> None:
        for key in ("reference_audio", "reference_audio_transcript"):
            rel = persona.get(key)
            if not rel:
                continue
            target = self._safe_audio_path(str(rel))
            if target is not None:
                try:
                    target.unlink()
                except OSError as exc:
                    logger.warning(
                        "persona %s: could not remove %s: %s", persona.get("name"), target, exc
                    )

    def _safe_audio_path(self, rel: str) -> Path | None:
        rel_path = Path(rel)
        if rel_path.is_absolute() or ".." in rel_path.parts:
            return None
        candidate = self.audio_dir.parent / rel_path
        try:
            candidate.resolve().relative_to(self.audio_dir.resolve())
        except (OSError, ValueError):
            return None
        return candidate if candidate.is_file() else None

    @staticmethod
    def _validate(persona: dict) -> dict:
        try:
            return Persona.model_validate(persona).model_dump()
        except ValidationError as exc:
            err = exc.errors()[0]
            loc = ".".join(str(part) for part in err["loc"])
            label = loc if loc else "persona"
            raise ValueError(f"invalid {label}: {err['msg']}") from exc
