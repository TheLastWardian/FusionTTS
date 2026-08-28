from fastapi import APIRouter, HTTPException, Request

from app.rooms import RoomExistsError, RoomNameReservedError
from app.schemas import Room

router = APIRouter()


def _room_config_store(request: Request):
    return request.app.state.app_state.rooms


@router.get("/rooms")
async def list_rooms(request: Request) -> dict:
    return {"rooms": _room_config_store(request).list()}


@router.post("/rooms", status_code=201)
async def create_room(request: Request, payload: Room) -> dict:
    try:
        return _room_config_store(request).create(payload.model_dump())
    except RoomNameReservedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except RoomExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.put("/rooms/{name}")
async def update_room(request: Request, name: str, payload: Room) -> dict:
    try:
        return _room_config_store(request).update(name, payload.model_dump())
    except RoomNameReservedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except KeyError:
        raise HTTPException(status_code=404, detail=f"room not found: {name}")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/rooms/{name}")
async def delete_room(request: Request, name: str) -> dict:
    state = request.app.state.app_state
    try:
        state.rooms.delete(name)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"room not found: {name}")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    state.drop_room_store(name)
    return {"deleted": name}
