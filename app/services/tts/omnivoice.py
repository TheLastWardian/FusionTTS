import asyncio
import base64
import logging
import os
import subprocess
from pathlib import Path

import httpx

from app import paths
from app.config import ConfigStore
from app.logging_setup import keep_latest, unique_log_path
from app.services.tts.engine import (
    TTSClientError,
    TTSNotReadyError,
    TTSError,
    TTSResult,
    TTSTimeoutError,
)

logger = logging.getLogger(__name__)

HEALTH_POLL_INTERVAL = 0.2
HEALTH_TIMEOUT = 15.0
READY_POLL_INTERVAL = 1.0
READY_TIMEOUT = 120.0
CLIENT_TIMEOUT = httpx.Timeout(10.0, connect=10.0)
STATUS_TIMEOUT = httpx.Timeout(2.0, connect=2.0)
EMPTY_AUDIO_MAX_BYTES = 1000
SYNTH_MAX_ATTEMPTS = 3


def _json_detail(resp: httpx.Response) -> str:
    try:
        data = resp.json()
        if isinstance(data, dict) and "detail" in data:
            return str(data["detail"])
    except (ValueError, KeyError):
        pass
    return resp.text[:500]


class OmniVoiceEngine:
    def __init__(
        self,
        config: ConfigStore,
        server_dir: Path | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._config = config
        self._server_dir = (
            Path(server_dir) if server_dir is not None else paths.BASE_DIR / "tts-server"
        )
        self._owns_client = http_client is None
        self._client = (
            http_client
            if http_client is not None
            else httpx.AsyncClient(timeout=CLIENT_TIMEOUT)
        )
        self._proc: subprocess.Popen | None = None
        self._proc_log = None
        self._closed = False
        self._lock = asyncio.Lock()

    def _resolve_python(self) -> str:
        python = self._config.get("tts_server_python")
        if python:
            return python
        default = paths.BASE_DIR.parent / "OmniVoice" / "venv" / "Scripts" / "python.exe"
        if not default.exists():
            raise TTSError(f"python del server TTS no encontrado: {default}")
        return str(default)

    def _base_url(self) -> str:
        return f"http://127.0.0.1:{self._config.get('tts_server_port')}"

    def _proc_alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    async def spawn(self) -> None:
        async with self._lock:
            if self._closed:
                raise TTSError("engine closed")
            if self._proc_alive():
                return
            python = self._resolve_python()
            port = self._config.get("tts_server_port")
            env = dict(os.environ)
            env["PORT"] = str(port)
            env["HOST"] = "127.0.0.1"
            env["HF_HUB_OFFLINE"] = "1"
            env["OMNIVOICE_INT8"] = "1" if self._config.get("tts_int8") else "0"
            env["TTS_ALIGNMENT"] = str(self._config.get("tts_alignment"))
            if self._proc_log is not None:
                self._proc_log.close()
                self._proc_log = None
            # sesion propia del TTS server: un archivo por spawn (timestamp
            # del instante) + cleanup de la carpeta (ultimos 10)
            server_log_dir = paths.BASE_DIR / "logs" / "tts-server"
            log_path = unique_log_path(server_log_dir, "server", fresh=True)
            keep_latest(server_log_dir)
            self._proc_log = open(log_path, "a", encoding="utf-8")
            try:
                self._proc = subprocess.Popen(
                    [python, "server.py"],
                    cwd=self._server_dir,
                    env=env,
                    stdout=self._proc_log,
                    stderr=subprocess.STDOUT,
                )
            except OSError as exc:
                self._proc_log.close()
                self._proc_log = None
                self._proc = None
                raise TTSError(f"no se pudo arrancar el server TTS: {exc}") from exc
            logger.info("tts server spawn: pid=%d port=%d", self._proc.pid, port)
            await self._wait_healthy()

    async def _wait_healthy(self) -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + HEALTH_TIMEOUT
        while True:
            if not self._proc_alive():
                code = self._proc.returncode
                logger.warning("tts server crash detectado (exit=%s)", code)
                raise TTSError(f"el server TTS murio al arrancar (exit code {code})")
            try:
                resp = await self._client.get(self._base_url() + "/health")
                if resp.status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            if loop.time() >= deadline:
                raise TTSError(
                    f"el server TTS no responde a /health tras {HEALTH_TIMEOUT:.0f} s"
                )
            await asyncio.sleep(HEALTH_POLL_INTERVAL)

    async def start(self) -> None:
        await self.spawn()
        if (await self._server_status()) == "ready":
            return
        await self._load_and_wait_ready()

    async def _server_status(self) -> str:
        try:
            resp = await self._client.get(self._base_url() + "/status")
            resp.raise_for_status()
            return resp.json().get("status")
        except httpx.HTTPError as exc:
            raise TTSError(f"el server TTS no responde: {exc}") from exc

    async def _load_and_wait_ready(self) -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + READY_TIMEOUT
        try:
            resp = await self._client.post(
                self._base_url() + "/load",
                timeout=httpx.Timeout(READY_TIMEOUT, connect=10.0),
            )
        except httpx.TimeoutException:
            pass
        except httpx.HTTPError as exc:
            raise TTSError(f"no se pudo cargar el modelo: {exc}") from exc
        else:
            if resp.status_code >= 400:
                raise TTSClientError(_json_detail(resp), resp.status_code)
        while True:
            if not self._proc_alive():
                code = self._proc.returncode
                logger.warning("tts server crash detectado (exit=%s)", code)
                raise TTSError(f"el server TTS murio durante /load (exit code {code})")
            try:
                if (await self._server_status()) == "ready":
                    return
            except TTSError:
                pass
            if loop.time() >= deadline:
                raise TTSError(f"timeout esperando 'ready' tras {READY_TIMEOUT:.0f} s")
            await asyncio.sleep(READY_POLL_INTERVAL)

    async def stop(self) -> None:
        """Mata el proceso del server: VRAM a 0 (el contexto CUDA muere con el).
        Un warm process conservaria ~1 GB de VRAM; el proximo start() re-spawnea."""
        if not self._proc_alive():
            return
        proc = self._proc
        self._proc = None
        proc.terminate()
        for _ in range(50):
            if proc.poll() is not None:
                break
            await asyncio.sleep(0.1)
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        log = self._proc_log
        self._proc_log = None
        if log is not None:
            log.close()
        logger.info("tts server detenido (pid=%d); VRAM liberada", proc.pid)

    async def close(self) -> None:
        async with self._lock:
            if self._closed:
                return
            self._closed = True
            proc = self._proc
            self._proc = None
            if proc is not None and proc.poll() is None:
                proc.terminate()
                for _ in range(50):
                    if proc.poll() is not None:
                        break
                    await asyncio.sleep(0.1)
                if proc.poll() is None:
                    proc.kill()
                    proc.wait()
                    logger.info("tts server kill (pid=%d)", proc.pid)
            log = self._proc_log
            self._proc_log = None
        if log is not None:
            log.close()
        if self._owns_client:
            await self._client.aclose()

    async def status(self) -> dict:
        if not self._proc_alive():
            return {"state": "stopped", "server": None}
        try:
            resp = await self._client.get(
                self._base_url() + "/status", timeout=STATUS_TIMEOUT
            )
            if resp.status_code == 200:
                return {"state": "running", "server": resp.json()}
        except httpx.HTTPError:
            pass
        return {"state": "running", "server": None}

    async def synthesize(
        self,
        text: str,
        audio_base64: str = "",
        prompt_text: str = "",
        *,
        language: str | None = None,
        abort_event: asyncio.Event | None = None,
    ) -> TTSResult:
        if abort_event is not None and abort_event.is_set():
            raise TTSError("aborted")
        await self._ensure_ready()
        cfg = self._config
        timeout = httpx.Timeout(cfg.get("tts_sentence_timeout"), connect=10.0)
        url = self._base_url() + "/synthesize"
        seed = cfg.get("tts_seed")
        for attempt in range(1, SYNTH_MAX_ATTEMPTS + 1):
            body = {
                "text": text,
                "audio_base64": audio_base64,
                "prompt_text": prompt_text,
                "language": language if language is not None else cfg.get("tts_language"),
                "num_steps": cfg.get("tts_num_steps"),
                "guidance_scale": cfg.get("tts_guidance_scale"),
                "seed": seed if attempt == 1 else None,
                "speed": cfg.get("tts_speed"),
                "instruct": cfg.get("tts_instruct"),
            }
            try:
                resp = await self._client.post(url, json=body, timeout=timeout)
            except httpx.TimeoutException as exc:
                raise TTSTimeoutError(
                    f"timeout en /synthesize tras {cfg.get('tts_sentence_timeout')} s"
                ) from exc
            except httpx.HTTPError as exc:
                raise TTSClientError(f"/synthesize: {exc}", 0) from exc
            if resp.status_code == 503:
                raise TTSNotReadyError(_json_detail(resp))
            if resp.status_code >= 400:
                raise TTSClientError(_json_detail(resp), resp.status_code)
            try:
                data = resp.json()
                audio = base64.b64decode(data["audio_base64"])
                sample_rate = int(data["sample_rate"])
            except (ValueError, KeyError, TypeError) as exc:
                raise TTSClientError(
                    f"respuesta invalida de /synthesize: {resp.text[:200]}", 200
                ) from exc
            if len(audio) >= EMPTY_AUDIO_MAX_BYTES:
                if attempt > 1:
                    logger.info(
                        "audio valido en intento %d/%d", attempt, SYNTH_MAX_ATTEMPTS
                    )
                words = data.get("words")
                if not isinstance(words, list):
                    words = None
                return TTSResult(audio=audio, sample_rate=sample_rate, words=words)
            logger.warning(
                "audio vacio de /synthesize (intento %d/%d, %d bytes)",
                attempt,
                SYNTH_MAX_ATTEMPTS,
                len(audio),
            )
            if abort_event is not None and abort_event.is_set():
                raise TTSError("aborted")
        raise TTSClientError(
            f"audio vacio de /synthesize tras {SYNTH_MAX_ATTEMPTS} intentos", 200
        )

    async def _ensure_ready(self) -> None:
        if self._closed:
            raise TTSError("engine closed")
        if not self._proc_alive():
            await self.spawn()
        if (await self._server_status()) != "ready":
            await self._load_and_wait_ready()
