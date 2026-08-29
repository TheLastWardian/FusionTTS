import json

import pytest

from app import paths
from app.config import ConfigError, ConfigStore

ALL_KEYS = {
    "llm_base_url",
    "llm_model",
    "llm_temperature",
    "llm_top_p",
    "llm_max_tokens",
    "tts_enabled",
    "tts_engine",
    "tts_mode",
    "tts_num_steps",
    "tts_guidance_scale",
    "tts_seed",
    "tts_speed",
    "tts_language",
    "tts_instruct",
    "tts_sentence_timeout",
    "silence_ms",
    "tts_server_python",
    "tts_server_port",
    "tts_int8",
    "asr_model",
    "asr_device",
    "asr_timeout",
    "max_persona_replies",
    "persona_name_mentions",
    "max_context_turns",
    "save_history",
    "save_audio",
}


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "SETTINGS_PATH", tmp_path / "settings.json")
    return ConfigStore()


def test_defaults(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "SETTINGS_PATH", tmp_path / "settings.json")
    store = ConfigStore()
    assert set(store.all()) == ALL_KEYS
    assert store.get("llm_base_url") == "http://localhost:8080"
    assert store.get("llm_max_tokens") == 20600
    assert store.get("tts_seed") is None
    assert store.get("tts_enabled") is False
    assert store.get("save_history") is True


def test_settings_file_created_on_init(store, tmp_path):
    path = tmp_path / "settings.json"
    assert path.exists()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert set(data) == ALL_KEYS
    assert data["tts_engine"] == "omnivoice"


def test_set_get(store):
    store.set("llm_temperature", 0.7)
    assert store.get("llm_temperature") == 0.7


def test_round_trip_persistence(store, tmp_path):
    store.set("llm_max_tokens", 5000)
    store.set("asr_device", "cuda")
    path = tmp_path / "settings.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["llm_max_tokens"] == 5000
    reloaded = ConfigStore(settings_path=path)
    assert reloaded.get("llm_max_tokens") == 5000
    assert reloaded.get("asr_device") == "cuda"


def test_unknown_key_rejected(store):
    with pytest.raises(ConfigError):
        store.set("bogus_key", 1)
    with pytest.raises(ConfigError):
        store.get("bogus_key")


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("llm_temperature", "hot"),
        ("llm_temperature", True),
        ("tts_num_steps", 2.5),
        ("tts_num_steps", "20"),
        ("tts_enabled", 1),
        ("silence_ms", "80"),
        ("tts_speed", ["1.0"]),
        ("llm_model", 42),
    ],
)
def test_rejects_wrong_type(store, key, value):
    with pytest.raises(ConfigError):
        store.set(key, value)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("llm_temperature", 2.5),
        ("llm_temperature", -0.5),
        ("llm_top_p", 1.1),
        ("llm_top_p", -0.1),
        ("llm_max_tokens", 0),
        ("llm_max_tokens", 100001),
        ("tts_num_steps", 0),
        ("tts_num_steps", 101),
        ("tts_guidance_scale", 0.05),
        ("tts_guidance_scale", 3.5),
        ("tts_seed", 2**32),
        ("tts_seed", -1),
        ("tts_speed", 0.4),
        ("tts_speed", 2.1),
        ("tts_sentence_timeout", 4),
        ("tts_sentence_timeout", 301),
        ("silence_ms", -1),
        ("silence_ms", 1001),
        ("tts_server_port", 1023),
        ("tts_server_port", 65536),
        ("asr_timeout", 9),
        ("asr_timeout", 601),
        ("max_persona_replies", 0),
        ("max_persona_replies", 6),
        ("max_context_turns", -1),
        ("max_context_turns", 501),
    ],
)
def test_rejects_out_of_range(store, key, value):
    with pytest.raises(ConfigError):
        store.set(key, value)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("tts_engine", "qwen3"),
        ("asr_model", "xxl"),
        ("asr_device", "metal"),
    ],
)
def test_rejects_bad_allowed_value(store, key, value):
    with pytest.raises(ConfigError):
        store.set(key, value)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("tts_engine", "omnivoice"),
        ("asr_model", "large-v3"),
        ("asr_device", "cuda"),
        ("asr_device", "cpu"),
    ],
)
def test_accepts_allowed_values(store, key, value):
    assert store.set(key, value) == value


def test_tts_seed_null_and_int(store):
    store.set("tts_seed", None)
    assert store.get("tts_seed") is None
    store.set("tts_seed", 424242)
    assert store.get("tts_seed") == 424242
    with pytest.raises(ConfigError):
        store.set("tts_seed", "abc")


def test_float_field_accepts_int(store):
    store.set("llm_temperature", 1)
    assert store.get("llm_temperature") == 1


def test_side_effect_receives_old_and_new(store):
    calls = []
    store.register_effects("llm_temperature", lambda old, new: calls.append((old, new)))
    store.set("llm_temperature", 0.5)
    assert calls == [(1.0, 0.5)]
    store.set("llm_temperature", 0.9)
    assert calls == [(1.0, 0.5), (0.5, 0.9)]


def test_side_effect_not_fired_for_other_keys(store):
    calls = []
    store.register_effects("llm_temperature", lambda old, new: calls.append((old, new)))
    store.set("llm_top_p", 0.8)
    assert calls == []


def test_tts_enabled_forced_false_at_startup(tmp_path, monkeypatch):
    path = tmp_path / "settings.json"
    path.write_text(
        json.dumps({"tts_enabled": True, "llm_temperature": 0.4}), encoding="utf-8"
    )
    monkeypatch.setattr(paths, "SETTINGS_PATH", path)
    store = ConfigStore()
    assert store.get("tts_enabled") is False
    assert store.get("llm_temperature") == 0.4
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["tts_enabled"] is True


def test_invalid_persisted_value_falls_back_to_default(tmp_path, monkeypatch):
    path = tmp_path / "settings.json"
    path.write_text(
        json.dumps(
            {"llm_temperature": "hot", "tts_engine": "bogus", "save_audio": False}
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(paths, "SETTINGS_PATH", path)
    store = ConfigStore()
    assert store.get("llm_temperature") == 1.0
    assert store.get("tts_engine") == "omnivoice"
    assert store.get("save_audio") is False


def test_set_persists_immediately(store, tmp_path):
    store.set("save_audio", False)
    data = json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))
    assert data["save_audio"] is False
