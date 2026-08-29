from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

from app.schemas import MessageTextUpdate
from app.services.chat_context import build_llm_messages, build_system_prompt
from app.services.llm import LLMError
from app.services.persona_router import resolve_room_personas

router = APIRouter()

FILE_MEDIA_TYPES = {
    ".wav": "audio/wav",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
}


@router.get("/session/history")
async def session_history(request: Request, room: str | None = None) -> dict:
    if room is None:
        raise HTTPException(status_code=400, detail="room is required")
    try:
        store = request.app.state.app_state.get_room_store(room)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"room": store.room_name, "messages": list(store.history)}


@router.get("/rooms/{room}/context-usage")
async def room_context_usage(request: Request, room: str) -> dict:
    # Cuanto contexto ocupa la room AHORA: arma los mensajes exactos que
    # se enviarian al LLM (mismas reglas que el chat) y el server los
    # tokeniza (sonda max_tokens=0). System prompt de la 1ra persona.
    state = request.app.state.app_state
    try:
        store = state.get_room_store(room)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    config = state.config
    eligible = resolve_room_personas(state.rooms, state.personas, room)
    if not eligible or state.llm is None:
        raise HTTPException(status_code=503, detail="no LLM available for this room")
    persona = state.personas.get(eligible[0])
    if persona is None:
        raise HTTPException(status_code=503, detail="room persona not found")
    system_prompt = build_system_prompt(config, persona)
    messages = build_llm_messages(
        system_prompt,
        eligible[0],
        store.history,
        config.get("max_context_turns"),
    )
    try:
        prompt_tokens = await state.llm.count_tokens(messages)
    except LLMError as exc:
        raise HTTPException(
            status_code=503, detail=f"LLM server unavailable: {exc.detail[:200]}"
        ) from exc
    try:
        context_window = await state.llm.get_context_window()
    except LLMError:
        context_window = None
    percent = (
        round(prompt_tokens / context_window * 100, 1) if context_window else None
    )
    max_turns = config.get("max_context_turns") or 0
    return {
        "prompt_tokens": prompt_tokens,
        "context_window": context_window,
        "percent": percent,
        "turns_included": min(len(store.history), max_turns) if max_turns else 0,
        "history_total": len(store.history),
    }


@router.patch("/rooms/{room}/messages/{message_uuid}")
async def edit_message(
    request: Request, room: str, message_uuid: str, body: MessageTextUpdate
) -> dict:
    try:
        store = request.app.state.app_state.get_room_store(room)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        return store.edit_message(message_uuid, body.text)
    except KeyError:
        raise HTTPException(
            status_code=404, detail=f"message not found: {message_uuid}"
        ) from None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None


@router.delete("/rooms/{room}/messages/{message_uuid}", status_code=204)
async def delete_message(request: Request, room: str, message_uuid: str) -> None:
    try:
        store = request.app.state.app_state.get_room_store(room)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not store.delete_message(message_uuid):
        raise HTTPException(status_code=404, detail=f"message not found: {message_uuid}")


@router.get("/rooms/{room}/file/{filename:path}")
async def room_file(request: Request, room: str, filename: str) -> FileResponse:
    try:
        store = request.app.state.app_state.get_room_store(room)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not filename or Path(filename).is_absolute() or ".." in Path(filename).parts:
        raise HTTPException(status_code=400, detail=f"invalid filename: {filename}")
    target = store.dir / filename
    try:
        target.resolve().relative_to(store.dir.resolve())
    except (OSError, ValueError):
        raise HTTPException(status_code=400, detail=f"invalid filename: {filename}")
    if not target.is_file():
        raise HTTPException(status_code=404, detail=f"file not found: {filename}")
    return FileResponse(target, media_type=FILE_MEDIA_TYPES.get(target.suffix.lower(), "application/octet-stream"))
