import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import UsageRecord


def record_usage(
    db: Session,
    *,
    user_id: uuid.UUID,
    org_id: uuid.UUID,
    feature: str,
    provider: str,
    model: str,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cost_usd: float | None = 0.0,
    latency_ms: int | None = None,
    status: str = "ok",
    error_code: str | None = None,
    error_message: str | None = None,
    conversation_id: uuid.UUID | None = None,
    message_id: uuid.UUID | None = None,
) -> UsageRecord:
    rec = UsageRecord(
        user_id=user_id,
        org_id=org_id,
        feature=feature,
        provider=provider,
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_usd=cost_usd or 0.0,
        latency_ms=latency_ms,
        status=status,
        error_code=error_code,
        error_message=(error_message or None) and error_message[:2000],
        conversation_id=conversation_id,
        message_id=message_id,
    )
    db.add(rec)
    return rec


def spent_today_usd(db: Session, user_id: uuid.UUID) -> float:
    since = datetime.now(UTC) - timedelta(days=1)
    total = (
        db.query(func.coalesce(func.sum(UsageRecord.cost_usd), 0.0))
        .filter(UsageRecord.user_id == user_id, UsageRecord.created_at >= since)
        .scalar()
    )
    return float(total or 0.0)


def over_budget(db: Session, user_id: uuid.UUID) -> bool:
    return spent_today_usd(db, user_id) >= get_settings().daily_cost_limit_usd
