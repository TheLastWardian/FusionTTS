from pathlib import Path

from app.routers.personas import _read_transcript, _resolve_audio_path
from app.services.asr.engine import ASREngineError
from test_personas_from_audio import client, make_persona, upload


def test_retranscribe_happy(client):
    c, asr, _, tmp = client
    r = upload(c, "Tifa_Eng.wav")
    assert r.status_code == 200
    original = r.json()["reference_audio"]

    asr.text = "texto B de la nueva transcripcion"
    r = c.post("/api/personas/Tifa/retranscribe")
    assert r.status_code == 200
    body = r.json()
    assert body["transcript"] == "texto B de la nueva transcripcion"
    assert body["name"] == "Tifa"
    assert body["reference_audio"] == original
    assert body["reference_audio_transcript"] == "personas_audio/Tifa_Eng.txt"
    assert body["tts_capable"] is True
    assert (tmp / "personas_audio" / "Tifa_Eng.txt").read_text(encoding="utf-8") == asr.text
    assert asr.calls[-1][0].endswith("Tifa_Eng.wav")


def test_retranscribe_pasa_idioma(client):
    c, asr, _, _ = client
    r = upload(c, "Zack.wav", language="es")
    assert r.status_code == 200
    r = c.post("/api/personas/Zack/retranscribe")
    assert r.status_code == 200
    assert asr.calls[-1][1] == "es"


def test_retranscribe_404(client):
    c, _, _, _ = client
    r = c.post("/api/personas/Nadie/retranscribe")
    assert r.status_code == 404
    assert "Nadie" in r.json()["detail"]


def test_retranscribe_400_sin_referencia(client):
    c, asr, _, _ = client
    r = c.post("/api/personas", json=make_persona("SinVoz"))
    assert r.status_code == 201
    r = c.post("/api/personas/SinVoz/retranscribe")
    assert r.status_code == 400
    assert asr.calls == []


def test_retranscribe_502(client):
    c, asr, _, tmp = client
    r = upload(c, "Tifa_Eng.wav")
    assert r.status_code == 200
    before = (tmp / "personas_audio" / "Tifa_Eng.txt").read_text(encoding="utf-8")
    asr.error = ASREngineError("boom en retranscribe")
    r = c.post("/api/personas/Tifa/retranscribe")
    assert r.status_code == 502
    assert "boom en retranscribe" in r.json()["detail"]
    assert (tmp / "personas_audio" / "Tifa_Eng.txt").read_text(encoding="utf-8") == before


def test_transcript_put_happy(client):
    c, asr, _, tmp = client
    r = upload(c, "Tifa_Eng.wav")
    assert r.status_code == 200
    r = c.put("/api/personas/Tifa/transcript", json={"transcript": "texto editado"})
    assert r.status_code == 200
    assert r.json() == {"transcript": "texto editado"}
    assert (tmp / "personas_audio" / "Tifa_Eng.txt").read_text(encoding="utf-8") == "texto editado"


def test_transcript_put_404(client):
    c, _, _, _ = client
    r = c.put("/api/personas/Nadie/transcript", json={"transcript": "x"})
    assert r.status_code == 404


def test_transcript_put_400_sin_path(client):
    c, _, _, _ = client
    r = c.post("/api/personas", json=make_persona("SinVoz"))
    assert r.status_code == 201
    r = c.put("/api/personas/SinVoz/transcript", json={"transcript": "x"})
    assert r.status_code == 400


def test_get_unico_incluye_transcript(client):
    c, asr, _, _ = client
    r = upload(c, "Tifa_Eng.wav")
    assert r.status_code == 200
    r = c.get("/api/personas/Tifa")
    assert r.status_code == 200
    body = r.json()
    assert body["transcript"] == asr.text
    assert body["tts_capable"] is True


def test_get_unico_transcript_null(client):
    c, _, _, _ = client
    r = c.post("/api/personas", json=make_persona("SinVoz"))
    assert r.status_code == 201
    r = c.get("/api/personas/SinVoz")
    assert r.status_code == 200
    assert r.json()["transcript"] is None


def test_list_no_cambia(client):
    c, asr, _, _ = client
    upload(c, "Tifa_Eng.wav")
    r = c.get("/api/personas")
    assert r.status_code == 200
    assert "transcript" not in r.json()["personas"][0]


def test_resolve_audio_path():
    class Store:
        audio_dir = Path("/base/personas_audio")

    assert _resolve_audio_path(Store(), "personas_audio/a.wav").as_posix() == "/base/personas_audio/a.wav"
    assert _resolve_audio_path(Store(), None) is None
    assert _resolve_audio_path(Store(), "") is None
    assert _resolve_audio_path(Store(), "../outside.wav") is None
    assert _resolve_audio_path(Store(), "/absolute.wav") is None
    assert _read_transcript(Store(), None) is None
