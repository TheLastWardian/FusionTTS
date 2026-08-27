"""Repro T11: audio vacio en voice-clone de Tifa (speak_persona_Tifa.wav = 44 bytes).

Lanza el tts-server DIRECTO (sin app, sin LLM) y prueba una matriz:
  A: Tifa + texto EN + language=en  (control: el prompt de Tifa funciona?)
  B: Tifa + texto ES + language=en  (la combinacion exacta que fallo en el e2e)
  C: Jean + texto ES + language=en  (aisla: problema solo de Tifa, o prompt+ES en general)
  D: Tifa + texto ES + language=es  (si el problema es el parametro language)

Parametros de generacion identicos a la app: num_steps=20, guidance_scale=1.5, speed=1.0.

Uso:      venv\Scripts\python.exe tts_repro_tifa.py
VRAM:     solo el modelo TTS (~2.2 GB). NO necesita ningun LLM cargado,
          pero si hay otro modelo ocupando la GPU debe haber ~2.5 GB libres.
Duracion: ~1-2 min (load ~10 s + 4 sintetizaciones).
Salida:   tts_e2e_results/repro_{A,B,C,D}.wav + tabla en pantalla + repro_server.log.
"""

import base64
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx
import numpy as np
import soundfile as sf

BASE = Path(__file__).resolve().parent
RESULTS_DIR = BASE / "tts_e2e_results"
OMNI_PY = BASE.parent / "OmniVoice" / "venv" / "Scripts" / "python.exe"
SERVER_DIR = BASE / "tts-server"
SERVER_LOG = RESULTS_DIR / "repro_server.log"
PORT = 5501
BASE_URL = f"http://127.0.0.1:{PORT}"

TEXT_EN = "This voice belongs to a persona with reference audio. Hopefully it sounds natural."
TEXT_ES = "Esta voz pertenece a una persona con audio de referencia. Esperemos que suene natural."

TIFA_WAV = "personas_audio/Tifa_FF_eng_trimmed.wav"
TIFA_TXT = "personas_audio/Tifa_FF_eng_trimmed.txt"
JEAN_WAV = "personas_audio/Jean_Eng_trimmed.wav"
JEAN_TXT = "personas_audio/Jean_Eng_trimmed.txt"

CASES = [
    ("A", "Tifa + EN + lang=en", TIFA_WAV, TIFA_TXT, TEXT_EN, "en"),
    ("B", "Tifa + ES + lang=en", TIFA_WAV, TIFA_TXT, TEXT_ES, "en"),
    ("C", "Jean + ES + lang=en", JEAN_WAV, JEAN_TXT, TEXT_ES, "en"),
    ("D", "Tifa + ES + lang=es", TIFA_WAV, TIFA_TXT, TEXT_ES, "es"),
]


def wait_http(url: str, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if httpx.get(url, timeout=3.0).status_code == 200:
                return True
        except httpx.HTTPError:
            pass
        time.sleep(0.5)
    return False


def main() -> int:
    if not OMNI_PY.exists():
        print(f"python del server no encontrado: {OMNI_PY}")
        return 1
    for ref in (TIFA_WAV, TIFA_TXT, JEAN_WAV, JEAN_TXT):
        if not (BASE / ref).exists():
            print(f"referencia no encontrada: {ref}")
            return 1
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    env = dict(os.environ)
    env.update({"PORT": str(PORT), "HOST": "127.0.0.1", "HF_HUB_OFFLINE": "1"})
    log_fh = open(SERVER_LOG, "wb")
    proc = subprocess.Popen(
        [str(OMNI_PY), "server.py"],
        cwd=str(SERVER_DIR),
        env=env,
        stdout=log_fh,
        stderr=subprocess.STDOUT,
    )
    try:
        print(f"arrancando tts-server en :{PORT} (log: {SERVER_LOG}) ...")
        if not wait_http(BASE_URL + "/health", 90.0):
            print("FAIL: el server no responde a /health; revisar repro_server.log")
            return 1
        print("server arriba; cargando modelo (~10-30 s) ...")
        httpx.post(BASE_URL + "/load", timeout=180.0)
        deadline = time.monotonic() + 180.0
        ready = False
        while time.monotonic() < deadline:
            try:
                if httpx.get(BASE_URL + "/status", timeout=3.0).json().get("status") == "ready":
                    ready = True
                    break
            except httpx.HTTPError:
                pass
            time.sleep(2.0)
        if not ready:
            print("FAIL: timeout esperando modelo ready")
            return 1
        print("modelo ready\n")

        results = []
        for case_id, desc, ref_wav, ref_txt, text, lang in CASES:
            audio_b64 = base64.b64encode((BASE / ref_wav).read_bytes()).decode()
            prompt_text = (BASE / ref_txt).read_text(encoding="utf-8").strip()
            body = {
                "text": text,
                "audio_base64": audio_b64,
                "prompt_text": prompt_text,
                "language": lang,
                "num_steps": 20,
                "guidance_scale": 1.5,
                "speed": 1.0,
            }
            t0 = time.monotonic()
            try:
                r = httpx.post(BASE_URL + "/synthesize", json=body, timeout=300.0)
                raw = base64.b64decode(r.json()["audio_base64"])
            except Exception as exc:
                print(f"[{case_id}] {desc}: ERROR {type(exc).__name__}: {exc}")
                results.append((case_id, desc, "ERROR", 0.0, 0.0))
                continue
            out = RESULTS_DIR / f"repro_{case_id}.wav"
            out.write_bytes(raw)
            arr, sr = sf.read(str(out), dtype="float32")
            peak = float(np.max(np.abs(arr))) if arr.size else 0.0
            dur = arr.size / sr if sr else 0.0
            verdict = "VACIO" if len(raw) < 1000 else "OK"
            results.append((case_id, desc, verdict, round(dur, 2), peak))
            print(f"[{case_id}] {desc}: {verdict}  dur={dur:.2f}s  peak={peak:.4f}  ({time.monotonic() - t0:.1f}s)  -> repro_{case_id}.wav")

        print("\nRESUMEN:")
        for case_id, desc, verdict, dur, peak in results:
            print(f"  [{case_id}] {desc:<22} {verdict:<6} dur={dur:>7.2f}s  peak={peak:.4f}")
        return 0
    finally:
        try:
            httpx.post(BASE_URL + "/unload", timeout=60.0)
        except httpx.HTTPError:
            pass
        proc.terminate()
        try:
            proc.wait(timeout=15.0)
        except subprocess.TimeoutExpired:
            proc.kill()
        log_fh.close()
        print("\nserver detenido y modelo descargado.")


if __name__ == "__main__":
    sys.exit(main())
