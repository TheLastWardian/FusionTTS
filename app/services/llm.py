import asyncio
import json
import time
from collections.abc import AsyncGenerator

import httpx

from app.config import ConfigStore


class LLMError(Exception):
    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


def _error_detail(exc: Exception, url: str) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        return f"HTTP {exc.response.status_code} {url}: {exc.response.text[:500]}"
    if isinstance(exc, httpx.HTTPError):
        return f"{exc.__class__.__name__} {url}: {exc}"
    return f"{exc.__class__.__name__}: {exc}"


class LLMClient:
    def __init__(
        self,
        config: ConfigStore,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._config = config
        self._owns_client = http_client is None
        self._client = (
            http_client
            if http_client is not None
            else httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=10.0, write=30.0))
        )
        self._ctx_cache: int | None = None
        self._ctx_at = 0.0

    def _base_url(self) -> str:
        return str(self._config.get("llm_base_url")).rstrip("/")

    def _build_body(self, messages: list[dict], stream: bool, max_tokens: int | None = None) -> dict:
        body: dict = {
            "messages": messages,
            "stream": stream,
            "temperature": self._config.get("llm_temperature"),
            "top_p": self._config.get("llm_top_p"),
            "max_tokens": (
                max_tokens if max_tokens is not None else self._config.get("llm_max_tokens")
            ),
        }
        model = self._config.get("llm_model")
        if model:
            body["model"] = model
        return body

    async def chat(self, messages: list[dict], max_tokens: int | None = None) -> str:
        url = self._base_url() + "/v1/chat/completions"
        try:
            resp = await self._client.post(url, json=self._build_body(messages, stream=False, max_tokens=max_tokens))
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"]
        except LLMError:
            raise
        except Exception as exc:
            raise LLMError(_error_detail(exc, url)) from exc

    async def stream_chat(
        self,
        messages: list[dict],
        cancel_event: asyncio.Event | None = None,
    ) -> AsyncGenerator[str, None]:
        url = self._base_url() + "/v1/chat/completions"
        try:
            async with self._client.stream(
                "POST", url, json=self._build_body(messages, stream=True)
            ) as resp:
                if resp.status_code >= 400:
                    body = (await resp.aread()).decode("utf-8", "replace")[:500]
                    raise LLMError(f"HTTP {resp.status_code} {url}: {body}")
                async for line in resp.aiter_lines():
                    if cancel_event is not None and cancel_event.is_set():
                        return
                    line = line.strip()
                    if not line.startswith("data:"):
                        continue
                    payload = line[len("data:") :].strip()
                    if payload == "[DONE]":
                        return
                    try:
                        chunk = json.loads(payload)
                        content = chunk["choices"][0]["delta"].get("content")
                    except (ValueError, KeyError, IndexError, TypeError) as exc:
                        raise LLMError(f"bad SSE payload: {payload[:200]}") from exc
                    if content:
                        yield content
        except LLMError:
            raise
        except Exception as exc:
            raise LLMError(_error_detail(exc, url)) from exc

    async def get_context_window(self) -> int | None:
        # Ventana real del server: llama.cpp expone n_ctx en /props.
        # Cacheada 5 min (la room la usa para el indicador de contexto).
        now = time.monotonic()
        if self._ctx_cache is not None and now - self._ctx_at < 300:
            return self._ctx_cache
        url = self._base_url() + "/props"
        try:
            # timeout corto: la sonda no debe colgarse tras una generacion
            # larga del server (slot unico)
            resp = await self._client.get(url, timeout=httpx.Timeout(15.0, connect=10.0))
            resp.raise_for_status()
            data = resp.json()
            window = self._parse_context_window(data)
            if window is None:
                raise ValueError("n_ctx not found in /props response")
            self._ctx_cache = window
            self._ctx_at = now
            return window
        except Exception as exc:
            self._ctx_cache = None
            self._ctx_at = 0.0
            raise LLMError(_error_detail(exc, url)) from exc

    @staticmethod
    def _parse_context_window(data: dict) -> int | None:
        # La ubicacion de n_ctx varia entre builds de llama.cpp
        dgs = data.get("default_generation_settings") or {}
        for candidate in (
            dgs.get("n_ctx"),
            (dgs.get("params") or {}).get("n_ctx"),
            data.get("n_ctx"),
        ):
            if isinstance(candidate, int) and candidate > 0:
                return candidate
        return None

    async def count_tokens(self, messages: list[dict]) -> int:
        # Sonda: el server tokeniza el prompt y devuelve usage sin generar
        # (max_tokens=0; llama.cpp aun produce ~1 token, coste despreciable).
        url = self._base_url() + "/v1/chat/completions"
        body: dict = {
            "messages": messages,
            "stream": False,
            "temperature": 0.0,
            "top_p": 1.0,
            "max_tokens": 0,
        }
        model = self._config.get("llm_model")
        if model:
            body["model"] = model
        try:
            resp = await self._client.post(
                url, json=body, timeout=httpx.Timeout(30.0, connect=10.0)
            )
            resp.raise_for_status()
            return int(resp.json()["usage"]["prompt_tokens"])
        except LLMError:
            raise
        except Exception as exc:
            raise LLMError(_error_detail(exc, url)) from exc

    async def get_loaded_models(self) -> list[str]:
        url = self._base_url() + "/v1/models"
        try:
            resp = await self._client.get(url)
            resp.raise_for_status()
            data = resp.json()
            return [m["id"] for m in data["data"]]
        except LLMError:
            raise
        except Exception as exc:
            raise LLMError(_error_detail(exc, url)) from exc

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()
