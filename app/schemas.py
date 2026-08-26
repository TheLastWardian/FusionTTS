from pydantic import BaseModel, JsonValue


class HealthResponse(BaseModel):
    status: str


class ConfigSetRequest(BaseModel):
    key: str
    value: JsonValue


class ConfigValueResponse(BaseModel):
    key: str
    value: JsonValue
