import asyncio
import json
import py_compile
import subprocess
import sys
import time

import pytest

from app import paths
from app.config import ConfigStore
from app.services.asr.engine import ASREngine, ASREngineError, ASRError, ASRTimeoutError
from app.services.asr.manager import ASRManager

FAKE_WORKER = '''
import json
import os
import sys
import time

raw = sys.stdin.readline()
cmd = json.loads(raw) if raw.strip() else {}

mode = os.getenv("ASR_MODE", "ok")
delay = os.getenv("ASR_DELAY")
overlap_file = os.getenv("ASR_OVERLAP_FILE")
cmd_file = os.getenv("ASR_CMD_FILE")

if cmd_file:
    with open(cmd_file, "a", encoding="utf-8") as f:
        f.write(json.dumps(cmd) + "\\n")

if overlap_file:
    with open(overlap_file, "a", encoding="utf-8") as f:
        f.write("in\\n")

if delay:
    time.sleep(float(delay))

if overlap_file:
    with open(overlap_file, "a", encoding="utf-8") as f:
        f.write("out\\n")

if mode == "crash":
    print("fake worker crash", file=sys.stderr, flush=True)
    sys.exit(3)

if mode == "garbage":
    sys.stdout.write("esto no es json\\n")
    sys.stdout.flush()
    sys.exit(0)

if mode == "error":
    print("fake worker: error simulado", file=sys.stderr, flush=True)
    sys.stdout.write(json.dumps({"ok": False, "error": "fake asr error"}) + "\\n")
    sys.stdout.flush()
    sys.exit(1)

resp = {
    "ok": True,
    "text": "fake transcription",
    "device": cmd.get("device"),
    "model": cmd.get("model"),
    "language": cmd.get("language"),
    "path": cmd.get("path"),
}
sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\\n")
sys.stdout.flush()
'''


@pytest.fixture
async def make_manager(tmp_path):
    fake_path = tmp_path / "fake_worker.py"
    fake_path.write_text(FAKE_WORKER, encoding="utf-8")
    managers = []
    counter = 0

    def make(**overrides):
        nonlocal counter
        counter += 1
        config = ConfigStore(settings_path=tmp_path / f"settings_{counter}.json")
        for key, value in overrides.items():
            config.set(key, value)
        manager = ASRManager(config, worker_script=fake_path, python=sys.executable)
        managers.append(manager)
        return manager, config

    yield make

    for manager in managers:
        await manager.close()


async def test_01_transcribe_spawns_and_kills(make_manager):
    manager, _ = make_manager()
    assert isinstance(manager, ASREngine)
    text = await manager.transcribe("audio.wav", language="en")
    assert text == "fake transcription"
    assert manager._proc is not None
    assert manager._proc.poll() is not None
    assert manager._proc.returncode == 0
    assert not manager._proc_alive()


async def test_02_command_body_from_config(make_manager, tmp_path, monkeypatch):
    manager, _ = make_manager(asr_model="small", asr_device="cuda")
    cmd_file = tmp_path / "cmds.jsonl"
    monkeypatch.setenv("ASR_CMD_FILE", str(cmd_file))
    await manager.transcribe("personas_audio/Tifa_FF_eng_trimmed.wav", language="en")
    cmd = json.loads(cmd_file.read_text(encoding="utf-8").strip().splitlines()[-1])
    assert cmd == {
        "cmd": "transcribe",
        "path": "personas_audio/Tifa_FF_eng_trimmed.wav",
        "language": "en",
        "model": "small",
        "device": "cuda",
    }


async def test_03_hot_reload_model(make_manager, tmp_path, monkeypatch):
    manager, config = make_manager()
    cmd_file = tmp_path / "cmds.jsonl"
    monkeypatch.setenv("ASR_CMD_FILE", str(cmd_file))
    await manager.transcribe("a.wav")
    config.set("asr_model", "base")
    await manager.transcribe("b.wav")
    lines = cmd_file.read_text(encoding="utf-8").strip().splitlines()
    assert json.loads(lines[0])["model"] == "medium"
    assert json.loads(lines[1])["model"] == "base"


async def test_04_timeout_kills_worker(make_manager, monkeypatch):
    monkeypatch.setenv("ASR_DELAY", "12")
    manager, _ = make_manager(asr_timeout=10)
    t0 = time.monotonic()
    with pytest.raises(ASRTimeoutError):
        await manager.transcribe("lento.wav")
    elapsed = time.monotonic() - t0
    assert 9.5 <= elapsed <= 16.0
    assert manager._proc is not None
    assert manager._proc.poll() is not None
    assert not manager._proc_alive()


async def test_05_worker_crash_no_response(make_manager, monkeypatch):
    monkeypatch.setenv("ASR_MODE", "crash")
    manager, _ = make_manager()
    with pytest.raises(ASREngineError) as exc_info:
        await manager.transcribe("a.wav")
    assert "sin responder" in exc_info.value.detail
    assert "exit=3" in exc_info.value.detail
    assert "fake worker crash" in exc_info.value.detail
    assert manager._proc is not None
    assert manager._proc.poll() is not None


async def test_06_worker_garbage_output(make_manager, monkeypatch):
    monkeypatch.setenv("ASR_MODE", "garbage")
    manager, _ = make_manager()
    with pytest.raises(ASREngineError) as exc_info:
        await manager.transcribe("a.wav")
    assert "no-JSON" in exc_info.value.detail
    assert manager._proc is not None
    assert manager._proc.poll() is not None


async def test_07_worker_reports_error(make_manager, monkeypatch):
    monkeypatch.setenv("ASR_MODE", "error")
    manager, _ = make_manager()
    with pytest.raises(ASREngineError) as exc_info:
        await manager.transcribe("a.wav")
    assert "fake asr error" in exc_info.value.detail
    assert "fake worker: error simulado" in exc_info.value.detail
    assert manager._proc is not None
    assert manager._proc.poll() is not None


async def test_08_serialization_one_at_a_time(make_manager, tmp_path, monkeypatch):
    monkeypatch.setenv("ASR_DELAY", "0.4")
    overlap_file = tmp_path / "overlap.txt"
    monkeypatch.setenv("ASR_OVERLAP_FILE", str(overlap_file))
    manager, _ = make_manager()
    results = await asyncio.gather(
        manager.transcribe("a.wav"), manager.transcribe("b.wav")
    )
    assert results == ["fake transcription", "fake transcription"]
    assert overlap_file.read_text(encoding="utf-8") == "in\nout\nin\nout\n"
    assert manager._proc is not None
    assert manager._proc.poll() is not None


async def test_09_close_kills_live_and_idempotent(make_manager):
    manager, _ = make_manager()
    await manager._spawn()
    assert manager._proc_alive()
    await manager.close()
    assert manager._proc is not None
    assert manager._proc.poll() is not None
    await manager.close()
    with pytest.raises(ASRError, match="engine closed"):
        await manager.transcribe("a.wav")


async def test_10_close_without_spawn(make_manager):
    manager, _ = make_manager()
    await manager.close()
    await manager.close()
    assert manager._proc is None


def test_11_real_worker_selftest():
    worker = paths.BASE_DIR / "app" / "services" / "asr" / "worker.py"
    py_compile.compile(str(worker), doraise=True)
    proc = subprocess.run(
        [sys.executable, str(worker), "--selftest"],
        capture_output=True,
        text=True,
        timeout=90,
    )
    assert proc.returncode == 0, proc.stderr
    assert ("[ASR] device=cuda" in proc.stdout) or ("[ASR] device=cpu" in proc.stdout)
    assert "faster-whisper=" in proc.stdout
    assert "ctranslate2=" in proc.stdout
    assert "[ASR] selftest ok" in proc.stdout


def test_12_real_worker_rejects_bad_command():
    worker = paths.BASE_DIR / "app" / "services" / "asr" / "worker.py"
    proc = subprocess.run(
        [sys.executable, str(worker)],
        input='{"cmd": "nada"}\n',
        capture_output=True,
        text=True,
        timeout=90,
    )
    assert proc.returncode == 1
    data = json.loads(proc.stdout.strip().splitlines()[-1])
    assert data["ok"] is False
    assert "cmd desconocido" in data["error"]
