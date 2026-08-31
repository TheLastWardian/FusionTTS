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


FOR_INSTRUCT_NAME = "For Instruct"


def ensure_for_instruct(store: PersonaStore) -> None:
    """Persona-sistema para voice design (instruct): sin audio de referencia,
    el modelo genera la voz con el instruct global. Idempotente: si se borro,
    se re-crea con defaults en el proximo arranque."""
    if store.get(FOR_INSTRUCT_NAME) is not None:
        return
    try:
        store.create(
            {
                "name": FOR_INSTRUCT_NAME,
                "description": (
                    "Voz de prueba para voice design (instruct). Sin audio de "
                    "referencia: el modelo genera la voz con el instruct de la TTS."
                ),
                "system_prompt": (
                    "You are For Instruct, a neutral test voice for voice design "
                    "(instruct) experiments. Keep replies short and natural."
                ),
                "router_hints": [],
                "avatar_color": "#888888",
                "avatar_image": None,
                "reference_audio": None,
                "reference_audio_transcript": None,
                "reference_audio_language": None,
            }
        )
        logger.info("persona-sistema %s creada (voice design / instruct)", FOR_INSTRUCT_NAME)
    except (ValueError, PersonaExistsError) as exc:
        logger.warning("no se pudo crear la persona-sistema %s: %s", FOR_INSTRUCT_NAME, exc)


class PersonaStore:
    def __init__(
        self,
        personas_yaml: Path | None = None,
        audio_dir: Path | None = None,
        avatar_dir: Path | None = None,
        rooms: RoomConfigStore | None = None,
    ) -> None:
        self.yaml_path = Path(personas_yaml) if personas_yaml is not None else paths.PERSONAS_YAML
        self.audio_dir = Path(audio_dir) if audio_dir is not None else paths.PERSONAS_AUDIO_DIR
        self.avatar_dir = Path(avatar_dir) if avatar_dir is not None else paths.PERSONAS_AVATARS_DIR
        self.rooms: RoomConfigStore | None = rooms
        self._personas: list[dict] = []
        self._layout: list[dict] | None = None
        self._layout_columns: int = 2
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
            if isinstance(raw, dict):
                layout_raw = raw.get("layout")
                self._layout = layout_raw if isinstance(layout_raw, list) else None
                cols = raw.get("layout_columns")
                if isinstance(cols, int) and not isinstance(cols, bool) and 1 <= cols <= 4:
                    self._layout_columns = cols

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
        payload = {"personas": self._personas}
        if self._layout is not None:
            payload["layout"] = self._layout
        payload["layout_columns"] = self._layout_columns
        payload = yaml.safe_dump(
            payload,
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

    def set_avatar(self, name: str, rel: str | None) -> dict:
        with self._lock:
            for i, p in enumerate(self._personas):
                if p.get("name") == name:
                    self._personas[i]["avatar_image"] = rel
                    self._persist_locked()
                    return dict(p)
            raise KeyError(name)

    def rename(self, old_name: str, new_name: str) -> dict:
        with self._lock:
            index = next(
                (i for i, p in enumerate(self._personas) if p.get("name") == old_name),
                None,
            )
            if index is None:
                raise KeyError(old_name)
            if any(p.get("name") == new_name for p in self._personas):
                raise PersonaExistsError(f"persona already exists: {new_name}")
            candidate = dict(self._personas[index])
            candidate["name"] = new_name
            validated = self._validate(candidate)
            self._personas[index] = validated
            self._persist_locked()
        self._rename_avatar_file(old_name, new_name)
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
        self._delete_avatar_file(persona)

    def get_layout(self) -> list[dict]:
        with self._lock:
            return self._normalize_layout(self._layout)

    def save_layout(self, layout: list, columns: int | None = None) -> list[dict]:
        validated = self._validate_layout(layout)
        if columns is not None:
            if not isinstance(columns, int) or isinstance(columns, bool) or not 1 <= columns <= 4:
                raise ValueError("columns debe ser un entero entre 1 y 4")
        with self._lock:
            if columns is not None:
                self._layout_columns = columns
            self._layout = self._normalize_layout(validated)
            self._persist_locked()
            return [dict(entry) for entry in self._layout]

    def get_layout_columns(self) -> int:
        with self._lock:
            return self._layout_columns

    def _normalize_layout(self, raw: list | None) -> list[dict]:
        """Reglas de la spec v2: desconocidos fuera, duplicados ganan la
        primera aparicion, For Instruct fuera, faltantes al final (orden de store)."""
        existing = {p["name"] for p in self._personas if p.get("name")}
        existing.discard(FOR_INSTRUCT_NAME)
        out: list[dict] = []
        seen: set[str] = set()
        folders: set[str] = set()
        if isinstance(raw, list):
            for entry in raw:
                if not isinstance(entry, dict):
                    continue
                name = entry.get("name")
                if not isinstance(name, str) or not name:
                    continue
                etype = entry.get("type")
                if etype == "persona":
                    if name in existing and name not in seen:
                        seen.add(name)
                        out.append({"type": "persona", "name": name})
                elif etype == "folder":
                    if name in folders:
                        continue
                    # seen se actualiza al filtrar: un miembro duplicado DENTRO
                    # de la misma carpeta tambien queda descartado (gana la primera)
                    members = entry.get("personas")
                    clean = []
                    for m in (members if isinstance(members, list) else []):
                        if isinstance(m, str) and m in existing and m not in seen:
                            seen.add(m)
                            clean.append(m)
                    folders.add(name)
                    out.append({"type": "folder", "name": name, "personas": clean})
        for persona in self._personas:
            name = persona.get("name")
            if name in existing and name not in seen:
                seen.add(name)
                out.append({"type": "persona", "name": name})
        return out

    @staticmethod
    def _validate_layout(layout: list) -> list[dict]:
        if not isinstance(layout, list):
            raise ValueError("layout debe ser una lista")
        out: list[dict] = []
        folder_names: set[str] = set()
        seen: set[str] = set()
        for entry in layout:
            if not isinstance(entry, dict):
                raise ValueError("cada entrada del layout debe ser un objeto")
            etype = entry.get("type")
            name = entry.get("name")
            if not isinstance(name, str) or not name:
                raise ValueError("entrada de layout: 'name' debe ser un string no vacio")
            if etype == "persona":
                if name in seen:
                    raise ValueError(f"la persona {name!r} aparece mas de una vez en el layout")
                seen.add(name)
                out.append({"type": "persona", "name": name})
            elif etype == "folder":
                if name in folder_names:
                    raise ValueError(f"carpeta duplicada: {name!r}")
                folder_names.add(name)
                members = entry.get("personas", [])
                if not isinstance(members, list) or not all(isinstance(m, str) for m in members):
                    raise ValueError(f"carpeta {name!r}: 'personas' debe ser una lista de strings")
                for m in members:
                    if m in seen:
                        raise ValueError(f"la persona {m!r} aparece mas de una vez en el layout")
                    seen.add(m)
                out.append({"type": "folder", "name": name, "personas": list(members)})
            else:
                raise ValueError(f"type de entrada de layout invalido: {etype!r}")
        return out

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

    def _safe_avatar_path(self, rel: str) -> Path | None:
        rel_path = Path(rel)
        if rel_path.is_absolute() or ".." in rel_path.parts:
            return None
        candidate = self.avatar_dir.parent / rel_path
        try:
            candidate.resolve().relative_to(self.avatar_dir.resolve())
        except (OSError, ValueError):
            return None
        return candidate if candidate.is_file() else None

    def _delete_avatar_file(self, persona: dict) -> None:
        rel = persona.get("avatar_image")
        if not rel:
            return
        target = self._safe_avatar_path(str(rel))
        if target is not None:
            try:
                target.unlink()
            except OSError as exc:
                logger.warning(
                    "persona %s: could not remove avatar %s: %s",
                    persona.get("name"), target, exc,
                )

    def _rename_avatar_file(self, old_name: str, new_name: str) -> None:
        persona = self.get(new_name)
        rel = persona.get("avatar_image") if persona else None
        if not rel:
            return
        src = self._safe_avatar_path(str(rel))
        if src is None:
            return
        dst = self.avatar_dir / f"{new_name}{src.suffix}"
        try:
            src.replace(dst)
        except OSError as exc:
            logger.warning(
                "persona %s -> %s: could not rename avatar %s: %s", old_name, new_name, src, exc
            )
            return
        with self._lock:
            for p in self._personas:
                if p.get("name") == new_name:
                    p["avatar_image"] = f"{self.avatar_dir.name}/{dst.name}"
            self._persist_locked()

    @staticmethod
    def _validate(persona: dict) -> dict:
        try:
            return Persona.model_validate(persona).model_dump()
        except ValidationError as exc:
            err = exc.errors()[0]
            loc = ".".join(str(part) for part in err["loc"])
            label = loc if loc else "persona"
            raise ValueError(f"invalid {label}: {err['msg']}") from exc
