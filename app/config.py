import json
import os
from dataclasses import dataclass
from pathlib import Path

from app import paths


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class KeySpec:
    kind: str
    minimum: float | None = None
    maximum: float | None = None
    allowed: tuple | None = None


DEFAULTS: dict = {
    "llm_base_url": "http://localhost:8080",
    "llm_model": "",
    "llm_temperature": 1.0,
    "llm_top_p": 1.0,
    "llm_max_tokens": 20600,
    "tts_enabled": False,
    "tts_engine": "omnivoice",
    "tts_mode": "sentences",
    "tts_num_steps": 20,
    "tts_guidance_scale": 1.5,
    "tts_seed": None,
    "tts_speed": 1.0,
    "tts_language": "en",
    "tts_instruct": "",
    "tts_sentence_timeout": 45,
    "silence_ms": 80,
    "tts_server_python": "",
    "tts_server_port": 5500,
    "asr_model": "medium",
    "asr_device": "auto",
    "asr_timeout": 120,
    "max_persona_replies": 2,
    "persona_name_mentions": True,
    "max_context_turns": 5,
    "echo_chamber": False,
    "save_history": True,
    "save_audio": True,
}

SPECS: dict[str, KeySpec] = {
    "llm_base_url": KeySpec("str"),
    "llm_model": KeySpec("str"),
    "llm_temperature": KeySpec("float", 0, 2),
    "llm_top_p": KeySpec("float", 0, 1),
    "llm_max_tokens": KeySpec("int", 1, 100000),
    "tts_enabled": KeySpec("bool"),
    "tts_engine": KeySpec("str", allowed=("omnivoice",)),
    "tts_mode": KeySpec("str", allowed=("sentences", "full")),
    "tts_num_steps": KeySpec("int", 1, 100),
    "tts_guidance_scale": KeySpec("float", 0.1, 3),
    "tts_seed": KeySpec("int_or_null", 0, 2**32 - 1),
    "tts_speed": KeySpec("float", 0.5, 2),
    "tts_language": KeySpec("str"),
    "tts_instruct": KeySpec("str"),
    "tts_sentence_timeout": KeySpec("int", 5, 300),
    "silence_ms": KeySpec("int", 0, 1000),
    "tts_server_python": KeySpec("str"),
    "tts_server_port": KeySpec("int", 1024, 65535),
    "asr_model": KeySpec("str", allowed=("tiny", "base", "small", "medium", "large-v3")),
    "asr_device": KeySpec("str", allowed=("auto", "cuda", "cpu")),
    "asr_timeout": KeySpec("int", 10, 600),
    "max_persona_replies": KeySpec("int", 1, 5),
    "persona_name_mentions": KeySpec("bool"),
    "max_context_turns": KeySpec("int", 0, 50),
    "echo_chamber": KeySpec("bool"),
    "save_history": KeySpec("bool"),
    "save_audio": KeySpec("bool"),
}


class ConfigStore:
    def __init__(self, settings_path: Path | None = None) -> None:
        self._path = Path(settings_path) if settings_path is not None else paths.SETTINGS_PATH
        self._state: dict = dict(DEFAULTS)
        self._effects: dict[str, list] = {}
        self._load()
        self._state["tts_enabled"] = False  # on-demand TTS: never autostart at boot

    def get(self, key: str):
        if key not in SPECS:
            raise ConfigError(f"unknown config key: '{key}'")
        return self._state[key]

    def set(self, key: str, value):
        self._validate(key, value)
        old = self._state[key]
        self._state[key] = value
        self._persist()
        for effect in self._effects.get(key, ()):
            effect(old, value)
        return value

    def all(self) -> dict:
        return dict(self._state)

    def register_effects(self, key: str, effect) -> None:
        self._effects.setdefault(key, []).append(effect)

    def _validate(self, key: str, value) -> None:
        if key not in SPECS:
            raise ConfigError(f"unknown config key: '{key}'")
        spec = SPECS[key]
        if spec.kind == "bool":
            if not isinstance(value, bool):
                raise ConfigError(f"{key}: expected bool, got {type(value).__name__}")
        elif spec.kind == "int":
            if isinstance(value, bool) or not isinstance(value, int):
                raise ConfigError(f"{key}: expected int, got {type(value).__name__}")
            self._check_range(key, value, spec)
        elif spec.kind == "float":
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ConfigError(f"{key}: expected float, got {type(value).__name__}")
            self._check_range(key, value, spec)
        elif spec.kind == "str":
            if not isinstance(value, str):
                raise ConfigError(f"{key}: expected str, got {type(value).__name__}")
        elif spec.kind == "int_or_null":
            if value is not None:
                if isinstance(value, bool) or not isinstance(value, int):
                    raise ConfigError(f"{key}: expected int or null, got {type(value).__name__}")
                self._check_range(key, value, spec)
        if spec.allowed is not None and value not in spec.allowed:
            raise ConfigError(f"{key}: {value!r} not in allowed values {list(spec.allowed)}")

    @staticmethod
    def _check_range(key: str, value, spec: KeySpec) -> None:
        if (spec.minimum is not None and value < spec.minimum) or (
            spec.maximum is not None and value > spec.maximum
        ):
            raise ConfigError(f"{key}: {value} out of range [{spec.minimum}, {spec.maximum}]")

    def _load(self) -> None:
        if not self._path.exists():
            self._persist()
            return
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raw = {}
        if isinstance(raw, dict):
            for key in DEFAULTS:
                if key in raw:
                    try:
                        self._validate(key, raw[key])
                        self._state[key] = raw[key]
                    except ConfigError:
                        continue  # invalid persisted value -> keep default

    def _persist(self) -> None:
        tmp = self._path.with_name(self._path.name + ".tmp")
        tmp.write_text(
            json.dumps(self._state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        os.replace(tmp, self._path)
