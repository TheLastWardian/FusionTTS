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
    return {
        "room": store.room_name,
        "messages": list(store.history),
        "summary": store.load_summary(),
    }


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
    eligible = resolve_room_personas(state.rooms, state.personas, room, state.config)
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


# Sin limite de tokens en el prompt: es un resumen, no llega al budget de
# output (COMPACT_MAX_TOKENS es el tope duro, margen para thinking).
_COMPACT_SYSTEM_TEMPLATE = """You are a memory compression assistant for a multi-character chat room.
Your job: produce a structured summary that lets EVERY character in the room
continue the conversation without seeing the original messages.

CAST (who is in this room):
- User: the human participant. Their messages are first-class context:
  preserve what they say, ask, decide, promise and feel (as stated, never
  inferred) with the same weight as the characters' lines. Never invent
  their unspoken thoughts or intentions.
{cast}

Analyze the conversation and determine its type, then apply the matching strategy:

**ROLEPLAY / CHARACTER (default):**
- Write as a neutral scene report covering ALL characters - no first person:
  each character reads the same summary, so none can be the narrator.
- Preserve: relationship dynamics between every pair of characters, emotional
  beats, key events, character development, unresolved tensions.
- Include: names, locations, significant objects, established facts about the world.
- Keep each character's traits, voice and promises distinct - do not blend
  characters together or attribute lines to the wrong speaker.

**GENERAL / OTHER:**
- Preserve: key facts established, user preferences revealed, ongoing tasks,
  important context for future responses.
- Prioritize recency: more recent exchanges matter more than older ones.

Rules:
- This is a rolling summary: fold the previous summary into the new one, drop
  what is no longer relevant, and make the result fully self-contained. If
  the previous summary is empty, just summarize the conversation.
- Write the summary in the language the CHARACTERS speak in the conversation
  (not the user's, if they differ), so characters keep answering in that
  language. In a mixed conversation use the characters' dominant language.
  Keep names, places and quoted terms in their original language.
- Be dense and specific - avoid vague summaries.
- Never invent details, dialogue or events that are not in the conversation.
- Preserve proper nouns, names and specific details exactly.
- If something was explicitly decided or agreed upon, include it.
- Images appear as [image: ...] markers: keep only what the marker describes,
  never describe what you cannot see.
- Do NOT include meta-commentary about the summary itself.

Use exactly this structure:

## Characters & state
Who is present, how they are, their current relationships.

## Current situation
Where the scene is now: what just happened, the exact moment it cuts.

## Open threads
Promises, mysteries, pending decisions, conflicts, plans.

## Key facts
Names, places, objects, world rules, events that already happened."""


def _compact_system_prompt(cast: list[str]) -> str:
    return _COMPACT_SYSTEM_TEMPLATE.format(cast="\n".join(cast))


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
    eligible = resolve_room_personas(state.rooms, state.personas, room, state.config)
    if not eligible:
        raise HTTPException(status_code=503, detail="room has no personas")
    previous = store.load_summary()
    parts = [
        "PREVIOUS SUMMARY (may be empty - it covers everything before the "
        "new conversation):\n" + (previous or ""),
        "Conversation to compress (messages since the previous summary):\n"
        + _compact_transcript(targets),
    ]
    by_name = {p["name"]: p for p in state.personas.list()}
    cast = []
    for name in eligible:
        desc = (by_name.get(name) or {}).get("description") or ""
        cast.append(f"- {name}: {desc}" if desc else f"- {name}")
    try:
        summary = await state.llm.chat(
            [
                {"role": "system", "content": _compact_system_prompt(cast)},
                {"role": "user", "content": "\n\n".join(parts)},
            ],
            max_tokens=COMPACT_MAX_TOKENS,
            temperature=1.0,
        )
    except LLMError as exc:
        raise HTTPException(
            status_code=503, detail=f"LLM server unavailable: {exc.detail[:200]}"
        ) from exc
    summary = summary.strip()
    if not summary:
        raise HTTPException(status_code=502, detail="the LLM returned an empty summary")
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
