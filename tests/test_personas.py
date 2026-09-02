import pytest
from fastapi.testclient import TestClient

from app import paths
from app.main import app
from app.personas import FOR_INSTRUCT_NAME, PersonaExistsError, PersonaStore


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "SETTINGS_PATH", tmp_path / "settings.json")
    monkeypatch.setattr(paths, "CHATROOMS_DIR", tmp_path / "chatrooms")
    monkeypatch.setattr(paths, "CHATROOMS_YAML", tmp_path / "chatrooms.yaml")
    monkeypatch.setattr(paths, "PERSONAS_YAML", tmp_path / "personas.yaml")
    monkeypatch.setattr(paths, "PERSONAS_AUDIO_DIR", tmp_path / "personas_audio")
    monkeypatch.setattr(paths, "PERSONAS_AVATARS_DIR", tmp_path / "personas_avatars")
    with TestClient(app) as c:
        yield c


def make_persona(name="Jean", **overrides):
    persona = {
        "name": name,
        "description": f"{name} desc",
        "system_prompt": f"You are {name}.",
        "router_hints": ["genshin", "mondstadt"],
        "avatar_color": "#87CEEB",
        "avatar_image": None,
        "reference_audio": f"personas_audio/{name}_Eng_trimmed.wav",
        "reference_audio_transcript": f"personas_audio/{name}_Eng_trimmed.txt",
        "reference_audio_language": "en",
    }
    persona.update(overrides)
    return persona


def test_list_solo_persona_sistema(client):
    # el lifespan auto-crea "For Instruct" (voice design / instruct)
    r = client.get("/api/personas")
    assert r.status_code == 200
    assert [p["name"] for p in r.json()["personas"]] == [FOR_INSTRUCT_NAME]


def test_create_get_list_roundtrip(client):
    p = make_persona()
    r = client.post("/api/personas", json=p)
    assert r.status_code == 201
    body = r.json()
    assert body == {**p, "tts_capable": True}

    names = [p["name"] for p in client.get("/api/personas").json()["personas"]]
    assert names == [FOR_INSTRUCT_NAME, "Jean"]

    r = client.get("/api/personas/Jean")
    assert r.status_code == 200
    assert r.json() == {**body, "transcript": None}


def test_create_duplicate_409(client):
    client.post("/api/personas", json=make_persona())
    r = client.post("/api/personas", json=make_persona(description="otra"))
    assert r.status_code == 409
    assert "Jean" in r.json()["detail"]


def test_tts_capable_false_without_reference_audio(client):
    p = make_persona(
        reference_audio=None,
        reference_audio_transcript=None,
        reference_audio_language=None,
    )
    r = client.post("/api/personas", json=p)
    assert r.status_code == 201
    assert r.json()["tts_capable"] is False
    assert client.get("/api/personas/Jean").json()["tts_capable"] is False


@pytest.mark.parametrize("bad_name", ["a/b", "a\\b", "a..b", "café", ""])
def test_create_invalid_name(client, bad_name):
    r = client.post("/api/personas", json=make_persona(name=bad_name))
    assert r.status_code in (400, 422)


def test_create_missing_fields_422(client):
    r = client.post("/api/personas", json={"name": "Jean", "description": "x"})
    assert r.status_code == 422


def test_get_missing_404(client):
    assert client.get("/api/personas/Nadie").status_code == 404


def test_update(client):
    client.post("/api/personas", json=make_persona())
    updated = make_persona(description="nueva desc")
    r = client.put("/api/personas/Jean", json=updated)
    assert r.status_code == 200
    assert r.json()["description"] == "nueva desc"
    assert r.json()["tts_capable"] is True
    assert client.get("/api/personas/Jean").json()["description"] == "nueva desc"


def test_update_missing_404(client):
    r = client.put("/api/personas/Nadie", json=make_persona(name="Nadie"))
    assert r.status_code == 404


def test_update_name_mismatch_400(client):
    client.post("/api/personas", json=make_persona())
    r = client.put("/api/personas/Jean", json=make_persona(name="Fischl"))
    assert r.status_code == 400


def test_delete_missing_404(client):
    assert client.delete("/api/personas/Nadie").status_code == 404


def test_delete_cascades_to_rooms_and_audio(client, tmp_path):
    audio = tmp_path / "personas_audio"
    audio.mkdir()
    jean_wav = audio / "Jean_Eng_trimmed.wav"
    jean_txt = audio / "Jean_Eng_trimmed.txt"
    fischl_wav = audio / "Fischl_Eng_trimmed.wav"
    jean_wav.write_bytes(b"RIFF-jean")
    jean_txt.write_text("transcripción de Jean", encoding="utf-8")
    fischl_wav.write_bytes(b"RIFF-fischl")

    client.post("/api/personas", json=make_persona("Jean"))
    client.post("/api/personas", json=make_persona("Fischl"))
    r = client.post(
        "/api/rooms",
        json={"name": "test", "persona_names": ["Jean", "Fischl"], "echo_chamber": False},
    )
    assert r.status_code == 201

    r = client.delete("/api/personas/Jean")
    assert r.status_code == 200
    assert client.get("/api/personas/Jean").status_code == 404
    assert not jean_wav.exists()
    assert not jean_txt.exists()
    assert fischl_wav.exists()

    rooms = client.get("/api/rooms").json()["rooms"]
    assert rooms == [
        {
            "name": "test",
            "persona_names": ["Fischl"],
            "echo_chamber": False,
            "auto_chat": False,
        }
    ]


def test_yaml_roundtrip_preserves_fields(tmp_path):
    yaml_path = tmp_path / "personas.yaml"
    audio_dir = tmp_path / "personas_audio"
    original = make_persona(
        "Keqing",
        description="Yuheng, ¡pragmática! 😊",
        system_prompt="You are Keqing from Genshin Impact.",
        router_hints=["genshin", "liyue", "electro"],
        avatar_color="#7B68EE",
        avatar_image=None,
        reference_audio=None,
        reference_audio_transcript=None,
        reference_audio_language=None,
    )
    store = PersonaStore(personas_yaml=yaml_path, audio_dir=audio_dir)
    assert store.create(original) == original

    reloaded = PersonaStore(personas_yaml=yaml_path, audio_dir=audio_dir)
    assert reloaded.get("Keqing") == original
    raw = yaml_path.read_text(encoding="utf-8")
    assert "¡pragmática! 😊" in raw
    assert "\\u" not in raw


def test_load_normalizes_string_router_hints(tmp_path):
    yaml_path = tmp_path / "personas.yaml"
    yaml_path.write_text(
        "personas:\n"
        "- name: Tifa\n"
        "  description: d\n"
        "  system_prompt: s\n"
        "  router_hints: final fantasy, ff7, martial arts\n"
        "  avatar_color: '#2E8B57'\n"
        "  avatar_image: null\n"
        "  reference_audio: null\n"
        "  reference_audio_transcript: null\n"
        "  reference_audio_language: null\n",
        encoding="utf-8",
    )
    store = PersonaStore(personas_yaml=yaml_path, audio_dir=tmp_path / "personas_audio")
    assert store.get("Tifa")["router_hints"] == ["final fantasy", "ff7", "martial arts"]


def test_missing_yaml_creates_empty(tmp_path):
    yaml_path = tmp_path / "personas.yaml"
    store = PersonaStore(personas_yaml=yaml_path, audio_dir=tmp_path / "personas_audio")
    assert store.list() == []
    assert yaml_path.exists()


def test_store_crud_errors(tmp_path):
    store = PersonaStore(
        personas_yaml=tmp_path / "personas.yaml", audio_dir=tmp_path / "personas_audio"
    )
    store.create(make_persona("Jean"))
    with pytest.raises(PersonaExistsError):
        store.create(make_persona("Jean"))
    with pytest.raises(KeyError):
        store.update("Nadie", make_persona(name="Nadie"))
    with pytest.raises(KeyError):
        store.delete("Nadie")
    with pytest.raises(ValueError):
        store.create(make_persona(name="a/b"))
    store.delete("Jean")
    assert store.list() == []


def test_delete_does_not_escape_audio_dir(tmp_path):
    audio = tmp_path / "personas_audio"
    audio.mkdir()
    outside = tmp_path / "outside.wav"
    outside.write_bytes(b"data")
    store = PersonaStore(
        personas_yaml=tmp_path / "personas.yaml", audio_dir=audio
    )
    store.create(
        make_persona(
            "Jean",
            reference_audio="personas_audio/../outside.wav",
            reference_audio_transcript=None,
        )
    )
    store.delete("Jean")
    assert outside.exists()


def test_persona_audio_serves_file(client, tmp_path):
    audio = tmp_path / "personas_audio"
    audio.mkdir()
    (audio / "Jean_Eng_trimmed.wav").write_bytes(b"RIFF" + b"\x00" * 100)
    r = client.get("/api/persona-audio/Jean_Eng_trimmed.wav")
    assert r.status_code == 200
    assert r.headers["content-type"] == "audio/wav"
    assert len(r.content) > 0


def test_persona_audio_missing_404(client):
    assert client.get("/api/persona-audio/nope.wav").status_code == 404


@pytest.mark.parametrize("bad", ["..%2Fx", "a/b", "sub%5Cfile.wav"])
def test_persona_audio_invalid_400(client, bad):
    r = client.get(f"/api/persona-audio/{bad}")
    assert r.status_code == 400


# --- avatares ---


def test_avatar_upload_serve_and_delete(client, tmp_path):
    client.post("/api/personas", json=make_persona())
    avatars = tmp_path / "personas_avatars"
    data = b"\x89PNG fake bytes"

    r = client.put(
        "/api/personas/Jean/avatar",
        files={"file": ("Jean.png", data, "image/png")},
    )
    assert r.status_code == 200
    assert r.json()["avatar_image"] == "personas_avatars/Jean.png"
    assert (avatars / "Jean.png").read_bytes() == data

    r = client.get("/api/personas/Jean/avatar")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    assert r.content == data
    assert (
        client.get("/api/personas/Jean").json()["avatar_image"]
        == "personas_avatars/Jean.png"
    )

    r = client.delete("/api/personas/Jean/avatar")
    assert r.status_code == 200
    assert r.json()["avatar_image"] is None
    assert not (avatars / "Jean.png").exists()
    assert client.get("/api/personas/Jean/avatar").status_code == 404
    assert client.delete("/api/personas/Jean/avatar").status_code == 400


def test_avatar_upload_wrong_ext_400(client):
    client.post("/api/personas", json=make_persona())
    r = client.put(
        "/api/personas/Jean/avatar",
        files={"file": ("Jean.exe", b"x", "application/octet-stream")},
    )
    assert r.status_code == 400


def test_avatar_upload_too_big_400(client):
    client.post("/api/personas", json=make_persona())
    r = client.put(
        "/api/personas/Jean/avatar",
        files={"file": ("Jean.png", b"\x00" * (15 * 1024 * 1024 + 1), "image/png")},
    )
    assert r.status_code == 400
    assert "15MB" in r.json()["detail"]


def test_avatar_upload_missing_persona_404(client):
    r = client.put(
        "/api/personas/Nadie/avatar", files={"file": ("x.png", b"x", "image/png")}
    )
    assert r.status_code == 404
    assert client.get("/api/personas/Nadie/avatar").status_code == 404


def test_avatar_upload_replaces_old_file(client, tmp_path):
    client.post("/api/personas", json=make_persona())
    avatars = tmp_path / "personas_avatars"
    client.put(
        "/api/personas/Jean/avatar", files={"file": ("Jean.png", b"png-bytes", "image/png")}
    )
    client.put(
        "/api/personas/Jean/avatar", files={"file": ("Jean.jpg", b"jpg-bytes", "image/jpeg")}
    )
    assert not (avatars / "Jean.png").exists()
    assert (avatars / "Jean.jpg").read_bytes() == b"jpg-bytes"
    assert client.get("/api/personas/Jean").json()["avatar_image"] == "personas_avatars/Jean.jpg"


def test_delete_persona_removes_avatar(client, tmp_path):
    client.post("/api/personas", json=make_persona())
    avatars = tmp_path / "personas_avatars"
    client.put(
        "/api/personas/Jean/avatar", files={"file": ("Jean.png", b"x", "image/png")}
    )
    assert client.delete("/api/personas/Jean").status_code == 200
    assert not (avatars / "Jean.png").exists()


def test_delete_persona_avatar_no_escape(client, tmp_path):
    avatars = tmp_path / "personas_avatars"
    avatars.mkdir()
    outside = tmp_path / "outside.png"
    outside.write_bytes(b"data")
    store = PersonaStore(
        personas_yaml=tmp_path / "personas.yaml",
        audio_dir=tmp_path / "personas_audio",
        avatar_dir=avatars,
    )
    store.create(make_persona("Jean", avatar_image="personas_avatars/../outside.png"))
    store.delete("Jean")
    assert outside.exists()


def test_rename_persona_renames_avatar(client, tmp_path):
    client.post("/api/personas", json=make_persona())
    avatars = tmp_path / "personas_avatars"
    client.put(
        "/api/personas/Jean/avatar", files={"file": ("Jean.png", b"x", "image/png")}
    )
    r = client.post("/api/personas/Jean/rename", json={"name": "Jeanette"})
    assert r.status_code == 200
    assert r.json()["avatar_image"] == "personas_avatars/Jeanette.png"
    assert not (avatars / "Jean.png").exists()
    assert (avatars / "Jeanette.png").read_bytes() == b"x"
    assert client.get("/api/personas/Jeanette/avatar").status_code == 200
