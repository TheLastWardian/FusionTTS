import asyncio
import threading
from typing import Any

from app.config import ConfigStore
from app.persistence import RoomStore
from app.personas import PersonaStore
from app.rooms import RoomConfigStore
from app.services.llm import LLMClient


class AppState:
    def __init__(self, config: ConfigStore) -> None:
        self.config: ConfigStore = config
        self.llm: LLMClient | None = None
        self.personas: PersonaStore | None = None
        self.rooms: RoomConfigStore | None = None
        self.tts_engine: Any = None
        self.dispatcher: Any = None
        self.asr_manager: Any = None
        self.pending_personas: dict[str, dict] = {}
        self.pending_personas_lock = threading.Lock()
        self._room_stores: dict[str, RoomStore] = {}
        self._room_stores_lock = threading.Lock()
        self.cancel_event = asyncio.Event()

    def get_room_store(self, room_name: str) -> RoomStore:
        with self._room_stores_lock:
            store = self._room_stores.get(room_name)
            if store is None:
                store = RoomStore(room_name, self.config)
                store.load()
                self._room_stores[room_name] = store
            return store

    def drop_room_store(self, room_name: str) -> None:
        with self._room_stores_lock:
            self._room_stores.pop(room_name, None)
