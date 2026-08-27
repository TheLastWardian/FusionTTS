import io
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import paths
from app.main import app
from app.routers.personas import parse_name_from_filename, sanitize_audio_stem
from app.services.asr.engine import ASREngineError, ASRTimeoutError
from app.services.llm import LLMError

VALID_JSON = json.dumps(
    {
        "name": "Tifa",
        "source": "Final Fantasy VII",
        "description": "Martial artist and bar owner of Seventh Heaven.",
        "system_prompt": "You are Tifa Lockhart from Final Fantasy VII.",
        "color": "#2E8B57",
        "language": "en",
    }
)


class FakeASR:
    def __init__(self, text="hola, esta es una transcripcion de prueba", error=None) -> None:
        self.text = text
        self.error = error
        self.calls: list[tuple[str, str | None]] = []

    async def transcribe(self, path, language=None) -> str:
        self.calls.append((str(path), language))
        if self.error is not None:
            raise self.error
        return self.text

    async def close(self) -> None:
        return None


class FakeLLM:
    def __init__(
        self,
        models=None,
        chat_result=None,
        chat_error=None,
        models_error=None,
    ) -> None:
        self.models = models if models is not None else []
        self.chat_result = chat_result
        self.chat_error = chat_error
        self.models_error = models_error
        self.models_calls = 0
        self.chat_calls: list[tuple[list[dict], int | None]] = []

    async def get_loaded_models(self) -> list[str]:
        self.models_calls += 1
        if self.models_error is not None:
            raise self.models_error
        return list(self.models)

    async def chat(self, messages: list[dict], max_tokens: int | None = None) -> str:
        self.chat_calls.append((messages, max_tokens))
        if self.chat_error is not None:
            raise self.chat_error
        return self.chat_result

    async def close(self) -> None:
        return None


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "BASE_DIR", tmp_path)
    monkeypatch.setattr(paths, "SETTINGS_PATH", tmp_path / "settings.json")
    monkeypatch.setattr(paths, "CHATROOMS_DIR", tmp_path / "chatrooms")
    monkeypatch.setattr(paths, "CHATROOMS_YAML", tmp_path / "chatrooms.yaml")
    monkeypatch.setattr(paths, "PERSONAS_YAML", tmp_path / "personas.yaml")
    monkeypatch.setattr(paths, "PERSONAS_AUDIO_DIR", tmp_path / "personas_audio")
    asr = FakeASR()
    llm = FakeLLM()
    with TestClient(app) as c:
        state = app.state.app_state
        state.asr_manager = asr
        state.llm = llm
        yield c, asr, llm, tmp_path


def upload(c, filename, content=b"RIFF-fake-wav", name=None, language=None):
    data = {}
    if name is not None:
        data["name"] = name
    if language is not None:
        data["language"] = language
    return c.post(
        "/api/personas/from-audio",
        files={"file": (filename, io.BytesIO(content), "audio/wav")},
        data=data,
    )


def make_persona(name="Zack", **overrides):
    persona = {
        "name": name,
        "description": f"{name} desc",
        "system_prompt": f"You are {name}.",
        "router_hints": [],
        "avatar_color": "#888888",
        "avatar_image": None,
        "reference_audio": None,
        "reference_audio_transcript": None,
        "reference_audio_language": None,
    }
    persona.update(overrides)
    return persona


def test_happy_path_con_llm(client):
    c, asr, llm, tmp = client
    llm.models = ["test-model"]
    llm.chat_result = VALID_JSON
    r = upload(c, "Tifa_Eng.wav")
    assert r.status_code == 200
    body = r.json()
    assert body["generated"] is True
    assert body["warning"] is None
    assert body["transcript"] == asr.text
    assert body["name"] == "Tifa"
    assert body["description"] == "Final Fantasy VII: Martial artist and bar owner of Seventh Heaven."
    assert body["system_prompt"] == "You are Tifa Lockhart from Final Fantasy VII."
    assert body["avatar_color"] == "#2E8B57"
    assert body["reference_audio"] == "personas_audio/Tifa_Eng.wav"
    assert body["reference_audio_transcript"] == "personas_audio/Tifa_Eng.txt"
    assert body["reference_audio_language"] == "en"
    assert body["tts_capable"] is True
    assert body["router_hints"] == []
    assert body["avatar_image"] is None

    wav = tmp / "personas_audio" / "Tifa_Eng.wav"
    txt = tmp / "personas_audio" / "Tifa_Eng.txt"
    assert wav.read_bytes() == b"RIFF-fake-wav"
    assert txt.read_text(encoding="utf-8") == asr.text
    assert Path(asr.calls[0][0]) == wav
    assert asr.calls[0][1] == "en"

    messages, max_tokens = llm.chat_calls[0]
    assert max_tokens is not None
    assert asr.text in messages[0]["content"]
    assert "Tifa_Eng.wav" in messages[0]["content"]

    assert c.get("/api/personas/Tifa").status_code == 200
    assert c.get("/api/personas").json()["personas"][0]["name"] == "Tifa"


def test_llm_json_con_fences(client):
    c, _, llm, _ = client
    llm.models = ["test-model"]
    llm.chat_result = "```json\n" + VALID_JSON + "\n```"
    r = upload(c, "Tifa_Eng.wav")
    assert r.status_code == 200
    body = r.json()
    assert body["generated"] is True
    assert body["name"] == "Tifa"


def test_sin_llm_cargado(client):
    c, asr, llm, tmp = client
    r = upload(c, "Zack.wav")
    assert r.status_code == 200
    body = r.json()
    assert body["generated"] is False
    assert body["warning"] and "LLM" in body["warning"]
    assert body["transcript"] == asr.text
    assert body["name"] == "Zack"
    assert body["description"] == asr.text
    assert body["system_prompt"] == "You are Zack."
    assert body["avatar_color"] == "#888888"
    assert body["reference_audio"] == "personas_audio/Zack.wav"
    assert body["reference_audio_transcript"] == "personas_audio/Zack.txt"
    assert body["reference_audio_language"] is None
    assert body["tts_capable"] is True
    assert llm.chat_calls == []
    assert asr.calls[0][1] is None
    assert (tmp / "personas_audio" / "Zack.txt").read_text(encoding="utf-8") == asr.text


def test_llm_get_models_falla(client):
    c, _, llm, _ = client
    llm.models_error = LLMError("connection refused")
    r = upload(c, "Zack.wav")
    assert r.status_code == 200
    body = r.json()
    assert body["generated"] is False
    assert "LLM" in body["warning"]
    assert llm.chat_calls == []


def test_llm_chat_falla(client):
    c, _, llm, _ = client
    llm.models = ["test-model"]
    llm.chat_error = LLMError("timeout")
    r = upload(c, "Zack.wav")
    assert r.status_code == 200
    body = r.json()
    assert body["generated"] is False
    assert "LLM" in body["warning"]
    assert len(llm.chat_calls) == 1


def test_llm_basura_fallback(client):
    c, _, llm, _ = client
    llm.models = ["test-model"]
    llm.chat_result = "esto no es json, es prosa suelta"
    r = upload(c, "Zack.wav")
    assert r.status_code == 200
    body = r.json()
    assert body["generated"] is False
    assert "JSON" in body["warning"]
    assert body["name"] == "Zack"


def test_llm_faltan_keys_fallback(client):
    c, _, llm, _ = client
    llm.models = ["test-model"]
    llm.chat_result = json.dumps({"name": "Zack", "description": "x"})
    r = upload(c, "Zack.wav")
    assert r.status_code == 200
    body = r.json()
    assert body["generated"] is False
    assert body["warning"] is not None
    assert body["name"] == "Zack"


def test_llm_nombre_invalido_fallback(client):
    c, _, llm, _ = client
    llm.models = ["test-model"]
    bad = json.loads(VALID_JSON)
    bad["name"] = "bad/name"
    llm.chat_result = json.dumps(bad)
    r = upload(c, "Zack.wav")
    assert r.status_code == 200
    body = r.json()
    assert body["generated"] is False
    assert body["name"] == "Zack"


def test_asr_error_502_y_limpieza(client):
    c, asr, _, tmp = client
    asr.error = ASREngineError("boom simulado\nstderr del worker:\ntail de error")
    r = upload(c, "Tifa_Eng.wav")
    assert r.status_code == 502
    assert "boom simulado" in r.json()["detail"]
    assert "tail de error" in r.json()["detail"]
    assert not (tmp / "personas_audio" / "Tifa_Eng.wav").exists()
    assert not (tmp / "personas_audio" / "Tifa_Eng.txt").exists()
    assert c.get("/api/personas").json() == {"personas": []}


def test_asr_timeout_502_y_limpieza(client):
    c, asr, _, tmp = client
    asr.error = ASRTimeoutError("timeout de transcripcion tras 120 s")
    r = upload(c, "Tifa_Eng.wav")
    assert r.status_code == 502
    assert not (tmp / "personas_audio" / "Tifa_Eng.wav").exists()
    assert c.get("/api/personas").json() == {"personas": []}


@pytest.mark.parametrize(
    ("filename", "expected_name", "expected_lang"),
    [
        ("Tifa_Eng.wav", "Tifa", "en"),
        ("Cloud_Latino.wav", "Cloud", "es"),
        ("Zack.wav", "Zack", None),
        ("tifa_eng.wav", "tifa", "en"),
        ("CLOUD_LATINO.wav", "CLOUD", "es"),
    ],
)
def test_parseo_nombre(client, filename, expected_name, expected_lang):
    c, asr, _, _ = client
    r = upload(c, filename)
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == expected_name
    assert body["reference_audio_language"] == expected_lang
    assert asr.calls[0][1] == expected_lang


def test_override_nombre(client):
    c, asr, _, tmp = client
    r = upload(c, "Tifa_Eng.wav", name="Tifa Lockhart")
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "Tifa Lockhart"
    assert body["reference_audio"] == "personas_audio/Tifa_Eng.wav"
    assert c.get("/api/personas/Tifa Lockhart").status_code == 200
    assert (tmp / "personas_audio" / "Tifa_Eng.wav").exists()


def test_override_language(client):
    c, asr, _, _ = client
    r = upload(c, "Cloud_Latino.wav", language="en")
    assert r.status_code == 200
    assert r.json()["reference_audio_language"] == "en"
    assert asr.calls[0][1] == "en"


def test_traversal_se_guarda_dentro(client):
    c, asr, _, tmp = client
    r = upload(c, "../../evil.wav")
    assert r.status_code == 200
    assert r.json()["name"] == "evil"
    assert r.json()["reference_audio"] == "personas_audio/evil.wav"
    assert (tmp / "personas_audio" / "evil.wav").exists()
    assert not (tmp / "evil.wav").exists()
    assert sorted(p.name for p in (tmp / "personas_audio").iterdir()) == ["evil.txt", "evil.wav"]


def test_chars_raros_se_sanitizean(client):
    c, _, _, tmp = client
    r = upload(c, 'Tifa <Eng> "final".wav', name="Tifa")
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "Tifa"
    saved = list((tmp / "personas_audio").glob("*.wav"))
    assert len(saved) == 1
    stem = saved[0].stem
    assert not any(ch in stem for ch in '<>:"/\\|?*')
    assert stem.isascii()
    assert body["reference_audio"] == f"personas_audio/{saved[0].name}"


def test_no_wav_400(client):
    c, _, _, tmp = client
    r = upload(c, "nota.mp3")
    assert r.status_code == 400
    assert not (tmp / "personas_audio").exists()


@pytest.mark.parametrize(
    ("filename", "name_body"),
    [
        ("Jean.trimmed.wav", None),
        ("café.wav", None),
        ("Tifa_Eng.wav", "bad/name"),
        ("_Eng.wav", None),
    ],
)
def test_nombre_invalido_400(client, filename, name_body):
    c, asr, _, tmp = client
    r = upload(c, filename, name=name_body)
    assert r.status_code == 400
    assert asr.calls == []
    assert not (tmp / "personas_audio").exists()


def test_duplicado_409_y_limpieza(client):
    c, _, _, tmp = client
    r = c.post("/api/personas", json=make_persona("Zack"))
    assert r.status_code == 201
    r = upload(c, "Zack.wav")
    assert r.status_code == 409
    assert "Zack" in r.json()["detail"]
    assert not (tmp / "personas_audio" / "Zack.wav").exists()
    assert c.get("/api/personas/Zack").json()["reference_audio"] is None


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("Tifa_Eng.wav", ("Tifa", "en")),
        ("cloud_latino.WAV", ("cloud", "es")),
        ("Zack.wav", ("Zack", None)),
        ("Jean_Eng_trimmed.wav", ("Jean_Eng_trimmed", None)),
        ("_Eng.wav", ("", "en")),
        ("_Latino.wav", ("", "es")),
    ],
)
def test_parseo_nombre_unit(filename, expected):
    assert parse_name_from_filename(filename) == expected


def test_sanitize_stem_unit():
    assert sanitize_audio_stem('a<b>c:d"e/f\\g|h?i*j') == "a_b_c_d_e_f_g_h_i_j"
    assert sanitize_audio_stem("  padded  ") == "padded"
    assert sanitize_audio_stem("...") == ""
    assert sanitize_audio_stem("...x...") == "x"
    assert sanitize_audio_stem("Tifa_Eng") == "Tifa_Eng"
    assert sanitize_audio_stem("linea1\nlinea2") == "linea1_linea2"
