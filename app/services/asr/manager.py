import asyncio
import json
import logging
import os
import subprocess
import sys
from pathlib import Path

from app import paths
from app.config import ConfigStore
from app.services.asr.engine import ASREngineError, ASRError, ASRTimeoutError

logger = logging.getLogger(__name__)

EXIT_GRACE_POLLS = 20
EXIT_GRACE_INTERVAL = 0.05
KILL_GRACE_POLLS = 50
KILL_GRACE_INTERVAL = 0.1
STDERR_TAIL_CHARS = 1500


class ASRManager:
    def __init__(
        self,
        config: ConfigStore,
        worker_script: Path | str | None = None,
        python: str | None = None,
    ) -> None:
        self._config = config
        self._worker_script = (
            Path(worker_script)
            if worker_script is not None
            else paths.BASE_DIR / "app" / "services" / "asr" / "worker.py"
        )
        self._python = python if python is not None else sys.executable
        self._log_path = self._worker_script.parent / "worker.log"
        self._proc: subprocess.Popen | None = None
        self._proc_log = None
        self._closed = False
        self._lock = asyncio.Lock()

    def _proc_alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    async def _spawn(self) -> None:
        if self._proc_alive():
            return
        env = dict(os.environ)
        env["HF_HUB_OFFLINE"] = "1"
        env["PYTHONUNBUFFERED"] = "1"
        try:
            self._proc_log = open(self._log_path, "a", encoding="utf-8")
        except OSError as exc:
            raise ASREngineError(
                f"no se pudo abrir el log del worker ASR: {exc}"
            ) from exc
        try:
            self._proc = subprocess.Popen(
                [self._python, str(self._worker_script)],
                env=env,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=self._proc_log,
            )
        except OSError as exc:
            self._proc_log.close()
            self._proc_log = None
            self._proc = None
            raise ASREngineError(f"no se pudo arrancar el worker ASR: {exc}") from exc
        logger.info("asr worker spawn: pid=%d", self._proc.pid)

    async def _teardown(self) -> int | None:
        proc = self._proc
        if proc is None:
            self._close_log()
            return None
        for _ in range(EXIT_GRACE_POLLS):
            if proc.poll() is not None:
                break
            await asyncio.sleep(EXIT_GRACE_INTERVAL)
        if proc.poll() is None:
            proc.terminate()
            for _ in range(KILL_GRACE_POLLS):
                if proc.poll() is not None:
                    break
                await asyncio.sleep(KILL_GRACE_INTERVAL)
            if proc.poll() is None:
                proc.kill()
                proc.wait()
                logger.info("asr worker kill (pid=%d)", proc.pid)
        code = proc.returncode
        self._close_log()
        return code

    def _close_log(self) -> None:
        log = self._proc_log
        self._proc_log = None
        if log is not None:
            log.close()

    def _stderr_tail(self) -> str:
        try:
            text = self._log_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""
        return text[-STDERR_TAIL_CHARS:].strip()

    def _engine_error(self, detail: str, exit_code: int | None) -> ASREngineError:
        if exit_code is not None:
            detail = f"{detail} (exit={exit_code})"
        tail = self._stderr_tail()
        if tail:
            detail = f"{detail}\nstderr del worker:\n{tail}"
        return ASREngineError(detail)

    async def _exchange(
        self, path: str, language: str | None, timeout: float
    ) -> tuple[bytes | None, bool]:
        proc = self._proc
        cmd = {
            "cmd": "transcribe",
            "path": path,
            "language": language,
            "model": self._config.get("asr_model"),
            "device": self._config.get("asr_device"),
        }
        payload = (json.dumps(cmd, ensure_ascii=False) + "\n").encode("utf-8")
        try:
            proc.stdin.write(payload)
            proc.stdin.flush()
        except (OSError, ValueError, AttributeError) as exc:
            raise ASREngineError(
                f"no se pudo enviar el comando al worker ASR: {exc}"
            ) from exc
        loop = asyncio.get_running_loop()
        try:
            line = await asyncio.wait_for(
                loop.run_in_executor(None, proc.stdout.readline), timeout
            )
        except asyncio.TimeoutError:
            return None, True
        return line, False

    async def transcribe(self, path: str | Path, language: str | None = None) -> str:
        async with self._lock:
            if self._closed:
                raise ASRError("engine closed")
            timeout = float(self._config.get("asr_timeout"))
            await self._spawn()
            exit_code: int | None = None
            failed: ASREngineError | None = None
            line: bytes | None = b""
            timed_out = False
            try:
                line, timed_out = await self._exchange(str(path), language, timeout)
            except ASREngineError as exc:
                failed = exc
            finally:
                exit_code = await self._teardown()
            if failed is not None:
                raise self._engine_error(failed.detail, exit_code) from failed
            if timed_out:
                raise ASRTimeoutError(f"timeout de transcripcion tras {timeout:.0f} s")
            if line is None or not line.strip():
                raise self._engine_error("el worker ASR termino sin responder", exit_code)
            try:
                data = json.loads(line.decode("utf-8"))
            except (ValueError, UnicodeDecodeError) as exc:
                raise self._engine_error(
                    f"respuesta no-JSON del worker: {line[:200]!r}", exit_code
                ) from exc
            if not isinstance(data, dict) or data.get("ok") is not True:
                detail = data.get("error") if isinstance(data, dict) else None
                if not isinstance(detail, str):
                    detail = "respuesta invalida"
                raise self._engine_error(f"error del worker ASR: {detail}", exit_code)
            text = data.get("text")
            if not isinstance(text, str):
                raise self._engine_error(
                    "respuesta del worker ASR sin campo 'text'", exit_code
                )
            logger.info(
                "asr transcripcion ok: %d chars (device=%s model=%s lang=%s)",
                len(text),
                data.get("device"),
                data.get("model"),
                data.get("language"),
            )
            return text

    async def close(self) -> None:
        async with self._lock:
            if self._closed:
                return
            self._closed = True
            await self._teardown()
