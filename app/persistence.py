import json
import logging
import os
import re
import shutil
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from app import paths
from app.config import ConfigStore

logger = logging.getLogger(__name__)

ROOM_NAME_RE = re.compile(r"^[A-Za-z0-9 _-]+$")
_ALPHA_RUN_RE = re.compile(r"[A-Za-z0-9]+")


def validate_room_name(name) -> str:
    if not isinstance(name, str) or not name:
        raise ValueError("invalid room name: must be a non-empty string")
    if "/" in name or "\\" in name or ".." in name:
        raise ValueError(f"invalid room name: {name!r}")
    if not ROOM_NAME_RE.fullmatch(name):
        raise ValueError(f"invalid room name: {name!r}")
    return name


def new_message(role: str, sender: str, text: str, audio=None, image=None, message_uuid=None) -> dict:
    return {
        "uuid": message_uuid or str(uuid.uuid4()),
        "role": role,
        "sender": sender,
        "text": text,
        "audio": list(audio or []),
        "image": image,
        "ts": datetime.now(timezone.utc).isoformat(),
    }


class RoomStore:
    def __init__(self, room_name: str, config: ConfigStore, root: Path | None = None) -> None:
        self.room_name = validate_room_name(room_name)
        self.config = config
        self.root = Path(root) if root is not None else paths.CHATROOMS_DIR
        self.dir = self.root / self.room_name
        self.history: list[dict] = []
        self._lock = threading.Lock()
        self._pending_audio: dict[str, list[str]] = {}

    @property
    def history_path(self) -> Path:
        return self.dir / "history.json"

    def _atomic_write_bytes(self, path: Path, data: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_bytes(data)
        os.replace(tmp, path)

    def _write_history(self) -> None:
        payload = json.dumps(self.history, indent=2, ensure_ascii=False) + "\n"
        self._atomic_write_bytes(self.history_path, payload.encode("utf-8"))

    def load(self) -> list[dict]:
        with self._lock:
            self.history = []
            if not self.config.get("save_history"):
                return self.history
            if not self.history_path.exists():
                return self.history
            try:
                raw = json.loads(self.history_path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                logger.warning(
                    "room %s: unreadable history.json (%s); starting with empty history",
                    self.room_name,
                    exc,
                )
                return self.history
            self.history = raw if isinstance(raw, list) else []
            return self.history

    def append(self, message: dict) -> dict:
        with self._lock:
            self.history.append(message)
            pending = self._pending_audio.pop(message.get("uuid"), [])
            if pending:
                message["audio"].extend(pending)
            if self.config.get("save_history"):
                self._write_history()
        return message

    def save_wav(self, persona: str, msg_uuid: str, index: int, wav_bytes: bytes) -> str | None:
        if not self.config.get("save_audio"):
            return None
        rel = f"{self._persona_dirname(persona)}/{msg_uuid}_{index}.wav"
        with self._lock:
            self._atomic_write_bytes(self.dir / rel, wav_bytes)
        return rel

    @staticmethod
    def _persona_dirname(persona) -> str:
        # Mismo charset que ROOM_NAME_RE (sin puntos: imposibilidad de "../").
        name = re.sub(r"[^A-Za-z0-9 _-]", "_", str(persona)).strip(" _")
        return name or "persona"

    def add_audio(self, msg_uuid: str, rel_path: str) -> bool:
        # Registra el wav guardado en el audio[] del mensaje. El audio puede
        # llegar ANTES de que el mensaje se appendee (TTS corre en paralelo al
        # LLM): si no existe todavía, queda encolado y lo drena append().
        with self._lock:
            for m in self.history:
                if m.get("uuid") == msg_uuid:
                    m["audio"].append(rel_path)
                    if self.config.get("save_history"):
                        self._write_history()
                    return True
            self._pending_audio.setdefault(msg_uuid, []).append(rel_path)
            return False

    def save_image(self, image_bytes: bytes, ext: str = ".png") -> str | None:
        if not self.config.get("save_history"):
            return None
        ext = self._sanitize_ext(ext)
        rel = f"images/{uuid.uuid4()}{ext}"
        with self._lock:
            self._atomic_write_bytes(self.dir / rel, image_bytes)
        return rel

    @staticmethod
    def _sanitize_ext(ext: str) -> str:
        match = _ALPHA_RUN_RE.search(str(ext).lstrip("."))
        return "." + (match.group(0).lower() if match else "png")

    def delete(self) -> None:
        with self._lock:
            shutil.rmtree(self.dir, ignore_errors=True)
