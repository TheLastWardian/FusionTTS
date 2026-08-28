import pytest
from fastapi.testclient import TestClient

from app import paths
from app.main import app
from app.persistence import RoomStore, new_message


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "SETTINGS_PATH", tmp_path / "settings.json")
    monkeypatch.setattr(paths, "CHATROOMS_DIR", tmp_path / "chatrooms")
    monkeypatch.setattr(paths, "CHATROOMS_YAML", tmp_path / "chatrooms.yaml")
    monkeypatch.setattr(paths, "PERSONAS_YAML", tmp_path / "personas.yaml")
    monkeypatch.setattr(paths, "PERSONAS_AUDIO_DIR", tmp_path / "personas_audio")
    with TestClient(app) as c:
        yield c


def test_history_returns_seeded_messages(client):
    store = client.app.state.app_state.get_room_store("test-room")
    m1 = new_message("user", "user", "hola 😊")
    m2 = new_message("assistant", "Jean", "¡Hola! 🎉")
    store.append(m1)
    store.append(m2)

    r = client.get("/api/session/history", params={"room": "test-room"})
    assert r.status_code == 200
    data = r.json()
    assert data["room"] == "test-room"
    assert data["messages"] == [m1, m2]


def test_history_missing_room_200_empty(client):
    r = client.get("/api/session/history", params={"room": "no-such-room"})
    assert r.status_code == 200
    assert r.json() == {"room": "no-such-room", "messages": []}


def test_history_reads_persisted_history(client, tmp_path):
    store = client.app.state.app_state.get_room_store("seeded")
    store.append(new_message("user", "user", "persistido"))
    on_disk = (tmp_path / "chatrooms" / "seeded" / "history.json").read_text(encoding="utf-8")
    assert "persistido" in on_disk
    fresh = RoomStore(
        "seeded", client.app.state.app_state.config, root=tmp_path / "chatrooms"
    )
    fresh.load()
    assert fresh.history[0]["text"] == "persistido"


@pytest.mark.parametrize("room", ["../evil", "a/b", "bad!name", ""])
def test_history_invalid_room_400(client, room):
    r = client.get("/api/session/history", params={"room": room})
    assert r.status_code == 400


def test_history_missing_room_param_400(client):
    r = client.get("/api/session/history")
    assert r.status_code == 400


def test_delete_message_204_removes_from_context(client):
    store = client.app.state.app_state.get_room_store("test-room")
    m1 = new_message("user", "user", "hola")
    m2 = new_message("assistant", "Jean", "chao")
    store.append(m1)
    store.append(m2)

    r = client.delete(f"/api/rooms/test-room/messages/{m1['uuid']}")
    assert r.status_code == 204
    assert [m["uuid"] for m in store.history] == [m2["uuid"]]
    data = client.get("/api/session/history", params={"room": "test-room"}).json()
    assert [m["uuid"] for m in data["messages"]] == [m2["uuid"]]


def test_delete_message_404_unknown(client):
    store = client.app.state.app_state.get_room_store("test-room")
    store.append(new_message("user", "user", "hola"))
    r = client.delete("/api/rooms/test-room/messages/nope")
    assert r.status_code == 404
    assert len(store.history) == 1


def test_delete_message_invalid_room_400(client):
    r = client.delete("/api/rooms/bad!name/messages/x")
    assert r.status_code == 400
