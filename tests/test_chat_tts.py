"""Chat SSE + TTS integration tests.

Reuses the MockLLM/SSEStream fakes from test_chat (OpenAI-compatible mock
transport) and the FakeEngine from test_tts_router; state.tts_engine and
state.dispatcher._engine are swapped for the fake so no real TTS backend
(no GPU, no :8080) is touched.
"""

import asyncio
import base64
import json
import threading
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from app import paths
from app.main import app
from app.services.llm import LLMClient
from test_chat import MockLLM, make_persona, parse_events
from test_tts_router import FakeEngine

PERSONAS = [
    ("Jean", ["mondstadt", "protector"]),
    ("Fischl", ["snezhnaya", "owl"]),
    ("Keqing", ["liyue", "yuheng"]),
]
ROOM_PERSONAS = ["Jean", "Fischl", "Keqing"]

FAKE_AUDIO_B64 = base64.b64encode(b"FAKEWAV").decode()


@pytest.fixture
def client(tmp_path, monkeypatch):
    mock_llm = MockLLM()
    fake = FakeEngine()
    monkeypatch.setattr(paths, "SETTINGS_PATH", tmp_path / "settings.json")
    monkeypatch.setattr(paths, "CHATROOMS_DIR", tmp_path / "chatrooms")
    monkeypatch.setattr(paths, "CHATROOMS_YAML", tmp_path / "chatrooms.yaml")
    monkeypatch.setattr(paths, "PERSONAS_YAML", tmp_path / "personas.yaml")
    monkeypatch.setattr(paths, "PERSONAS_AUDIO_DIR", tmp_path / "personas_audio")
    with TestClient(app) as c:
        state = app.state.app_state
        state.llm = LLMClient(
            state.config, httpx.AsyncClient(transport=httpx.MockTransport(mock_llm.handler))
        )
        state.tts_engine = fake
        state.dispatcher._engine = fake
        for name, hints in PERSONAS:
            assert c.post("/api/personas", json=make_persona(name, hints)).status_code == 201
        assert (
            c.post(
                "/api/rooms",
                json={"name": "test", "persona_names": ROOM_PERSONAS, "echo_chamber": False},
            ).status_code
            == 201
        )
        yield c, mock_llm, fake


def test_chat_tts_full_audio(client):
    c, mock_llm, fake = client
    assert (
        c.post("/api/config", json={"key": "max_persona_replies", "value": 1}).status_code
        == 200
    )
    assert c.post("/api/config", json={"key": "tts_enabled", "value": True}).status_code == 200
    text = (
        "Hola, amigo mío. Qué gusto saber de ti. Espero que este día esté lleno de cosas bonitas. "
        "¿Cómo va todo por ahí, con tu gente y tus planes? Me encantaría escuchar todas las "
        "historias nuevas. Cuéntamelo luego, con calma y un buen café."
    )
    words = text.split(" ")
    mock_llm.stream_responses = [[words[0]] + [f" {w}" for w in words[1:]]]
    resp = c.post(
        "/api/chat",
        json={"message": "hola", "who_answers": "random", "chat_room": "test"},
    )
    assert resp.status_code == 200
    events = parse_events(resp.text)
    types = [e["type"] for e in events]
    assert types[0] == "tts_state"
    assert events[0] == {"type": "tts_state", "state": "on"}
    assert types[1] == "start"
    assert types[-1] == "complete"
    assert "token" in types
    assert "done" in types
    assert "error" not in types
    start = events[1]
    assert start["persona"] in ROOM_PERSONAS
    done = events[types.index("done")]
    assert done["text"] == text
    assert done["message_id"] == start["message_id"]
    assert events[-1] == {"type": "complete", "cancelled": False}

    tts_states = [e["state"] for e in events if e["type"] == "tts_state"]
    assert tts_states == ["on"]
    assert types.index("tts_state") < types.index("audio_chunk")

    chunks = [e for e in events if e["type"] == "audio_chunk"]
    assert len(chunks) >= 2
    for e in chunks:
        assert e["message_id"] == start["message_id"]
        assert e["persona"] == start["persona"]
        assert e["audio"] == FAKE_AUDIO_B64
        assert e["sample_rate"] == 24000
    ids = [e["sentence_id"] for e in chunks]
    assert all(a < b for a, b in zip(ids, ids[1:]))

    assert len(fake.synth_calls) >= 2
    assert all(len(text) <= 120 for text, _, _, _ in fake.synth_calls)


def test_chat_tts_off_no_events(client):
    c, mock_llm, fake = client
    assert (
        c.post("/api/config", json={"key": "max_persona_replies", "value": 1}).status_code
        == 200
    )
    mock_llm.stream_responses = [["Hola", ".", " ¿Cómo", " estás", "?"]]
    resp = c.post(
        "/api/chat",
        json={"message": "hola", "who_answers": "random", "chat_room": "test"},
    )
    assert resp.status_code == 200
    events = parse_events(resp.text)
    types = [e["type"] for e in events]
    assert "audio_chunk" not in types
    assert "tts_state" not in types
    assert types[0] == "start"
    assert "token" in types
    assert "done" in types
    assert events[-1] == {"type": "complete", "cancelled": False}
    assert fake.synth_calls == []


def test_chat_tts_stop_mid_stream(client):
    c, mock_llm, fake = client
    state = app.state.app_state
    assert (
        c.post("/api/config", json={"key": "max_persona_replies", "value": 1}).status_code
        == 200
    )
    assert c.post("/api/config", json={"key": "tts_enabled", "value": True}).status_code == 200

    tokens = ["Hola", ".", " ¿Cómo", " estás", "?", " Espero", " que", " estés", " hoy", "."]

    class StopStream(httpx.AsyncByteStream):
        def __aiter__(self):
            async def gen():
                for i, token in enumerate(tokens):
                    if i == 2:
                        asyncio.create_task(state.dispatcher.stop())
                    yield (
                        "data: "
                        + json.dumps({"choices": [{"delta": {"content": token}}]})
                        + "\n\n"
                    ).encode("utf-8")
                yield b"data: [DONE]\n\n"

            return gen()

    async def handler(request):
        return httpx.Response(200, stream=StopStream())

    state.llm = LLMClient(
        state.config, httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )

    resp = c.post(
        "/api/chat",
        json={"message": "hola", "who_answers": "random", "chat_room": "test"},
    )
    assert resp.status_code == 200
    events = parse_events(resp.text)
    types = [e["type"] for e in events]
    assert types[-1] == "complete"
    assert events[-1] == {"type": "complete", "cancelled": False}
    assert "audio_chunk" not in types
    stopped = [e for e in events if e["type"] == "tts_state" and e["state"] == "stopped"]
    assert stopped
    assert events.index(stopped[0]) < len(events) - 1
    assert state.dispatcher.is_stopped() is True


def test_chat_cancel_stops_dispatcher(client):
    c, mock_llm, fake = client
    state = app.state.app_state
    assert (
        c.post("/api/config", json={"key": "max_persona_replies", "value": 1}).status_code
        == 200
    )
    assert c.post("/api/config", json={"key": "tts_enabled", "value": True}).status_code == 200
    mock_llm.stream_responses = [[f"t{i:02d}" for i in range(50)]]
    mock_llm.pace = 0.01

    box = {}

    def run():
        box["resp"] = c.post(
            "/api/chat",
            json={"message": "hola", "who_answers": "random", "chat_room": "test"},
        )

    t = threading.Thread(target=run)
    t.start()
    time.sleep(0.2)
    r = c.post("/api/chat/cancel")
    assert r.status_code == 200
    assert r.json() == {"status": "cancelled"}
    t.join(timeout=15)
    assert not t.is_alive()

    resp = box["resp"]
    assert resp.status_code == 200
    events = parse_events(resp.text)
    types = [e["type"] for e in events]
    assert 1 <= len([e for e in events if e["type"] == "token"]) < 50
    assert "done" in types
    assert events[-1] == {"type": "complete", "cancelled": True}
    assert state.dispatcher.is_stopped() is True
