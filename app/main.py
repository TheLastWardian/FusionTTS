from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app import paths
from app.config import ConfigStore
from app.personas import PersonaStore
from app.routers import chat as chat_router
from app.routers import config as config_router
from app.routers import personas as personas_router
from app.routers import rooms as rooms_router
from app.routers import sessions as sessions_router
from app.rooms import RoomConfigStore
from app.schemas import HealthResponse
from app.services.llm import LLMClient
from app.state import AppState


@asynccontextmanager
async def lifespan(app: FastAPI):
    config = ConfigStore()
    state = AppState(config)
    state.llm = LLMClient(config)
    state.rooms = RoomConfigStore(config)
    state.personas = PersonaStore(rooms=state.rooms)
    state.rooms.personas = state.personas
    app.state.app_state = state
    yield
    await state.llm.close()


app = FastAPI(title="FusionTTS", lifespan=lifespan)


@app.get("/api/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(status="ok")


app.include_router(chat_router.router, prefix="/api")
app.include_router(config_router.router, prefix="/api")
app.include_router(sessions_router.router, prefix="/api")
app.include_router(personas_router.router, prefix="/api")
app.include_router(rooms_router.router, prefix="/api")
app.mount("/", StaticFiles(directory=paths.STATIC_DIR, html=True), name="static")
