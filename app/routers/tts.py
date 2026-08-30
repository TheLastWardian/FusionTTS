import asyncio
import logging
from collections.abc import Coroutine
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response

from app.schemas import SpeakRequest
from app.services.tts.engine import TTSError

logger = logging.getLogger(__name__)

router = APIRouter(tags=["tts"])

_background_tasks: set[asyncio.Task] = set()


def _state(request: Request):
    return request.app.state.app_state


def _spawn(coro: Coroutine[Any, Any, Any]) -> None:
    task = asyncio.create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


@router.get("/status")
async def status(request: Request) -> dict:
    state = _state(request)
    task = getattr(state, "_tts_start_task", None)
    return {
        "engine": await state.tts_engine.status(),
        "dispatcher": {
            "paused": state.dispatcher.is_paused(),
            "stopped": state.dispatcher.is_stopped(),
            "idle": state.dispatcher.is_idle(),
        },
        # hay un start en vuelo (spawn/load en curso): el cliente lo usa para
        # distinguir "cargando" de "el enable fallo" (sin esta, tras un fallo
        # el chip vuelve a off y el flag pendiente se traga clicks hasta el
        # watchdog, y el boton parece roto)
        "starting": task is not None and not task.done(),
    }


def _bump_gen(state) -> int:
    state._tts_gen = getattr(state, "_tts_gen", 0) + 1
    return state._tts_gen


async def _start_and_arm(state, gen: int) -> None:
    try:
        await state.tts_engine.start()
    except Exception as exc:
        logger.warning("tts engine no pudo arrancar: %s", exc)
        return
    if getattr(state, "_tts_gen", 0) != gen:
        logger.info("tts: se desactivo durante la carga; no habilito el config")
        return
    state.config.set("tts_enabled", True)


@router.post("/enable")
async def enable(request: Request) -> JSONResponse:
    state = _state(request)
    task = getattr(state, "_tts_start_task", None)
    if task is not None and not task.done():
        return JSONResponse({"status": "enabling"}, status_code=202)
    status = await state.tts_engine.status()
    server = status.get("server") or {}
    if status["state"] == "running" and server.get("status") == "ready":
        state.config.set("tts_enabled", True)
        return JSONResponse({"status": "already"})
    task = asyncio.create_task(_start_and_arm(state, getattr(state, "_tts_gen", 0)))
    state._tts_start_task = task
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return JSONResponse({"status": "enabling"}, status_code=202)


@router.post("/disable")
async def disable(request: Request) -> dict:
    state = _state(request)
    _bump_gen(state)  # anula cualquier start en vuelo (no arma tras la carga)
    try:
        await state.tts_engine.stop()
    except TTSError as exc:
        logger.warning("tts disable: %s", exc)
    await state.dispatcher.stop()
    state.config.set("tts_enabled", False)
    return {"status": "disabled"}


@router.post("/stop")
async def stop(request: Request) -> dict:
    await _state(request).dispatcher.stop()
    return {"status": "stopped"}


@router.post("/pause")
async def pause(request: Request) -> dict:
    await _state(request).dispatcher.pause()
    return {"status": "paused"}


@router.post("/resume")
async def resume(request: Request) -> dict:
    await _state(request).dispatcher.resume()
    return {"status": "resumed"}


@router.post("/speak")
async def speak(payload: SpeakRequest, request: Request) -> Response:
    state = _state(request)
    if (await state.tts_engine.status())["state"] != "running":
        raise HTTPException(status_code=409, detail="TTS no está activo")
    if payload.persona:
        audio_b64, transcript, language = state.dispatcher.resolve_persona(payload.persona)
    else:
        audio_b64, transcript, language = "", "", None
    try:
        result = await state.tts_engine.synthesize(
            payload.text, audio_b64, transcript, language=language
        )
    except TTSError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return Response(
        content=result.audio,
        media_type="audio/wav",
        headers={"X-Sample-Rate": str(result.sample_rate)},
    )
