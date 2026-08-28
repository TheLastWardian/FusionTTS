import yaml

import pytest
from fastapi.testclient import TestClient

from app import paths
from app.config import ConfigStore
from app.main import app
from app.persistence import new_message
from app.rooms import RoomConfigStore


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "SETTINGS_PATH", tmp_path / "settings.json")
    monkeypatch.setattr(paths, "CHATROOMS_DIR", tmp_path / "chatrooms")
    monkeypatch.setattr(paths, "CHATROOMS_YAML", tmp_path / "chatrooms.yaml")
    monkeypatch.setattr(paths, "PERSONAS_YAML", tmp_path / "personas.yaml")
    monkeypatch.setattr(paths, "PERSONAS_AUDIO_DIR", tmp_path / "personas_audio")
    with TestClient(app) as c:
        yield c


def seed_persona(client, name="Jean"):
    r = client.post(
        "/api/personas",
        json={
            "name": name,
            "description": f"{name} desc",
            "system_prompt": f"You are {name}.",
            "router_hints": ["hint"],
            "avatar_color": "#87CEEB",
            "avatar_image": None,
            "reference_audio": f"personas_audio/{name}.wav",
            "reference_audio_transcript": f"personas_audio/{name}.txt",
            "reference_audio_language": "en",
        },
    )
    assert r.status_code == 201


def test_list_empty(client):
    r = client.get("/api/rooms")
    assert r.status_code == 200
    assert r.json() == {"rooms": []}


def test_create_room_persists_yaml(client, tmp_path):
    seed_persona(client, "Jean")
    seed_persona(client, "Fischl")
    body = {"name": "test", "persona_names": ["Jean", "Fischl"], "echo_chamber": True}
    r = client.post("/api/rooms", json=body)
    assert r.status_code == 201
    assert r.json() == body
    assert client.get("/api/rooms").json() == {"rooms": [body]}

    on_disk = yaml.safe_load(
        (tmp_path / "chatrooms.yaml").read_text(encoding="utf-8")
    )
    assert on_disk == {"chat_rooms": [body]}


def test_create_room_missing_persona_400(client):
    seed_persona(client, "Jean")
    r = client.post(
        "/api/rooms",
        json={"name": "test", "persona_names": ["Jean", "Keqing"], "echo_chamber": False},
    )
    assert r.status_code == 400
    assert "Keqing" in r.json()["detail"]


@pytest.mark.parametrize("bad_name", ["", "a/b", "../evil", "a\\b", "bad!name", "a..b"])
def test_create_room_invalid_name_400(client, bad_name):
    seed_persona(client, "Jean")
    r = client.post(
        "/api/rooms",
        json={"name": bad_name, "persona_names": ["Jean"], "echo_chamber": False},
    )
    assert r.status_code == 400


def test_create_room_duplicate_409(client):
    seed_persona(client, "Jean")
    body = {"name": "test", "persona_names": ["Jean"], "echo_chamber": False}
    assert client.post("/api/rooms", json=body).status_code == 201
    r = client.post("/api/rooms", json=body)
    assert r.status_code == 409
    assert "test" in r.json()["detail"]


@pytest.mark.parametrize("reserved", ["main", "default", "Main", "DEFAULT"])
def test_create_room_reserved_name_409(client, reserved):
    seed_persona(client, "Jean")
    r = client.post(
        "/api/rooms",
        json={"name": reserved, "persona_names": ["Jean"], "echo_chamber": False},
    )
    assert r.status_code == 409
    assert "reserved" in r.json()["detail"]


def test_update_room_to_reserved_name_409(client):
    seed_persona(client, "Jean")
    client.post(
        "/api/rooms",
        json={"name": "test", "persona_names": ["Jean"], "echo_chamber": False},
    )
    r = client.put(
        "/api/rooms/test",
        json={"name": "default", "persona_names": ["Jean"], "echo_chamber": False},
    )
    assert r.status_code == 409
    assert "reserved" in r.json()["detail"]


def test_update_room(client):
    seed_persona(client, "Jean")
    seed_persona(client, "Fischl")
    client.post(
        "/api/rooms",
        json={"name": "test", "persona_names": ["Jean"], "echo_chamber": False},
    )
    body = {"name": "test", "persona_names": ["Jean", "Fischl"], "echo_chamber": True}
    r = client.put("/api/rooms/test", json=body)
    assert r.status_code == 200
    assert r.json() == body
    assert client.get("/api/rooms").json()["rooms"] == [body]


def test_update_room_missing_404(client):
    seed_persona(client, "Jean")
    r = client.put(
        "/api/rooms/none",
        json={"name": "none", "persona_names": ["Jean"], "echo_chamber": False},
    )
    assert r.status_code == 404


def test_update_room_invalid_persona_400(client):
    seed_persona(client, "Jean")
    client.post(
        "/api/rooms",
        json={"name": "test", "persona_names": ["Jean"], "echo_chamber": False},
    )
    r = client.put(
        "/api/rooms/test",
        json={"name": "test", "persona_names": ["Mona"], "echo_chamber": False},
    )
    assert r.status_code == 400
    assert "Mona" in r.json()["detail"]


def test_delete_room_cascades_to_chatrooms_dir(client, tmp_path):
    room_dir = tmp_path / "chatrooms" / "test"
    (room_dir / "images").mkdir(parents=True)
    (room_dir / "history.json").write_text("[]\n", encoding="utf-8")
    (room_dir / "abc_0.wav").write_bytes(b"RIFF")

    seed_persona(client, "Jean")
    client.post(
        "/api/rooms",
        json={"name": "test", "persona_names": ["Jean"], "echo_chamber": False},
    )
    r = client.delete("/api/rooms/test")
    assert r.status_code == 200
    assert not (tmp_path / "chatrooms" / "test").exists()
    assert client.get("/api/rooms").json() == {"rooms": []}


def test_delete_room_missing_404(client):
    assert client.delete("/api/rooms/none").status_code == 404


def test_delete_room_clears_session_cache(client):
    seed_persona(client, "Jean")
    client.post(
        "/api/rooms",
        json={"name": "test", "persona_names": ["Jean"], "echo_chamber": False},
    )
    store = client.app.state.app_state.get_room_store("test")
    store.append(new_message("user", "user", "hola"))

    assert client.delete("/api/rooms/test").status_code == 200
    r = client.get("/api/session/history", params={"room": "test"})
    assert r.json() == {"room": "test", "messages": []}


def test_missing_yaml_creates_empty(tmp_path):
    store = RoomConfigStore(
        ConfigStore(settings_path=tmp_path / "settings.json"),
        chatrooms_yaml=tmp_path / "chatrooms.yaml",
        chatrooms_root=tmp_path / "chatrooms",
    )
    assert store.list() == []
    assert (tmp_path / "chatrooms.yaml").exists()
    data = yaml.safe_load((tmp_path / "chatrooms.yaml").read_text(encoding="utf-8"))
    assert data == {"chat_rooms": []}
