from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

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
