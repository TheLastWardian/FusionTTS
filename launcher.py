# launcher.py — arranque de un click: app FusionTTS (+ TTS server bajo demanda por el app). Ctrl+C / cerrar ventana apagan todo.

import ctypes
import json
import os
import signal
import subprocess
import sys
import time
import urllib.request

BASE = os.path.dirname(os.path.abspath(__file__))
APP_PYTHON = os.path.join(BASE, "venv", "Scripts", "python.exe")
APP_PORT = 8000
APP_URL = "http://localhost:8000"
LLM_CHECK_TIMEOUT = 3
APP_HEALTH_TIMEOUT = 60


def read_settings(path=None):
    if path is None:
        path = os.path.join(BASE, "settings.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        print(f"WARNING: settings.json does not exist ({path}), using defaults.")
        return {}
    except (json.JSONDecodeError, ValueError, OSError) as exc:
        print(f"WARNING: settings.json is invalid ({exc}), using defaults.")
        return {}
    if not isinstance(data, dict):
        print(f"WARNING: settings.json is not a JSON object ({path}), using defaults.")
        return {}
    return data


def resolve_omnivoice_dir(settings, base=BASE):
    raw = str(settings.get("omnivoice_dir") or "").strip()
    if not raw:
        return os.path.normpath(os.path.join(base, "..", "OmniVoice"))
    if os.path.isabs(raw):
        return os.path.normpath(raw)
    return os.path.normpath(os.path.join(base, raw))


def resolve_tts_python(settings, base=BASE):
    python = str(settings.get("tts_server_python") or "").strip()
    if python and os.path.exists(python):
        return os.path.normpath(python)
    default = os.path.normpath(
        os.path.join(
            resolve_omnivoice_dir(settings, base), "venv", "Scripts", "python.exe"
        )
    )
    if os.path.exists(default):
        return default
    return None


def wait_http(url, timeout, interval=1.0):
    deadline = time.time() + timeout
    while True:
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:
                if resp.status == 200:
                    return True
        except Exception:
            pass
        if time.time() >= deadline:
            return False
        time.sleep(min(interval, max(0.0, deadline - time.time())))


def _kill_tree(pid):
    try:
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(pid)],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass


def main():
    if not os.path.exists(APP_PYTHON):
        print("ERROR: venv does not exist. Run setup.bat first.")
        sys.exit(1)

    settings = read_settings()
    tts_py = resolve_tts_python(settings)

    print("=" * 60)
    print("  FusionTTS - starts the app (the TTS server is spawned by the app, on demand)")
    print("=" * 60)
    print(f"  App:   {APP_URL}")
    if tts_py:
        print(f"  TTS:   OK - server python: {tts_py}")
    else:
        print("  TTS:   WARNING - TTS python not found; TTS will not work")
    llm_url = str(settings.get("llm_base_url") or "").strip().rstrip("/")
    if not llm_url:
        llm_url = "http://localhost:8080"
    if wait_http(llm_url + "/health", LLM_CHECK_TIMEOUT):
        print(f"  LLM:   OK - {llm_url}")
    else:
        print(f"  LLM:   WARNING - not detected at {llm_url}; chat will not work until you start it")
    print()

    proc = subprocess.Popen(
        [
            APP_PYTHON,
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(APP_PORT),
            "--log-level",
            "info",
        ],
        cwd=BASE,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
    )

    def shutdown(signum, frame):
        print("\nShutting down...")
        try:
            # CTRL_BREAK (1), no CTRL_C (0): uvicorn (CREATE_NEW_PROCESS_GROUP)
            # ignora el CTRL_C empircamente pero cierra al instante con BREAK
            # (sale el teardown de lifespan: mata TTS/ASR y libera VRAM).
            ctypes.windll.kernel32.GenerateConsoleCtrlEvent(1, proc.pid)
        except Exception:
            try:
                proc.terminate()
            except Exception:
                pass
        try:
            proc.wait(timeout=10)
        except Exception:
            _kill_tree(proc.pid)
            try:
                proc.wait()
            except Exception:
                pass
        print("Done.")
        os._exit(0)

    signal.signal(signal.SIGINT, shutdown)
    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, shutdown)

    if not wait_http(APP_URL + "/api/health", APP_HEALTH_TIMEOUT):
        print("ERROR: the app did not start within 60 s. Killing the process.")
        _kill_tree(proc.pid)
        sys.exit(1)

    print(f"  App ready: {APP_URL}")
    print()
    print("  CTRL+C = SHUTDOWN ALL")
    print("  CLOSE THE WINDOW = SHUTDOWN ALL")
    print()

    while proc.poll() is None:
        time.sleep(0.5)

    code = proc.returncode if proc.returncode is not None else 0
    print(f"\nThe server exited (exit code {code}).")
    _kill_tree(proc.pid)
    sys.exit(code)


if __name__ == "__main__":
    main()
