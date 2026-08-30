from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

from app.persistence import ReprocessBlocked
from app.schemas import MessageTextUpdate, ReprocessRequest
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
        summary=store.load_summary(),
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


@router.delete("/rooms/{room}/messages", status_code=204)
async def clear_room_messages(request: Request, room: str) -> None:
    try:
        store = request.app.state.app_state.get_room_store(room)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    store.clear_history()


@router.post("/rooms/{room}/messages/{message_uuid}/reprocess")
async def reprocess_message(
    request: Request, room: str, message_uuid: str, body: ReprocessRequest | None = None
) -> dict:
    # Rewind: borra el mensaje de usuario y todo lo posterior. El frontend
    # re-envia el texto para que la room vuelva a responder. Si hay mensajes
    # de usuario debajo y no viene confirm -> 409 (el UI pide confirmacion).
    try:
        store = request.app.state.app_state.get_room_store(room)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    confirm = body.confirm if body is not None else False
    try:
        text, removed = store.reprocess_truncate(message_uuid, confirm)
    except KeyError:
        raise HTTPException(
            status_code=404, detail=f"message {message_uuid} not found in room {room}"
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except ReprocessBlocked as exc:
        raise HTTPException(
            status_code=409,
            detail={"users_after": exc.users_after},
        )
    return {"text": text, "removed": removed}


COMPACT_KEEP_LAST = 10
COMPACT_MIN_TARGETS = 4
# El modelo hace thinking: el preamble consume el budget de output. Con
# 1500 se quedaba dentro del thinking y el contenido visible salia vacio
# (502). 10000 deja margen para thinking + resumen.
COMPACT_MAX_TOKENS = 10000


def _compact_system_prompt(persona_name: str) -> str:
    return (
        "Eres el encargado de resumir una conversacion de roleplay para que "
        f"el personaje {persona_name} pueda continuar la escena sin ver los "
        "mensajes originales.\n"
        "Reglas:\n"
        "- Escribi en el mismo idioma en que se desarrollo la conversacion.\n"
        "- No inventes detalles, dialogos ni eventos que no esten ahi.\n"
        "- Maximo ~800 palabras.\n"
        "- Usá exactamente esta estructura:\n"
        "\n"
        "## Personajes y estado\n"
        "Quienes participan, como estan, sus relaciones actuales.\n"
        "\n"
        "## Situacion actual\n"
        "Donde quedo la escena: que acaba de pasar, en que momento se corta.\n"
        "\n"
        "## Hilos abiertos\n"
        "Promesas, misterios, decisiones pendientes, conflictos, planes.\n"
        "\n"
        "## Hechos clave\n"
        "Nombres, lugares, objetos, reglas del mundo, eventos ya ocurridos."
    )


def _compact_transcript(messages: list[dict]) -> str:
    lines = []
    for m in messages:
        text = m.get("text", "")
        if m.get("role") == "user":
            lines.append(f"User: {text}")
        else:
            lines.append(f"{m.get('sender')}: {text}")
    return "\n\n".join(lines)


@router.post("/rooms/{room}/compact")
async def compact_room(request: Request, room: str) -> dict:
    # Compactar = resumen rolling: resume todo lo que no este en los ultimos
    # COMPACT_KEEP_LAST mensajes (incluyendo el resumen previo, si hay) y
    # marca esos mensajes como "compacted": dejan de ir al contexto y los
    # representa el summary (build_llm_messages).
    state = request.app.state.app_state
    try:
        store = state.get_room_store(room)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if state.llm is None:
        raise HTTPException(status_code=503, detail="no LLM available")
    targets = store.compact_targets(COMPACT_KEEP_LAST)
    if len(targets) < COMPACT_MIN_TARGETS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"no hay suficientes mensajes para compactar ({len(targets)}; "
                f"minimo {COMPACT_MIN_TARGETS}; se conservan los ultimos "
                f"{COMPACT_KEEP_LAST})"
            ),
        )
    eligible = resolve_room_personas(state.rooms, state.personas, room)
    if not eligible:
        raise HTTPException(status_code=503, detail="room has no personas")
    previous = store.load_summary()
    parts = []
    if previous:
        parts.append("## Resumen anterior\n" + previous)
    parts.append("## Conversacion a resumir\n" + _compact_transcript(targets))
    try:
        summary = await state.llm.chat(
            [
                {"role": "system", "content": _compact_system_prompt(eligible[0])},
                {"role": "user", "content": "\n\n".join(parts)},
            ],
            max_tokens=COMPACT_MAX_TOKENS,
            temperature=0.3,
        )
    except LLMError as exc:
        raise HTTPException(
            status_code=503, detail=f"LLM server unavailable: {exc.detail[:200]}"
        ) from exc
    summary = summary.strip()
    if not summary:
        raise HTTPException(status_code=502, detail="el LLM devolvio un resumen vacio")
    marked = store.apply_compaction([m["uuid"] for m in targets], summary)
    try:
        summary_tokens = await state.llm.count_tokens(
            [{"role": "user", "content": summary}]
        )
    except LLMError:
        summary_tokens = None
    return {
        "compacted": marked,
        "kept": COMPACT_KEEP_LAST,
        "summary": summary,
        "summary_tokens": summary_tokens,
    }


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
