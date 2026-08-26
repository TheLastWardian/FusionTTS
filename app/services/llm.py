import asyncio
import json
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

    def _base_url(self) -> str:
        return str(self._config.get("llm_base_url")).rstrip("/")

    def _build_body(self, messages: list[dict], stream: bool) -> dict:
        body: dict = {
            "messages": messages,
            "stream": stream,
            "temperature": self._config.get("llm_temperature"),
            "top_p": self._config.get("llm_top_p"),
            "max_tokens": self._config.get("llm_max_tokens"),
        }
        model = self._config.get("llm_model")
        if model:
            body["model"] = model
        return body

    async def chat(self, messages: list[dict]) -> str:
        url = self._base_url() + "/v1/chat/completions"
        try:
            resp = await self._client.post(url, json=self._build_body(messages, stream=False))
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
