import base64
import binascii
import json
import logging
import random
import uuid
from typing import AsyncIterator

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from app.persistence import new_message, validate_room_name
from app.schemas import ChatRequest
from app.services import vision
from app.services.chat_context import build_llm_messages
from app.services.llm import LLMError
from app.services.persona_router import pick_persona, resolve_room_personas

logger = logging.getLogger(__name__)

router = APIRouter(tags=["chat"])

_VISION_INSTRUCTION = (
    "Describe this image briefly for a chat context. "
    "Focus on people, scene and text visible."
)

_EXT_BY_MIME = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
    "image/gif": ".gif",
}


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


def _ext_for_mime(mime: str) -> str:
    return _EXT_BY_MIME.get(mime.lower(), ".png")


def _find_room(state, chat_room: str) -> dict | None:
    return next(
        (
            r
            for r in state.rooms.list()
            if str(r.get("name", "")).lower() == chat_room.lower()
        ),
        None,
    )


async def _chat_stream(req: ChatRequest, state) -> AsyncIterator[str]:
    config = state.config
    room_store = state.get_room_store(req.chat_room)
    eligible = resolve_room_personas(state.rooms, state.personas, req.chat_room)
    if not eligible:
        yield _sse(
            {"type": "error", "message": f"No eligible personas for room '{req.chat_room}'"}
        )
        yield _sse({"type": "complete", "cancelled": state.cancel_event.is_set()})
        return

    max_replies = min(config.get("max_persona_replies"), len(eligible))
    room = _find_room(state, req.chat_room)
    echo = bool(room and room.get("echo_chamber"))
    if echo:
        max_replies = 1

    user_message_id = req.message_id or str(uuid.uuid4())

    image_rel = None
    description = None
    if req.image_base64 and req.image_mime:
        try:
            image_bytes = base64.b64decode(req.image_base64, validate=True)
        except (binascii.Error, ValueError) as exc:
            logger.warning("chat: corrupt image base64, continuing without image: %s", exc)
            image_bytes = None
        if image_bytes:
            ext = _ext_for_mime(req.image_mime)
            image_rel = room_store.save_image(image_bytes, ext)
            try:
                description = await vision.describe_image(
                    state.llm, image_bytes, ext, _VISION_INSTRUCTION
                )
            except Exception as exc:
                logger.warning("chat: image description failed: %s", exc)
                description = None

    room_store.append(new_message("user", "user", req.message, image=image_rel))

    try:
        first = await pick_persona(
            req.who_answers,
            req.message,
            eligible,
            state.personas,
            state.llm,
            config,
            room_store.history,
        )
    except ValueError as exc:
        yield _sse({"type": "error", "message": str(exc)})
        yield _sse({"type": "complete", "cancelled": state.cancel_event.is_set()})
        return

    replied: list[str] = []
    for index in range(max_replies):
        if state.cancel_event.is_set():
            break
        if index == 0:
            persona_name = first
        else:
            remaining = [n for n in eligible if n not in replied]
            if not remaining:
                break
            persona_name = random.choice(remaining)

        persona = state.personas.get(persona_name)
        if persona is None:
            yield _sse({"type": "error", "message": f"Persona {persona_name} not found"})
            yield _sse({"type": "complete", "cancelled": state.cancel_event.is_set()})
            return
        replied.append(persona_name)

        # id is generated before "start" so T10 can stamp it onto every audio
        # chunk streamed for this message (audio must resolve to the right
        # message before the text stream even finishes)
        assistant_message_id = str(uuid.uuid4())
        yield _sse(
            {
                "type": "start",
                "persona": persona_name,
                "user_message_id": user_message_id,
                "message_id": assistant_message_id,
            }
        )

        if echo:
            full_text = req.message
            yield _sse({"type": "token", "persona": persona_name, "token": full_text})
        else:
            messages = build_llm_messages(
                persona.get("system_prompt", ""),
                persona_name,
                room_store.history,
                config.get("max_context_turns"),
            )
            if index == 0 and description:
                for message in reversed(messages):
                    if message["role"] == "user":
                        message["content"] += f"\n\n[Attached image description: {description}]"
                        break
            full_text = ""
            try:
                async for token in state.llm.stream_chat(messages, state.cancel_event):
                    full_text += token
                    yield _sse(
                        {"type": "token", "persona": persona_name, "token": token}
                    )
            except LLMError as exc:
                yield _sse({"type": "error", "message": str(exc)})
                yield _sse({"type": "complete", "cancelled": state.cancel_event.is_set()})
                return
            if state.cancel_event.is_set():
                room_store.append(new_message("assistant", persona_name, full_text))
                yield _sse(
                    {
                        "type": "done",
                        "persona": persona_name,
                        "text": full_text,
                        "message_id": assistant_message_id,
                    }
                )
                break

        room_store.append(new_message("assistant", persona_name, full_text))
        yield _sse(
            {
                "type": "done",
                "persona": persona_name,
                "text": full_text,
                "message_id": assistant_message_id,
            }
        )

    yield _sse({"type": "complete", "cancelled": state.cancel_event.is_set()})


@router.post("/chat")
async def chat(req: ChatRequest, request: Request):
    try:
        validate_room_name(req.chat_room)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    state = request.app.state.app_state
    state.cancel_event.clear()
    return StreamingResponse(
        _chat_stream(req, state),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/chat/cancel")
async def chat_cancel(request: Request) -> dict:
    request.app.state.app_state.cancel_event.set()
    return {"status": "cancelled"}
