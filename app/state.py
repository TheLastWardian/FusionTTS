from typing import Any

from app.config import ConfigStore


class AppState:
    def __init__(self, config: ConfigStore) -> None:
        self.config: ConfigStore = config
        self.tts_engine: Any = None
        self.dispatcher: Any = None
        self.asr_manager: Any = None
