"""T11 - Verificacion e2e de TTS en hardware (semi-automatico).

Uso:  venv\\Scripts\\python.exe tts_e2e_check.py [--port 8100]

Precondicion: LLM cargado en LM Studio (:8080) — los pasos de chat lo usan.
Usa un LLM liviano: el TTS ocupa ~2.2 GB y ambos deben caber en la GPU.
Nota: los pasos de chat escriben mensajes de prueba en el historial del room.

Recorre cada paso con Enter (s = saltar, q = salir; sale limpia en cualquier
punto: mata app+server y escribe result.json/result.log).
Resultados: tts_e2e_results/result.json + result.log + WAVs.
"""

import argparse
import base64
import binascii
import datetime as dt
import io
import json
import subprocess
import sys
import time
from pathlib import Path

import httpx
import numpy as np
import soundfile as sf

BASE = Path(__file__).resolve().parent
RESULTS_DIR = BASE / "tts_e2e_results"
APP_LOG = RESULTS_DIR / "app.log"
RESULT_JSON = RESULTS_DIR / "result.json"
RESULT_LOG = RESULTS_DIR / "result.log"

TEST_TEXT_AUTO = "Hola. Este es un mensaje de prueba para verificar el sistema de voz de FusionTTS."
TEST_TEXT_PERSONA = "This voice belongs to a persona with reference audio. Hopefully it sounds natural."
CHAT_TEXT = "Hola! Responde con una frase corta, estamos probando la voz."
CHAT_TEXT_RESPAWN = "Hola de nuevo. Esta voz se genero despues de matar el server a mano."

_log_lines: list[str] = []


def log(msg: str = "") -> None:
    print(msg)
    _log_lines.append(msg)


def vram_mib() -> int:
    out = subprocess.run(
        ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    return int(out.splitlines()[0].strip())


PS_FIND_SERVER = (
    "Get-CimInstance Win32_Process | Where-Object { "
    "$_.Name -like 'python*' -and $_.CommandLine -like '*server.py*' -and "
    "$_.CommandLine -notlike '*uvicorn*' -and $_.CommandLine -notlike '*tts_e2e_check*' } | "
    "Select-Object -ExpandProperty ProcessId"
)


def find_server_pids() -> list[int]:
    r = subprocess.run(
        ["powershell", "-NoProfile", "-Command", PS_FIND_SERVER],
        capture_output=True,
        text=True,
    )
    return [int(line.strip()) for line in r.stdout.split() if line.strip().isdigit()]


def kill_pids(pids: list[int]) -> None:
    for pid in pids:
        subprocess.run(
            ["powershell", "-NoProfile", "-Command", f"Stop-Process -Id {pid} -Force"],
            capture_output=True,
        )


def alive(pid: int) -> bool:
    r = subprocess.run(
        ["powershell", "-NoProfile", "-Command", f"Get-Process -Id {pid} -ErrorAction SilentlyContinue"],
        capture_output=True,
    )
    return bool(r.stdout.strip())


def wait_server_pids_gone(timeout: float = 15.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not find_server_pids():
            return True
        time.sleep(0.5)
    return not find_server_pids()


def verify_wav(path: Path) -> dict:
    info = sf.info(str(path))
    data, sr = sf.read(str(path), dtype="float32")
    peak = float(np.max(np.abs(data))) if data.size else 0.0
    duration = data.size / sr if sr else 0.0
    ok = info.samplerate >= 8000 and duration >= 0.5 and peak > 0.005
    return {
        "file": path.name,
        "bytes": path.stat().st_size,
        "sample_rate": info.samplerate,
        "duration_s": round(duration, 2),
        "peak_amplitude": round(peak, 4),
        "ok": bool(ok),
    }


class Ctx:
    def __init__(self, port: int) -> None:
        self.port = port
        self.base_url = f"http://127.0.0.1:{port}"
        self.http = httpx.Client(base_url=self.base_url, timeout=30.0)
        self.app_proc: subprocess.Popen | None = None
        self.app_log_fh = None
        self.vram: dict[str, int] = {}
        self.wav_files: list[str] = []
        self.steps: list[dict] = []


def record(ctx: Ctx, name: str, status: str, details: dict) -> None:
    ctx.steps.append({"name": name, "status": status, "details": details})
    log(f"  [{status}] {json.dumps(details, ensure_ascii=False)}")


def read_cmd(prompt: str) -> str:
    try:
        return input(prompt).lstrip("\ufeff").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return "q"


def step_start_app(ctx: Ctx) -> tuple[str, dict]:
    ctx.app_log_fh = open(APP_LOG, "wb")
    ctx.app_proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(ctx.port)],
        cwd=str(BASE),
        stdout=ctx.app_log_fh,
        stderr=subprocess.STDOUT,
    )
    deadline = time.monotonic() + 30.0
    while time.monotonic() < deadline:
        if ctx.app_proc.poll() is not None:
            return "FAIL", {"note": "el proceso de la app murio al arrancar", "exit": ctx.app_proc.poll(), "log": str(APP_LOG)}
        try:
            if ctx.http.get("/api/health").status_code == 200:
                return "PASS", {"app_pid": ctx.app_proc.pid, "port": ctx.port, "log": str(APP_LOG)}
        except httpx.HTTPError:
            pass
        time.sleep(0.5)
    return "FAIL", {"note": "timeout esperando /health", "log": str(APP_LOG)}


def engine_status(ctx: Ctx) -> dict:
    return ctx.http.get("/api/tts/status").json()


def set_config(ctx: Ctx, key: str, value) -> bool:
    try:
        r = ctx.http.post("/api/config", json={"key": key, "value": value}, timeout=10.0)
        return r.status_code == 200
    except httpx.HTTPError:
        return False


def step_enable_tts(ctx: Ctx) -> tuple[str, dict]:
    if not set_config(ctx, "tts_enabled", True):
        return "FAIL", {"note": "no se pudo setear tts_enabled=true en config"}
    r = ctx.http.post("/api/tts/enable", timeout=10.0)
    if r.status_code not in (200, 202):
        return "FAIL", {"enable_status": r.status_code, "body": r.text[:200]}
    t0 = time.monotonic()
    deadline = time.monotonic() + 180.0
    last = {}
    while time.monotonic() < deadline:
        try:
            data = engine_status(ctx)
            last = data
            server = (data.get("engine") or {}).get("server") or {}
            if server.get("status") == "ready":
                return "PASS", {"enable_http": r.status_code, "load_time_s": round(time.monotonic() - t0, 1), "server": server}
        except httpx.HTTPError:
            pass
        time.sleep(2.0)
    return "FAIL", {"note": "timeout esperando modelo ready", "last_status": last}


def step_speak(ctx: Ctx, text: str, persona: str | None, out_name: str, long_timeout: bool = False) -> tuple[str, dict]:
    body: dict = {"text": text}
    if persona:
        body["persona"] = persona
    r = ctx.http.post("/api/tts/speak", json=body, timeout=300.0 if long_timeout else 120.0)
    if r.status_code != 200:
        return "FAIL", {"http": r.status_code, "body": r.text[:300], "persona": persona}
    wav_path = RESULTS_DIR / out_name
    wav_path.write_bytes(r.content)
    check = verify_wav(wav_path)
    ctx.wav_files.append(str(wav_path))
    details = {"http": 200, "persona": persona, **check}
    return ("PASS" if check["ok"] else "FAIL"), details


def step_speak_persona(ctx: Ctx) -> tuple[str, dict]:
    data = ctx.http.get("/api/personas").json()
    personas = data.get("personas", []) if isinstance(data, dict) else data
    with_ref = next((p for p in personas if isinstance(p, dict) and p.get("reference_audio")), None)
    if with_ref is None:
        return "SKIP", {"note": "ninguna persona con reference_audio"}
    return step_speak(ctx, TEST_TEXT_PERSONA, with_ref["name"], f"speak_persona_{with_ref['name']}.wav")


def step_disable_tts(ctx: Ctx) -> tuple[str, dict]:
    r = ctx.http.post("/api/tts/disable", timeout=300.0)
    if r.status_code != 200:
        set_config(ctx, "tts_enabled", False)
        return "FAIL", {"http": r.status_code, "body": r.text[:200]}
    deadline = time.monotonic() + 60.0
    while time.monotonic() < deadline:
        try:
            server = (engine_status(ctx).get("engine") or {}).get("server") or {}
            if server.get("status") == "unloaded":
                reset = set_config(ctx, "tts_enabled", False)
                return "PASS", {"server_status": "unloaded", "tts_enabled_reset": reset}
        except httpx.HTTPError:
            pass
        time.sleep(2.0)
    set_config(ctx, "tts_enabled", False)
    return "FAIL", {"note": "timeout esperando unloaded", "tts_enabled_reset": True}


def pick_room_persona(ctx: Ctx) -> tuple[str, str] | None:
    try:
        rooms = ctx.http.get("/api/rooms", timeout=10.0).json()
        personas = ctx.http.get("/api/personas", timeout=10.0).json()
    except httpx.HTTPError:
        return None
    room_list = rooms.get("rooms", []) if isinstance(rooms, dict) else rooms
    persona_list = personas.get("personas", []) if isinstance(personas, dict) else personas
    pnames = {p.get("name") for p in persona_list if isinstance(p, dict)}
    for room in room_list:
        if not isinstance(room, dict):
            continue
        for name in room.get("persona_names") or []:
            if name in pnames:
                return str(room.get("name")), str(name)
    return None


def read_chat_sse(ctx: Ctx, message: str, who_answers: str, chat_room: str, timeout_s: float, out_name: str) -> tuple[str, dict]:
    info: dict = {
        "tts_on": False,
        "tts_stopped": False,
        "tokens_chars": 0,
        "audio_chunks": 0,
        "audio_bytes": 0,
        "complete": False,
        "cancelled": False,
        "error": None,
        "persona": None,
    }
    wav_chunks: list[bytes] = []
    body = {"message": message, "who_answers": who_answers, "chat_room": chat_room}
    try:
        with ctx.http.stream(
            "POST", "/api/chat", json=body,
            timeout=httpx.Timeout(timeout_s, connect=10.0),
        ) as resp:
            if resp.status_code != 200:
                raw = resp.read()
                return "FAIL", {"http": resp.status_code, "body": raw[:300].decode("utf-8", "replace"), **info}
            for line in resp.iter_lines():
                line = line.strip()
                if not line.startswith("data: "):
                    continue
                try:
                    ev = json.loads(line[len("data: "):])
                except json.JSONDecodeError:
                    continue
                etype = ev.get("type")
                if etype == "tts_state":
                    if ev.get("state") == "on":
                        info["tts_on"] = True
                    elif ev.get("state") == "stopped":
                        info["tts_stopped"] = True
                elif etype == "start":
                    info["persona"] = ev.get("persona")
                elif etype == "token":
                    info["tokens_chars"] += len(ev.get("token", ""))
                elif etype == "audio_chunk":
                    info["audio_chunks"] += 1
                    try:
                        raw = base64.b64decode(ev.get("audio", ""), validate=True)
                    except (binascii.Error, ValueError):
                        raw = b""
                    info["audio_bytes"] += len(raw)
                    if raw:
                        wav_chunks.append(raw)
                elif etype == "error":
                    info["error"] = str(ev.get("message"))
                elif etype == "complete":
                    info["complete"] = True
                    info["cancelled"] = bool(ev.get("cancelled"))
                    break
    except httpx.TimeoutException:
        return "FAIL", {"note": f"timeout esperando complete tras {timeout_s:.0f} s", **info}
    except httpx.HTTPError as exc:
        return "FAIL", {"note": f"error de red en el stream SSE: {exc}", **info}
    if wav_chunks:
        wav_path = RESULTS_DIR / out_name
        segments = []
        rate = 24000
        for blob in wav_chunks:
            data, sr = sf.read(io.BytesIO(blob), dtype="float32")
            segments.append(data)
            rate = int(sr)
        full = np.concatenate(segments)
        sf.write(str(wav_path), full, rate)
        info["wav_file"] = wav_path.name
        info["wav_duration_s"] = round(full.size / rate, 2)
        ctx.wav_files.append(str(wav_path))
    ok = (
        info["complete"]
        and not info["cancelled"]
        and info["error"] is None
        and info["tts_on"]
        and info["audio_chunks"] >= 1
    )
    return ("PASS" if ok else "FAIL"), info


def step_chat_tts(ctx: Ctx) -> tuple[str, dict]:
    pick = pick_room_persona(ctx)
    if pick is None:
        return "SKIP", {"note": "sin rooms con personas validas"}
    room, persona = pick
    status, info = read_chat_sse(ctx, CHAT_TEXT, persona, room, timeout_s=180.0, out_name="chat_audio.wav")
    return status, {"room": room, "who_answers": persona, **info}


def step_controls(ctx: Ctx) -> tuple[str, dict]:
    def dispatcher_state() -> dict:
        return engine_status(ctx).get("dispatcher", {})

    out: dict = {}
    r = ctx.http.post("/api/tts/pause", timeout=10.0)
    d = dispatcher_state()
    out["pause"] = {"http": r.status_code, "paused": d.get("paused")}
    if r.status_code != 200 or d.get("paused") is not True:
        return "FAIL", out
    r = ctx.http.post("/api/tts/resume", timeout=10.0)
    d = dispatcher_state()
    out["resume"] = {"http": r.status_code, "paused": d.get("paused")}
    if r.status_code != 200 or d.get("paused") is not False:
        return "FAIL", out
    r = ctx.http.post("/api/tts/stop", timeout=10.0)
    d = dispatcher_state()
    out["stop"] = {"http": r.status_code, "stopped": d.get("stopped")}
    if r.status_code != 200 or d.get("stopped") is not True:
        return "FAIL", out
    return "PASS", out


def step_kill_server(ctx: Ctx) -> tuple[str, dict]:
    pids = find_server_pids()
    if not pids:
        return "FAIL", {"note": "no se encontro el proceso del server"}
    kill_pids(pids)
    gone = wait_server_pids_gone(10.0)
    return ("PASS" if gone else "FAIL"), {"killed_pids": pids, "gone": gone}


def step_respawn_chat(ctx: Ctx) -> tuple[str, dict]:
    pick = pick_room_persona(ctx)
    if pick is None:
        return "SKIP", {"note": "sin rooms con personas validas"}
    room, persona = pick
    t0 = time.monotonic()
    status, info = read_chat_sse(ctx, CHAT_TEXT_RESPAWN, persona, room, timeout_s=300.0, out_name="chat_respawn_audio.wav")
    info["total_time_s"] = round(time.monotonic() - t0, 1)
    return status, {"room": room, "who_answers": persona, **info}


def step_final_cleanup(ctx: Ctx) -> tuple[str, dict]:
    cleanup_processes(ctx)
    time.sleep(3.0)
    ctx.vram["final_mib"] = vram_mib()
    return "PASS", {
        "final_vram_mib": ctx.vram["final_mib"],
        "final_delta_vs_baseline_mib": ctx.vram["final_mib"] - ctx.vram.get("baseline_mib", 0),
    }


def cleanup_processes(ctx: Ctx) -> None:
    if ctx.app_proc is not None and ctx.app_proc.poll() is None:
        ctx.app_proc.terminate()
        try:
            ctx.app_proc.wait(timeout=15.0)
        except subprocess.TimeoutExpired:
            ctx.app_proc.kill()
            try:
                ctx.app_proc.wait(timeout=10.0)
            except subprocess.TimeoutExpired:
                pass
    leftover = find_server_pids()
    if leftover:
        kill_pids(leftover)
        wait_server_pids_gone(10.0)


def write_results(ctx: Ctx, started: dt.datetime) -> int:
    passed = sum(1 for s in ctx.steps if s["status"] == "PASS")
    failed = sum(1 for s in ctx.steps if s["status"] == "FAIL")
    skipped = sum(1 for s in ctx.steps if s["status"] == "SKIP")
    result = {
        "timestamp": dt.datetime.now().isoformat(timespec="seconds"),
        "duration_s": round((dt.datetime.now() - started).total_seconds(), 1),
        "port": ctx.port,
        "steps": ctx.steps,
        "vram": {k: v for k, v in ctx.vram.items()},
        "wav_files": ctx.wav_files,
        "app_log": str(APP_LOG),
        "summary": {"pass": passed, "fail": failed, "skip": skipped},
    }
    RESULT_JSON.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    RESULT_LOG.write_text("\n".join(_log_lines), encoding="utf-8")
    log("")
    log("=" * 72)
    log(f"RESUMEN: {passed} PASS / {failed} FAIL / {skipped} SKIP")
    for s in ctx.steps:
        log(f"  [{s['status']}] {s['name']}")
    log(f"VRAM: baseline={ctx.vram.get('baseline_mib')} loaded={ctx.vram.get('loaded_mib')} "
        f"after_disable={ctx.vram.get('after_disable_mib')} final={ctx.vram.get('final_mib')} (MiB)")
    log(f"Resultados: {RESULT_JSON}")
    log(f"Log:        {RESULT_LOG}")
    log(f"Escucha los WAV en: {RESULTS_DIR}")
    return 0 if failed == 0 else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="T11 e2e TTS hardware check")
    parser.add_argument("--port", type=int, default=8100)
    args = parser.parse_args()

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    started = dt.datetime.now()
    ctx = Ctx(args.port)

    log("=" * 72)
    log("T11 - Verificacion e2e TTS en hardware")
    log(f"Hora: {started.isoformat(timespec='seconds')}  Puerto: {args.port}")
    log("Control: Enter = siguiente paso | s = saltar | q = salir (limpia al salir)")
    log("Precondicion: LLM liviano cargado en LM Studio (:8080) para los pasos de chat")
    log("=" * 72)

    plan: list[tuple[int, str, str, object, bool, bool]] = [
        (2, "start_app", "Arranca la app con uvicorn (no usa VRAM).",
         lambda: step_start_app(ctx), False, True),
        (3, "enable_tts", "tts_enabled=true + carga del modelo en la GPU (1-3 min).",
         lambda: step_enable_tts(ctx), False, True),
        (4, "vram_loaded", "Lee la VRAM con el modelo cargado.",
         lambda: vram_step("loaded_mib"), True, False),
        (5, "speak_auto", f"Voz auto: {TEST_TEXT_AUTO!r}",
         lambda: step_speak(ctx, TEST_TEXT_AUTO, None, "speak_auto.wav"), True, False),
        (6, "speak_persona", "Sintesis con la primera persona que tenga audio de referencia.",
         lambda: step_speak_persona(ctx), True, False),
        (7, "chat_tts", "Chat SSE con TTS on (usa el LLM): espera tts_state on + audio_chunk.",
         lambda: step_chat_tts(ctx), True, False),
        (8, "controls", "pause/resume/stop del dispatcher (queda stoppeado; el proximo chat lo resetea).",
         lambda: step_controls(ctx), True, False),
        (9, "kill_server", "Simula un crash: mata el tts-server con el TTS SIGUIENDO activo.",
         lambda: step_kill_server(ctx), True, False),
        (10, "respawn_chat", "Chat tras el crash: debe re-spawnear + cargar + sintetizar (1-3 min).",
         lambda: step_respawn_chat(ctx), True, False),
        (11, "disable_tts", "Unload del modelo (libera VRAM; el proceso queda vivo) + tts_enabled=false.",
         lambda: step_disable_tts(ctx), True, False),
        (12, "vram_after_disable", "Lee la VRAM tras el unload (queda vivo el server: algo de CUDA residual es normal).",
         lambda: vram_step("after_disable_mib"), True, False),
        (13, "final_cleanup", "Cierra la app (el engine mata al server) y verifica VRAM final.",
         lambda: step_final_cleanup(ctx), False, False),
    ]

    def vram_step(label: str) -> tuple[str, dict]:
        time.sleep(3.0)
        ctx.vram[label] = vram_mib()
        return "PASS", {
            "vram_mib": ctx.vram[label],
            "delta_vs_baseline_mib": ctx.vram[label] - ctx.vram.get("baseline_mib", 0),
        }

    def run_step(no: int, name: str, desc: str, fn, skippable: bool) -> str:
        log(f"\n=== Paso {no}/{len(plan)}: {name} ===")
        log(desc)
        opts = "[Enter] ejecutar | [s] saltar | [q] salir:" if skippable else "[Enter] ejecutar | [q] salir:"
        while True:
            ans = read_cmd(f"  {opts} ")
            if ans == "":
                break
            if ans == "s" and skippable:
                record(ctx, name, "SKIP", {"note": "saltado por el usuario"})
                return "SKIP"
            if ans == "q":
                return "quit"
            log("  responda con Enter, s o q")
        try:
            status, details = fn()
        except Exception as exc:
            status, details = "FAIL", {"note": f"excepcion: {type(exc).__name__}: {exc}"}
        record(ctx, name, status, details)
        return status

    ctx.vram["baseline_mib"] = vram_mib()
    record(ctx, "baseline_vram", "PASS", {"vram_mib": ctx.vram["baseline_mib"]})

    kill_ok = False
    try:
        for no, name, desc, fn, skippable, hard in plan:
            if name == "respawn_chat" and not kill_ok:
                record(ctx, name, "SKIP", {"note": "kill_server no paso"})
                continue
            status = run_step(no, name, desc, fn, skippable)
            if name == "kill_server":
                kill_ok = status == "PASS"
            if status == "quit":
                log("\nSalida solicitada por el usuario.")
                break
            if status == "FAIL" and hard:
                log("\nPaso critico fallo; el test no puede continuar.")
                break
    finally:
        try:
            cleanup_processes(ctx)
        except Exception as exc:
            log(f"aviso: limpieza final incompleta: {exc}")
        if ctx.app_log_fh is not None:
            ctx.app_log_fh.close()
        try:
            code = write_results(ctx, started)
        except Exception as exc:
            log(f"aviso: no se pudo escribir el resultado: {exc}")
            code = 1
    return code


if __name__ == "__main__":
    sys.exit(main())
