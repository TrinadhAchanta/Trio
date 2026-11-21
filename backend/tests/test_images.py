"""Image generation: mock generator, OpenAI/xAI adapters (recorded response shapes), API, credits, isolation."""

import base64
import io
import json

import httpx
import pytest
from PIL import Image

from app.config import Settings
from app.db import SessionLocal
from app.models import FileObject, UsageRecord
from app.providers.base import ProviderError
from app.providers.images import IMAGE_CATALOG, ImageService

from .conftest import make_client, register


def png_bytes(color=(255, 0, 0)):
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), color).save(buf, "PNG")
    return buf.getvalue()


async def test_openai_adapter_request_and_b64_response():
    seen = {}

    def handler(req: httpx.Request):
        seen["auth"] = req.headers["authorization"]
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(png_bytes()).decode(),
                                                   "revised_prompt": "a red square"}]})

    svc = ImageService(Settings(openai_api_key="sk-x"), client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    m = svc.resolve("openai:gpt-image-1")
    img = await svc.generate(m, "red square", "1024x1024")
    assert seen["auth"] == "Bearer sk-x" and seen["body"] == {"model": "gpt-image-1", "prompt": "red square", "n": 1,
                                                              "size": "1024x1024"}
    assert img.data.startswith(b"\x89PNG") and img.revised_prompt == "a red square"


async def test_adapter_maps_policy_errors():
    def handler(req):
        return httpx.Response(400, json={"error": {"message": "Your request was rejected by the safety system."}})

    svc = ImageService(Settings(xai_api_key="x"), client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    with pytest.raises(ProviderError) as e:
        await svc.generate(svc.resolve("xai:grok-2-image"), "x", "1024x768")
    assert e.value.code == "content_blocked" and not e.value.retryable


def test_auto_prefers_real_image_models():
    assert ImageService(Settings(openai_api_key="k")).resolve("auto").provider == "openai"
    assert ImageService(Settings()).resolve("auto").provider == "mock"
    assert {m.provider for m in IMAGE_CATALOG} == {"openai", "xai", "mock"}


def test_generate_list_delete_with_mock(authed):
    models = authed.get("/api/images/models").json()["models"]
    assert {m["id"]: m["configured"] for m in models}["openai:gpt-image-1"] is False
    r = authed.post("/api/images", json={"prompt": "A lighthouse at dusk", "model": "auto"})
    assert r.status_code == 201, r.text
    g = r.json()
    assert g["mock"] is True and g["url"].startswith("/api/files/")
    content = authed.get(g["url"])
    assert content.status_code == 200 and content.content.startswith(b"\x89PNG")
    assert Image.open(io.BytesIO(content.content)).size == (1024, 1024)
    assert len(authed.get("/api/images").json()) == 1
    with SessionLocal() as db:
        u = db.query(UsageRecord).one()
        assert u.feature == "images" and u.credits == 1
    assert authed.delete(f"/api/images/{g['id']}").status_code == 204
    with SessionLocal() as db:
        assert db.query(FileObject).count() == 0


def test_validation_and_isolation(authed):
    assert authed.post("/api/images", json={"prompt": "x", "model": "openai:gpt-image-1"}).status_code == 422  # too short
    r = authed.post("/api/images", json={"prompt": "a cat", "model": "openai:gpt-image-1"})
    assert r.status_code == 400 and "not configured" in r.json()["detail"]
    r = authed.post("/api/images", json={"prompt": "a cat", "model": "mock:mock-image", "size": "7x7"})
    assert r.status_code == 400
    g = authed.post("/api/images", json={"prompt": "a cat", "model": "mock:mock-image"}).json()
    other = make_client()
    register(other, "ivan@example.com")
    assert other.get(g["url"]).status_code == 404
    assert other.delete(f"/api/images/{g['id']}").status_code == 404
