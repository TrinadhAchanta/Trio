"""Collects quality evidence for the router from evaluation results and user ratings (cached)."""

import time

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import EvalResult, Message
from .router import Evidence

_TTL_S = 300.0
_cache: tuple[float, Evidence] | None = None


def load_evidence(db: Session, *, force: bool = False) -> Evidence:
    global _cache
    now = time.monotonic()
    if _cache and not force and now - _cache[0] < _TTL_S:
        return _cache[1]
    ev = Evidence()
    for model_id, task_type, score in db.execute(
        select(EvalResult.model_id, EvalResult.task_type, EvalResult.score).order_by(EvalResult.created_at.desc()).limit(20000)
    ):
        ev.add(model_id, task_type, float(score))
    for provider, model, task_type, rating in db.execute(
        select(Message.provider, Message.model, Message.task_type, Message.rating)
        .where(Message.rating.is_not(None), Message.task_type.is_not(None), Message.provider.is_not(None))
        .limit(20000)
    ):
        ev.add(f"{provider}:{model}", task_type, 1.0 if rating > 0 else 0.0)
    _cache = (now, ev)
    return ev


def invalidate() -> None:
    global _cache
    _cache = None
