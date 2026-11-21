"""Model comparison: one prompt, several models, streamed side by side.

SSE events (each carries ``model`` = "provider:model"):
  start  {model, label, provider, mock}
  delta  {model, text}
  done   {model, latency_ms, ttft_ms, input_tokens, output_tokens, cost_usd}
  error  {model, code, message}
  end    {}
"""

import asyncio
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from ..db import SessionLocal, get_db
from ..models import User
from ..providers.base import ChatMessage, ModelInfo, Provider, ProviderError
from ..providers.mock_provider import estimate_tokens
from ..schemas import CompareIn, CompareVoteIn, SynthesizeIn
from ..security import audit, client_ip, get_current_user, primary_org_id
from ..services.usage import record_usage
from .chat import _precheck, _streaming_response, resolve_model, sse

router = APIRouter(prefix="/api/compare", tags=["compare"])

COMPARE_SYSTEM = "You are a helpful assistant. Answer clearly and concisely in Markdown."
SYNTH_SYSTEM = (
    "You combine several AI models' answers to the same question into one answer. Keep what the answers "
    "agree on, point out where they disagree, and do not invent facts none of them gave. Attribute specific "
    "claims to the model that made them using its label in square brackets, e.g. [Model A]. End with a short "
    "'Sources' line listing which models contributed. This is a synthesis, not an objective ranking."
)


async def _run_one(queue: asyncio.Queue, provider: Provider, info: ModelInfo, messages: list[ChatMessage],
                   system: str, mode: str, user_id, org_id, feature: str):
    start = time.perf_counter()
    ttft = None
    parts: list[str] = []
    usage = None
    await queue.put(sse("start", {"model": info.id, "label": info.label, "provider": info.provider,
                                  "mock": info.provider == "mock"}))
    error: ProviderError | None = None
    try:
        async for ev in provider.stream(info.model, messages, system=system, max_tokens=2048, reasoning=mode):  # type: ignore[arg-type]
            if ev.type == "text":
                if ttft is None:
                    ttft = int((time.perf_counter() - start) * 1000)
                parts.append(ev.text)
                await queue.put(sse("delta", {"model": info.id, "text": ev.text}))
            else:
                usage = ev
    except ProviderError as e:
        error = e
    except Exception:
        error = ProviderError("internal_error", "Unexpected error from this model.")
    latency = int((time.perf_counter() - start) * 1000)
    text = "".join(parts)
    in_tok = usage.input_tokens if usage else estimate_tokens(system + messages[-1].content)
    out_tok = usage.output_tokens if usage else estimate_tokens(text) if text else 0
    cost = info.cost(in_tok, out_tok)
    with SessionLocal() as db:
        record_usage(db, user_id=user_id, org_id=org_id, feature=feature, provider=info.provider, model=info.model,
                     input_tokens=in_tok, output_tokens=out_tok, cost_usd=cost, latency_ms=latency,
                     status="error" if error else "ok", error_code=error.code if error else None,
                     error_message=error.message if error else None)
        db.commit()
    if error:
        await queue.put(sse("error", {"model": info.id, "code": error.code, "message": error.message}))
    else:
        await queue.put(sse("done", {"model": info.id, "latency_ms": latency, "ttft_ms": ttft,
                                     "input_tokens": in_tok, "output_tokens": out_tok, "cost_usd": cost}))


async def _fan_in(jobs):
    queue: asyncio.Queue = asyncio.Queue()
    tasks = [asyncio.create_task(job(queue)) for job in jobs]

    async def closer():
        await asyncio.gather(*tasks, return_exceptions=True)
        await queue.put(None)

    closer_task = asyncio.create_task(closer())
    try:
        while True:
            item = await queue.get()
            if item is None:
                break
            yield item
        yield sse("end", {})
    finally:
        for t in tasks:
            t.cancel()
        closer_task.cancel()


@router.post("")
async def compare(body: CompareIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _precheck(db, user)
    if len(set(body.models)) != len(body.models):
        raise HTTPException(status_code=400, detail="Pick different models to compare.")
    resolved = [resolve_model(m, body.prompt, body.reasoning_mode, False) for m in body.models]
    org_id = primary_org_id(db, user)
    messages = [ChatMessage(role="user", content=body.prompt)]

    def job(p, i):
        return lambda q: _run_one(q, p, i, messages, COMPARE_SYSTEM, body.reasoning_mode, user.id, org_id, "compare")

    return _streaming_response(_fan_in([job(p, i) for p, i, _ in resolved]))


@router.post("/synthesize")
async def synthesize(body: SynthesizeIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _precheck(db, user)
    provider, info, _ = resolve_model(body.model, body.prompt, "balanced", False)
    blocks = []
    for a in body.answers:
        label = a.model
        blocks.append(f'<answer model="{label}">\n{a.text}\n</answer>')
    content = f"Question:\n{body.prompt}\n\nAnswers from different models:\n\n" + "\n\n".join(blocks)
    org_id = primary_org_id(db, user)
    messages = [ChatMessage(role="user", content=content)]
    return _streaming_response(_fan_in([
        lambda q: _run_one(q, provider, info, messages, SYNTH_SYSTEM, "balanced", user.id, org_id, "synthesis")
    ]))


@router.post("/vote", status_code=204)
def vote(body: CompareVoteIn, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Store which answer the user preferred. This is subjective feedback, not a benchmark."""
    audit(db, "compare.vote", user_id=user.id, org_id=primary_org_id(db, user), ip=client_ip(request),
          details={"preferred": body.preferred_model, "models": body.models[:4], "prompt_chars": len(body.prompt)})
    db.commit()
