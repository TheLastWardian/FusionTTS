import json
import logging
import re
import uuid
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse

from app import paths
from app.personas import FOR_INSTRUCT_NAME, PersonaExistsError
from app.schemas import (
    PERSONA_NAME_RE,
    Persona,
    PersonaDraftAccept,
    PersonaRename,
    TranscriptUpdate,
)
from app.services.asr.engine import ASREngineError, ASRError
from app.services.llm import LLMError

logger = logging.getLogger(__name__)

router = APIRouter()

AUDIO_MEDIA_TYPES = {
    ".wav": "audio/wav",
    ".mp3": "audio/mpeg",
    ".ogg": "audio/ogg",
    ".flac": "audio/flac",
}

AVATAR_MEDIA_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
}
AVATAR_MAX_BYTES = 15 * 1024 * 1024

_INVALID_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_ENG_SUFFIX = "_Eng"
_LATINO_SUFFIX = "_Latino"
_DEFAULT_COLOR = "#888888"
_HEX_COLOR = re.compile(r"^#[0-9a-fA-F]{3}([0-9a-fA-F]{3})?$")
_GENERATE_MAX_TOKENS = 1200


def sanitize_audio_stem(stem: str) -> str:
    return _INVALID_FILENAME_CHARS.sub("_", stem).strip().strip(".")


def parse_name_from_filename(filename: str) -> tuple[str, str | None]:
    stem = Path(filename).stem
    if stem.lower().endswith(_ENG_SUFFIX.lower()):
        return stem[: -len(_ENG_SUFFIX)], "en"
    if stem.lower().endswith(_LATINO_SUFFIX.lower()):
        return stem[: -len(_LATINO_SUFFIX)], "es"
    return stem, None


def _build_prompt(filename: str, transcript: str, language: str | None) -> str:
    return (
        "You are creating a character persona for a role-play chat app from an uploaded voice sample.\n"
        "Using the filename and the transcription below, output a JSON object with EXACTLY these keys:\n"
        '- "name": the character name (letters, numbers, spaces and hyphens only)\n'
        '- "source": the fictional origin (game, anime, show) if identifiable, else ""\n'
        '- "description": one or two sentences describing the character\n'
        '- "system_prompt": the character persona written in first person, starting exactly with "You are" (e.g. "You are Yuffie, a ..."). Never use meta phrasing like "role-play", "roleplay" or "act as"\n'
        '- "color": a hex color for the avatar (e.g. "#2E8B57")\n'
        '- "language": the language the character speaks (ISO code, e.g. "en", "es")\n'
        "If you cannot identify the character, use sensible defaults from the name and the audio content.\n"
        "Respond with ONLY the JSON object: no markdown fences, no extra text.\n\n"
        f"Filename: {filename}\n"
        f"Audio language hint: {language or 'unknown'}\n"
        f"Transcription:\n{transcript}"
    )


def _extract_json_object(text: str) -> dict | None:
    candidate = text.strip()
    if candidate.startswith("```"):
        candidate = re.sub(r"^```[a-zA-Z]*\s*|\s*```\s*$", "", candidate, flags=re.S).strip()
    try:
        data = json.loads(candidate)
    except ValueError:
        start = candidate.find("{")
        end = candidate.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            data = json.loads(candidate[start : end + 1])
        except ValueError:
            return None
    return data if isinstance(data, dict) else None


def _persona_from_llm(data: dict) -> tuple[dict, str] | None:
    name = data.get("name")
    if not isinstance(name, str) or not re.fullmatch(PERSONA_NAME_RE, name.strip()):
        return None
    source = data.get("source")
    description = data.get("description")
    system_prompt = data.get("system_prompt")
    color = data.get("color")
    language = data.get("language")
    if not isinstance(source, str):
        return None
    for value in (description, system_prompt, color, language):
        if not isinstance(value, str) or not value.strip():
            return None
    if source.strip():
        description = f"{source.strip()}: {description.strip()}"
    persona = {
        "name": name.strip(),
        "description": description,
        "system_prompt": system_prompt,
        "router_hints": [],
        "avatar_color": color.strip() if _HEX_COLOR.fullmatch(color.strip()) else _DEFAULT_COLOR,
        "avatar_image": None,
    }
    return persona, language.strip()


def _bare_persona(name: str, transcript: str) -> dict:
    return {
        "name": name,
        "description": transcript,
        "system_prompt": f"You are {name}.",
        "router_hints": [],
        "avatar_color": _DEFAULT_COLOR,
        "avatar_image": None,
    }


async def _generate_persona(
    state,
    name: str,
    transcript: str,
    language: str | None,
    filename: str,
) -> tuple[dict, bool, str | None, str | None]:
    warning: str | None = None
    data: dict | None = None
    llm = state.llm
    if llm is not None:
        try:
            models = await llm.get_loaded_models()
        except LLMError as exc:
            models = []
            warning = f"no se pudo consultar el LLM ({exc.detail}); se usa la ficha minima"
        if models:
            raw: str | None = None
            try:
                raw = await llm.chat(
                    [{"role": "user", "content": _build_prompt(filename, transcript, language)}],
                    max_tokens=_GENERATE_MAX_TOKENS,
                )
            except LLMError as exc:
                warning = f"el LLM no respondio ({exc.detail}); se usa la ficha minima"
            if raw is not None and (data := _extract_json_object(raw)) is None:
                warning = "el LLM no devolvio un JSON valido; se usa la ficha minima"
    if data is not None:
        result = _persona_from_llm(data)
        if result is not None:
            persona, llm_language = result
            return persona, True, warning, llm_language
        warning = warning or "el JSON del LLM no tenia los campos esperados; se usa la ficha minima"
    if warning is None:
        warning = "sin LLM cargado; se crea la persona con la ficha minima (editala para completarla)"
    return _bare_persona(name, transcript), False, warning, language


def _remove_quietly(path: Path) -> None:
    try:
        path.unlink()
    except OSError as exc:
        logger.warning("from-audio: no se pudo borrar %s: %s", path, exc)


def _remove_quietly_dir(path: Path) -> None:
    try:
        path.rmdir()
    except OSError:
        pass


def _persona_store(request: Request):
    return request.app.state.app_state.personas


def _pending_dir() -> Path:
    return Path(paths.PERSONAS_PENDING_DIR)


def _get_draft(state, token: str) -> dict | None:
    with state.pending_personas_lock:
        draft = state.pending_personas.get(token)
        return dict(draft) if draft is not None else None


def _with_tts_capable(persona: dict) -> dict:
    data = dict(persona)
    data["tts_capable"] = persona.get("reference_audio") is not None
    return data


def _resolve_audio_path(store, rel: str | None) -> Path | None:
    if not rel:
        return None
    rel_path = Path(rel)
    if rel_path.is_absolute() or ".." in rel_path.parts:
        return None
    candidate = store.audio_dir.parent / rel_path
    try:
        candidate.resolve().relative_to(store.audio_dir.resolve())
    except (OSError, ValueError):
        return None
    return candidate


def _read_transcript(store, rel: str | None) -> str | None:
    path = _resolve_audio_path(store, rel)
    if path is None:
        return None
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


@router.get("/personas")
async def list_personas(request: Request) -> dict:
    personas = _persona_store(request).list()
    if not request.app.state.app_state.config.get("show_for_instruct"):
        personas = [p for p in personas if p.get("name") != FOR_INSTRUCT_NAME]
    return {"personas": [_with_tts_capable(p) for p in personas]}


@router.get("/personas/{name}")
async def get_persona(request: Request, name: str) -> dict:
    store = _persona_store(request)
    persona = store.get(name)
    if persona is None:
        raise HTTPException(status_code=404, detail=f"persona not found: {name}")
    data = _with_tts_capable(persona)
    data["transcript"] = _read_transcript(store, persona.get("reference_audio_transcript"))
    return data


@router.post("/personas/{name}/retranscribe")
async def retranscribe_persona(request: Request, name: str) -> dict:
    state = request.app.state.app_state
    store = _persona_store(request)
    persona = store.get(name)
    if persona is None:
        raise HTTPException(status_code=404, detail=f"persona not found: {name}")
    wav_path = _resolve_audio_path(store, persona.get("reference_audio"))
    if wav_path is None or not wav_path.is_file():
        raise HTTPException(
            status_code=400,
            detail="la persona no tiene una referencia de voz (.wav) para re-transcribir",
        )
    try:
        transcript = await state.asr_manager.transcribe(
            wav_path, language=persona.get("reference_audio_language")
        )
    except ASRError as exc:
        detail = exc.detail if isinstance(exc, ASREngineError) else str(exc)
        raise HTTPException(status_code=502, detail=f"ASR fallo: {detail}") from exc

    txt_rel = persona.get("reference_audio_transcript")
    txt_path = _resolve_audio_path(store, txt_rel)
    if txt_path is None:
        txt_path = wav_path.with_suffix(".txt")
        txt_rel = f"{store.audio_dir.name}/{txt_path.name}"
        persona = store.update(name, {**persona, "reference_audio_transcript": txt_rel})
    try:
        txt_path.parent.mkdir(parents=True, exist_ok=True)
        txt_path.write_text(transcript, encoding="utf-8")
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"error de archivo: {exc}") from exc
    return {**_with_tts_capable(persona), "transcript": transcript}


@router.put("/personas/{name}/transcript")
async def update_transcript(request: Request, name: str, payload: TranscriptUpdate) -> dict:
    store = _persona_store(request)
    persona = store.get(name)
    if persona is None:
        raise HTTPException(status_code=404, detail=f"persona not found: {name}")
    txt_path = _resolve_audio_path(store, persona.get("reference_audio_transcript"))
    if txt_path is None:
        raise HTTPException(
            status_code=400,
            detail="la persona no tiene un path de transcripción (reference_audio_transcript)",
        )
    try:
        txt_path.parent.mkdir(parents=True, exist_ok=True)
        txt_path.write_text(payload.transcript, encoding="utf-8")
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"error de archivo: {exc}") from exc
    return {"transcript": payload.transcript}


@router.post("/personas", status_code=201)
async def create_persona(request: Request, payload: Persona) -> dict:
    try:
        return _with_tts_capable(_persona_store(request).create(payload.model_dump()))
    except PersonaExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.put("/personas/{name}")
async def update_persona(request: Request, name: str, payload: Persona) -> dict:
    try:
        return _with_tts_capable(_persona_store(request).update(name, payload.model_dump()))
    except KeyError:
        raise HTTPException(status_code=404, detail=f"persona not found: {name}")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _resolve_avatar_file(store, rel: str | None) -> Path | None:
    if not rel:
        return None
    rel_path = Path(rel)
    if rel_path.is_absolute() or ".." in rel_path.parts:
        return None
    candidate = store.avatar_dir.parent / rel_path
    try:
        candidate.resolve().relative_to(store.avatar_dir.resolve())
    except (OSError, ValueError):
        return None
    return candidate


@router.put("/personas/{name}/avatar")
async def upload_avatar(request: Request, name: str, file: UploadFile = File(...)) -> dict:
    store = _persona_store(request)
    persona = store.get(name)
    if persona is None:
        raise HTTPException(status_code=404, detail=f"persona not found: {name}")
    filename = Path(file.filename or "").name
    suffix = Path(filename).suffix.lower()
    if suffix not in AVATAR_MEDIA_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"formato de imagen no soportado: {suffix or 'sin extension'} (usa .png, .jpg, .webp o .gif)",
        )
    data = await file.read()
    if len(data) > AVATAR_MAX_BYTES:
        raise HTTPException(
            status_code=400,
            detail=f"imagen demasiado pesada: {len(data) / 1024 / 1024:.1f}MB (maximo 15MB)",
        )
    store.avatar_dir.mkdir(parents=True, exist_ok=True)
    target = store.avatar_dir / f"{name}{suffix}"
    old = _resolve_avatar_file(store, persona.get("avatar_image"))
    if old is not None and old != target:
        _remove_quietly(old)
    try:
        target.write_bytes(data)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"error de archivo: {exc}") from exc
    updated = store.set_avatar(name, f"{store.avatar_dir.name}/{target.name}")
    return _with_tts_capable(updated)


@router.delete("/personas/{name}/avatar")
async def delete_avatar(request: Request, name: str) -> dict:
    store = _persona_store(request)
    persona = store.get(name)
    if persona is None:
        raise HTTPException(status_code=404, detail=f"persona not found: {name}")
    if not persona.get("avatar_image"):
        raise HTTPException(status_code=400, detail="la persona no tiene foto")
    target = _resolve_avatar_file(store, persona["avatar_image"])
    if target is not None:
        _remove_quietly(target)
    updated = store.set_avatar(name, None)
    return _with_tts_capable(updated)


@router.get("/personas/{name}/avatar")
async def get_avatar(request: Request, name: str) -> FileResponse:
    store = _persona_store(request)
    persona = store.get(name)
    if persona is None:
        raise HTTPException(status_code=404, detail=f"persona not found: {name}")
    target = _resolve_avatar_file(store, persona.get("avatar_image"))
    if target is None or not target.is_file():
        raise HTTPException(status_code=404, detail="la persona no tiene foto")
    return FileResponse(
        target, media_type=AVATAR_MEDIA_TYPES.get(target.suffix.lower(), "application/octet-stream")
    )


@router.delete("/personas/{name}")
async def delete_persona(request: Request, name: str) -> dict:
    try:
        _persona_store(request).delete(name)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"persona not found: {name}")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"deleted": name}


@router.post("/personas/{name}/rename")
async def rename_persona(request: Request, name: str, payload: PersonaRename) -> dict:
    state = request.app.state.app_state
    store = _persona_store(request)
    new_name = payload.name
    current = store.get(name)
    if current is None:
        raise HTTPException(status_code=404, detail=f"persona not found: {name}")
    if new_name == name:
        return _with_tts_capable(current)
    if store.get(new_name) is not None:
        raise HTTPException(status_code=409, detail=f"persona already exists: {new_name}")
    try:
        renamed = store.rename(name, new_name)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"persona not found: {name}")
    except PersonaExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if state.rooms is not None:
        state.rooms.rename_persona(name, new_name)
    return _with_tts_capable(renamed)


@router.post("/personas/from-audio")
async def create_persona_from_audio(
    request: Request,
    file: UploadFile = File(...),
    name: str | None = Form(None),
    language: str | None = Form(None),
) -> dict:
    state = request.app.state.app_state
    store = _persona_store(request)

    filename = Path(file.filename or "").name
    if not filename.lower().endswith(".wav"):
        raise HTTPException(status_code=400, detail="solo se aceptan archivos .wav")

    parsed_name, parsed_language = parse_name_from_filename(filename)
    final_name = (name.strip() if name else "") or parsed_name
    final_language = language if language else parsed_language
    if not re.fullmatch(PERSONA_NAME_RE, final_name):
        raise HTTPException(
            status_code=400,
            detail=(
                f"nombre de persona invalido: {final_name!r}; nombra el archivo "
                "<Nombre>.wav, <Nombre>_Eng.wav o <Nombre>_Latino.wav o envia 'name'"
            ),
        )

    stem = sanitize_audio_stem(Path(filename).stem)
    if not stem:
        raise HTTPException(
            status_code=400, detail="no se pudo derivar un nombre de archivo valido del filename"
        )

    pending_dir = _pending_dir()
    wav_path = pending_dir / f"{stem}.wav"
    txt_path = pending_dir / f"{stem}.txt"
    pending_dir.mkdir(parents=True, exist_ok=True)
    wav_path.write_bytes(await file.read())

    try:
        try:
            transcript = await state.asr_manager.transcribe(wav_path, language=final_language)
        except ASRError as exc:
            detail = exc.detail if isinstance(exc, ASREngineError) else str(exc)
            raise HTTPException(status_code=502, detail=f"ASR fallo: {detail}") from exc
        txt_path.write_text(transcript, encoding="utf-8")

        persona, generated, warning, persona_language = await _generate_persona(
            state, final_name, transcript, final_language, filename
        )
        if warning:
            logger.warning("from-audio (%s): %s", filename, warning)
    except HTTPException:
        _remove_quietly(wav_path)
        _remove_quietly(txt_path)
        _remove_quietly_dir(pending_dir)
        raise

    token = uuid.uuid4().hex[:12]
    with state.pending_personas_lock:
        state.pending_personas[token] = {
            "stem": stem,
            "language": persona_language,
            "persona": persona,
            "transcript": transcript,
            "generated": generated,
            "warning": warning,
        }
    return {
        "token": token,
        "name": persona["name"],
        "description": persona["description"],
        "system_prompt": persona["system_prompt"],
        "avatar_color": persona["avatar_color"],
        "language": persona_language,
        "transcript": transcript,
        "generated": generated,
        "warning": warning,
    }


@router.post("/personas/pending/{token}/accept")
async def accept_pending_persona(
    request: Request, token: str, payload: PersonaDraftAccept
) -> dict:
    state = request.app.state.app_state
    store = _persona_store(request)
    draft = _get_draft(state, token)
    if draft is None:
        raise HTTPException(status_code=404, detail=f"draft not found: {token}")
    if store.get(payload.name) is not None:
        raise HTTPException(status_code=409, detail=f"persona ya existe: {payload.name}")

    base_stem = draft["stem"]
    audio_dir = store.audio_dir
    stem = base_stem
    if (audio_dir / f"{stem}.wav").exists() or (audio_dir / f"{stem}.txt").exists():
        stem = f"{base_stem}-{token[:6]}"
    audio_dir.mkdir(parents=True, exist_ok=True)
    pending_dir = _pending_dir()
    wav_dst = audio_dir / f"{stem}.wav"
    txt_dst = audio_dir / f"{stem}.txt"
    (pending_dir / f"{base_stem}.wav").replace(wav_dst)
    (pending_dir / f"{base_stem}.txt").replace(txt_dst)
    _remove_quietly_dir(pending_dir)

    persona = dict(draft["persona"])
    color = payload.color.strip()
    try:
        created = store.create(
            {
                **persona,
                "name": payload.name,
                "description": payload.description,
                "system_prompt": payload.system_prompt,
                "avatar_color": color if _HEX_COLOR.fullmatch(color) else _DEFAULT_COLOR,
                "reference_audio": f"{audio_dir.name}/{stem}.wav",
                "reference_audio_transcript": f"{audio_dir.name}/{stem}.txt",
                "reference_audio_language": draft["language"],
            }
        )
    except PersonaExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    txt_dst.write_text(payload.transcript, encoding="utf-8")

    with state.pending_personas_lock:
        state.pending_personas.pop(token, None)
    return {
        **_with_tts_capable(created),
        "transcript": payload.transcript,
        "generated": draft["generated"],
        "warning": draft["warning"],
    }


@router.delete("/personas/pending/{token}")
async def reject_pending_persona(request: Request, token: str) -> dict:
    state = request.app.state.app_state
    with state.pending_personas_lock:
        draft = state.pending_personas.pop(token, None)
    if draft is None:
        raise HTTPException(status_code=404, detail=f"draft not found: {token}")
    pending_dir = _pending_dir()
    _remove_quietly(pending_dir / f"{draft['stem']}.wav")
    _remove_quietly(pending_dir / f"{draft['stem']}.txt")
    _remove_quietly_dir(pending_dir)
    return {"rejected": token}


@router.post("/personas/pending/{token}/retranscribe")
async def retranscribe_pending_persona(request: Request, token: str) -> dict:
    state = request.app.state.app_state
    draft = _get_draft(state, token)
    if draft is None:
        raise HTTPException(status_code=404, detail=f"draft not found: {token}")
    pending_dir = _pending_dir()
    wav_path = pending_dir / f"{draft['stem']}.wav"
    txt_path = pending_dir / f"{draft['stem']}.txt"
    try:
        transcript = await state.asr_manager.transcribe(wav_path, language=draft["language"])
    except ASRError as exc:
        detail = exc.detail if isinstance(exc, ASREngineError) else str(exc)
        raise HTTPException(status_code=502, detail=f"ASR fallo: {detail}") from exc
    try:
        txt_path.parent.mkdir(parents=True, exist_ok=True)
        txt_path.write_text(transcript, encoding="utf-8")
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"error de archivo: {exc}") from exc
    with state.pending_personas_lock:
        if token in state.pending_personas:
            state.pending_personas[token]["transcript"] = transcript
    return {"transcript": transcript}


@router.get("/persona-audio/{filename:path}")
async def persona_audio(request: Request, filename: str) -> FileResponse:
    if not filename or "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(status_code=400, detail=f"invalid audio filename: {filename}")
    store = _persona_store(request)
    target = store.audio_dir / filename
    try:
        target.resolve().relative_to(store.audio_dir.resolve())
    except (OSError, ValueError):
        raise HTTPException(status_code=400, detail=f"invalid audio filename: {filename}")
    if not target.is_file():
        raise HTTPException(status_code=404, detail=f"audio not found: {filename}")
    return FileResponse(target, media_type=AUDIO_MEDIA_TYPES.get(target.suffix.lower()))
