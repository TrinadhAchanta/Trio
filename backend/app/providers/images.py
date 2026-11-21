"""Image generation adapters.

- OpenAI Images API (``POST /v1/images/generations``, base64 output).
- xAI image generation (OpenAI-compatible ``/v1/images/generations``).
- Mock: draws a clearly labeled placeholder PNG locally (no AI involved) so the feature can be used and
  tested without keys.

Model ids and per-image prices are catalog defaults to verify against each provider's current docs; they
can be overridden in the model catalog JSON (``image_models``).
"""

import base64
import io
import textwrap
from dataclasses import dataclass

import httpx

from ..config import Settings
from .base import ProviderError, status_to_code


@dataclass(frozen=True)
class ImageModel:
    provider: str
    model: str
    label: str
    price_per_image_usd: float | None
    sizes: tuple[str, ...] = ("1024x1024", "1536x1024", "1024x1536")
    source: str = "default (verify)"

    @property
    def id(self) -> str:
        return f"{self.provider}:{self.model}"


IMAGE_CATALOG = [
    ImageModel("openai", "gpt-image-1", "GPT Image 1", 0.042),
    ImageModel("xai", "grok-2-image", "Grok 2 Image", 0.07, sizes=("1024x768",)),
    ImageModel("mock", "mock-image", "Mock image (demo, no AI)", 0.0, source="built-in"),
]


@dataclass
class GeneratedImage:
    data: bytes
    media_type: str
    revised_prompt: str | None = None


def _raise_for(r: httpx.Response, label: str) -> None:
    if r.status_code >= 400:
        try:
            msg = r.json().get("error", {})
            msg = msg.get("message") if isinstance(msg, dict) else str(msg)
        except ValueError:
            msg = r.text[:200]
        code, retryable = status_to_code(r.status_code)
        if r.status_code == 400 and msg and ("safety" in msg.lower() or "policy" in msg.lower()):
            code, retryable = "content_blocked", False
        raise ProviderError(code, f"{label}: {msg or 'request failed'}", retryable=retryable, status=r.status_code)


async def _openai_compatible(base: str, key: str, label: str, model: str, prompt: str, size: str,
                             client: httpx.AsyncClient | None, extra: dict) -> GeneratedImage:
    async with (client or httpx.AsyncClient(timeout=120)) as c:
        try:
            r = await c.post(f"{base.rstrip('/')}/images/generations", headers={"Authorization": f"Bearer {key}"},
                             json={"model": model, "prompt": prompt, "n": 1, **extra})
        except httpx.TimeoutException as e:
            raise ProviderError("provider_timeout", f"{label} took too long.", retryable=True) from e
        except httpx.HTTPError as e:
            raise ProviderError("provider_unavailable", f"{label} could not be reached.", retryable=True) from e
        _raise_for(r, label)
        item = (r.json().get("data") or [{}])[0]
        if item.get("b64_json"):
            return GeneratedImage(base64.b64decode(item["b64_json"]), "image/png", item.get("revised_prompt"))
        if item.get("url"):
            img = await c.get(item["url"])  # provider-hosted URL returned by the API itself
            _raise_for(img, label)
            return GeneratedImage(img.content, img.headers.get("content-type", "image/png").split(";")[0],
                                  item.get("revised_prompt"))
    raise ProviderError("bad_response", f"{label} returned no image.")


def mock_image(prompt: str, size: str) -> GeneratedImage:
    from PIL import Image, ImageDraw

    w, h = (int(x) for x in size.split("x"))
    w, h = min(w, 1024), min(h, 1024)
    img = Image.new("RGB", (w, h), (238, 242, 247))
    d = ImageDraw.Draw(img)
    for i in range(0, w + h, 48):  # diagonal stripes so it is obviously a placeholder
        d.line([(i, 0), (i - h, h)], fill=(226, 232, 240), width=18)
    d.rectangle([24, 24, w - 24, h - 24], outline=(100, 116, 139), width=4)
    lines = ["DEMO IMAGE", "No AI image model was called.", "", *textwrap.wrap(f"Prompt: {prompt}", 48)[:8]]
    y = h // 2 - 12 * len(lines)
    for line in lines:
        d.text((48, y), line, fill=(30, 41, 59))
        y += 24
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return GeneratedImage(buf.getvalue(), "image/png")


class ImageService:
    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None):
        self.s, self.client = settings, client

    def configured(self, provider: str) -> bool:
        return {"openai": bool(self.s.openai_api_key), "xai": bool(self.s.xai_api_key),
                "mock": self.s.enable_mock_provider}.get(provider, False)

    def models(self) -> list[ImageModel]:
        return list(IMAGE_CATALOG)

    def available(self) -> list[ImageModel]:
        return [m for m in IMAGE_CATALOG if self.configured(m.provider)]

    def resolve(self, model_id: str) -> ImageModel | None:
        if model_id == "auto":
            real = [m for m in self.available() if m.provider != "mock"]
            return (real or self.available() or [None])[0]
        return next((m for m in IMAGE_CATALOG if m.id == model_id), None)

    async def generate(self, m: ImageModel, prompt: str, size: str) -> GeneratedImage:
        if m.provider == "mock":
            return mock_image(prompt, size)
        if m.provider == "openai":
            return await _openai_compatible(self.s.openai_api_base, self.s.openai_api_key, "OpenAI", m.model, prompt, size,
                                            self.client, {"size": size})
        if m.provider == "xai":
            return await _openai_compatible(self.s.xai_api_base, self.s.xai_api_key, "xAI", m.model, prompt, size,
                                            self.client, {"response_format": "b64_json"})
        raise ProviderError("provider_not_configured", "Unknown image provider.")
