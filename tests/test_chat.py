"""Chat SSE tests.

AppState is injected the same way as in the T3/T5 tests (monkeypatched paths +
TestClient lifespan), then the lifespan-created AppState.llm is swapped for an
LLMClient backed by httpx.MockTransport that emulates the OpenAI-compatible
server and captures every request body. starlette's TestClient generates the
whole SSE body before returning, so the cancel test runs the chat request in a
background thread and issues POST /api/chat/cancel from the main thread.
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
from app.persistence import new_message
from app.services.llm import LLMClient

PNG_1X1_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)

PERSONAS = [
    ("Jean", ["mondstadt", "protector"]),
    ("Fischl", ["snezhnaya", "owl"]),
    ("Keqing", ["liyue", "yuheng"]),
]
ROOM_PERSONAS = ["Jean", "Fischl", "Keqing"]


class SSEStream(httpx.AsyncByteStream):
    def __init__(self, tokens, pace=0.0, error_after=None):
        self.tokens = list(tokens)
        self.pace = pace
        self.error_after = error_after

    def __aiter__(self):
        async def gen():
            for i, token in enumerate(self.tokens):
                if self.error_after is not None and i >= self.error_after:
                    raise httpx.ReadError("simulated connection drop")
                chunk = json.dumps({"choices": [{"delta": {"content": token}}]})
                yield f"data: {chunk}\n\n".encode("utf-8")
                if self.pace:
                    await asyncio.sleep(self.pace)
            yield b"data: [DONE]\n\n"

        return gen()


class MockLLM:
    def __init__(self, stream_responses=None, chat_content="Jean", pace=0.0, error_after=None):
        self.stream_responses = [
            list(t) for t in (stream_responses or [["Hola", " desde ", "aqui", "."]])
        ]
        self.chat_content = chat_content
        self.pace = pace
        self.error_after = error_after
        self.calls = []
        self.stream_calls = 0

    async def handler(self, request):
        body = json.loads(request.content)
        self.calls.append(body)
        if not body["stream"]:
            return httpx.Response(
                200, json={"choices": [{"message": {"content": self.chat_content}}]}
            )
        self.stream_calls += 1
        tokens = self.stream_responses[(self.stream_calls - 1) % len(self.stream_responses)]
        return httpx.Response(200, stream=SSEStream(tokens, self.pace, self.error_after))


def non_stream_calls(mock):
    return [c for c in mock.calls if not c["stream"]]


def stream_calls(mock):
    return [c for c in mock.calls if c["stream"]]


def parse_events(text):
    events = []
    for block in text.split("\n\n"):
        block = block.strip()
        if not block:
            continue
        for line in block.splitlines():
            if line.startswith("data: "):
                events.append(json.loads(line[len("data: ") :]))
    return events


def make_persona(name, hints):
    return {
        "name": name,
        "description": f"{name} desc",
        "system_prompt": f"You are {name}.",
        "router_hints": list(hints),
        "avatar_color": "#87CEEB",
        "avatar_image": None,
        "reference_audio": None,
        "reference_audio_transcript": None,
        "reference_audio_language": None,
    }


@pytest.fixture
def mock_llm():
    return MockLLM()


@pytest.fixture
def client(tmp_path, monkeypatch, mock_llm):
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
        for name, hints in PERSONAS:
            assert c.post("/api/personas", json=make_persona(name, hints)).status_code == 201
        assert (
            c.post(
                "/api/rooms",
                json={"name": "test", "persona_names": ROOM_PERSONAS, "echo_chamber": False},
            ).status_code
            == 201
        )
        assert (
            c.post(
                "/api/rooms",
                json={"name": "echo", "persona_names": ["Fischl"], "echo_chamber": True},
            ).status_code
            == 201
        )
        yield c


def test_random_full_flow(client, mock_llm, tmp_path):
    assert (
        client.post("/api/config", json={"key": "max_persona_replies", "value": 1}).status_code
        == 200
    )
    resp = client.post(
        "/api/chat",
        json={
            "message": "hola",
            "who_answers": "random",
            "chat_room": "test",
            "message_id": "custom-123",
        },
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    events = parse_events(resp.text)
    types = [e["type"] for e in events]
    assert types[0] == "start"
    assert types[-1] == "complete"
    assert types.index("done") > types.index("start")
    assert "error" not in types
    start = events[0]
    assert start["persona"] in ROOM_PERSONAS
    assert start["user_message_id"] == "custom-123"
    assert start["message_id"]
    tokens = [e for e in events if e["type"] == "token"]
    assert tokens and all(t["persona"] == start["persona"] for t in tokens)
    done = events[types.index("done")]
    assert done["text"] == "Hola desde aqui."
    assert done["message_id"] == start["message_id"]
    assert events[-1] == {"type": "complete", "cancelled": False}

    history = json.loads(
        (tmp_path / "chatrooms" / "test" / "history.json").read_text(encoding="utf-8")
    )
    assert [m["role"] for m in history] == ["user", "assistant"]
    assert history[0]["text"] == "hola"
    assert history[0]["sender"] == "user"
    assert history[1]["sender"] == start["persona"]
    assert history[1]["text"] == "Hola desde aqui."


def test_two_replies_different_personas_with_cross_context(client, mock_llm):
    mock_llm.stream_responses = [["uno", " A"], ["dos", " B"]]
    resp = client.post(
        "/api/chat",
        json={"message": "hola", "who_answers": "random", "chat_room": "test"},
    )
    assert resp.status_code == 200
    events = parse_events(resp.text)
    types = [e["type"] for e in events]
    assert types.count("start") == 2
    assert types.count("done") == 2
    personas = [e["persona"] for e in events if e["type"] == "start"]
    assert len(set(personas)) == 2
    assert events[-1] == {"type": "complete", "cancelled": False}

    calls = stream_calls(mock_llm)
    assert len(calls) == 2
    second = calls[1]["messages"]
    assert second[0]["role"] == "system"
    assert {"role": "user", "content": f"[{personas[0]}]: uno A"} in second


def test_router_uses_llm_with_hints(client, mock_llm):
    mock_llm.chat_content = "Jean"
    resp = client.post(
        "/api/chat",
        json={"message": "hola", "who_answers": "router", "chat_room": "test"},
    )
    assert resp.status_code == 200
    events = parse_events(resp.text)
    assert events[0]["persona"] == "Jean"
    calls = non_stream_calls(mock_llm)
    assert len(calls) == 1
    system_prompt = calls[0]["messages"][0]["content"]
    assert "mondstadt" in system_prompt
    assert "snezhnaya" in system_prompt
    assert "liyue" in system_prompt
    assert "Choose from: Jean, Fischl, Keqing" in system_prompt
    assert calls[0]["messages"][1] == {"role": "user", "content": "Pick one persona."}
    assert calls[0]["max_tokens"] == 16


def test_router_unknown_name_falls_back_to_random(client, mock_llm):
    mock_llm.chat_content = "Nadie"
    resp = client.post(
        "/api/chat",
        json={"message": "hola", "who_answers": "router", "chat_room": "test"},
    )
    assert resp.status_code == 200
    events = parse_events(resp.text)
    assert "error" not in [e["type"] for e in events]
    assert events[0]["persona"] in ROOM_PERSONAS
    assert len(non_stream_calls(mock_llm)) == 1


def test_explicit_persona_and_unknown_value(client, mock_llm):
    resp = client.post(
        "/api/chat",
        json={"message": "hola", "who_answers": "Keqing", "chat_room": "test"},
    )
    assert resp.status_code == 200
    events = parse_events(resp.text)
    assert [e["type"] for e in events].count("start") == 1
    assert events[0]["persona"] == "Keqing"
    assert non_stream_calls(mock_llm) == []


def test_explicit_list_two_replies_in_order(client, mock_llm):
    mock_llm.stream_responses = [["uno", " A"], ["dos", " B"]]
    resp = client.post(
        "/api/chat",
        json={"message": "hola", "who_answers": ["Fischl", "Jean"], "chat_room": "test"},
    )
    assert resp.status_code == 200
    events = parse_events(resp.text)
    types = [e["type"] for e in events]
    assert types.count("start") == 2
    assert [e["persona"] for e in events if e["type"] == "start"] == ["Fischl", "Jean"]
    assert "error" not in types
    assert non_stream_calls(mock_llm) == []
    assert events[-1] == {"type": "complete", "cancelled": False}


def test_explicit_list_single_only_one_reply(client, mock_llm):
    resp = client.post(
        "/api/chat",
        json={"message": "hola", "who_answers": ["Keqing"], "chat_room": "test"},
    )
    assert resp.status_code == 200
    events = parse_events(resp.text)
    assert [e["type"] for e in events].count("start") == 1
    assert events[0]["persona"] == "Keqing"


def test_explicit_list_name_not_in_room_400(client, mock_llm):
    resp = client.post(
        "/api/chat",
        json={"message": "hola", "who_answers": ["Keqing"], "chat_room": "echo"},
    )
    assert resp.status_code == 400


def test_explicit_list_empty_400(client, mock_llm):
    resp = client.post(
        "/api/chat",
        json={"message": "hola", "who_answers": [], "chat_room": "test"},
    )
    assert resp.status_code == 400


def test_explicit_list_echo_room_only_first(client, mock_llm):
    assert (
        client.post(
            "/api/rooms",
            json={"name": "echo2", "persona_names": ["Fischl", "Jean"], "echo_chamber": True},
        ).status_code
        == 201
    )
    resp = client.post(
        "/api/chat",
        json={"message": "hola", "who_answers": ["Fischl", "Jean"], "chat_room": "echo2"},
    )
    assert resp.status_code == 200
    events = parse_events(resp.text)
    assert [e["type"] for e in events].count("start") == 1
    assert events[0]["persona"] == "Fischl"

    resp = client.post(
        "/api/chat",
        json={"message": "hola", "who_answers": "inexistente", "chat_room": "test"},
    )
    assert resp.status_code == 200
    events = parse_events(resp.text)
    assert "error" not in [e["type"] for e in events]
    assert events[0]["persona"] in ROOM_PERSONAS


def test_mention_short_circuits_router(client, mock_llm):
    resp = client.post(
        "/api/chat",
        json={"message": "Hey Jean, how are you?", "who_answers": "router", "chat_room": "test"},
    )
    assert resp.status_code == 200
    events = parse_events(resp.text)
    assert events[0]["persona"] == "Jean"
    assert non_stream_calls(mock_llm) == []


def test_mention_disabled_uses_router(client, mock_llm):
    assert (
        client.post("/api/config", json={"key": "persona_name_mentions", "value": False}).status_code
        == 200
    )
    mock_llm.chat_content = "Fischl"
    resp = client.post(
        "/api/chat",
        json={"message": "Hey Jean, how are you?", "who_answers": "router", "chat_room": "test"},
    )
    assert resp.status_code == 200
    assert len(non_stream_calls(mock_llm)) == 1
    events = parse_events(resp.text)
    assert events[0]["persona"] == "Fischl"


def test_multiple_mentions_fall_to_router(client, mock_llm):
    mock_llm.chat_content = "Keqing"
    resp = client.post(
        "/api/chat",
        json={"message": "Jean and Fischl, hi", "who_answers": "router", "chat_room": "test"},
    )
    assert resp.status_code == 200
    assert len(non_stream_calls(mock_llm)) == 1
    events = parse_events(resp.text)
    assert events[0]["persona"] == "Keqing"


def test_echo_chamber_verbatim_no_llm(client, mock_llm):
    resp = client.post(
        "/api/chat",
        json={"message": "hola eco", "who_answers": "random", "chat_room": "echo"},
    )
    assert resp.status_code == 200
    events = parse_events(resp.text)
    assert [e["type"] for e in events] == [
        "start",
        "token",
        "done",
        "text_done",
        "complete",
    ]
    assert events[0]["persona"] == "Fischl"
    assert events[1]["token"] == "hola eco"
    assert events[2]["text"] == "hola eco"
    assert mock_llm.calls == []


def test_image_saved_described_and_injected(client, mock_llm, tmp_path):
    mock_llm.chat_content = "a red square"
    resp = client.post(
        "/api/chat",
        json={
            "message": "mira esto",
            "who_answers": "random",
            "chat_room": "test",
            "image_base64": PNG_1X1_B64,
            "image_mime": "image/png",
        },
    )
    assert resp.status_code == 200
    events = parse_events(resp.text)
    assert "error" not in [e["type"] for e in events]

    images = list((tmp_path / "chatrooms" / "test" / "images").glob("*.png"))
    assert len(images) == 1

    history = json.loads(
        (tmp_path / "chatrooms" / "test" / "history.json").read_text(encoding="utf-8")
    )
    user_msg = next(m for m in history if m["role"] == "user")
    assert user_msg["image"] == f"images/{images[0].name}"

    vision_calls = non_stream_calls(mock_llm)
    assert len(vision_calls) == 1
    content = vision_calls[0]["messages"][0]["content"]
    assert content[0]["type"] == "text"
    assert "main action or event" in content[0]["text"]
    url = content[1]["image_url"]["url"]
    assert url.startswith("data:image/png;base64,")
    assert base64.b64decode(url.split(",", 1)[1]) == base64.b64decode(PNG_1X1_B64)

    stream_body = stream_calls(mock_llm)[0]["messages"]
    last_user = stream_body[-1]
    assert last_user["role"] == "user"
    assert last_user["content"].startswith("mira esto")
    assert "[Attached image: " in last_user["content"]
    assert "[Attached image description: a red square]" in last_user["content"]


def test_corrupt_image_b64_does_not_break_chat(client, mock_llm, tmp_path):
    resp = client.post(
        "/api/chat",
        json={
            "message": "hola",
            "who_answers": "random",
            "chat_room": "test",
            "image_base64": "!!!nope",
            "image_mime": "image/png",
        },
    )
    assert resp.status_code == 200
    events = parse_events(resp.text)
    assert "error" not in [e["type"] for e in events]
    history = json.loads(
        (tmp_path / "chatrooms" / "test" / "history.json").read_text(encoding="utf-8")
    )
    user_msg = next(m for m in history if m["role"] == "user")
    assert user_msg["image"] is None
    images_dir = tmp_path / "chatrooms" / "test" / "images"
    assert not images_dir.exists() or list(images_dir.iterdir()) == []


def test_cancel_stops_stream_midway(client, mock_llm, tmp_path):
    mock_llm.stream_responses = [[f"t{i:02d}" for i in range(50)]]
    mock_llm.pace = 0.01

    box = {}

    def run():
        box["resp"] = client.post(
            "/api/chat",
            json={"message": "hola", "who_answers": "random", "chat_room": "test"},
        )

    t = threading.Thread(target=run)
    t.start()
    time.sleep(0.2)
    r = client.post("/api/chat/cancel")
    assert r.status_code == 200
    assert r.json() == {"status": "cancelled"}
    t.join(timeout=15)
    assert not t.is_alive()

    resp = box["resp"]
    assert resp.status_code == 200
    events = parse_events(resp.text)
    types = [e["type"] for e in events]
    tokens = [e for e in events if e["type"] == "token"]
    assert 1 <= len(tokens) < 50
    assert "done" in types
    done = events[types.index("done")]
    assert done["text"] == "".join(tok["token"] for tok in tokens)
    assert events[-1] == {"type": "complete", "cancelled": True}

    history = json.loads(
        (tmp_path / "chatrooms" / "test" / "history.json").read_text(encoding="utf-8")
    )
    assert history[-1]["role"] == "assistant"
    assert history[-1]["text"] == done["text"]


def test_room_without_personas(client, mock_llm):
    r = client.post(
        "/api/rooms", json={"name": "empty", "persona_names": [], "echo_chamber": False}
    )
    assert r.status_code == 201
    resp = client.post(
        "/api/chat",
        json={"message": "hola", "who_answers": "random", "chat_room": "empty"},
    )
    assert resp.status_code == 200
    events = parse_events(resp.text)
    assert [e["type"] for e in events] == ["error", "complete"]
    assert "No eligible personas" in events[0]["message"]
    assert mock_llm.calls == []


def test_llm_dies_mid_stream(client, mock_llm):
    mock_llm.stream_responses = [["a", "b", "c", "d", "e"]]
    mock_llm.error_after = 3
    resp = client.post(
        "/api/chat",
        json={"message": "hola", "who_answers": "random", "chat_room": "test"},
    )
    assert resp.status_code == 200
    events = parse_events(resp.text)
    types = [e["type"] for e in events]
    tokens = [e for e in events if e["type"] == "token"]
    assert [t["token"] for t in tokens] == ["a", "b", "c"]
    assert "error" in types
    assert events[types.index("error")]["message"]
    assert "done" not in types
    assert events[-1] == {"type": "complete", "cancelled": False}


def test_max_context_turns_limits_history(client, mock_llm):
    assert (
        client.post("/api/config", json={"key": "max_context_turns", "value": 2}).status_code
        == 200
    )
    store = app.state.app_state.get_room_store("test")
    for i in range(6):
        if i % 2 == 0:
            store.append(new_message("user", "user", f"user msg {i}"))
        else:
            store.append(new_message("assistant", "Jean", f"assistant msg {i}"))

    resp = client.post(
        "/api/chat",
        json={"message": "last", "who_answers": "random", "chat_room": "test"},
    )
    assert resp.status_code == 200
    messages = stream_calls(mock_llm)[0]["messages"]
    assert messages[0]["role"] == "system"
    assert len(messages) == 3
    assert messages[-1] == {"role": "user", "content": "last"}
    assert messages[1]["content"] in ("assistant msg 5", "[Jean]: assistant msg 5")
    dumped = json.dumps(messages)
    assert "user msg 0" not in dumped
    assert "user msg 4" not in dumped
    assert "assistant msg 1" not in dumped


def test_cancel_without_active_chat(client, mock_llm):
    r = client.post("/api/chat/cancel")
    assert r.status_code == 200
    assert r.json() == {"status": "cancelled"}
    resp = client.post(
        "/api/chat",
        json={"message": "hola", "who_answers": "random", "chat_room": "test"},
    )
    assert resp.status_code == 200
    events = parse_events(resp.text)
    assert events[-1] == {"type": "complete", "cancelled": False}


def test_invalid_room_name_400(client):
    resp = client.post(
        "/api/chat",
        json={"message": "hola", "chat_room": "../etc"},
    )
    assert resp.status_code == 400


def test_empty_message_422(client):
    resp = client.post(
        "/api/chat",
        json={"message": "", "chat_room": "test"},
    )
    assert resp.status_code == 422


def test_vision_instruction_falls_back_to_default(tmp_path):
    from app.config import ConfigStore, VISION_PROMPT_DEFAULT
    from app.routers.chat import _vision_instruction

    config = ConfigStore(settings_path=tmp_path / "settings.json")
    assert _vision_instruction(config) == VISION_PROMPT_DEFAULT
    config.set("vision_prompt", "   ")
    assert _vision_instruction(config) == VISION_PROMPT_DEFAULT
    config.set("vision_prompt", "Describe only the dog.")
    assert _vision_instruction(config) == "Describe only the dog."
