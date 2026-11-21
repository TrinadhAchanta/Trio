"""Model catalog, usage summary and health."""

from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from ..models import UsageRecord, User
from ..providers.registry import get_registry
from ..security import get_current_user
from ..services.usage import spent_today_usd

router = APIRouter(prefix="/api", tags=["meta"])


@router.get("/health")
def health(db: Session = Depends(get_db)):
    db.execute(func.now().select())
    return {"status": "ok"}


@router.get("/models")
async def list_models(verify: bool = False, user: User = Depends(get_current_user)):
    """List providers and models. ``verify=true`` checks configured providers' live model lists."""
    reg = get_registry()
    remote: dict[str, set[str] | None] = {}
    if verify:
        for name, p in reg.providers.items():
            if p.configured() and name != "mock":
                ids = await p.list_remote_models()
                remote[name] = set(ids) if ids is not None else None
    providers = []
    for name, p in reg.providers.items():
        models = []
        for m in p.models():
            live = remote.get(name)
            models.append({
                "id": m.id, "provider": m.provider, "model": m.model, "label": m.label, "tier": m.tier,
                "vision": m.vision, "context_window": m.context_window,
                "input_price_per_mtok": m.input_price_per_mtok, "output_price_per_mtok": m.output_price_per_mtok,
                "source": m.source,
                "verified": None if live is None else (m.model in live),
            })
        providers.append({"name": name, "label": p.label, "configured": p.configured(), "mock": name == "mock",
                          "models": models})
    return {"providers": providers, "auto_available": bool(reg.available_models())}


@router.get("/usage/summary")
def usage_summary(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = (
        db.query(UsageRecord.provider, UsageRecord.model, UsageRecord.status,
                 func.count(), func.sum(UsageRecord.input_tokens), func.sum(UsageRecord.output_tokens),
                 func.sum(UsageRecord.cost_usd), func.avg(UsageRecord.latency_ms))
        .filter(UsageRecord.user_id == user.id)
        .group_by(UsageRecord.provider, UsageRecord.model, UsageRecord.status)
        .all()
    )
    recent = (db.query(UsageRecord).filter(UsageRecord.user_id == user.id)
              .order_by(UsageRecord.created_at.desc()).limit(50).all())
    return {
        "spent_last_24h_usd": round(spent_today_usd(db, user.id), 6),
        "daily_limit_usd": get_settings().daily_cost_limit_usd,
        "by_model": [
            {"provider": p, "model": m, "status": s, "requests": c, "input_tokens": int(i or 0),
             "output_tokens": int(o or 0), "cost_usd": round(float(cost or 0), 6),
             "avg_latency_ms": int(lat) if lat is not None else None}
            for p, m, s, c, i, o, cost, lat in rows
        ],
        "recent": [
            {"id": str(r.id), "feature": r.feature, "provider": r.provider, "model": r.model, "status": r.status,
             "input_tokens": r.input_tokens, "output_tokens": r.output_tokens, "cost_usd": r.cost_usd,
             "latency_ms": r.latency_ms, "error_code": r.error_code, "error_message": r.error_message,
             "created_at": r.created_at.isoformat()}
            for r in recent
        ],
    }
