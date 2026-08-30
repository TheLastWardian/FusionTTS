import json

import pytest
from fastapi.testclient import TestClient

from app import paths
from app.config import DEFAULTS
from app.main import app

# derivado de DEFAULTS: GET /api/config devuelve exactamente esas keys
ALL_KEYS = set(DEFAULTS.keys())


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "SETTINGS_PATH", tmp_path / "settings.json")
    monkeypatch.setattr(paths, "CHATROOMS_DIR", tmp_path / "chatrooms")
    monkeypatch.setattr(paths, "CHATROOMS_YAML", tmp_path / "chatrooms.yaml")
    monkeypatch.setattr(paths, "PERSONAS_YAML", tmp_path / "personas.yaml")
    monkeypatch.setattr(paths, "PERSONAS_AUDIO_DIR", tmp_path / "personas_audio")
    with TestClient(app) as c:
        yield c


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_get_config_returns_all_keys(client):
    r = client.get("/api/config")
    assert r.status_code == 200
    data = r.json()
    assert set(data) == ALL_KEYS
    assert data["llm_base_url"] == "http://localhost:8080"
    assert data["tts_enabled"] is False


def test_post_config_valid_updates_and_persists(client, tmp_path):
    r = client.post("/api/config", json={"key": "llm_temperature", "value": 0.7})
    assert r.status_code == 200
    assert r.json() == {"key": "llm_temperature", "value": 0.7}
    data = json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))
    assert data["llm_temperature"] == 0.7
    assert client.get("/api/config").json()["llm_temperature"] == 0.7


def test_post_config_bool(client):
    r = client.post("/api/config", json={"key": "save_history", "value": False})
    assert r.status_code == 200
    assert r.json() == {"key": "save_history", "value": False}


def test_post_config_null_seed(client):
    r = client.post("/api/config", json={"key": "tts_seed", "value": None})
    assert r.status_code == 200
    assert r.json() == {"key": "tts_seed", "value": None}


def test_post_config_unknown_key_400(client):
    r = client.post("/api/config", json={"key": "bogus", "value": 1})
    assert r.status_code == 400
    assert "bogus" in r.json()["detail"]


def test_post_config_wrong_type_400(client):
    r = client.post("/api/config", json={"key": "llm_temperature", "value": "hot"})
    assert r.status_code == 400
    assert "llm_temperature" in r.json()["detail"]


def test_post_config_out_of_range_400(client):
    r = client.post("/api/config", json={"key": "llm_temperature", "value": 5.0})
    assert r.status_code == 400
    assert "llm_temperature" in r.json()["detail"]


def test_post_config_bad_allowed_value_400(client):
    r = client.post("/api/config", json={"key": "tts_engine", "value": "qwen3"})
    assert r.status_code == 400
    assert "tts_engine" in r.json()["detail"]


def test_root_serves_placeholder(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "FusionTTS" in r.text
