import asyncio
import base64
import binascii
import json
import logging
import random
import re
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
from app.services.tts.splitter import FULL_CHUNK_LEN, chunk_text_punctuation

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


# Misma semantica que extractSentences de TalkWithMe: cada oracion que
# cierra en .!? es una unidad TTS por si sola (sin merge de 120 chars:
# los chunks largos son donde el modelo salta tramos a mitad de audio).
_SENT_RE = re.compile(r"[^.!?]*[.!?]+")


def _drain_sentences(buf: str) -> tuple[list[str], str]:
    ready: list[str] = []
    last_end = 0
    for m in _SENT_RE.finditer(buf):
        s = m.group(0).strip()
        if s:
            ready.append(s)
        last_end = m.end()
    return ready, buf[last_end:]


def _audio_event(chunk) -> dict:
    return {
        "type": "audio_chunk",
        "persona": chunk.persona,
        "message_id": chunk.message_id,
        "sentence_id": chunk.sentence_id,
        "text": chunk.text,
        "sample_rate": chunk.sample_rate,
        "audio": base64.b64encode(chunk.audio).decode(),
    }


async def _tts_pump(
    disp,
    local_q: asyncio.Queue,
    stop_event: asyncio.Event,
    drained_event: asyncio.Event,
    room_store=None,
) -> None:
    """Mueve audio y notificaciones de falla del dispatcher a local_q.

    Único consumidor de audio_q/fail_q de la ronda. Sale cuando stop_event
    está seteado y ambas cajas quedaron vacías (todo el audio ya está en
    local_q) y señala drained_event. Si room_store se pasa, cada chunk
    completo se persiste por room/persona (gated por save_audio en
    RoomStore.save_wav) y se registra en el audio[] del mensaje.
    """
    try:
        while True:
            audio_f = asyncio.ensure_future(disp.wait_audio())
            fail_f = asyncio.ensure_future(disp.wait_failure())
            stop_f = asyncio.ensure_future(stop_event.wait())
            try:
                done, _ = await asyncio.wait(
                    {audio_f, fail_f, stop_f}, return_when=asyncio.FIRST_COMPLETED
                )
            except BaseException:
                for f in (audio_f, fail_f, stop_f):
                    f.cancel()
                raise
            for f in done:
                if f is audio_f:
                    chunk = audio_f.result()
                    if room_store is not None:
                        rel = room_store.save_wav(
                            chunk.persona, chunk.message_id, chunk.sentence_id, chunk.audio
                        )
                        if rel is not None:
                            room_store.add_audio(chunk.message_id, rel)
                    local_q.put_nowait(_audio_event(chunk))
                elif f is fail_f:
                    local_q.put_nowait(fail_f.result())
            for f in (audio_f, fail_f, stop_f):
                if not f.done():
                    f.cancel()
            if stop_event.is_set() and disp.audio_empty() and disp.fail_empty():
                break
    finally:
        drained_event.set()


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

    tts_on = bool(config.get("tts_enabled"))
    tts_mode = config.get("tts_mode")
    local_q: asyncio.Queue | None = None
    pump_task: asyncio.Task | None = None
    pump_stop = asyncio.Event()
    pump_drained = asyncio.Event()
    finished = False

    def _drain_local() -> list[str]:
        out: list[str] = []
        if local_q is None:
            return out
        while True:
            try:
                out.append(_sse(local_q.get_nowait()))
            except asyncio.QueueEmpty:
                return out

    if tts_on:
        state.dispatcher.reset()
        local_q = asyncio.Queue()
        pump_task = asyncio.create_task(
            _tts_pump(state.dispatcher, local_q, pump_stop, pump_drained, room_store)
        )
        yield _sse({"type": "tts_state", "state": "on"})

    try:
        room = _find_room(state, req.chat_room)
        echo = bool(room and room.get("echo_chamber"))
        fixed: list[str] | None = None
        if isinstance(req.who_answers, list):
            # seleccion explicita: responden exactamente esas, en orden de clic
            fixed = list(req.who_answers)
            if echo:
                fixed = fixed[:1]
            max_replies = len(fixed)
        elif req.who_answers in eligible:
            # nombre explicito (str): responde solo esa
            fixed = [req.who_answers]
            max_replies = 1
        else:
            # "router" / "random" / nombre inexistente: comportamiento anterior
            max_replies = min(config.get("max_persona_replies"), len(eligible))
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

        room_store.append(
            new_message("user", "user", req.message, image=image_rel, message_uuid=user_message_id)
        )

        if fixed is not None:
            first = fixed[0]
        else:
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
                if tts_on:
                    await state.dispatcher.stop()
                break
            if fixed is not None:
                persona_name = fixed[index]
            elif index == 0:
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
            buf = ""

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
                if tts_on and tts_mode != "full":
                    ready, buf = _drain_sentences(full_text)
                    for sentence in ready:
                        await state.dispatcher.enqueue(sentence, assistant_message_id, persona_name)
                for ev in _drain_local():
                    yield ev
            else:
                global_prompt = config.get("global_system_prompt").strip()
                persona_prompt = (persona.get("system_prompt") or "").strip()
                system_prompt = "\n\n".join(
                    part for part in (global_prompt, persona_prompt) if part
                )
                messages = build_llm_messages(
                    system_prompt,
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
                pending_token = None
                tick = None
                try:
                    # Iteracion manual con tick de 1s: local_q se drena por token
                    # Y por tick. Sin el tick, un tramo sin tokens (fase thinking,
                    # time-to-first-token) retenia el audio ya sintetizado: el
                    # TTS sintetiza en paralelo, pero la entrega a SSE quedaba
                    # acoplada al flujo de tokens del LLM.
                    ait = state.llm.stream_chat(messages, state.cancel_event).__aiter__()
                    pending_token = asyncio.ensure_future(ait.__anext__())
                    while True:
                        tick = asyncio.ensure_future(asyncio.sleep(1.0))
                        done, _ = await asyncio.wait(
                            {pending_token, tick}, return_when=asyncio.FIRST_COMPLETED
                        )
                        if pending_token in done:
                            try:
                                token = pending_token.result()
                            except StopAsyncIteration:
                                pending_token = None
                            else:
                                full_text += token
                                yield _sse(
                                    {"type": "token", "persona": persona_name, "token": token}
                                )
                                # "full": se encola el texto completo al terminar (abajo)
                                if tts_on and tts_mode != "full":
                                    buf += token
                                    ready, buf = _drain_sentences(buf)
                                    for sentence in ready:
                                        await state.dispatcher.enqueue(
                                            sentence, assistant_message_id, persona_name
                                        )
                                pending_token = asyncio.ensure_future(ait.__anext__())
                        if tick not in done:
                            tick.cancel()
                        # audio en vivo: el pump ya movió chunks a local_q;
                        # el texto nunca espera por el TTS
                        for ev in _drain_local():
                            yield ev
                        if pending_token is None:
                            break
                except LLMError as exc:
                    if tts_on:
                        await state.dispatcher.stop()
                    yield _sse({"type": "error", "message": str(exc)})
                    yield _sse({"type": "complete", "cancelled": state.cancel_event.is_set()})
                    return
                finally:
                    for f in (pending_token, tick):
                        if f is not None and not f.done():
                            f.cancel()
                if state.cancel_event.is_set():
                    if tts_on:
                        await state.dispatcher.stop()
                    room_store.append(
                        new_message(
                            "assistant", persona_name, full_text, message_uuid=assistant_message_id
                        )
                    )
                    yield _sse(
                        {
                            "type": "done",
                            "persona": persona_name,
                            "text": full_text,
                            "message_id": assistant_message_id,
                        }
                    )
                    break

            room_store.append(
                new_message("assistant", persona_name, full_text, message_uuid=assistant_message_id)
            )
            yield _sse(
                {
                    "type": "done",
                    "persona": persona_name,
                    "text": full_text,
                    "message_id": assistant_message_id,
                }
            )
            if tts_on:
                if tts_mode == "full":
                    for chunk in chunk_text_punctuation(full_text, FULL_CHUNK_LEN):
                        await state.dispatcher.enqueue(
                            chunk, assistant_message_id, persona_name
                        )
                elif buf.strip():
                    await state.dispatcher.enqueue(
                        buf.strip(), assistant_message_id, persona_name
                    )
                for ev in _drain_local():
                    yield ev

        if tts_on:
            # Entrega en vivo: el wait de completion corre en paralelo como
            # task; se entregan los chunks a medida que el pump los produce
            # (local_q drenada antes de cada wait). La contabilidad es la
            # misma: complete solo cuando cada oración produjo audio o fue
            # contada como perdida.
            done_wait = asyncio.ensure_future(state.dispatcher.wait_until_done())
            while True:
                for ev in _drain_local():
                    yield ev
                if done_wait.done():
                    break
                get_f = asyncio.ensure_future(local_q.get())
                await asyncio.wait(
                    {get_f, done_wait}, return_when=asyncio.FIRST_COMPLETED
                )
                if not get_f.done():
                    get_f.cancel()
                else:
                    # local_q estaba vacía (acaba de drenarse): re-encolar
                    # conserva el orden
                    local_q.put_nowait(get_f.result())
            done_wait.result()
            pump_stop.set()
            await pump_drained.wait()
            pump_task.cancel()
            try:
                await pump_task
            except asyncio.CancelledError:
                pass
            if state.dispatcher.is_stopped():
                # stop: el audio pendiente no se emite (misma semántica de
                # siempre: nada de audio después de un stop)
                while True:
                    try:
                        local_q.get_nowait()
                    except asyncio.QueueEmpty:
                        break
                yield _sse({"type": "tts_state", "state": "stopped"})
            else:
                for ev in _drain_local():
                    yield ev

        finished = True
        yield _sse({"type": "complete", "cancelled": state.cancel_event.is_set()})
    finally:
        if tts_on and not finished:
            try:
                await state.dispatcher.stop()
            except Exception:
                logger.exception("chat: no se pudo detener el dispatcher")
        if pump_task is not None:
            pump_task.cancel()
            try:
                await pump_task
            except asyncio.CancelledError:
                pass


@router.post("/chat")
async def chat(req: ChatRequest, request: Request):
    try:
        validate_room_name(req.chat_room)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    state = request.app.state.app_state
    if isinstance(req.who_answers, list):
        names = list(dict.fromkeys(req.who_answers))
        if not names:
            raise HTTPException(status_code=400, detail="who_answers list is empty")
        eligible = resolve_room_personas(state.rooms, state.personas, req.chat_room)
        bad = [n for n in names if n not in eligible]
        if bad:
            raise HTTPException(
                status_code=400,
                detail="persona(s) no disponible(s) en la room: " + ", ".join(bad),
            )
        req.who_answers = names
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
