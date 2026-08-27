from pathlib import Path
from typing import Protocol, runtime_checkable


class ASRError(Exception):
    pass


class ASREngineError(ASRError):
    def __init__(self, detail: str = "") -> None:
        super().__init__(detail)
        self.detail = detail


class ASRTimeoutError(ASRError):
    pass


@runtime_checkable
class ASREngine(Protocol):
    async def transcribe(self, path: str | Path, language: str | None = None) -> str: ...

    async def close(self) -> None: ...
