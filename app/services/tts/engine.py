import asyncio
from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass
class TTSResult:
    audio: bytes
    sample_rate: int
    # Alineacion forzada por palabra [{text, start_ms, end_ms}] (karaoke);
    # None si el modo de alineacion esta apagado o la sintesis no la trajo.
    words: list | None = None


class TTSError(Exception):
    pass


class TTSClientError(TTSError):
    def __init__(self, detail: str, status_code: int) -> None:
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


class TTSNotReadyError(TTSClientError):
    def __init__(self, detail: str = "server no ready") -> None:
        super().__init__(detail, 503)


class TTSTimeoutError(TTSError):
    pass


@runtime_checkable
class TTSEngine(Protocol):
    async def spawn(self) -> None: ...

    async def start(self) -> None: ...

    async def stop(self) -> None: ...

    async def close(self) -> None: ...

    async def status(self) -> dict: ...

    async def synthesize(
        self,
        text: str,
        audio_base64: str = "",
        prompt_text: str = "",
        *,
        language: str | None = None,
        abort_event: asyncio.Event | None = None,
    ) -> TTSResult: ...
