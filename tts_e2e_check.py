"""T11 - Verificacion e2e de TTS en hardware (semi-automatico).

Uso:  venv\\Scripts\\python.exe tts_e2e_check.py [--port 8100]

Recorre cada paso con Enter (s = saltar, q = salir; sale limpia en cualquier
punto). El script no consume VRAM: solo la app + tts-server, que es lo que se
esta probando. Resultados: tts_e2e_results/result.json + result.log + WAVs.
"""

import argparse
import datetime as dt
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
TEST_TEXT_PERSONA = "Esta voz pertenece a una persona con audio de referencia. Esperemos que suene natural."
TEST_TEXT_RESPAWN = "Este mensaje fue sintetizado despues de que el servidor murio y renacio. La recuperacion pasiva funciona."

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


def step_enable_tts(ctx: Ctx) -> tuple[str, dict]:
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


def step_disable_tts(ctx: Ctx) -> tuple[str, dict]:
    r = ctx.http.post("/api/tts/disable", timeout=300.0)
    if r.status_code != 200:
        return "FAIL", {"http": r.status_code, "body": r.text[:200]}
    deadline = time.monotonic() + 60.0
    while time.monotonic() < deadline:
        try:
            server = (engine_status(ctx).get("engine") or {}).get("server") or {}
            if server.get("status") == "unloaded":
                return "PASS", {"server_status": "unloaded"}
        except httpx.HTTPError:
            pass
        time.sleep(2.0)
    return "FAIL", {"note": "timeout esperando unloaded"}


def step_speak_persona(ctx: Ctx) -> tuple[str, dict]:
    personas = ctx.http.get("/api/personas").json()
    with_ref = next((p for p in personas if isinstance(p, dict) and p.get("reference_audio")), None)
    if with_ref is None:
        return "SKIP", {"note": "ninguna persona con reference_audio"}
    return step_speak(ctx, TEST_TEXT_PERSONA, with_ref["name"], f"speak_persona_{with_ref['name']}.wav")


def step_kill_server(ctx: Ctx) -> tuple[str, dict]:
    pids = find_server_pids()
    if not pids:
        return "FAIL", {"note": "no se encontro el proceso del server"}
    kill_pids(pids)
    gone = wait_server_pids_gone(10.0)
    return ("PASS" if gone else "FAIL"), {"killed_pids": pids, "gone": gone}


def step_respawn_speak(ctx: Ctx) -> tuple[str, dict]:
    t0 = time.monotonic()
    status, details = step_speak(ctx, TEST_TEXT_RESPAWN, None, "speak_respawn.wav", long_timeout=True)
    details["total_time_s"] = round(time.monotonic() - t0, 1)
    return status, details


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
        "vram": ctx.vram,
        "wav_files": ctx.wav_files,
        "app_log": str(APP_LOG),
        "summary": {"pass": passed, "fail": failed, "skip": skipped},
    }
    RESULT_JSON.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    RESULT_LOG.write_text("\n".join(_log_lines) + "\n", encoding="utf-8")

    log("\n" + "=" * 72)
    log(f"RESUMEN: {passed} PASS / {failed} FAIL / {skipped} SKIP")
    for s in ctx.steps:
        log(f"  [{s['status']:4s}] {s['name']}")
    log(f"VRAM: baseline={ctx.vram.get('baseline_mib')} loaded={ctx.vram.get('loaded_mib')} "
        f"after_disable={ctx.vram.get('after_disable_mib')} final={ctx.vram.get('final_mib')} (MiB)")
    log(f"Resultados: {RESULT_JSON}")
    log(f"Log:        {RESULT_LOG}")
    log("Escucha los WAV en: " + str(RESULTS_DIR))
    return 1 if failed else 0


def read_cmd(prompt: str) -> str:
    try:
        return input(prompt).lstrip("\ufeff").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return "q"


def main() -> int:
    parser = argparse.ArgumentParser(description="T11 e2e TTS hardware check")
    parser.add_argument("--port", type=int, default=8100)
    args = parser.parse_args()

    RESULTS_DIR.mkdir(exist_ok=True)
    ctx = Ctx(args.port)
    started = dt.datetime.now()

    log("=" * 72)
    log("T11 - Verificacion e2e TTS en hardware")
    log(f"Hora: {started.isoformat(timespec='seconds')}  Puerto: {args.port}")
    log("Control: Enter = siguiente paso | s = saltar | q = salir (limpia al salir)")
    log("=" * 72)
    log("Paso  1. VRAM baseline (automatico)")
    log("Paso  2. Arranque de la app (uvicorn)")
    log("Paso  3. Enable TTS (carga del modelo, 1-3 min)")
    log("Paso  4. VRAM con modelo cargado")
    log("Paso  5. Sintesis 1: voz auto -> WAV")
    log("Paso  6. Sintesis 2: persona con referencia -> WAV")
    log("Paso  7. Disable TTS (unload)")
    log("Paso  8. VRAM tras disable (debe volver cerca del baseline)")
    log("Paso  9. Matar el server a mano")
    log("Paso 10. Respawn pasivo: /speak tras la muerte -> WAV")
    log("Paso 11. Limpieza final + VRAM final")
    print()
    ans = read_cmd("Enter para iniciar (q para salir sin hacer nada): ")
    if ans == "q":
        log("sin iniciar; nada que hacer")
        return 0

    def run_step(no: int, name: str, desc: str, fn, skippable: bool) -> str:
        log(f"\n=== Paso {no}/11: {name} ===")
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

    def vram_step(label: str) -> tuple[str, dict]:
        time.sleep(3.0)
        ctx.vram[label] = vram_mib()
        return "PASS", {
            "vram_mib": ctx.vram[label],
            "delta_vs_baseline_mib": ctx.vram[label] - ctx.vram.get("baseline_mib", 0),
        }

    ctx.vram["baseline_mib"] = vram_mib()
    record(ctx, "baseline_vram", "PASS", {"vram_mib": ctx.vram["baseline_mib"]})

    plan: list[tuple[int, str, str, object, bool, bool]] = [
        (2, "start_app", "Arranca la app con uvicorn (no usa VRAM).",
         lambda: step_start_app(ctx), False, True),
        (3, "enable_tts", "Carga el modelo en la GPU (1-3 min).", step_enable_tts, False, True),
        (4, "vram_loaded", "Lee la VRAM con el modelo cargado.",
         lambda: vram_step("loaded_mib"), True, False),
        (5, "speak_auto", f"Voz auto: {TEST_TEXT_AUTO!r}",
         lambda: step_speak(ctx, TEST_TEXT_AUTO, None, "speak_auto.wav"), True, False),
        (6, "speak_persona", "Sintesis con la primera persona que tenga audio de referencia.",
         step_speak_persona, True, False),
        (7, "disable_tts", "Unload del modelo (libera VRAM; el proceso queda vivo).", step_disable_tts, True, False),
        (8, "vram_after_disable", "Lee la VRAM tras el unload (debe estar cerca del baseline).",
         lambda: vram_step("after_disable_mib"), True, False),
        (9, "kill_server", "Mata el proceso del tts-server a mano.", step_kill_server, True, False),
        (10, "respawn_speak", "El /speak debe re-spawnear + cargar + sintetizar (1-3 min).",
         step_respawn_speak, True, False),
        (11, "final_cleanup", "Cierra la app (el engine mata al server) y verifica VRAM final.",
         step_final_cleanup, False, False),
    ]

    kill_ok = False
    try:
        for no, name, desc, fn, skippable, hard in plan:
            if name == "respawn_speak" and not kill_ok:
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
