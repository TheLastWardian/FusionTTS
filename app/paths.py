from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
SETTINGS_PATH = BASE_DIR / "settings.json"
STATIC_DIR = BASE_DIR / "static"
CHATROOMS_DIR = BASE_DIR / "chatrooms"
CHATROOMS_YAML = BASE_DIR / "chatrooms.yaml"
PERSONAS_AUDIO_DIR = BASE_DIR / "personas_audio"
PERSONAS_YAML = BASE_DIR / "personas.yaml"
