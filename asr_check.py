"""Verificacion manual T12: worker ASR real (faster-whisper) con el audio de Tifa.

Lanza app/services/asr/worker.py por subprocess con el python del venv de
FusionTTS (env HF_HUB_OFFLINE=1 + PYTHONUNBUFFERED=1, sin internet) y
transcribe personas_audio/Tifa_FF_eng_trimmed.wav (~4 s). Imprime el device
detectado, el texto transcrito, el transcript esperado
(personas_audio/Tifa_FF_eng_trimmed.txt) para comparar a ojo y la duracion.
Al terminar confirma que el proceso quedo muerto (cero VRAM en idle).

Uso:      venv/Scripts/python.exe asr_check.py
VRAM:     requiere CUDA con ~2 GB libres (modelo medium float16).
Duracion: ~30-90 s (arranque del worker + carga del modelo + audio).
"""

import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import paths  # noqa: E402
from app.config import ConfigStore  # noqa: E402

WORKER = paths.BASE_DIR / "app" / "services" / "asr" / "worker.py"
WAV = paths.PERSONAS_AUDIO_DIR / "Tifa_FF_eng_trimmed.wav"
TXT = paths.PERSONAS_AUDIO_DIR / "Tifa_FF_eng_trimmed.txt"


def main() -> int:
    if not (WAV.exists() and TXT.exists()):
        print(f"referencia no encontrada: {WAV}")
        return 1
    python = paths.BASE_DIR / "venv" / "Scripts" / "python.exe"
    if not python.exists():
        python = Path(sys.executable)
    config = ConfigStore()
    model = config.get("asr_model")
    device = config.get("asr_device")
    timeout = float(config.get("asr_timeout"))
    print(f"worker:   {WORKER}")
    print(f"python:   {python}")
    print(f"config:   model={model} device={device} timeout={timeout:.0f}s")
    print(f"audio:    {WAV}\n")

    env = dict(os.environ)
    env["HF_HUB_OFFLINE"] = "1"
    env["PYTHONUNBUFFERED"] = "1"
    proc = subprocess.Popen(
        [str(python), str(WORKER)],
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
    )
    t0 = time.monotonic()
    try:
        cmd = {
            "cmd": "transcribe",
            "path": str(WAV),
            "language": None,
            "model": model,
            "device": device,
        }
        proc.stdin.write((json.dumps(cmd) + "\n").encode("utf-8"))
        proc.stdin.flush()
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(proc.stdout.readline)
            try:
                raw = future.result(timeout=timeout)
            except FuturesTimeout:
                proc.kill()
                print(f"FAIL: timeout tras {timeout:.0f} s (revisar stderr de arriba)")
                return 1
        if not raw.strip():
            code = proc.wait(timeout=10)
            print(f"FAIL: el worker termino sin responder (exit={code}); revisar stderr")
            return 1
        try:
            data = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            print(f"FAIL: respuesta no-JSON del worker: {raw[:200]!r} ({exc})")
            return 1
        if not data.get("ok"):
            print(f"FAIL: error del worker: {data.get('error')}")
            return 1
        elapsed = time.monotonic() - t0
        print(f"device:     {data['device']} (compute_type={data.get('compute_type')})")
        print(f"idioma:     {data.get('language')}")
        print(
            f"duracion:   audio={data.get('duration')} s | worker={elapsed:.1f} s "
            "(incluye carga del modelo)"
        )
        print(f"transcrito: {data['text']!r}")
        print(f"esperado:   {TXT.read_text(encoding='utf-8').strip()!r}")
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            print("aviso: el worker no termino solo; se le mato")
        print(f"proceso muerto: exit={proc.returncode} (VRAM liberada)")
        return 0
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        for stream in (proc.stdin, proc.stdout):
            if stream is not None:
                try:
                    stream.close()
                except OSError:
                    pass


if __name__ == "__main__":
    sys.exit(main())
