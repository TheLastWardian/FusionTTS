import asyncio
import base64
import time
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from app import paths
from app.config import ConfigStore
from app.personas import PersonaStore
from app.services.tts.dispatcher import TTSDispatcher
from app.services.tts.engine import TTSError, TTSResult
from app.services.tts.splitter import chunk_text_punctuation


class FakeEngine:
    def __init__(self, delay: float = 0.0) -> None:
        self.delay = delay
        self.calls: list[tuple[str, str, str, str | None]] = []
        self.cancelled = 0
        self.fail: Exception | None = None
        self.fail_texts: set[str] = set()

    @property
    def count(self) -> int:
        return len(self.calls)

    async def synthesize(
        self,
        text: str,
        audio_base64: str = "",
        prompt_text: str = "",
        *,
        language: str | None = None,
        abort_event: asyncio.Event | None = None,
    ) -> TTSResult:
        self.calls.append((text, audio_base64, prompt_text, language))
        if self.fail is not None:
            raise self.fail
        if text in self.fail_texts:
            raise TTSError(f"falla programada: {text}")
        try:
            await asyncio.sleep(self.delay)
        except asyncio.CancelledError:
            self.cancelled += 1
            raise
        return TTSResult(b"FAKE-WAV", 24000)

    async def spawn(self) -> None:
        return None

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    async def close(self) -> None:
        return None

    async def status(self) -> dict:
        return {"state": "fake"}


def _persona_dict(name: str, **extra) -> dict:
    base = {
        "name": name,
        "description": "d",
        "system_prompt": "s",
        "router_hints": [],
        "avatar_color": "#000000",
        "avatar_image": None,
        "reference_audio": None,
        "reference_audio_transcript": None,
        "reference_audio_language": None,
    }
    base.update(extra)
    return base


def _write_ref(base: Path, name: str, transcript: str | None = None) -> str:
    sf.write(str(base / "personas_audio" / f"{name}.wav"), np.zeros(2400, dtype="float32"), 24000)
    if transcript is not None:
        (base / "personas_audio" / f"{name}.txt").write_text(transcript, encoding="utf-8")
    return f"personas_audio/{name}.wav"


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "BASE_DIR", tmp_path)
    (tmp_path / "personas_audio").mkdir()
    config = ConfigStore(settings_path=tmp_path / "settings.json")
    personas = PersonaStore(
        personas_yaml=tmp_path / "personas.yaml",
        audio_dir=tmp_path / "personas_audio",
    )
    return config, personas, tmp_path


@pytest.fixture
async def make_dispatcher(env):
    dispatchers: list[TTSDispatcher] = []

    def make(engine=None, **kwargs):
        d = TTSDispatcher(engine or FakeEngine(), env[1], env[0], **kwargs)
        dispatchers.append(d)
        return d

    yield make
    for d in dispatchers:
        await d.shutdown()


def test_01_splitter_abbreviations():
    assert chunk_text_punctuation("Dr. Smith said hi. Really?", 120) == [
        "Dr. Smith said hi. Really?"
    ]
    assert chunk_text_punctuation("Dr. Smith said hi. Really?", 10) == [
        "Dr. Smith said hi.",
        "Really?",
    ]
    assert chunk_text_punctuation("Dr. Smith is here.", 10) == ["Dr. Smith is here."]


def test_02_splitter_cjk():
    assert chunk_text_punctuation("你好。我是小明！", 120) == ["你好。我是小明！"]
    assert chunk_text_punctuation("你好。我是小明！", 4) == ["你好。", "我是小明！"]


def test_03_splitter_comas():
    assert chunk_text_punctuation("Hola; ¿qué tal? Bien, gracias.", 20) == [
        "Hola; ¿qué tal?",
        "Bien, gracias.",
    ]
    assert chunk_text_punctuation("Hola; ¿qué tal? Bien, gracias.", 5) == [
        "Hola;",
        "¿qué tal?",
        "Bien,",
        "gracias.",
    ]


def test_04_splitter_merge_cortos():
    assert chunk_text_punctuation("a. b. c.", 120) == ["a. b. c."]


def test_05_splitter_min_chunk_len():
    assert chunk_text_punctuation("aaaa. b.", 5) == ["aaaa.", "b."]
    assert chunk_text_punctuation("aaaa. b.", 5, 4) == ["aaaa. b."]


def test_06_splitter_vacio():
    assert chunk_text_punctuation("   ", 120) == []


async def test_07_orden_y_sentence_id(make_dispatcher, env):
    env[1].create(_persona_dict("P1"))
    engine = FakeEngine(delay=0.05)
    d = make_dispatcher(engine)
    await d.start()
    for i in range(3):
        await d.enqueue(f"s{i}", "M1", "P1")
    chunks = [await asyncio.wait_for(d.wait_audio(), 2.0) for _ in range(3)]
    assert [c.sentence_id for c in chunks] == [0, 1, 2]
    assert [c.message_id for c in chunks] == ["M1"] * 3
    assert [c.persona for c in chunks] == ["P1"] * 3
    assert all(c.audio == b"FAKE-WAV" and c.sample_rate == 24000 for c in chunks)
    assert [call[0] for call in engine.calls] == ["s0", "s1", "s2"]


async def test_08_no_backpressure(make_dispatcher, env):
    # cajas sin límite (modelo F5-TTS): encolar nunca espera al engine,
    # aunque el engine sea lento y la caja se llene
    env[1].create(_persona_dict("P1"))
    engine = FakeEngine(delay=0.4)
    d = make_dispatcher(engine)
    await d.start()
    t0 = time.monotonic()
    for i in range(4):
        await d.enqueue(f"s{i}", "M", "P1")
    elapsed = time.monotonic() - t0
    assert elapsed < 0.1
    chunks = [await asyncio.wait_for(d.wait_audio(), 5.0) for _ in range(4)]
    assert [c.sentence_id for c in chunks] == [0, 1, 2, 3]


async def test_09_pausa_conserva_cola(make_dispatcher, env):
    env[1].create(_persona_dict("P1"))
    engine = FakeEngine(delay=0.4)
    d = make_dispatcher(engine)
    await d.start()
    await d.pause()
    await d.enqueue("s1", "M", "P1")
    await d.enqueue("s2", "M", "P1")
    await asyncio.sleep(0.15)
    assert engine.count == 0
    assert engine.cancelled == 0
    assert d.audio_empty()
    await d.resume()
    c1 = await asyncio.wait_for(d.wait_audio(), 2.0)
    c2 = await asyncio.wait_for(d.wait_audio(), 2.0)
    assert [c.sentence_id for c in (c1, c2)] == [0, 1]
    assert engine.count == 2


async def test_10_pausa_no_pierde_en_vuelo(make_dispatcher, env):
    # Pausar no pierde audio: la oracion en curso termina y su chunk se
    # entrega; la siguiente espera en el gate de pausa hasta el resume
    env[1].create(_persona_dict("P1"))
    engine = FakeEngine(delay=0.4)
    d = make_dispatcher(engine)
    await d.start()
    for s in ("s1", "s2", "s3"):
        await d.enqueue(s, "M", "P1")
    c1 = await asyncio.wait_for(d.wait_audio(), 3.0)
    assert c1.sentence_id == 0
    await asyncio.sleep(0.05)
    assert engine.count == 2
    assert d._in_flight
    await d.pause()
    c2 = await asyncio.wait_for(d.wait_audio(), 3.0)
    assert c2.sentence_id == 1
    await asyncio.sleep(0.15)
    assert engine.cancelled == 0
    assert engine.count == 2
    assert d.audio_empty()
    await d.resume()
    c3 = await asyncio.wait_for(d.wait_audio(), 3.0)
    assert c3.sentence_id == 2
    assert engine.count == 3


async def test_11_stop(make_dispatcher, env):
    env[1].create(_persona_dict("P1"))
    engine = FakeEngine(delay=0.4)
    d = make_dispatcher(engine)
    await d.start()
    for s in ("s1", "s2", "s3"):
        await d.enqueue(s, "M", "P1")
    c1 = await asyncio.wait_for(d.wait_audio(), 3.0)
    assert c1.sentence_id == 0
    await asyncio.sleep(0.05)
    assert engine.count == 2
    await d.stop()
    await asyncio.sleep(0.05)
    assert engine.cancelled == 1
    await d.wait_until_done()
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(d.wait_audio(), 0.3)
    assert engine.count == 2
    assert d.is_idle()


async def test_12_reset_tras_stop(make_dispatcher, env):
    env[1].create(_persona_dict("P1"))
    engine = FakeEngine(delay=0.05)
    d = make_dispatcher(engine)
    await d.start()
    await d.enqueue("s1", "M", "P1")
    await asyncio.wait_for(d.wait_audio(), 2.0)
    await d.stop()
    await asyncio.sleep(0.05)
    d.reset()
    await d.enqueue("s4", "M", "P1")
    c = await asyncio.wait_for(d.wait_audio(), 2.0)
    assert c.sentence_id == 1
    assert engine.count == 2


async def test_13_enqueue_stoppeado_descartado(make_dispatcher, env):
    env[1].create(_persona_dict("P1"))
    engine = FakeEngine(delay=0.05)
    d = make_dispatcher(engine)
    await d.start()
    await d.stop()
    await asyncio.sleep(0.05)
    before = engine.count
    await d.enqueue("s9", "M", "P1")
    assert engine.count == before
    assert d.is_idle()


async def test_14_resolucion_persona(make_dispatcher, env):
    config, personas, base = env
    _write_ref(base, "ana", "hola mundo\n")
    personas.create(
        _persona_dict(
            "Ana",
            reference_audio="personas_audio/ana.wav",
            reference_audio_transcript="personas_audio/ana.txt",
            reference_audio_language="es",
        )
    )
    personas.create(_persona_dict("Beto"))
    expected_b64 = base64.b64encode(
        (base / "personas_audio" / "ana.wav").read_bytes()
    ).decode()
    engine = FakeEngine(delay=0.01)
    d = make_dispatcher(engine)
    await d.start()
    await d.enqueue("hola", "M", "Ana")
    await asyncio.wait_for(d.wait_audio(), 2.0)
    assert engine.calls[0] == ("hola", expected_b64, "hola mundo", "es")
    await d.enqueue("adios", "M", "Beto")
    await asyncio.wait_for(d.wait_audio(), 2.0)
    assert engine.calls[1] == ("adios", "", "", None)


async def test_15_cache_persona(make_dispatcher, env):
    config, personas, base = env
    _write_ref(base, "ana", "hola mundo\n")
    personas.create(
        _persona_dict(
            "Ana",
            reference_audio="personas_audio/ana.wav",
            reference_audio_transcript="personas_audio/ana.txt",
            reference_audio_language="es",
        )
    )
    engine = FakeEngine(delay=0.01)
    d = make_dispatcher(engine)
    await d.start()
    await d.enqueue("a1", "M", "Ana")
    await asyncio.wait_for(d.wait_audio(), 2.0)
    (base / "personas_audio" / "ana.txt").unlink()
    await d.enqueue("a2", "M", "Ana")
    await asyncio.wait_for(d.wait_audio(), 2.0)
    assert engine.calls[1] == ("a2", engine.calls[0][1], "hola mundo", "es")
    d.invalidate_persona("Ana")
    (base / "personas_audio" / "ana.wav").unlink()
    await d.enqueue("a3", "M", "Ana")
    await asyncio.wait_for(d.wait_audio(), 2.0)
    assert engine.calls[2] == ("a3", "", "", None)


async def test_16_wait_until_done_y_audio_empty(make_dispatcher, env):
    env[1].create(_persona_dict("P1"))
    engine = FakeEngine(delay=0.05)
    d = make_dispatcher(engine)
    await d.start()
    await d.enqueue("s1", "M", "P1")
    await d.enqueue("s2", "M", "P1")
    await d.wait_until_done()
    c1 = await asyncio.wait_for(d.wait_audio(), 2.0)
    c2 = await asyncio.wait_for(d.wait_audio(), 2.0)
    assert [c.sentence_id for c in (c1, c2)] == [0, 1]
    assert d.audio_empty()


async def test_17_shutdown_limpo(make_dispatcher):
    engine = FakeEngine(delay=0.05)
    d = make_dispatcher(engine)
    await d.start()
    await d.shutdown()
    assert d.is_idle()
    await d.enqueue("x", "M", "P1")
    assert engine.count == 0
    d2 = make_dispatcher(FakeEngine())
    with pytest.raises(TTSError):
        await d2.enqueue("y", "M", "P1")


async def test_18_fail_loud(make_dispatcher, env):
    # una síntesis fallida se cuenta y notifica (sin drop silencioso)
    env[1].create(_persona_dict("P1"))
    engine = FakeEngine()
    engine.fail = TTSError("audio vacío")
    d = make_dispatcher(engine)
    await d.start()
    await d.enqueue("s0", "M", "P1")
    await d.enqueue("s1", "M", "P1")
    await asyncio.wait_for(d.wait_until_done(), 2.0)
    assert d.is_idle()
    assert d.audio_empty()
    ev1 = await asyncio.wait_for(d.wait_failure(), 1.0)
    ev2 = await asyncio.wait_for(d.wait_failure(), 1.0)
    assert [ev["state"] for ev in (ev1, ev2)] == ["error", "error"]
    assert ev1["failed"] == 1 and ev1["total"] == 2
    assert ev2["failed"] == 2 and ev2["total"] == 2
    assert d.fail_empty()


async def test_19_fail_loud_mix_ok(make_dispatcher, env):
    # falla intercalada: las buenas llegan, la mala se cuenta y notifica
    env[1].create(_persona_dict("P1"))
    engine = FakeEngine(delay=0.01)
    engine.fail_texts = {"bad1"}
    d = make_dispatcher(engine)
    await d.start()
    await d.enqueue("ok0", "M", "P1")
    await d.enqueue("bad1", "M", "P1")
    await d.enqueue("ok2", "M", "P1")
    await asyncio.wait_for(d.wait_until_done(), 2.0)
    c0 = await asyncio.wait_for(d.wait_audio(), 1.0)
    c2 = await asyncio.wait_for(d.wait_audio(), 1.0)
    assert d.audio_empty()
    assert [c.sentence_id for c in (c0, c2)] == [0, 2]
    ev = await asyncio.wait_for(d.wait_failure(), 1.0)
    assert ev["failed"] == 1 and ev["total"] == 3
    assert d.fail_empty()
    assert d.is_idle()


async def test_20_wait_until_done_regresion_carrera(make_dispatcher, env):
    # is_idle no puede ser true antes de que el último chunk entre a audio_q
    env[1].create(_persona_dict("P1"))
    engine = FakeEngine(delay=0.02)
    d = make_dispatcher(engine)
    await d.start()
    for i in range(5):
        await d.enqueue(f"s{i}", "M", "P1")
    await asyncio.wait_for(d.wait_until_done(), 3.0)
    for i in range(5):
        chunk = await asyncio.wait_for(d.wait_audio(), 0.2)
        assert chunk.sentence_id == i
    assert d.audio_empty()


async def test_21_reset_limpiacajas_y_contadores(make_dispatcher, env):
    # reset (nueva ronda) vacía cajas y contadores; nada viejo filtra
    env[1].create(_persona_dict("P1"))
    engine = FakeEngine(delay=0.05)
    d = make_dispatcher(engine)
    await d.start()
    await d.enqueue("vieja", "M_ANTIGUO", "P1")
    c = await asyncio.wait_for(d.wait_audio(), 2.0)
    assert c.message_id == "M_ANTIGUO"
    d.reset()
    assert d.is_idle()
    await d.enqueue("nueva", "M_NUEVO", "P1")
    c2 = await asyncio.wait_for(d.wait_audio(), 2.0)
    assert c2.message_id == "M_NUEVO"
    await d.wait_until_done()
    assert d.audio_empty()
