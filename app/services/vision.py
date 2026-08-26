import base64

from app.services.llm import LLMClient

_MIME_BY_EXT = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "webp": "image/webp",
    "gif": "image/gif",
}


def _mime_for(ext: str) -> str:
    return _MIME_BY_EXT.get(ext.lstrip(".").lower(), "image/png")


async def describe_image(
    client: LLMClient, image_bytes: bytes, ext: str, instruction: str
) -> str:
    encoded = base64.b64encode(image_bytes).decode("ascii")
    message = {
        "role": "user",
        "content": [
            {"type": "text", "text": instruction},
            {
                "type": "image_url",
                "image_url": {"url": f"data:{_mime_for(ext)};base64,{encoded}"},
            },
        ],
    }
    return await client.chat([message])
