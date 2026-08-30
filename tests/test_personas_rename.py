import pytest
from fastapi.testclient import TestClient

from app import paths
from app.main import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "SETTINGS_PATH", tmp_path / "settings.json")
    monkeypatch.setattr(paths, "CHATROOMS_DIR", tmp_path / "chatrooms")
    monkeypatch.setattr(paths, "CHATROOMS_YAML", tmp_path / "chatrooms.yaml")
    monkeypatch.setattr(paths, "PERSONAS_YAML", tmp_path / "personas.yaml")
    monkeypatch.setattr(paths, "PERSONAS_AUDIO_DIR", tmp_path / "personas_audio")
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


def test_rename_happy(client):
    client.post("/api/personas", json=make_persona("Jean"))
    r = client.post("/api/personas/Jean/rename", json={"name": "Jean D"})
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "Jean D"
    assert body["description"] == "Jean desc"
    assert body["reference_audio"] == "personas_audio/Jean_Eng_trimmed.wav"
    assert body["tts_capable"] is True
    assert client.get("/api/personas/Jean").status_code == 404
    assert client.get("/api/personas/Jean D").status_code == 200
    names = [p["name"] for p in client.get("/api/personas").json()["personas"]]
    assert "Jean D" in names and "Jean" not in names


def test_rename_mapea_rooms(client):
    client.post("/api/personas", json=make_persona("Jean"))
    client.post("/api/personas", json=make_persona("Kazuha"))
    r = client.post(
        "/api/rooms",
        json={"name": "dojo", "persona_names": ["Jean", "Kazuha"], "echo_chamber": False},
    )
    assert r.status_code == 201
    r = client.post("/api/personas/Jean/rename", json={"name": "Jean D"})
    assert r.status_code == 200
    rooms = client.get("/api/rooms").json()["rooms"]
    dojo = next(room for room in rooms if room["name"] == "dojo")
    assert dojo["persona_names"] == ["Jean D", "Kazuha"]


def test_rename_desconocido_404(client):
    r = client.post("/api/personas/Nadie/rename", json={"name": "Otro"})
    assert r.status_code == 404


def test_rename_destino_ocupado_409(client):
    client.post("/api/personas", json=make_persona("Jean"))
    client.post("/api/personas", json=make_persona("Kazuha"))
    r = client.post("/api/personas/Jean/rename", json={"name": "Kazuha"})
    assert r.status_code == 409
    assert client.get("/api/personas/Jean").status_code == 200
    assert client.get("/api/personas/Kazuha").status_code == 200


def test_rename_nombre_invalido_400(client):
    client.post("/api/personas", json=make_persona("Jean"))
    r = client.post("/api/personas/Jean/rename", json={"name": "bad/name"})
    assert r.status_code in (400, 422)
    assert client.get("/api/personas/Jean").status_code == 200


def test_rename_self_no_hace_nada(client):
    client.post("/api/personas", json=make_persona("Jean"))
    r = client.post("/api/personas/Jean/rename", json={"name": "Jean"})
    assert r.status_code == 200
    assert r.json()["name"] == "Jean"
