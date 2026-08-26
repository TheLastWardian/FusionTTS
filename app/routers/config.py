from fastapi import APIRouter, HTTPException, Request

from app.config import ConfigError
from app.schemas import ConfigSetRequest, ConfigValueResponse

router = APIRouter()


def _store(request: Request):
    return request.app.state.app_state.config


@router.get("/config")
async def get_config(request: Request) -> dict:
    return _store(request).all()


@router.post("/config", response_model=ConfigValueResponse)
async def set_config(payload: ConfigSetRequest, request: Request) -> ConfigValueResponse:
    try:
        value = _store(request).set(payload.key, payload.value)
    except ConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ConfigValueResponse(key=payload.key, value=value)
