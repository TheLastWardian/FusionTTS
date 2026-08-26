from pathlib import Path

from app.config import ConfigStore
from app.services.tts.engine import TTSEngine
from app.services.tts.omnivoice import OmniVoiceEngine


def create_engine(name: str, config: ConfigStore, server_dir: Path | None = None) -> TTSEngine:
    if name == "omnivoice":
        return OmniVoiceEngine(config, server_dir=server_dir)
    raise ValueError(f"motor TTS desconocido: '{name}'")
