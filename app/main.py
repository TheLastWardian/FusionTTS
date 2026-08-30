import shutil
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app import paths
from app.config import ConfigStore
from app.personas import PersonaStore, ensure_for_instruct
from app.routers import chat as chat_router
from app.routers import config as config_router
from app.routers import personas as personas_router
from app.routers import rooms as rooms_router
from app.routers import sessions as sessions_router
from app.routers import tts as tts_router
from app.rooms import RoomConfigStore
from app.logging_setup import setup_logging
from app.schemas import HealthResponse
from app.services.asr.manager import ASRManager
from app.services.llm import LLMClient
from app.services.tts.dispatcher import TTSDispatcher
from app.services.tts.registry import create_engine
from app.state import AppState


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    config = ConfigStore()
    state = AppState(config)
    shutil.rmtree(paths.PERSONAS_PENDING_DIR, ignore_errors=True)
    state.llm = LLMClient(config)
    state.tts_engine = create_engine(config.get("tts_engine"), config)
    state.asr_manager = ASRManager(config)
    state.rooms = RoomConfigStore(config)
    state.personas = PersonaStore(rooms=state.rooms)
    ensure_for_instruct(state.personas)
    state.dispatcher = TTSDispatcher(
        engine=state.tts_engine,
        personas=state.personas,
        config=config,
    )
    await state.dispatcher.start()
    state.rooms.personas = state.personas
    app.state.app_state = state
    yield
    await state.llm.close()
    await state.dispatcher.shutdown()
    await state.tts_engine.close()
    await state.asr_manager.close()


app = FastAPI(title="FusionTTS", lifespan=lifespan)


@app.get("/api/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(status="ok")


app.include_router(chat_router.router, prefix="/api")
app.include_router(config_router.router, prefix="/api")
app.include_router(sessions_router.router, prefix="/api")
app.include_router(personas_router.router, prefix="/api")
app.include_router(rooms_router.router, prefix="/api")
app.include_router(tts_router.router, prefix="/api/tts")
app.mount("/", StaticFiles(directory=paths.STATIC_DIR, html=True), name="static")
