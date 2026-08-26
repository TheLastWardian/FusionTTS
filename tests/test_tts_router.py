import asyncio
import base64
import time

import numpy as np
import pytest
import soundfile as sf
from fastapi.testclient import TestClient

from app import paths
from app.main import app
from app.services.tts.engine import TTSResult


class FakeEngine:
    def __init__(self, start_delay: float = 0.2) -> None:
        self._state = "stopped"
        self.start_delay = start_delay
        self.start_calls = 0
        self.stop_calls = 0
        self.synth_calls: list[tuple[str, str, str, str | None]] = []

    @property
    def last_text(self) -> str | None:
        return self.synth_calls[-1][0] if self.synth_calls else None

    async def spawn(self) -> None:
        return None

    async def start(self) -> None:
        self.start_calls += 1
        await asyncio.sleep(self.start_delay)
        self._state = "running"

    async def stop(self) -> None:
        self.stop_calls += 1
        self._state = "stopped"

    async def close(self) -> None:
        return None

    async def status(self) -> dict:
        return {"state": self._state, "server": None}

    async def synthesize(
        self,
        text: str,
        audio_base64: str = "",
        prompt_text: str = "",
        *,
        language: str | None = None,
        abort_event: asyncio.Event | None = None,
    ) -> TTSResult:
        self.synth_calls.append((text, audio_base64, prompt_text, language))
        return TTSResult(b"FAKEWAV", 24000)


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "BASE_DIR", tmp_path)
    monkeypatch.setattr(paths, "SETTINGS_PATH", tmp_path / "settings.json")
    monkeypatch.setattr(paths, "CHATROOMS_DIR", tmp_path / "chatrooms")
    monkeypatch.setattr(paths, "CHATROOMS_YAML", tmp_path / "chatrooms.yaml")
    monkeypatch.setattr(paths, "PERSONAS_YAML", tmp_path / "personas.yaml")
    monkeypatch.setattr(paths, "PERSONAS_AUDIO_DIR", tmp_path / "personas_audio")
    fake = FakeEngine()
    with TestClient(app) as c:
        state = app.state.app_state
        state.tts_engine = fake
        state.dispatcher._engine = fake
        yield c, fake


def test_01_status(client):
    c, _ = client
    r = c.get("/api/tts/status")
    assert r.status_code == 200
    data = r.json()
    assert data["engine"] == {"state": "stopped", "server": None}
    assert data["dispatcher"] == {"paused": False, "stopped": False, "idle": True}


def test_02_enable_async(client):
    c, fake = client
    t0 = time.monotonic()
    r = c.post("/api/tts/enable")
    elapsed = time.monotonic() - t0
    assert r.status_code == 202
    assert r.json() == {"status": "enabling"}
    assert elapsed < 0.2
    deadline = time.monotonic() + 3.0
    data = {}
    while time.monotonic() < deadline:
        data = c.get("/api/tts/status").json()
        if data["engine"]["state"] == "running":
            break
        time.sleep(0.05)
    assert data["engine"]["state"] == "running"
    assert fake.start_calls == 1


def test_03_disable(client):
    c, fake = client
    r = c.post("/api/tts/disable")
    assert r.status_code == 200
    assert r.json() == {"status": "disabled"}
    assert fake.stop_calls == 1
    data = c.get("/api/tts/status").json()
    assert data["engine"]["state"] == "stopped"
    assert data["dispatcher"]["stopped"] is True


def test_04_stop(client):
    c, fake = client
    r = c.post("/api/tts/stop")
    assert r.status_code == 200
    assert r.json() == {"status": "stopped"}
    assert fake.stop_calls == 0
    data = c.get("/api/tts/status").json()
    assert data["dispatcher"]["stopped"] is True


def test_05_pause_resume(client):
    c, _ = client
    r = c.post("/api/tts/pause")
    assert r.status_code == 200
    assert r.json() == {"status": "paused"}
    assert c.get("/api/tts/status").json()["dispatcher"]["paused"] is True
    r = c.post("/api/tts/resume")
    assert r.status_code == 200
    assert r.json() == {"status": "resumed"}
    assert c.get("/api/tts/status").json()["dispatcher"]["paused"] is False


def test_06_speak_tts_off_409(client):
    c, _ = client
    r = c.post("/api/tts/speak", json={"text": "hola"})
    assert r.status_code == 409
    assert r.json() == {"detail": "TTS no está activo"}


def test_07_speak(client):
    c, fake = client
    fake._state = "running"
    r = c.post("/api/tts/speak", json={"text": "hola"})
    assert r.status_code == 200
    assert r.headers["content-type"] == "audio/wav"
    assert r.headers["x-sample-rate"] == "24000"
    assert r.content == b"FAKEWAV"
    assert fake.last_text == "hola"
    assert fake.synth_calls[-1] == ("hola", "", "", None)


def test_08_speak_empty_text_422(client):
    c, _ = client
    r = c.post("/api/tts/speak", json={"text": ""})
    assert r.status_code == 422


def test_09_enable_already_running(client):
    c, fake = client
    fake._state = "running"
    r = c.post("/api/tts/enable")
    assert r.status_code == 200
    assert r.json() == {"status": "already"}
    assert fake.start_calls == 0


def test_10_speak_with_persona(client, tmp_path):
    c, fake = client
    (tmp_path / "personas_audio").mkdir()
    sf.write(
        str(tmp_path / "personas_audio" / "ana.wav"),
        np.zeros(2400, dtype="float32"),
        24000,
    )
    (tmp_path / "personas_audio" / "ana.txt").write_text("hola mundo\n", encoding="utf-8")
    r = c.post(
        "/api/personas",
        json={
            "name": "Ana",
            "description": "d",
            "system_prompt": "s",
            "router_hints": [],
            "avatar_color": "#000000",
            "avatar_image": None,
            "reference_audio": "personas_audio/ana.wav",
            "reference_audio_transcript": "personas_audio/ana.txt",
            "reference_audio_language": "es",
        },
    )
    assert r.status_code == 201
    fake._state = "running"
    expected_b64 = base64.b64encode(
        (tmp_path / "personas_audio" / "ana.wav").read_bytes()
    ).decode()
    r = c.post("/api/tts/speak", json={"text": "hola", "persona": "Ana"})
    assert r.status_code == 200
    assert r.content == b"FAKEWAV"
    assert fake.synth_calls[-1] == ("hola", expected_b64, "hola mundo", "es")
