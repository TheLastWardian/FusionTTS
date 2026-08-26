import asyncio
import base64
import json

import httpx
import pytest

from app.config import ConfigStore
from app.services.llm import LLMClient, LLMError
from app.services.vision import describe_image


@pytest.fixture
def config(tmp_path):
    return ConfigStore(settings_path=tmp_path / "settings.json")


def make_client(config, handler):
    transport = httpx.MockTransport(handler)
    return LLMClient(config, httpx.AsyncClient(transport=transport))


def sse_body(tokens):
    parts = [
        f"data: {json.dumps({'choices': [{'delta': {'content': t}}]})}\n\n" for t in tokens
    ]
    parts.append("data: [DONE]\n\n")
    return "".join(parts).encode("utf-8")


def chat_response(content):
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


async def test_stream_chat_yields_tokens(config):
    async def handler(request):
        assert str(request.url) == "http://localhost:8080/v1/chat/completions"
        body = json.loads(request.content)
        assert body["stream"] is True
        assert body["messages"] == [{"role": "user", "content": "hola"}]
        return httpx.Response(200, content=sse_body(["Hola", " ", "mundo", "!"]))

    client = make_client(config, handler)
    tokens = [t async for t in client.stream_chat([{"role": "user", "content": "hola"}])]
    assert tokens == ["Hola", " ", "mundo", "!"]


async def test_stream_chat_cancel_stops_early(config):
    class PacedStream(httpx.AsyncByteStream):
        def __aiter__(self):
            async def gen():
                for i in range(10):
                    chunk = json.dumps({"choices": [{"delta": {"content": f"t{i}"}}]})
                    yield f"data: {chunk}\n\n".encode("utf-8")
                    await asyncio.sleep(0.001)
                yield b"data: [DONE]\n\n"

            return gen()

    async def handler(request):
        return httpx.Response(200, stream=PacedStream())

    client = make_client(config, handler)
    cancel = asyncio.Event()
    got = []
    async for token in client.stream_chat([{"role": "user", "content": "hola"}], cancel):
        got.append(token)
        if len(got) == 1:
            cancel.set()
    assert 1 <= len(got) <= 2


async def test_chat_returns_content_and_sends_config_params(config):
    config.set("llm_model", "qwen3-27b")
    captured = {}

    async def handler(request):
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content)
        return chat_response("hola mundo")

    client = make_client(config, handler)
    result = await client.chat([{"role": "user", "content": "hola"}])
    assert result == "hola mundo"
    assert captured["url"] == "http://localhost:8080/v1/chat/completions"
    body = captured["body"]
    assert body["model"] == "qwen3-27b"
    assert body["temperature"] == 1.0
    assert body["top_p"] == 1.0
    assert body["max_tokens"] == 20600
    assert body["stream"] is False
    assert body["messages"] == [{"role": "user", "content": "hola"}]


async def test_chat_reads_config_per_request(config):
    seen = []

    async def handler(request):
        seen.append(json.loads(request.content))
        return chat_response("ok")

    client = make_client(config, handler)
    await client.chat([{"role": "user", "content": "uno"}])
    config.set("llm_temperature", 0.1)
    config.set("llm_base_url", "http://localhost:9999")
    await client.chat([{"role": "user", "content": "dos"}])
    assert seen[0]["temperature"] == 1.0
    assert seen[1]["temperature"] == 0.1


async def test_chat_base_url_read_per_request(config):
    urls = []

    async def handler(request):
        urls.append(str(request.url))
        return chat_response("ok")

    client = make_client(config, handler)
    await client.chat([{"role": "user", "content": "uno"}])
    config.set("llm_base_url", "http://localhost:9999")
    await client.chat([{"role": "user", "content": "dos"}])
    assert urls == [
        "http://localhost:8080/v1/chat/completions",
        "http://localhost:9999/v1/chat/completions",
    ]


async def test_chat_empty_model_omits_model_key(config):
    captured = {}

    async def handler(request):
        captured["body"] = json.loads(request.content)
        return chat_response("ok")

    client = make_client(config, handler)
    assert config.get("llm_model") == ""
    await client.chat([{"role": "user", "content": "x"}])
    assert "model" not in captured["body"]


async def test_get_loaded_models(config):
    async def handler(request):
        assert str(request.url) == "http://localhost:8080/v1/models"
        return httpx.Response(200, json={"data": [{"id": "m1"}, {"id": "m2"}]})

    client = make_client(config, handler)
    assert await client.get_loaded_models() == ["m1", "m2"]


async def test_get_loaded_models_server_500(config):
    async def handler(request):
        return httpx.Response(500, json={"error": "boom"})

    client = make_client(config, handler)
    with pytest.raises(LLMError) as excinfo:
        await client.get_loaded_models()
    assert excinfo.value.detail


async def test_chat_http_error_raises_llm_error(config):
    async def handler(request):
        return httpx.Response(503, json={"error": "overloaded"})

    client = make_client(config, handler)
    with pytest.raises(LLMError) as excinfo:
        await client.chat([{"role": "user", "content": "x"}])
    assert "503" in excinfo.value.detail


async def test_chat_connection_error_raises_llm_error(config):
    async def handler(request):
        raise httpx.ConnectError("connection refused")

    client = make_client(config, handler)
    with pytest.raises(LLMError):
        await client.chat([{"role": "user", "content": "x"}])


async def test_stream_chat_connection_error_raises_llm_error(config):
    async def handler(request):
        raise httpx.ConnectError("connection refused")

    client = make_client(config, handler)
    with pytest.raises(LLMError):
        async for _ in client.stream_chat([{"role": "user", "content": "x"}]):
            pass


async def test_stream_chat_http_error_raises_llm_error(config):
    async def handler(request):
        return httpx.Response(500, json={"error": "boom"})

    client = make_client(config, handler)
    with pytest.raises(LLMError):
        async for _ in client.stream_chat([{"role": "user", "content": "x"}]):
            pass


async def test_describe_image_sends_multimodal(config):
    image_bytes = b"\x89PNG\r\n\x1a\nfake-png"
    captured = {}

    async def handler(request):
        captured["body"] = json.loads(request.content)
        return chat_response("una gata naranja")

    client = make_client(config, handler)
    result = await describe_image(client, image_bytes, ".png", "describe esta imagen")
    assert result == "una gata naranja"
    message = captured["body"]["messages"][0]
    assert message["role"] == "user"
    assert message["content"][0] == {"type": "text", "text": "describe esta imagen"}
    assert message["content"][1]["type"] == "image_url"
    url = message["content"][1]["image_url"]["url"]
    assert url.startswith("data:image/png;base64,")
    assert base64.b64decode(url.split(",", 1)[1]) == image_bytes


@pytest.mark.parametrize(
    ("ext", "mime"),
    [
        (".png", "image/png"),
        ("jpg", "image/jpeg"),
        (".jpeg", "image/jpeg"),
        ("webp", "image/webp"),
        ("gif", "image/gif"),
        (".tiff", "image/png"),
    ],
)
async def test_describe_image_mime_by_ext(config, ext, mime):
    captured = {}

    async def handler(request):
        captured["body"] = json.loads(request.content)
        return chat_response("ok")

    client = make_client(config, handler)
    await describe_image(client, b"xy", ext, "d")
    url = captured["body"]["messages"][0]["content"][1]["image_url"]["url"]
    assert url.startswith(f"data:{mime};base64,")


async def test_close_with_injected_client_keeps_working(config):
    async def handler(request):
        return chat_response("ok")

    client = make_client(config, handler)
    await client.close()
    assert await client.chat([{"role": "user", "content": "x"}]) == "ok"


async def test_close_own_client_stops_requests(config):
    client = LLMClient(config)
    await client.close()
    with pytest.raises(LLMError):
        await client.get_loaded_models()
