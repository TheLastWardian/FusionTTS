from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

from app.personas import PersonaExistsError
from app.schemas import Persona

router = APIRouter()

AUDIO_MEDIA_TYPES = {
    ".wav": "audio/wav",
    ".mp3": "audio/mpeg",
    ".ogg": "audio/ogg",
    ".flac": "audio/flac",
}


def _persona_store(request: Request):
    return request.app.state.app_state.personas


def _with_tts_capable(persona: dict) -> dict:
    data = dict(persona)
    data["tts_capable"] = persona.get("reference_audio") is not None
    return data


@router.get("/personas")
async def list_personas(request: Request) -> dict:
    return {"personas": [_with_tts_capable(p) for p in _persona_store(request).list()]}


@router.get("/personas/{name}")
async def get_persona(request: Request, name: str) -> dict:
    persona = _persona_store(request).get(name)
    if persona is None:
        raise HTTPException(status_code=404, detail=f"persona not found: {name}")
    return _with_tts_capable(persona)


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


@router.delete("/personas/{name}")
async def delete_persona(request: Request, name: str) -> dict:
    try:
        _persona_store(request).delete(name)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"persona not found: {name}")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"deleted": name}


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
