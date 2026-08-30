import json
import threading
import uuid
from datetime import datetime, timezone

import pytest

from app.config import ConfigStore
from app.persistence import RoomStore, new_message


@pytest.fixture
def config(tmp_path):
    return ConfigStore(settings_path=tmp_path / "settings.json")


@pytest.fixture
def store(config, tmp_path):
    return RoomStore("test-room", config, root=tmp_path / "chatrooms")


def msg(role="user", sender="user", text="hola", **overrides):
    message = new_message(role, sender, text)
    message.update(overrides)
    return message


def test_round_trip_history_unicode_and_emoji(store, config, tmp_path):
    m1 = msg("user", "user", "hola, ¿cómo estás? 😊")
    m2 = msg("assistant", "Jean", "¡Hola! ¿Qué tal? 🎉")
    m3 = msg(
        "user",
        "user",
        "tú bien? ñandú ñoño",
        uuid="u3",
        audio=["u3_0.wav"],
        image="images/u3.png",
    )
    store.append(m1)
    store.append(m2)
    store.append(m3)

    raw = store.history_path.read_text(encoding="utf-8")
    assert "hola, ¿cómo estás? 😊" in raw
    assert "\\u" not in raw

    reloaded = RoomStore("test-room", config, root=tmp_path / "chatrooms")
    reloaded.load()
    assert reloaded.history == [m1, m2, m3]


def test_message_schema_fields():
    m = new_message("assistant", "Fischl", "hola")
    assert set(m) == {"uuid", "role", "sender", "text", "audio", "image", "ts"}
    uuid.UUID(m["uuid"])
    datetime.fromisoformat(m["ts"])
    assert m["audio"] == []
    assert m["image"] is None


def test_append_no_file_when_save_history_off(store, config):
    config.set("save_history", False)
    store.append(msg())
    assert len(store.history) == 1
    assert not store.history_path.exists()


def test_save_wav_disabled(store, config):
    config.set("save_audio", False)
    assert store.save_wav("Jean", "abc", 0, b"RIFF-data") is None
    assert not (store.dir / "Jean").exists()


def test_save_wav_enabled(store):
    name = store.save_wav("Jean", "abc-123", 0, b"RIFF-data")
    assert name == "Jean/abc-123_0.wav"
    assert (store.dir / name).read_bytes() == b"RIFF-data"


def test_save_wav_creates_dir_on_demand(config, tmp_path):
    store = RoomStore("fresh", config, root=tmp_path / "chatrooms")
    name = store.save_wav("Fischl", "u1", 2, b"data")
    assert name == "Fischl/u1_2.wav"
    assert (store.dir / name).is_file()


def test_save_wav_persona_dir_sanitized_no_escape(store):
    name = store.save_wav("a/b\\c..d!", "u1", 0, b"data")
    assert ".." not in name
    assert name == "a_b_c__d/u1_0.wav"
    path = store.dir / name
    assert path.is_file()
    assert path.resolve().is_relative_to(store.dir.resolve())


def test_save_wav_persona_empty_fallback(store):
    name = store.save_wav("!!!", "u1", 0, b"data")
    assert name == "persona/u1_0.wav"
    assert (store.dir / name).is_file()


def test_add_audio_appends_to_message_and_rewrites(store, config):
    m = msg(uuid="u1")
    store.append(m)
    assert store.add_audio("u1", "Jean/u1_0.wav") is True
    assert m["audio"] == ["Jean/u1_0.wav"]
    data = json.loads(store.history_path.read_text(encoding="utf-8"))
    assert data[0]["audio"] == ["Jean/u1_0.wav"]


def test_add_audio_before_append_flushed_on_append(store, config):
    # race real: el TTS entrega audio antes de que el mensaje se appendee
    assert store.add_audio("u1", "Jean/u1_0.wav") is False
    m = msg(uuid="u1")
    store.append(m)
    assert m["audio"] == ["Jean/u1_0.wav"]
    data = json.loads(store.history_path.read_text(encoding="utf-8"))
    assert data[0]["audio"] == ["Jean/u1_0.wav"]
    assert store.add_audio("u1", "Jean/u1_1.wav") is True
    data = json.loads(store.history_path.read_text(encoding="utf-8"))
    assert data[0]["audio"] == ["Jean/u1_0.wav", "Jean/u1_1.wav"]


def test_add_audio_unknown_uuid_buffers(store, config):
    assert store.add_audio("nunca", "Jean/x_0.wav") is False
    assert store._pending_audio == {"nunca": ["Jean/x_0.wav"]}
    assert store.history == []


def test_add_audio_in_memory_only_when_save_history_off(store, config):
    config.set("save_history", False)
    m = msg(uuid="u1")
    store.append(m)
    assert store.add_audio("u1", "Jean/u1_0.wav") is True
    assert m["audio"] == ["Jean/u1_0.wav"]


def test_delete_message_removes_and_persists(store):
    m1, m2, m3 = msg(uuid="u1"), msg(uuid="u2"), msg(uuid="u3")
    for m in (m1, m2, m3):
        store.append(m)
    assert store.delete_message("u2") is True
    assert store.history == [m1, m3]
    data = json.loads(store.history_path.read_text(encoding="utf-8"))
    assert [m["uuid"] for m in data] == ["u1", "u3"]


def test_delete_message_unknown_returns_false(store):
    m = msg(uuid="u1")
    store.append(m)
    before = store.history_path.read_text(encoding="utf-8")
    assert store.delete_message("nope") is False
    assert store.history == [m]
    assert store.history_path.read_text(encoding="utf-8") == before


def test_delete_message_save_history_off(store, config):
    config.set("save_history", False)
    store.append(msg(uuid="u1"))
    assert store.delete_message("u1") is True
    assert store.history == []
    assert not store.history_path.exists()


def test_delete_message_keeps_files_on_disk(store):
    m = msg(uuid="u1")
    store.append(m)
    rel = store.save_wav("Jean", "u1", 0, b"RIFF")
    assert store.add_audio("u1", rel) is True
    assert store.delete_message("u1") is True
    # el mensaje desaparece del contexto; el wav se queda en disco
    assert store.history == []
    assert (store.dir / rel).is_file()
    assert json.loads(store.history_path.read_text(encoding="utf-8")) == []


def test_save_image_creates_file(store):
    rel = store.save_image(b"\x89PNG-data", ".png")
    assert rel.startswith("images/")
    assert rel.endswith(".png")
    assert (store.dir / rel).read_bytes() == b"\x89PNG-data"


def test_save_image_disabled_when_save_history_off(store, config):
    config.set("save_history", False)
    assert store.save_image(b"x", ".png") is None
    assert not (store.dir / "images").exists()


def test_save_image_ext_sanitized_no_escape(store):
    rel = store.save_image(b"x", ".png/../x")
    assert ".." not in rel
    assert "/../" not in rel
    assert rel.endswith(".png")
    assert (store.dir / rel).is_file()


def test_save_image_ext_default_when_unreadable(store):
    rel = store.save_image(b"x", "...")
    assert rel.endswith(".png")
    assert (store.dir / rel).is_file()


@pytest.mark.parametrize(
    "name",
    ["", "../evil", "a/b", "a\\b", "bad!name", "a..b", "café", None, 42],
)
def test_room_name_rejected(name, config, tmp_path):
    with pytest.raises(ValueError):
        RoomStore(name, config, root=tmp_path / "chatrooms")


def test_load_missing_file_empty(store):
    assert store.load() == []
    assert not store.history_path.exists()


def test_load_corrupt_json_empty(config, tmp_path):
    root = tmp_path / "chatrooms"
    room_dir = root / "broken"
    room_dir.mkdir(parents=True)
    (room_dir / "history.json").write_text("{not valid json", encoding="utf-8")
    store = RoomStore("broken", config, root=root)
    assert store.load() == []


def test_load_non_list_json_empty(config, tmp_path):
    root = tmp_path / "chatrooms"
    room_dir = root / "weird"
    room_dir.mkdir(parents=True)
    (room_dir / "history.json").write_text('{"a": 1}', encoding="utf-8")
    store = RoomStore("weird", config, root=root)
    assert store.load() == []


def test_load_flag_off_still_loads_existing(config, tmp_path):
    # save_history solo controla guardado, no carga: si el archivo existe se
    # carga igual.
    root = tmp_path / "chatrooms"
    room_dir = root / "old"
    room_dir.mkdir(parents=True)
    expected = new_message("user", "user", "antes")
    (room_dir / "history.json").write_text(
        json.dumps([expected]), encoding="utf-8"
    )
    config.set("save_history", False)
    store = RoomStore("old", config, root=root)
    assert store.load() == [expected]


def test_concurrent_appends(store, config):
    barrier = threading.Barrier(4)

    def worker(tid):
        barrier.wait()
        for i in range(25):
            store.append(new_message("user", "user", f"t{tid}-{i}"))

    threads = [threading.Thread(target=worker, args=(t,)) for t in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(store.history) == 100
    data = json.loads(store.history_path.read_text(encoding="utf-8"))
    assert len(data) == 100
    assert len({m["uuid"] for m in data}) == 100


def test_no_tmp_leftovers(store):
    store.append(msg())
    store.save_wav("Jean", "u1", 0, b"data")
    store.save_image(b"x")
    assert not list(store.dir.glob("*.tmp"))
    assert not list(store.dir.glob("**/*.tmp"))


def test_delete_removes_directory(store):
    store.append(msg())
    store.save_wav("Jean", "u1", 0, b"data")
    store.save_image(b"x")
    assert store.dir.exists()
    store.delete()
    assert not store.dir.exists()
    store.delete()


def test_compact_targets_keeps_tail_and_skips_compacted(store):
    for i in range(15):
        store.append(msg("user", "user", f"m{i}", uuid=f"u{i}"))
    targets = store.compact_targets(10)
    assert [m["uuid"] for m in targets] == ["u0", "u1", "u2", "u3", "u4"]
    # los compactados dejan de ser targets
    assert store.apply_compaction([m["uuid"] for m in targets], "resumen") == 5
    assert store.compact_targets(10) == []


def test_compact_targets_empty_when_not_enough(store):
    for i in range(8):
        store.append(msg())
    assert store.compact_targets(10) == []


def test_apply_compaction_marks_and_persists(store, tmp_path):
    for i in range(12):
        store.append(msg("user", "user", f"m{i}", uuid=f"u{i}"))
    targets = store.compact_targets(10)
    marked = store.apply_compaction([m["uuid"] for m in targets], "resumen X")
    assert marked == 2
    assert store.load_summary() == "resumen X"
    assert (store.summary_path).read_text(encoding="utf-8") == "resumen X"
    on_disk = json.loads(store.history_path.read_text(encoding="utf-8"))
    assert [m["uuid"] for m in on_disk if m.get("compacted")] == ["u0", "u1"]
    # rolling: solo lo no-compactado queda pendiente
    assert store.compact_targets(10) == []


def test_apply_compaction_memory_only_when_save_history_off(store, config):
    config.set("save_history", False)
    for i in range(12):
        store.append(msg("user", "user", f"m{i}", uuid=f"u{i}"))
    targets = store.compact_targets(10)
    marked = store.apply_compaction([m["uuid"] for m in targets], "resumen Y")
    assert marked == 2
    assert store.load_summary() is None  # no escrito a disco
    assert not store.summary_path.exists()
    assert store.history[0]["compacted"] is True  # pero si en memoria
    assert not store.history_path.exists()


def test_clear_history_removes_summary(store):
    store.append(msg())
    store.apply_compaction([store.history[0]["uuid"]], "resumen Z")
    assert store.summary_path.exists()
    store.clear_history()
    assert not store.summary_path.exists()
    assert store.load_summary() is None


def test_load_summary_missing_and_empty(store):
    assert store.load_summary() is None
    store.summary_path.parent.mkdir(parents=True, exist_ok=True)
    store.summary_path.write_text("   \n", encoding="utf-8")
    assert store.load_summary() is None
