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
    assert r.json() == {
        "room": "no-such-room",
        "messages": [],
        "summary": None,
    }


def test_history_includes_compaction_summary(client):
    store = client.app.state.app_state.get_room_store("sumroom")
    store.append(new_message("user", "user", "hola"))
    assert client.get("/api/session/history", params={"room": "sumroom"}).json()["summary"] is None
    store.apply_compaction([store.history[0]["uuid"]], "RESUMEN VISIBLE")
    r = client.get("/api/session/history", params={"room": "sumroom"})
    assert r.json()["summary"] == "RESUMEN VISIBLE"


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


def test_reprocess_200_rewinds_last_user_message(client):
    store = client.app.state.app_state.get_room_store("rp")
    u1 = new_message("user", "user", "primera")
    a1 = new_message("assistant", "Jean", "respuesta 1")
    u2 = new_message("user", "user", "segunda")
    a2 = new_message("assistant", "Jean", "respuesta 2")
    for m in (u1, a1, u2, a2):
        store.append(m)

    r = client.post(f"/api/rooms/rp/messages/{u2['uuid']}/reprocess")
    assert r.status_code == 200
    assert r.json() == {"text": "segunda", "removed": 2}
    assert [m["uuid"] for m in store.history] == [u1["uuid"], a1["uuid"]]


def test_reprocess_200_confirmed_with_users_below(client):
    store = client.app.state.app_state.get_room_store("rp2")
    u1 = new_message("user", "user", "primera")
    a1 = new_message("assistant", "Jean", "respuesta 1")
    u2 = new_message("user", "user", "segunda")
    for m in (u1, a1, u2):
        store.append(m)

    r = client.post(
        f"/api/rooms/rp2/messages/{u1['uuid']}/reprocess", json={"confirm": True}
    )
    assert r.status_code == 200
    assert r.json() == {"text": "primera", "removed": 3}
    assert store.history == []


def test_reprocess_409_users_below_without_confirm(client):
    store = client.app.state.app_state.get_room_store("rp3")
    u1 = new_message("user", "user", "primera")
    a1 = new_message("assistant", "Jean", "respuesta 1")
    u2 = new_message("user", "user", "segunda")
    for m in (u1, a1, u2):
        store.append(m)

    r = client.post(f"/api/rooms/rp3/messages/{u1['uuid']}/reprocess")
    assert r.status_code == 409
    assert r.json()["detail"] == {"users_after": 1}
    assert len(store.history) == 3  # nada se borro


def test_reprocess_404_unknown_message(client):
    client.app.state.app_state.get_room_store("rp4").append(
        new_message("user", "user", "hola")
    )
    r = client.post("/api/rooms/rp4/messages/nope/reprocess")
    assert r.status_code == 404


def test_reprocess_400_assistant_message(client):
    store = client.app.state.app_state.get_room_store("rp5")
    a1 = new_message("assistant", "Jean", "respuesta")
    store.append(a1)
    r = client.post(f"/api/rooms/rp5/messages/{a1['uuid']}/reprocess")
    assert r.status_code == 400


class FakeLLM:
    def __init__(self, reply="RESUMEN OK"):
        self.reply = reply
        self.calls = []

    async def chat(self, messages, max_tokens=None, temperature=None):
        self.calls.append((messages, max_tokens, temperature))
        return self.reply

    async def count_tokens(self, messages):
        return 10

    async def close(self):
        pass


def _seed_room(client, room, n):
    store = client.app.state.app_state.get_room_store(room)
    for i in range(n):
        if i % 2 == 0:
            store.append(new_message("user", "user", f"user-{i}"))
        else:
            store.append(new_message("assistant", "Jean", f"asst-{i}"))
    return store


def _add_persona(client):
    client.app.state.app_state.personas.create(
        {
            "name": "Jean",
            "description": "d",
            "system_prompt": "s",
            "router_hints": [],
            "avatar_color": "#f00",
            "avatar_image": None,
            "reference_audio": None,
            "reference_audio_transcript": None,
            "reference_audio_language": None,
        }
    )


def test_compact_200_marks_and_saves_summary(client):
    _add_persona(client)
    # room registrada con solo Jean: sin record, el compact cae a todas las
    # personas (incluida la sistema "For Instruct") y el prompt de resumen
    # usaria el system prompt equivocado
    client.post(
        "/api/rooms",
        json={"name": "rp", "persona_names": ["Jean"], "echo_chamber": False},
    )
    store = _seed_room(client, "rp", 25)  # 25 -> se compactan 15
    fake = FakeLLM()
    client.app.state.app_state.llm = fake
    r = client.post("/api/rooms/rp/compact")
    assert r.status_code == 200
    data = r.json()
    assert data["compacted"] == 15
    assert data["kept"] == 10
    assert data["summary"] == "RESUMEN OK"
    assert data["summary_tokens"] == 10
    assert store.load_summary() == "RESUMEN OK"
    compacted = [m for m in store.history if m.get("compacted")]
    assert len(compacted) == 15
    assert all(not m.get("compacted") for m in store.history[-10:])
    # el prompt de resumen vio el transcript y el system orientado a roleplay
    (msgs, max_tokens, temperature) = fake.calls[0]
    assert "Jean" in msgs[0]["content"]
    assert "user-0" in msgs[1]["content"]
    assert "user-14" in msgs[1]["content"]
    assert max_tokens == 10000
    assert temperature == 0.3


def test_compact_rolling_includes_previous_summary(client):
    _add_persona(client)
    store = _seed_room(client, "rp2", 14)
    fake = FakeLLM()
    client.app.state.app_state.llm = fake
    client.post("/api/rooms/rp2/compact")
    # 4 mensajes nuevos + compactar de nuevo: el resumen anterior va al prompt
    for i in range(4):
        store.append(new_message("user", "user", f"nuevo-{i}"))
    r = client.post("/api/rooms/rp2/compact")
    assert r.status_code == 200
    (msgs, _, _) = fake.calls[1]
    assert "## Resumen anterior" in msgs[1]["content"]
    assert "RESUMEN OK" in msgs[1]["content"]
    # la 2da vez compactan los siguientes 4 no-resumidos (user-4..asst-7)
    assert "user-4" in msgs[1]["content"]
    assert "nuevo-0" not in msgs[1]["content"]  # sigue en la cola de 10


def test_compact_400_not_enough_messages(client):
    _add_persona(client)
    _seed_room(client, "small", 12)  # solo 2 fuera de la cola de 10
    client.app.state.app_state.llm = FakeLLM()
    r = client.post("/api/rooms/small/compact")
    assert r.status_code == 400
    assert "suficientes mensajes" in r.json()["detail"]


def test_compact_503_no_llm(client):
    _seed_room(client, "nollm", 25)
    original = client.app.state.app_state.llm
    client.app.state.app_state.llm = None
    try:
        r = client.post("/api/rooms/nollm/compact")
        assert r.status_code == 503
    finally:
        client.app.state.app_state.llm = original


def test_compact_invalid_room_400(client):
    r = client.post("/api/rooms/bad!name/compact")
    assert r.status_code == 400
