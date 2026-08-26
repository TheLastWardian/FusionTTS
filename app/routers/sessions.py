from fastapi import APIRouter, HTTPException, Request

router = APIRouter()


@router.get("/session/history")
async def session_history(request: Request, room: str | None = None) -> dict:
    if room is None:
        raise HTTPException(status_code=400, detail="room is required")
    try:
        store = request.app.state.app_state.get_room_store(room)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"room": store.room_name, "messages": list(store.history)}
