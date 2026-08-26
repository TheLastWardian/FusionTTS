"""Tests de tts-server/server.py con modelo mockeado (sin torch, sin VRAM)."""

import base64
import io

import numpy as np
import pytest
import soundfile as sf
from fastapi.testclient import TestClient

import server


class FakeModel:
    """Modelo fake: registra las llamadas, no hace inferencia."""

    def __init__(self):
        self.prompt_calls = 0
        self.prompts = []
        self.generate_calls = []

    def create_voice_clone_prompt(self, ref_audio, ref_text=None, **kw):
        self.prompt_calls += 1
        p = f"fake-prompt-{self.prompt_calls}"
        self.prompts.append(p)
        return p

    def generate(self, text, **kwargs):
        self.generate_calls.append({"text": text, **kwargs})
        return np.zeros(24000, dtype=np.float32)  # 24 kHz, 1 s


@pytest.fixture
def env(monkeypatch):
    """App con modelo fake + carga mockeada; estado reseteado por test."""
    fake = FakeModel()
    state = {"loads": 0}

    def fake_load():
        state["loads"] += 1
        server._model = fake
        return fake

    monkeypatch.setattr(server, "_status", "unloaded")
    monkeypatch.setattr(server, "_model", None)
    monkeypatch.setattr(server, "_load_model_impl", fake_load)
    server._prompt_cache.clear()

    with TestClient(server.app) as client:
        yield {"client": client, "fake": fake, "state": state}


def _wav_b64(n: int = 24000, sr: int = 24000, val: float = 0.0) -> str:
    buf = io.BytesIO()
    sf.write(buf, np.full(n, val, dtype=np.float32), sr, format="WAV")
    return base64.b64encode(buf.getvalue()).decode("utf-8")


def test_health(env):
    r = env["client"].get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["model"] == server.MODEL_NAME
    assert body["device"] == server.DEVICE


def test_status_initial_unloaded(env):
    r = env["client"].get("/status")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "unloaded"
    assert body["model"] == server.MODEL_NAME
    assert body["device"] == server.DEVICE


def test_synthesize_unloaded_returns_503(env):
    r = env["client"].post("/synthesize", json={"text": "hola"})
    assert r.status_code == 503
    assert r.json()["detail"] == "Modelo no cargado"


def test_load_idempotent(env):
    c = env["client"]
    r1 = c.post("/load")
    assert r1.status_code == 200
    assert r1.json()["status"] == "ready"
    assert c.get("/status").json()["status"] == "ready"
    r2 = c.post("/load")
    assert r2.status_code == 200
    assert r2.json()["status"] == "ready"
    assert env["state"]["loads"] == 1  # la carga se ejecuto una sola vez


def test_synthesize_voice_clone_params(env):
    c = env["client"]
    c.post("/load")
    r = c.post("/synthesize", json={
        "text": "hola mundo",
        "audio_base64": _wav_b64(val=0.1),
        "prompt_text": "texto de referencia",
        "num_steps": 20,
        "guidance_scale": 1.5,
        "speed": 1.2,
        "language": "es",
    })
    assert r.status_code == 200
    body = r.json()
    assert body["sample_rate"] == 24000
    raw = base64.b64decode(body["audio_base64"])
    data, sr = sf.read(io.BytesIO(raw), dtype="float32")
    assert sr == 24000
    assert len(data) == 24000

    call = env["fake"].generate_calls[-1]
    assert call["text"] == "hola mundo"
    assert call["num_step"] == 20
    assert call["guidance_scale"] == 1.5
    assert call["speed"] == 1.2
    assert call["language"] == "es"
    assert call["voice_clone_prompt"] == env["fake"].prompts[-1]
    assert call["instruct"] is None
    assert env["fake"].prompt_calls == 1


def test_prompt_cache_lru_hit(env):
    c = env["client"]
    c.post("/load")
    body = {
        "text": "a",
        "audio_base64": _wav_b64(val=0.1),
        "prompt_text": "ref",
    }
    assert c.post("/synthesize", json=body).status_code == 200
    assert c.post("/synthesize", json=body).status_code == 200
    assert env["fake"].prompt_calls == 1  # hit de caché
    other = dict(body, audio_base64=_wav_b64(val=0.2))
    assert c.post("/synthesize", json=other).status_code == 200
    assert env["fake"].prompt_calls == 2  # nuevo audio -> miss


def test_audio_base64_without_prompt_text_400(env):
    c = env["client"]
    c.post("/load")
    r = c.post("/synthesize", json={"text": "a", "audio_base64": _wav_b64()})
    assert r.status_code == 400
    assert "prompt_text" in r.json()["detail"]
    assert env["fake"].prompt_calls == 0


def test_instruct_mode(env):
    c = env["client"]
    c.post("/load")
    r = c.post("/synthesize", json={"text": "a", "instruct": "voz grave"})
    assert r.status_code == 200
    call = env["fake"].generate_calls[-1]
    assert call["instruct"] == "voz grave"
    assert call["voice_clone_prompt"] is None
    assert env["fake"].prompt_calls == 0


def test_auto_mode(env):
    c = env["client"]
    c.post("/load")
    r = c.post("/synthesize", json={"text": "a"})
    assert r.status_code == 200
    call = env["fake"].generate_calls[-1]
    assert call["instruct"] is None
    assert call["voice_clone_prompt"] is None
    assert env["fake"].prompt_calls == 0


def test_unload(env):
    c = env["client"]
    c.post("/load")
    r = c.post("/unload")
    assert r.status_code == 200
    assert r.json()["status"] == "unloaded"
    assert c.get("/status").json()["status"] == "unloaded"
    r2 = c.post("/synthesize", json={"text": "a"})
    assert r2.status_code == 503
    assert r2.json()["detail"] == "Modelo no cargado"


def test_empty_language_becomes_none(env):
    c = env["client"]
    c.post("/load")
    r = c.post("/synthesize", json={"text": "a", "language": ""})
    assert r.status_code == 200
    assert env["fake"].generate_calls[-1]["language"] is None


def test_seed_is_ignored(env):
    c = env["client"]
    c.post("/load")
    r = c.post("/synthesize", json={"text": "a", "seed": 42})
    assert r.status_code == 200
    assert "seed" not in env["fake"].generate_calls[-1]
