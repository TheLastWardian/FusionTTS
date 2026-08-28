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
from app.routers.chat import _drain_sentences
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


def test_chat_tts_live_audio_interleaved(client):
    # audio en vivo: el primer chunk suena mientras la ronda sigue
    # generando (antes del done de la segunda persona), no al final
    c, mock_llm, fake = client
    assert (
        c.post("/api/config", json={"key": "max_persona_replies", "value": 2}).status_code
        == 200
    )
    assert c.post("/api/config", json={"key": "tts_enabled", "value": True}).status_code == 200
    text1 = (
        "Primera respuesta de la primera persona. Tiene varias oraciones para el TTS. "
        "Y esta es la última oración de esta respuesta."
    )
    text2 = "Segunda respuesta de la otra persona. Con otra oración. Y finaliza aquí."
    w1 = text1.split(" ")
    w2 = text2.split(" ")
    mock_llm.stream_responses = [
        [w1[0]] + [f" {w}" for w in w1[1:]],
        [w2[0]] + [f" {w}" for w in w2[1:]],
    ]
    mock_llm.pace = 0.01
    resp = c.post(
        "/api/chat",
        json={"message": "hola", "who_answers": "random", "chat_room": "test"},
    )
    assert resp.status_code == 200
    events = parse_events(resp.text)
    types = [e["type"] for e in events]
    assert "error" not in types
    starts = [e for e in events if e["type"] == "start"]
    dones = [i for i, e in enumerate(events) if e["type"] == "done"]
    assert len(starts) == 2
    assert len(dones) == 2
    first_audio = next(i for i, e in enumerate(events) if e["type"] == "audio_chunk")
    assert first_audio < dones[1]
    assert events[-1] == {"type": "complete", "cancelled": False}
    # los chunks de cada persona llevan el message_id de su propio start
    by_msg = {s["message_id"]: 0 for s in starts}
    for e in events:
        if e["type"] == "audio_chunk":
            by_msg[e["message_id"]] = by_msg.get(e["message_id"], 0) + 1
    assert all(v >= 1 for v in by_msg.values())


def test_drain_sentences_por_oracion_sin_merge():
    # semantica TalkWithMe: cada oracion es una unidad; el fragmento sin
    # terminal se queda en el buffer
    assert _drain_sentences("Hola mundo.") == (["Hola mundo."], "")
    assert _drain_sentences("Sin terminal") == ([], "Sin terminal")
    ready, rest = _drain_sentences("Hola mundo. ¿Qué tal? Bien")
    assert ready == ["Hola mundo.", "¿Qué tal?"]
    assert rest == " Bien"
    # puntuacion corrida se consume entera con su oracion
    ready, rest = _drain_sentences("Uno. Dos... Tres!")
    assert ready == ["Uno.", "Dos...", "Tres!"]
    assert rest == ""
    # paridad con TalkWithMe: las abreviaturas cortan (no hay lista)
    assert _drain_sentences("Mr. Smith va.") == (["Mr.", "Smith va."], "")


def test_chat_tts_per_sentence_units(client):
    # sin merge: cada oracion va a su propia sintesis (el merge de 120
    # chars es donde el modelo saltaba tramos a mitad de audio)
    c, mock_llm, fake = client
    assert (
        c.post("/api/config", json={"key": "max_persona_replies", "value": 1}).status_code
        == 200
    )
    assert c.post("/api/config", json={"key": "tts_enabled", "value": True}).status_code == 200
    text = "Primera oración. Segunda oración. Tercera oración."
    words = text.split(" ")
    mock_llm.stream_responses = [[words[0]] + [f" {w}" for w in words[1:]]]
    resp = c.post(
        "/api/chat",
        json={"message": "hola", "who_answers": "random", "chat_room": "test"},
    )
    assert resp.status_code == 200
    events = parse_events(resp.text)
    assert "error" not in [e["type"] for e in events]

    calls = [t for t, _, _, _ in fake.synth_calls]
    assert calls == ["Primera oración.", "Segunda oración.", "Tercera oración."]

    chunks = [e for e in events if e["type"] == "audio_chunk"]
    # cada chunk lleva el texto de su oracion (para el boton de play)
    assert [e["text"] for e in chunks] == calls
    assert all(e["audio"] == FAKE_AUDIO_B64 for e in chunks)


def test_chat_tts_full_mode_chunks(client):
    c, mock_llm, fake = client
    assert (
        c.post("/api/config", json={"key": "max_persona_replies", "value": 1}).status_code
        == 200
    )
    assert c.post("/api/config", json={"key": "tts_enabled", "value": True}).status_code == 200
    assert c.post("/api/config", json={"key": "tts_mode", "value": "full"}).status_code == 200
    text = (
        "Hola, qué gusto saber de ti por fin después de tanto tiempo sin noticias. "
        "Espero que todo esté bien por tu lado, con la familia y los proyectos de siempre. "
        "Acá por mi parte hemos pasado un par de semanas movidas entre el trabajo y el viaje. "
        "La buena noticia es que todo salió mejor de lo que esperábamos al principio. "
        "Me encantaría que pudiéramos quedarnos a tomar un café la próxima semana. "
        "Avísame cuándo te conviene y organizamos algo cerca de donde vives, sin prisa."
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
    assert "error" not in types
    assert "done" in types
    assert "audio_chunk" in types

    # modo "full": el texto completo (~469 chars) encola 2 bloques de <= 400
    # en orden (con CHUNK_LEN=120 serían 6 oraciones sueltas)
    chunks = [t for t, _, _, _ in fake.synth_calls]
    assert len(chunks) == 2
    for chunk in chunks:
        assert len(chunk) <= 400
    assert len(chunks[0]) > 120
    assert "".join(chunks).replace(" ", "") == text.replace(" ", "")
