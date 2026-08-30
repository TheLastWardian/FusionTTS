import pytest
from fastapi.testclient import TestClient

from app import paths
from app.main import app
from app.personas import FOR_INSTRUCT_NAME, ensure_for_instruct
from app.services.persona_router import resolve_room_personas


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "SETTINGS_PATH", tmp_path / "settings.json")
    monkeypatch.setattr(paths, "CHATROOMS_DIR", tmp_path / "chatrooms")
    monkeypatch.setattr(paths, "CHATROOMS_YAML", tmp_path / "chatrooms.yaml")
    monkeypatch.setattr(paths, "PERSONAS_YAML", tmp_path / "personas.yaml")
    monkeypatch.setattr(paths, "PERSONAS_AUDIO_DIR", tmp_path / "personas_audio")
    monkeypatch.setattr(paths, "PERSONAS_AVATARS_DIR", tmp_path / "personas_avatars")
    with TestClient(app) as c:
        yield c


def make_persona(name="Jean", **overrides):
    persona = {
        "name": name,
        "description": f"{name} desc",
        "system_prompt": f"You are {name}.",
        "router_hints": ["genshin", "mondstadt"],
        "avatar_color": "#87CEEB",
        "avatar_image": None,
        "reference_audio": f"personas_audio/{name}_Eng_trimmed.wav",
        "reference_audio_transcript": f"personas_audio/{name}_Eng_trimmed.txt",
        "reference_audio_language": "en",
    }
    persona.update(overrides)
    return persona


def test_lifespan_crea_persona_sistema(client):
    r = client.get("/api/personas")
    assert r.status_code == 200
    assert [p["name"] for p in r.json()["personas"]] == [FOR_INSTRUCT_NAME]
    p = client.get("/api/personas/For%20Instruct").json()
    assert p["reference_audio"] is None
    assert p["reference_audio_transcript"] is None
    assert p["reference_audio_language"] is None
    assert p["tts_capable"] is False
    assert p["avatar_color"] == "#888888"
    assert p["router_hints"] == []


def test_ensure_idempotente_y_recrea(client):
    state = client.app.state.app_state
    ensure_for_instruct(state.personas)
    ensure_for_instruct(state.personas)
    names = [p["name"] for p in state.personas.list()]
    assert names.count(FOR_INSTRUCT_NAME) == 1
    state.personas.delete(FOR_INSTRUCT_NAME)
    assert state.personas.get(FOR_INSTRUCT_NAME) is None
    ensure_for_instruct(state.personas)
    p = state.personas.get(FOR_INSTRUCT_NAME)
    assert p is not None
    assert p["reference_audio"] is None
    assert p["avatar_color"] == "#888888"


def test_config_default_visible(client):
    r = client.get("/api/config")
    assert r.status_code == 200
    assert r.json()["show_for_instruct"] is True


def test_list_oculta_con_flag_off(client):
    assert [p["name"] for p in client.get("/api/personas").json()["personas"]] == [
        FOR_INSTRUCT_NAME
    ]
    r = client.post("/api/config", json={"key": "show_for_instruct", "value": False})
    assert r.status_code == 200
    assert r.json()["value"] is False
    assert client.get("/api/personas").json()["personas"] == []
    # el GET unico sigue disponible (la salida del usuario es el toggle)
    assert client.get("/api/personas/For%20Instruct").status_code == 200
    client.post("/api/config", json={"key": "show_for_instruct", "value": True})
    assert [p["name"] for p in client.get("/api/personas").json()["personas"]] == [
        FOR_INSTRUCT_NAME
    ]


def test_resolve_room_personas_oculta(client):
    state = client.app.state.app_state
    client.post("/api/personas", json=make_persona("Jean"))
    client.post(
        "/api/rooms",
        json={
            "name": "r",
            "persona_names": ["Jean", FOR_INSTRUCT_NAME],
            "echo_chamber": False,
        },
    )
    assert resolve_room_personas(state.rooms, state.personas, "r", state.config) == [
        "Jean",
        FOR_INSTRUCT_NAME,
    ]
    assert resolve_room_personas(
        state.rooms, state.personas, "default", state.config
    ) == [FOR_INSTRUCT_NAME, "Jean"]
    client.post("/api/config", json={"key": "show_for_instruct", "value": False})
    assert resolve_room_personas(state.rooms, state.personas, "r", state.config) == [
        "Jean"
    ]
    assert resolve_room_personas(state.rooms, state.personas, "default", state.config) == [
        "Jean"
    ]
