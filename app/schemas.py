from pydantic import BaseModel, Field, JsonValue


class HealthResponse(BaseModel):
    status: str


class ConfigSetRequest(BaseModel):
    key: str
    value: JsonValue


class ConfigValueResponse(BaseModel):
    key: str
    value: JsonValue


PERSONA_NAME_RE = r"^[A-Za-z0-9 _-]+$"


class Persona(BaseModel):
    name: str = Field(min_length=1, pattern=PERSONA_NAME_RE)
    description: str
    system_prompt: str
    router_hints: list[str]
    avatar_color: str
    avatar_image: str | None
    reference_audio: str | None
    reference_audio_transcript: str | None
    reference_audio_language: str | None


class Room(BaseModel):
    name: str
    persona_names: list[str]
    echo_chamber: bool
