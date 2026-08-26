import threading
from typing import Any

from app.config import ConfigStore
from app.persistence import RoomStore
from app.services.llm import LLMClient


class AppState:
    def __init__(self, config: ConfigStore) -> None:
        self.config: ConfigStore = config
        self.llm: LLMClient | None = None
        self.tts_engine: Any = None
        self.dispatcher: Any = None
        self.asr_manager: Any = None
        self._room_stores: dict[str, RoomStore] = {}
        self._room_stores_lock = threading.Lock()

    def get_room_store(self, room_name: str) -> RoomStore:
        with self._room_stores_lock:
            store = self._room_stores.get(room_name)
            if store is None:
                store = RoomStore(room_name, self.config)
                store.load()
                self._room_stores[room_name] = store
            return store
