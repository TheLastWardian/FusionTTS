"""Worker ASR one-shot (faster-whisper como libreria, sin CLI).

IPC JSON (UNA linea de stdin, UNA linea de stdout, y termina):
  stdin:  {"cmd": "transcribe", "path": "...", "language": "en"|null,
           "model": "medium", "device": "auto|cuda|cpu"}
  stdout: {"ok": true, "text": "...", "device": "cuda",
           "compute_type": "float16", "model": "Systran/faster-whisper-medium",
           "language": "en", "duration": 4.12}
           o {"ok": false, "error": "..."}
  stderr: logs ([ASR] device=..., warnings, tracebacks)

El proceso termina despues de cada transcripcion; el manager lo mata
(cero VRAM en idle).
"""
import argparse
import json
import sys
import time

import ctranslate2
import faster_whisper
from faster_whisper import WhisperModel


def _reconfigure_stdio() -> None:
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, OSError, ValueError):
            pass


def _emit(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload) + "\n")
    sys.stdout.flush()


def _fail(detail: str) -> None:
    print(f"[ASR] error: {detail}", file=sys.stderr, flush=True)
    _emit({"ok": False, "error": detail})


def _cuda_available() -> bool:
    try:
        return ctranslate2.get_cuda_device_count() > 0
    except Exception:
        return False


def _load(model_name: str, device: str, compute_type: str) -> WhisperModel:
    return WhisperModel(model_name, device=device, compute_type=compute_type)


def _init_model(model_name: str, device: str) -> tuple[WhisperModel, str, str]:
    if device == "cpu":
        return _load(model_name, "cpu", "int8"), "cpu", "int8"
    if device == "cuda":
        return _load(model_name, "cuda", "float16"), "cuda", "float16"
    try:
        return _load(model_name, "cuda", "float16"), "cuda", "float16"
    except Exception as exc:
        print(
            f"[ASR] cuda fallido ({exc}); fallback cpu int8",
            file=sys.stderr,
            flush=True,
        )
        return _load(model_name, "cpu", "int8"), "cpu", "int8"


def _selftest() -> int:
    device = "cuda" if _cuda_available() else "cpu"
    print(f"[ASR] device={device}", flush=True)
    print(
        f"[ASR] faster-whisper={faster_whisper.__version__} "
        f"ctranslate2={ctranslate2.__version__}",
        flush=True,
    )
    print("[ASR] selftest ok", flush=True)
    return 0


def main() -> int:
    _reconfigure_stdio()
    parser = argparse.ArgumentParser(
        description="worker ASR one-shot (faster-whisper)"
    )
    parser.add_argument(
        "--selftest",
        action="store_true",
        help="imprime versiones y device sin cargar el modelo",
    )
    args = parser.parse_args()
    if args.selftest:
        return _selftest()

    raw = sys.stdin.readline()
    if not raw.strip():
        _fail("no se recibio comando en stdin")
        return 1
    try:
        cmd = json.loads(raw)
    except ValueError as exc:
        _fail(f"stdin no es JSON valido: {exc}")
        return 1
    if not isinstance(cmd, dict) or cmd.get("cmd") != "transcribe":
        unknown = cmd.get("cmd") if isinstance(cmd, dict) else type(cmd).__name__
        _fail(f"cmd desconocido: {unknown!r}")
        return 1

    path = cmd.get("path")
    if not isinstance(path, str) or not path:
        _fail("comando sin campo 'path'")
        return 1
    language = cmd.get("language") or None
    if language is not None and not isinstance(language, str):
        _fail(f"campo 'language' invalido: {language!r}")
        return 1
    model = str(cmd.get("model") or "medium")
    device = str(cmd.get("device") or "auto")
    model_name = f"Systran/faster-whisper-{model}"

    t0 = time.monotonic()
    try:
        model_obj, real_device, compute_type = _init_model(model_name, device)
    except Exception as exc:
        _fail(f"carga del modelo fallida ({model_name}, device={device}): {exc}")
        return 1
    print(
        f"[ASR] device={real_device} compute_type={compute_type} model={model_name}",
        file=sys.stderr,
        flush=True,
    )
    try:
        segments, info = model_obj.transcribe(path, language=language)
        text = " ".join(seg.text.strip() for seg in segments).strip()
    except Exception as exc:
        _fail(f"transcripcion fallida: {exc}")
        return 1
    duration = float(getattr(info, "duration", 0.0) or 0.0)
    print(
        f"[ASR] transcripcion ok: {len(text)} chars en "
        f"{time.monotonic() - t0:.1f}s (audio {duration:.1f}s, lang={info.language})",
        file=sys.stderr,
        flush=True,
    )
    _emit(
        {
            "ok": True,
            "text": text,
            "device": real_device,
            "compute_type": compute_type,
            "model": model_name,
            "language": info.language,
            "duration": round(duration, 3),
        }
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
