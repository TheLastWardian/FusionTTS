import json

import launcher


def test_read_settings_real_repo():
    d = launcher.read_settings()
    assert isinstance(d, dict)
    assert d["tts_server_port"] == 5500


def test_read_settings_custom_path(tmp_path):
    p = tmp_path / "settings.json"
    p.write_text(
        json.dumps({"llm_base_url": "http://localhost:9999", "tts_server_port": 5600}),
        encoding="utf-8",
    )
    assert launcher.read_settings(path=str(p)) == {
        "llm_base_url": "http://localhost:9999",
        "tts_server_port": 5600,
    }


def test_read_settings_key_absent(tmp_path):
    p = tmp_path / "settings.json"
    p.write_text(json.dumps({"llm_base_url": "http://localhost:9999"}), encoding="utf-8")
    d = launcher.read_settings(path=str(p))
    assert d.get("tts_server_port") is None


def test_read_settings_invalid_json(tmp_path, capsys):
    p = tmp_path / "settings.json"
    p.write_text("{no es json", encoding="utf-8")
    assert launcher.read_settings(path=str(p)) == {}
    assert "WARNING" in capsys.readouterr().out


def test_read_settings_missing_file(tmp_path, capsys):
    assert launcher.read_settings(path=str(tmp_path / "nope.json")) == {}
    assert "WARNING" in capsys.readouterr().out


def test_resolve_tts_python_settings_wins(tmp_path):
    py = tmp_path / "custom" / "python.exe"
    py.parent.mkdir()
    py.write_text("")
    d = launcher.resolve_tts_python({"tts_server_python": str(py)}, base=str(tmp_path))
    assert d == str(py)


def test_resolve_tts_python_omnivoice_sibling(tmp_path):
    base = tmp_path / "FusionTTS"
    base.mkdir()
    py = tmp_path / "OmniVoice" / "venv" / "Scripts" / "python.exe"
    py.parent.mkdir(parents=True)
    py.write_text("")
    d = launcher.resolve_tts_python({"tts_server_python": ""}, base=str(base))
    assert d == str(py)


def test_resolve_tts_python_none(tmp_path):
    assert (
        launcher.resolve_tts_python(
            {"tts_server_python": ""}, base=str(tmp_path / "missing")
        )
        is None
    )
    assert launcher.resolve_tts_python({}, base=str(tmp_path / "missing2")) is None


def test_resolve_omnivoice_dir_default_sibling(tmp_path):
    base = tmp_path / "FusionTTS"
    base.mkdir()
    assert launcher.resolve_omnivoice_dir({}, base=str(base)) == str(tmp_path / "OmniVoice")
    assert (
        launcher.resolve_omnivoice_dir({"omnivoice_dir": ""}, base=str(base))
        == str(tmp_path / "OmniVoice")
    )


def test_resolve_omnivoice_dir_absolute(tmp_path):
    target = tmp_path / "OV"
    assert (
        launcher.resolve_omnivoice_dir({"omnivoice_dir": str(target)}, base=str(tmp_path / "x"))
        == str(target)
    )


def test_resolve_omnivoice_dir_relative_to_base(tmp_path):
    base = tmp_path / "FusionTTS"
    base.mkdir()
    assert (
        launcher.resolve_omnivoice_dir({"omnivoice_dir": "..\\OV2"}, base=str(base))
        == str(tmp_path / "OV2")
    )


def test_resolve_tts_python_from_custom_omnivoice_dir(tmp_path):
    base = tmp_path / "FusionTTS"
    base.mkdir()
    py = tmp_path / "OV2" / "venv" / "Scripts" / "python.exe"
    py.parent.mkdir(parents=True)
    py.write_text("")
    assert (
        launcher.resolve_tts_python({"omnivoice_dir": "..\\OV2"}, base=str(base))
        == str(py)
    )


def test_resolve_tts_python_custom_dir_missing(tmp_path):
    base = tmp_path / "FusionTTS"
    base.mkdir()
    assert (
        launcher.resolve_tts_python({"omnivoice_dir": "..\\OV-falta"}, base=str(base))
        is None
    )


def test_wait_http_dead_port():
    assert launcher.wait_http("http://127.0.0.1:1/health", 1) is False
