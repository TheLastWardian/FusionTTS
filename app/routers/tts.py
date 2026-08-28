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
    return {
        "engine": await state.tts_engine.status(),
        "dispatcher": {
            "paused": state.dispatcher.is_paused(),
            "stopped": state.dispatcher.is_stopped(),
            "idle": state.dispatcher.is_idle(),
        },
    }


async def _start_and_arm(state) -> None:
    try:
        await state.tts_engine.start()
    except Exception as exc:
        logger.warning("tts engine no pudo arrancar: %s", exc)
        return
    state.config.set("tts_enabled", True)


@router.post("/enable")
async def enable(request: Request) -> JSONResponse:
    state = _state(request)
    status = await state.tts_engine.status()
    server = status.get("server") or {}
    if status["state"] == "running" and server.get("status") == "ready":
        state.config.set("tts_enabled", True)
        return JSONResponse({"status": "already"})
    _spawn(_start_and_arm(state))
    return JSONResponse({"status": "enabling"}, status_code=202)


@router.post("/disable")
async def disable(request: Request) -> dict:
    state = _state(request)
    await state.tts_engine.stop()
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
