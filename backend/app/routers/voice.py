"""Voice: server-side speech-to-text and text-to-speech through OpenAI's audio API when a key is set.

The browser also offers dictation (Web Speech API) and read-aloud (speechSynthesis) without any server
call; the UI uses those when the server has no voice provider.
"""

import time

import httpx
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from ..models import User
from ..security import get_current_user, primary_org_id
from ..services.usage import record_usage
from .chat import _precheck

router = APIRouter(prefix="/api/voice", tags=["voice"])

TRANSCRIBE_MODEL = "gpt-4o-mini-transcribe"  # catalog default, verify
TTS_MODEL = "gpt-4o-mini-tts"  # catalog default, verify
TRANSCRIBE_USD_PER_MIN = 0.003  # estimate, verify
TTS_USD_PER_1K_CHARS = 0.015  # estimate, verify
MAX_AUDIO_BYTES = 20 * 1024 * 1024
AUDIO_TYPES = {"audio/webm", "audio/ogg", "audio/mpeg", "audio/mp4", "audio/wav", "audio/x-wav", "audio/m4a"}
VOICES = ("alloy", "ash", "coral", "echo", "fable", "nova", "onyx", "sage", "shimmer")
_client: httpx.AsyncClient | None = None  # tests inject a mock transport here


class SpeechIn(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    voice: str = "alloy"


@router.get("/config")
def voice_config(user: User = Depends(get_current_user)):
    on = bool(get_settings().openai_api_key)
    return {"server_transcription": on, "server_speech": on, "provider": "OpenAI" if on else None,
            "voices": list(VOICES) if on else []}


def _key() -> str:
    key = get_settings().openai_api_key
    if not key:
        raise HTTPException(status_code=503, detail="Server voice is not configured (needs OPENAI_API_KEY). "
                                                    "Your browser's built-in voice features still work.")
    return key


async def _post(path: str, **kw) -> httpx.Response:
    s = get_settings()
    c = _client or httpx.AsyncClient(timeout=60)
    try:
        r = await c.post(f"{s.openai_api_base.rstrip('/')}{path}", headers={"Authorization": f"Bearer {_key()}"}, **kw)
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail="The voice provider could not be reached.") from e
    finally:
        if _client is None:
            await c.aclose()
    if r.status_code >= 400:
        raise HTTPException(status_code=502, detail=f"The voice provider returned an error ({r.status_code}).")
    return r


@router.post("/transcribe")
async def transcribe(file: UploadFile = File(...), user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _key()
    _precheck(db, user)
    ctype = (file.content_type or "").split(";")[0]
    if ctype not in AUDIO_TYPES:
        raise HTTPException(status_code=422, detail="Unsupported audio format.")
    data = await file.read(MAX_AUDIO_BYTES + 1)
    if len(data) > MAX_AUDIO_BYTES:
        raise HTTPException(status_code=413, detail="Audio is too long (20 MB max).")
    start = time.perf_counter()
    r = await _post("/audio/transcriptions", data={"model": TRANSCRIBE_MODEL},
                    files={"file": (file.filename or "audio.webm", data, ctype)})
    text = r.json().get("text", "")
    # Rough duration estimate for cost: ~16 KB per second of compressed speech.
    minutes = max(1 / 60, len(data) / 16_000 / 60)
    record_usage(db, user_id=user.id, org_id=primary_org_id(db, user), feature="voice", provider="openai",
                 model=TRANSCRIBE_MODEL, cost_usd=round(minutes * TRANSCRIBE_USD_PER_MIN, 6),
                 latency_ms=int((time.perf_counter() - start) * 1000))
    db.commit()
    return {"text": text}


@router.post("/speech")
async def speech(body: SpeechIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _key()
    _precheck(db, user)
    if body.voice not in VOICES:
        raise HTTPException(status_code=422, detail="Unknown voice.")
    start = time.perf_counter()
    r = await _post("/audio/speech", json={"model": TTS_MODEL, "voice": body.voice, "input": body.text,
                                           "response_format": "mp3"})
    record_usage(db, user_id=user.id, org_id=primary_org_id(db, user), feature="voice", provider="openai", model=TTS_MODEL,
                 cost_usd=round(len(body.text) / 1000 * TTS_USD_PER_1K_CHARS, 6),
                 latency_ms=int((time.perf_counter() - start) * 1000))
    db.commit()
    return Response(r.content, media_type="audio/mpeg", headers={"Cache-Control": "no-store"})
