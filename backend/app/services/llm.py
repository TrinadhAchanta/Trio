"""Non-streaming text completion over a RoutePlan, with fallback, health and usage recording.

Used by background features (deep research, document generation, agents) that need a full answer
rather than a live stream.
"""

import time
import uuid
from dataclasses import dataclass

from ..db import SessionLocal
from ..providers.base import ChatMessage, ModelInfo, ProviderError
from ..providers.mock_provider import estimate_tokens
from .health import health
from .usage import record_usage

FALLBACK_CODES = {"provider_unavailable", "provider_rate_limited", "provider_auth_failed", "model_not_found",
                  "provider_timeout", "internal_error"}


@dataclass
class Completion:
    text: str
    info: ModelInfo
    cost_usd: float
    notices: list[str]


async def complete(attempts, messages: list[ChatMessage], *, system: str, mode: str, max_tokens: int,
                   user_id: uuid.UUID, org_id: uuid.UUID, feature: str, task_type: str | None = None) -> Completion:
    notices: list[str] = []
    last_error: ProviderError | None = None
    for idx, (provider, info) in enumerate(attempts):
        start = time.perf_counter()
        parts: list[str] = []
        usage = None
        error: ProviderError | None = None
        try:
            async for ev in provider.stream(info.model, messages, system=system, max_tokens=max_tokens,
                                            reasoning=mode):  # type: ignore[arg-type]
                if ev.type == "text":
                    parts.append(ev.text)
                elif ev.type == "usage":
                    usage = ev
        except ProviderError as e:
            error = e
        except Exception:
            error = ProviderError("internal_error", "Unexpected error from the model.")
        latency = int((time.perf_counter() - start) * 1000)
        text = "".join(parts)
        in_tok = usage.input_tokens if usage else estimate_tokens(system + "".join(m.content for m in messages))
        out_tok = usage.output_tokens if usage else estimate_tokens(text)
        cost = info.cost(in_tok, out_tok) if (not error or text) else 0.0
        health.record(info.provider, info.model, error is None, latency if not error else None,
                      retryable=error.retryable if error else True, error=error.code if error else None)
        with SessionLocal() as db:
            record_usage(db, user_id=user_id, org_id=org_id, feature=feature, provider=info.provider, model=info.model,
                         input_tokens=in_tok, output_tokens=out_tok if text else 0, cost_usd=cost or 0.0,
                         latency_ms=latency, status="error" if error else "ok",
                         error_code=error.code if error else None, error_message=error.message if error else None,
                         task_type=task_type)
            db.commit()
        if not error:
            return Completion(text, info, cost or 0.0, notices)
        last_error = error
        if text or error.code not in FALLBACK_CODES or idx == len(attempts) - 1:
            break
        notices.append(f"{info.label} was unavailable ({error.message}), so {attempts[idx + 1][1].label} was used instead.")
    assert last_error is not None
    raise last_error
