"""Voice: config, OpenAI transcription/speech request shapes, validation, usage."""

import httpx
import pytest

from app.config import get_settings
from app.db import SessionLocal
from app.models import UsageRecord
from app.routers import voice


@pytest.fixture
def openai_voice(monkeypatch):
    seen = {}

    def handler(req: httpx.Request):
        seen.setdefault("paths", []).append(req.url.path)
        seen["auth"] = req.headers["authorization"]
        if req.url.path.endswith("/audio/transcriptions"):
            seen["body"] = req.content
            return httpx.Response(200, json={"text": "hello from the microphone"})
        seen["json"] = req.read().decode()
        return httpx.Response(200, content=b"ID3fake-mp3", headers={"content-type": "audio/mpeg"})

    monkeypatch.setattr(get_settings(), "openai_api_key", "sk-voice")
    monkeypatch.setattr(voice, "_client", httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    return seen


def test_unconfigured(authed):
    assert authed.get("/api/voice/config").json()["server_transcription"] is False
    r = authed.post("/api/voice/speech", json={"text": "hi"})
    assert r.status_code == 503 and "browser" in r.json()["detail"]


def test_transcribe_and_speak(authed, openai_voice):
    assert authed.get("/api/voice/config").json()["provider"] == "OpenAI"
    r = authed.post("/api/voice/transcribe", files={"file": ("a.webm", b"\x1aE\xdf\xa3" + b"0" * 32000, "audio/webm")})
    assert r.status_code == 200 and r.json()["text"] == "hello from the microphone"
    assert b'name="model"' in openai_voice["body"] and b"gpt-4o-mini-transcribe" in openai_voice["body"]
    r = authed.post("/api/voice/speech", json={"text": "Read this aloud", "voice": "nova"})
    assert r.status_code == 200 and r.headers["content-type"] == "audio/mpeg" and r.content.startswith(b"ID3")
    assert '"voice":"nova"' in openai_voice["json"].replace(" ", "") and openai_voice["auth"] == "Bearer sk-voice"
    with SessionLocal() as db:
        assert {u.feature for u in db.query(UsageRecord).all()} == {"voice"}


def test_validation(authed, openai_voice):
    assert authed.post("/api/voice/transcribe", files={"file": ("a.exe", b"MZ", "application/octet-stream")}).status_code == 422
    assert authed.post("/api/voice/speech", json={"text": "x", "voice": "robot"}).status_code == 422
