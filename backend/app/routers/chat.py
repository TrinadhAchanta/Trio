"""Chat endpoints: send a message (or regenerate) and stream the reply as server-sent events.

SSE event types sent to the browser:
  meta   {user_message?, assistant_message_id, provider, model, model_label, route_reason, mock}
  delta  {text}
  done   {message, artifacts, usage}
  error  {code, message, retryable, message_id}
"""

import asyncio
import base64
import json
import logging
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import SessionLocal, get_db
from ..models import Artifact, ArtifactVersion, Conversation, FileObject, Message, User
from ..providers.base import ChatMessage, ImageInput, ModelInfo, Provider, ProviderError
from ..providers.mock_provider import estimate_tokens
from ..providers.registry import get_registry
from ..schemas import ArtifactOut, MessageOut, RegenerateIn, SendMessageIn
from ..security import enforce_rate_limit, get_current_user, primary_org_id
from ..services import knowledge as kb_svc
from ..services.artifacts import ARTIFACT_SYSTEM_PROMPT, detect_artifacts
from ..services.billing import enforce_credits
from ..services.evidence import load_evidence
from ..services.files import get_storage
from ..services.health import health
from ..services.router import classify_task
from ..services.usage import over_budget, record_usage
from .conversations import get_owned_conversation

log = logging.getLogger("trio.chat")
router = APIRouter(prefix="/api/conversations", tags=["chat"])

DEFAULT_CONTEXT_TOKENS = 128_000
MAX_CONTEXT_TOKENS = 200_000  # cap per request to control spend even on 1M-context models


def sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


FALLBACK_CODES = {"provider_unavailable", "provider_rate_limited", "provider_auth_failed", "model_not_found",
                  "provider_not_configured", "internal_error"}
MAX_ATTEMPTS = 3


@dataclass
class RoutePlan:
    attempts: list[tuple[Provider, ModelInfo]]
    reason: str
    task_type: str


def resolve_model(model_id: str, prompt: str, mode: str, needs_vision: bool, user: User | None = None,
                  db: Session | None = None) -> RoutePlan:
    """Choose the model (or validate the user's choice) and an ordered fallback list."""
    reg = get_registry()
    prefs = (user.preferences or {}) if user else {}
    allowed = prefs.get("allowed_providers") or None
    preferred = prefs.get("preferred_providers") or None
    allow_fallback = prefs.get("allow_fallback", True)
    evidence = load_evidence(db) if db is not None else None
    decision = reg.route(prompt, mode, needs_vision, evidence=evidence,  # type: ignore[arg-type]
                         allowed_providers=allowed, preferred_providers=preferred)
    task_type = decision.task_type if decision else classify_task(prompt, needs_vision)[0]
    if model_id == "auto":
        if not decision:
            raise HTTPException(status_code=400, detail="No AI provider is configured on the server.")
        ranked = [(reg.providers[c.model.provider], c.model) for c in decision.ranked]
        # Never fall back from a real model to the mock: that would replace a real answer with demo text.
        ranked = ranked[:1] + [(p, m) for p, m in ranked[1:] if m.provider != "mock" or ranked[0][1].provider == "mock"]
        return RoutePlan(ranked[: MAX_ATTEMPTS if allow_fallback else 1], decision.reason, task_type)
    resolved = reg.resolve(model_id)
    if not resolved:
        raise HTTPException(status_code=400, detail=f"Unknown model '{model_id}'.")
    provider, info = resolved
    if not provider.configured():
        raise HTTPException(status_code=400, detail=f"{provider.label} is not configured on this server.")
    if needs_vision and not info.vision:
        raise HTTPException(status_code=400, detail=f"{info.label} does not accept images.")
    if allowed and info.provider not in allowed and info.provider != "mock":
        raise HTTPException(status_code=400, detail=f"{provider.label} is not in your allowed providers.")
    attempts = [(provider, info)]
    if allow_fallback and decision:
        attempts += [(reg.providers[c.model.provider], c.model) for c in decision.ranked
                     if c.model.id != info.id and (c.model.provider != "mock" or info.provider == "mock")]
    return RoutePlan(attempts[:MAX_ATTEMPTS], f"Selected manually: {info.label}", task_type)


def path_to(conv: Conversation, leaf_id: uuid.UUID) -> list[Message]:
    by_id = {m.id: m for m in conv.messages}
    path: list[Message] = []
    cur = by_id.get(leaf_id)
    seen: set[uuid.UUID] = set()
    while cur is not None and cur.id not in seen:
        seen.add(cur.id)
        path.append(cur)
        cur = by_id.get(cur.parent_id) if cur.parent_id else None
    return list(reversed(path))


def load_attachments(db: Session, user: User, ids: list) -> list[FileObject]:
    if not ids:
        return []
    uuids = [uuid.UUID(str(i)) for i in ids]
    files = db.query(FileObject).filter(FileObject.id.in_(uuids), FileObject.user_id == user.id).all()
    return files


def build_context(db: Session, user: User, path: list[Message], info: ModelInfo, max_tokens: int) -> list[ChatMessage]:
    storage = get_storage()
    out: list[ChatMessage] = []
    for m in path:
        if m.role not in ("user", "assistant"):
            continue
        if m.role == "assistant" and (m.status == "error" or not m.content):
            continue
        content = m.content
        images: list[ImageInput] = []
        if m.role == "user" and m.sources:
            content += kb_svc.sources_block(m.sources)
        if m.role == "user" and m.attachments:
            for f in load_attachments(db, user, m.attachments):
                if f.kind in ("text", "pdf") and f.extracted_text:
                    content += f'\n\n<attached_file name="{f.filename}">\n{f.extracted_text}\n</attached_file>'
                elif f.kind == "image":
                    if info.vision:
                        images.append(ImageInput(f.content_type, base64.b64encode(storage.get(f.storage_key)).decode()))
                    else:
                        content += f"\n\n[Image '{f.filename}' omitted: this model does not accept images]"
        # Merge consecutive same-role turns (e.g. after a failed reply) so every provider accepts them.
        if out and out[-1].role == m.role:
            out[-1].content += "\n\n" + content
            out[-1].images += images
        else:
            out.append(ChatMessage(role=m.role, content=content, images=images))  # type: ignore[arg-type]

    # Context-window management: drop the oldest turns until the estimate fits.
    budget = min(info.context_window or DEFAULT_CONTEXT_TOKENS, MAX_CONTEXT_TOKENS) - max_tokens - 2_000
    def total() -> int:
        return sum(estimate_tokens(c.content) + 1_000 * len(c.images) for c in out)
    while len(out) > 1 and total() > budget:
        out.pop(0)
    while out and out[0].role != "user":
        out.pop(0)
    if out and total() > budget:
        raise HTTPException(status_code=413, detail="This message and its attachments are too long for the model.")
    return out


def _finalize(*, user_id, org_id, conv_id, assistant_id, info: ModelInfo, text: str, usage, error: ProviderError | None,
              status: str, latency_ms: int, chat_messages: list[ChatMessage], task_type: str,
              notice: str | None = None) -> dict:
    in_tok = usage.input_tokens if usage else estimate_tokens("".join(c.content for c in chat_messages))
    out_tok = usage.output_tokens if usage else estimate_tokens(text) if text else 0
    cost = info.cost(in_tok, out_tok)
    with SessionLocal() as db:
        msg = db.get(Message, assistant_id)
        msg.content = text
        msg.provider, msg.model, msg.task_type, msg.notice = info.provider, info.model, task_type, notice
        msg.status = "error" if error else ("stopped" if status == "cancelled" else "complete")
        msg.error = f"{error.code}: {error.message}" if error else None
        msg.input_tokens, msg.output_tokens, msg.latency_ms = in_tok, out_tok, latency_ms
        record_usage(db, user_id=user_id, org_id=org_id, feature="chat", provider=info.provider, model=info.model,
                     input_tokens=in_tok, output_tokens=out_tok, cost_usd=cost, latency_ms=latency_ms,
                     status="error" if error else status, error_code=error.code if error else None,
                     error_message=error.message if error else None, conversation_id=conv_id, message_id=assistant_id,
                     task_type=task_type)
        artifacts: list[Artifact] = []
        if not error and status == "ok":
            for a in detect_artifacts(text):
                art = Artifact(user_id=user_id, org_id=org_id, conversation_id=conv_id, message_id=assistant_id,
                               title=a.title, kind=a.kind, current_version=1)
                art.versions.append(ArtifactVersion(version=1, content=a.content, source="ai"))
                db.add(art)
                artifacts.append(art)
        conv = db.get(Conversation, conv_id)
        conv.updated_at = datetime.now(UTC)
        db.commit()
        return {
            "message": MessageOut.model_validate(msg).model_dump(mode="json"),
            "artifacts": [ArtifactOut.model_validate(a).model_dump(mode="json") for a in artifacts],
            "usage": {"input_tokens": in_tok, "output_tokens": out_tok, "cost_usd": cost, "latency_ms": latency_ms},
        }


def _record_failed_attempt(*, user_id, org_id, conv_id, assistant_id, info: ModelInfo, error: ProviderError,
                           latency_ms: int, task_type: str) -> None:
    with SessionLocal() as db:
        record_usage(db, user_id=user_id, org_id=org_id, feature="chat", provider=info.provider, model=info.model,
                     latency_ms=latency_ms, status="error", error_code=error.code, error_message=error.message,
                     conversation_id=conv_id, message_id=assistant_id, task_type=task_type)
        db.commit()


async def stream_reply(*, user_id, org_id, conv_id, assistant_id, plan: RoutePlan, chat_messages: list[ChatMessage],
                       mode: str, user_message: dict | None):
    s = get_settings()
    provider, info = plan.attempts[0]
    yield sse("meta", {
        "user_message": user_message,
        "assistant_message_id": str(assistant_id),
        "provider": info.provider,
        "model": info.model,
        "model_label": info.label,
        "route_reason": plan.reason,
        "task_type": plan.task_type,
        "mock": info.provider == "mock",
    })
    start = time.perf_counter()
    parts: list[str] = []
    usage = None
    error: ProviderError | None = None
    status = "ok"
    notice: str | None = None
    result: dict = {}
    try:
        for idx, (provider, info) in enumerate(plan.attempts):
            attempt_start = time.perf_counter()
            error = None
            try:
                async for ev in provider.stream(info.model, chat_messages, system=ARTIFACT_SYSTEM_PROMPT,
                                                max_tokens=s.max_output_tokens, reasoning=mode):  # type: ignore[arg-type]
                    if ev.type == "text":
                        parts.append(ev.text)
                        yield sse("delta", {"text": ev.text})
                    elif ev.type == "usage":
                        usage = ev
                health.record(info.provider, info.model, True, int((time.perf_counter() - attempt_start) * 1000))
                break
            except ProviderError as e:
                error = e
            except (asyncio.CancelledError, GeneratorExit):
                raise
            except Exception:
                log.exception("Unexpected error while streaming from %s", info.id)
                error = ProviderError("internal_error", "Something went wrong while generating the response.")
            health.record(info.provider, info.model, False, retryable=error.retryable, error=error.code)
            last = idx == len(plan.attempts) - 1
            if parts or last or error.code not in FALLBACK_CODES:
                break
            _record_failed_attempt(user_id=user_id, org_id=org_id, conv_id=conv_id, assistant_id=assistant_id, info=info,
                                   error=error, latency_ms=int((time.perf_counter() - attempt_start) * 1000),
                                   task_type=plan.task_type)
            nxt = plan.attempts[idx + 1][1]
            notice = f"{info.label} was unavailable ({error.message}), so {nxt.label} answered instead."
            yield sse("fallback", {"from": info.id, "from_label": info.label, "to": nxt.id, "to_label": nxt.label,
                                   "provider": nxt.provider, "model": nxt.model, "model_label": nxt.label,
                                   "mock": nxt.provider == "mock", "reason": error.message, "code": error.code,
                                   "notice": notice})
    except (asyncio.CancelledError, GeneratorExit):
        status = "cancelled"
        raise
    finally:
        latency_ms = int((time.perf_counter() - start) * 1000)
        try:
            result = _finalize(user_id=user_id, org_id=org_id, conv_id=conv_id, assistant_id=assistant_id, info=info,
                               text="".join(parts), usage=usage, error=error, status=status,
                               latency_ms=latency_ms, chat_messages=chat_messages, task_type=plan.task_type,
                               notice=notice)
        except Exception:
            log.exception("Failed to save assistant message %s", assistant_id)
    if error:
        yield sse("error", {"code": error.code, "message": error.message, "retryable": error.retryable,
                            "message_id": str(assistant_id)})
    else:
        yield sse("done", result)


def _streaming_response(gen) -> StreamingResponse:
    return StreamingResponse(gen, media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def _precheck(db: Session, user: User) -> None:
    enforce_rate_limit(f"chat:{user.id}", get_settings().rate_limit_chat_per_minute)
    plan = enforce_credits(db, primary_org_id(db, user))
    if over_budget(db, user.id, plan.daily_cost_limit_usd):
        raise HTTPException(status_code=429, detail="You have reached today's usage limit. It resets within 24 hours.")


@router.post("/{conversation_id}/messages")
async def send_message(conversation_id: uuid.UUID, body: SendMessageIn, request: Request,
                       user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    conv = get_owned_conversation(db, user, conversation_id)
    _precheck(db, user)

    files = load_attachments(db, user, body.attachments)
    if len(files) != len(set(body.attachments)):
        raise HTTPException(status_code=400, detail="One or more attachments were not found.")
    needs_vision = any(f.kind == "image" for f in files)

    if body.branch_from_root:
        parent_id = None
    elif body.parent_id:
        parent = db.get(Message, body.parent_id)
        if not parent or parent.conversation_id != conv.id:
            raise HTTPException(status_code=400, detail="parent_id is not in this conversation.")
        parent_id = parent.id
    else:
        parent_id = conv.messages[-1].id if conv.messages else None

    plan = resolve_model(body.model, body.content, body.reasoning_mode, needs_vision, user, db)
    info = plan.attempts[0][1]

    sources = []
    if body.knowledge_base_ids:
        hits = kb_svc.search(db, user, body.knowledge_base_ids, body.content)
        sources = [{"n": i + 1, **h} for i, h in enumerate(hits)]
    user_msg = Message(conversation_id=conv.id, parent_id=parent_id, role="user", content=body.content,
                       attachments=[str(f.id) for f in files], task_type=plan.task_type, sources=sources)
    db.add(user_msg)
    db.flush()
    assistant = Message(conversation_id=conv.id, parent_id=user_msg.id, role="assistant", status="streaming",
                        provider=info.provider, model=info.model)
    db.add(assistant)
    if conv.title == "New conversation":
        first_line = body.content.strip().splitlines()[0] if body.content.strip() else "New conversation"
        conv.title = (first_line[:60] + "...") if len(first_line) > 60 else first_line
    conv.default_model = body.model
    conv.reasoning_mode = body.reasoning_mode
    db.commit()
    db.refresh(conv)

    chat_messages = build_context(db, user, path_to(conv, user_msg.id), info, get_settings().max_output_tokens)
    return _streaming_response(stream_reply(
        user_id=user.id, org_id=conv.org_id, conv_id=conv.id, assistant_id=assistant.id, plan=plan,
        chat_messages=chat_messages, mode=body.reasoning_mode,
        user_message=MessageOut.model_validate(user_msg).model_dump(mode="json"),
    ))


@router.post("/{conversation_id}/regenerate")
async def regenerate(conversation_id: uuid.UUID, body: RegenerateIn, user: User = Depends(get_current_user),
                     db: Session = Depends(get_db)):
    conv = get_owned_conversation(db, user, conversation_id)
    _precheck(db, user)
    user_msg = db.get(Message, body.user_message_id)
    if not user_msg or user_msg.conversation_id != conv.id or user_msg.role != "user":
        raise HTTPException(status_code=400, detail="user_message_id must be a user message in this conversation.")
    needs_vision = any(f.kind == "image" for f in load_attachments(db, user, user_msg.attachments))
    plan = resolve_model(body.model, user_msg.content, body.reasoning_mode, needs_vision, user, db)
    info = plan.attempts[0][1]
    assistant = Message(conversation_id=conv.id, parent_id=user_msg.id, role="assistant", status="streaming",
                        provider=info.provider, model=info.model)
    db.add(assistant)
    db.commit()
    db.refresh(conv)
    chat_messages = build_context(db, user, path_to(conv, user_msg.id), info, get_settings().max_output_tokens)
    return _streaming_response(stream_reply(
        user_id=user.id, org_id=conv.org_id, conv_id=conv.id, assistant_id=assistant.id, plan=plan,
        chat_messages=chat_messages, mode=body.reasoning_mode, user_message=None,
    ))
