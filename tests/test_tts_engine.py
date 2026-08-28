import asyncio
import socket
import sys
import time
from io import BytesIO
from pathlib import Path

import httpx
import pytest
import soundfile as sf

from app import paths
from app.config import ConfigStore
from app.services.tts.engine import (
    TTSClientError,
    TTSNotReadyError,
    TTSError,
    TTSTimeoutError,
)
from app.services.tts.omnivoice import OmniVoiceEngine
from app.services.tts.registry import create_engine

FAKE_SERVER = '''
import asyncio
import base64
import io
import os

import numpy as np
import soundfile as sf
import uvicorn
from fastapi import FastAPI, HTTPException

app = FastAPI()

_state = {"status": "unloaded", "load_calls": 0}
_calls = []
_empty = {"left": int(os.getenv("EMPTY_FIRST_N", "0"))}


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "model": "fake",
        "device": "cpu",
        "env": {
            "HF_HUB_OFFLINE": os.getenv("HF_HUB_OFFLINE"),
            "PORT": os.getenv("PORT"),
        },
    }


@app.get("/status")
async def status():
    return {"status": _state["status"], "model": "fake", "device": "cpu"}


@app.get("/counters")
async def counters():
    return {"load_calls": _state["load_calls"]}


@app.get("/calls")
async def calls():
    return {"calls": _calls}


@app.post("/load")
async def load():
    if _state["status"] != "ready":
        _state["load_calls"] += 1
        _state["status"] = "ready"
    return {"status": _state["status"]}


@app.post("/unload")
async def unload():
    _state["status"] = "unloaded"
    return {"status": _state["status"]}


@app.post("/synthesize")
async def synthesize(req: dict):
    _calls.append(req)
    delay = os.getenv("SYNTH_DELAY")
    if delay:
        await asyncio.sleep(float(delay))
    if req.get("text") == "boom":
        raise HTTPException(status_code=500, detail="boom detail")
    if _state["status"] != "ready":
        raise HTTPException(status_code=503, detail="Modelo no cargado")
    if _empty["left"] > 0:
        _empty["left"] -= 1
        return {
            "audio_base64": base64.b64encode(b"RIFF" + b"\\x00" * 36).decode("utf-8"),
            "sample_rate": 24000,
        }
    buf = io.BytesIO()
    sf.write(buf, np.zeros(2400, dtype="float32"), 24000, format="WAV")
    buf.seek(0)
    return {
        "audio_base64": base64.b64encode(buf.read()).decode("utf-8"),
        "sample_rate": 24000,
    }


if __name__ == "__main__":
    uvicorn.run(
        app,
        host=os.getenv("HOST", "127.0.0.1"),
        port=int(os.getenv("PORT", "5500")),
    )
'''


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture
async def make_engine(tmp_path):
    fake_dir = tmp_path / "fake-server"
    fake_dir.mkdir()
    (fake_dir / "server.py").write_text(FAKE_SERVER, encoding="utf-8")
    engines = []
    counter = 0

    def make(port=None, **overrides):
        nonlocal counter
        counter += 1
        config = ConfigStore(settings_path=tmp_path / f"settings_{counter}.json")
        config.set("tts_server_python", sys.executable)
        config.set("tts_server_port", port if port is not None else _free_port())
        for key, value in overrides.items():
            config.set(key, value)
        engine = OmniVoiceEngine(config, server_dir=fake_dir)
        engines.append(engine)
        return engine, config

    yield make

    for engine in engines:
        await engine.close()


async def _get(port: int, path: str):
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"http://127.0.0.1:{port}{path}")
    return resp.json()


async def test_01_status_without_process(make_engine):
    engine, _ = make_engine()
    assert await engine.status() == {"state": "stopped", "server": None}


async def test_02_spawn_env_and_status(make_engine):
    engine, config = make_engine()
    port = config.get("tts_server_port")
    await engine.spawn()
    st = await engine.status()
    assert st["state"] == "running"
    assert st["server"]["status"] == "unloaded"
    health = await _get(port, "/health")
    assert health["env"]["HF_HUB_OFFLINE"] == "1"
    assert health["env"]["PORT"] == str(port)


async def test_03_start_loads_model(make_engine):
    engine, config = make_engine()
    await engine.start()
    st = await engine.status()
    assert st["state"] == "running"
    assert st["server"]["status"] == "ready"
    counters = await _get(config.get("tts_server_port"), "/counters")
    assert counters["load_calls"] == 1


async def test_04_start_idempotent(make_engine):
    engine, config = make_engine()
    await engine.start()
    pid = engine._proc.pid
    await engine.start()
    assert engine._proc.pid == pid
    counters = await _get(config.get("tts_server_port"), "/counters")
    assert counters["load_calls"] == 1


async def test_05_synthesize_body_and_result(make_engine):
    engine, config = make_engine()
    config.set("tts_language", "es")
    config.set("tts_num_steps", 12)
    config.set("tts_guidance_scale", 2.5)
    config.set("tts_speed", 1.2)
    config.set("tts_instruct", "voz calmada")
    await engine.start()
    result = await engine.synthesize("hola", audio_base64="aWQ=", prompt_text="ref")
    assert result.sample_rate == 24000
    data, sr = sf.read(BytesIO(result.audio), dtype="float32")
    assert sr == 24000
    assert len(data) == 2400
    port = config.get("tts_server_port")
    calls = (await _get(port, "/calls"))["calls"]
    assert calls[-1] == {
        "text": "hola",
        "audio_base64": "aWQ=",
        "prompt_text": "ref",
        "language": "es",
        "num_steps": 12,
        "guidance_scale": 2.5,
        "seed": None,
        "speed": 1.2,
        "instruct": "voz calmada",
    }


async def test_05b_synthesize_language_empty_auto(make_engine):
    engine, config = make_engine()
    config.set("tts_language", "")
    await engine.start()
    await engine.synthesize("hola")
    port = config.get("tts_server_port")
    calls = (await _get(port, "/calls"))["calls"]
    assert calls[-1]["language"] == ""


async def test_06_stop_then_synthesize_auto_load(make_engine):
    engine, config = make_engine()
    await engine.start()
    pid = engine._proc.pid
    await engine.stop()
    # stop() mata el proceso: VRAM a 0
    assert engine._proc is None
    st = await engine.status()
    assert st["state"] == "stopped"
    assert st["server"] is None
    # el siguiente synthesize re-spawnea un proceso NUEVO que carga el modelo
    result = await engine.synthesize("hola")
    assert result.sample_rate == 24000
    assert engine._proc is not None
    assert engine._proc.pid != pid
    st = await engine.status()
    assert st["state"] == "running"
    assert st["server"]["status"] == "ready"
    # proceso nuevo: el contador de cargas arranca desde 0
    counters = await _get(config.get("tts_server_port"), "/counters")
    assert counters["load_calls"] == 1


async def test_07_respawn_after_crash(make_engine):
    engine, _ = make_engine()
    await engine.start()
    pid = engine._proc.pid
    engine._proc.kill()
    while engine._proc.poll() is None:
        await asyncio.sleep(0.05)
    result = await engine.synthesize("hola")
    assert result.sample_rate == 24000
    assert engine._proc.pid != pid
    st = await engine.status()
    assert st["state"] == "running"
    assert st["server"]["status"] == "ready"


async def test_08_close_blocks_respawn(make_engine):
    engine, _ = make_engine()
    await engine.start()
    await engine.close()
    assert engine._proc is None
    with pytest.raises(TTSError, match="engine closed"):
        await engine.synthesize("hola")
    assert await engine.status() == {"state": "stopped", "server": None}
    await engine.close()


async def test_09_cancel_abort(make_engine, monkeypatch):
    monkeypatch.setenv("SYNTH_DELAY", "30")
    engine, config = make_engine()
    await engine.start()
    task = asyncio.create_task(engine.synthesize("lento"))
    await asyncio.sleep(0.5)
    task.cancel()
    t0 = time.monotonic()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert time.monotonic() - t0 < 2.0
    st = await engine.status()
    assert st["state"] == "running"
    health = await _get(config.get("tts_server_port"), "/health")
    assert health["status"] == "ok"
    monkeypatch.delenv("SYNTH_DELAY")
    engine2, _ = make_engine()
    result = await engine2.synthesize("hola")
    assert result.sample_rate == 24000


async def test_10_timeout(make_engine, monkeypatch):
    monkeypatch.setenv("SYNTH_DELAY", "10")
    engine, _ = make_engine(tts_sentence_timeout=5)
    await engine.start()
    t0 = time.monotonic()
    with pytest.raises(TTSTimeoutError):
        await engine.synthesize("lento")
    elapsed = time.monotonic() - t0
    assert 4.5 <= elapsed <= 8.5
    st = await engine.status()
    assert st["state"] == "running"


async def test_11_error_500(make_engine):
    engine, _ = make_engine()
    await engine.start()
    with pytest.raises(TTSClientError) as exc_info:
        await engine.synthesize("boom")
    assert exc_info.value.status_code == 500
    assert exc_info.value.detail


async def test_12_not_ready_503(make_engine, monkeypatch):
    engine, _ = make_engine()
    await engine.spawn()

    async def _no_ensure():
        return None

    monkeypatch.setattr(engine, "_ensure_ready", _no_ensure)
    with pytest.raises(TTSNotReadyError) as exc_info:
        await engine.synthesize("hola")
    assert exc_info.value.status_code == 503


async def test_13_resolve_python(tmp_path):
    config = ConfigStore(settings_path=tmp_path / "settings_auto.json")
    engine = OmniVoiceEngine(config)
    expected = paths.BASE_DIR.parent / "OmniVoice" / "venv" / "Scripts" / "python.exe"
    assert Path(engine._resolve_python()) == expected
    assert expected.exists()
    config.set("tts_server_python", "C:\\Python311\\python.exe")
    assert engine._resolve_python() == "C:\\Python311\\python.exe"
    await engine.close()


async def test_14_registry(tmp_path):
    config = ConfigStore(settings_path=tmp_path / "settings_registry.json")
    engine = create_engine("omnivoice", config)
    assert isinstance(engine, OmniVoiceEngine)
    await engine.close()
    with pytest.raises(ValueError):
        create_engine("qwen3", config)


async def test_15_spawn_port_occupied(make_engine):
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    port = listener.getsockname()[1]
    try:
        engine, _ = make_engine(port=port)
        with pytest.raises(TTSError):
            await engine.spawn()
    finally:
        listener.close()
    assert engine._proc is None or engine._proc.poll() is not None


async def test_16_language_override(make_engine):
    engine, config = make_engine()
    config.set("tts_language", "es")
    await engine.start()
    await engine.synthesize("hola", language="en")
    port = config.get("tts_server_port")
    calls = (await _get(port, "/calls"))["calls"]
    assert calls[-1]["language"] == "en"
    await engine.synthesize("hola")
    calls = (await _get(port, "/calls"))["calls"]
    assert calls[-1]["language"] == "es"


async def test_17_synthesize_retries_empty_audio(make_engine, monkeypatch):
    monkeypatch.setenv("EMPTY_FIRST_N", "1")
    engine, config = make_engine()
    await engine.start()
    result = await engine.synthesize("hola")
    assert result.sample_rate == 24000
    data, sr = sf.read(BytesIO(result.audio), dtype="float32")
    assert sr == 24000
    assert len(data) == 2400
    port = config.get("tts_server_port")
    calls = (await _get(port, "/calls"))["calls"]
    assert len(calls) == 2


async def test_18_synthesize_empty_all_attempts_raises(make_engine, monkeypatch):
    monkeypatch.setenv("EMPTY_FIRST_N", "99")
    engine, _ = make_engine()
    await engine.start()
    with pytest.raises(TTSClientError, match="audio vacio"):
        await engine.synthesize("hola")


async def test_19_synthesize_retry_uses_random_seed(make_engine, monkeypatch):
    monkeypatch.setenv("EMPTY_FIRST_N", "1")
    engine, config = make_engine()
    config.set("tts_seed", 424242)
    await engine.start()
    await engine.synthesize("hola")
    port = config.get("tts_server_port")
    calls = (await _get(port, "/calls"))["calls"]
    assert calls[0]["seed"] == 424242
    assert calls[1]["seed"] is None
