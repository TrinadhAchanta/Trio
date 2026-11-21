"""Evaluation results (read-only): latest runs per suite and user satisfaction from ratings."""

from fastapi import APIRouter, Depends
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import EvalRun, Message, User
from ..security import get_current_user

router = APIRouter(prefix="/api/evals", tags=["evals"])


@router.get("/summary")
def eval_summary(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    latest = {}
    for suite in ("routing", "quality"):
        run = db.scalars(select(EvalRun).where(EvalRun.suite == suite).order_by(EvalRun.created_at.desc()).limit(1)).first()
        if run:
            latest[suite] = {"id": str(run.id), "dataset_version": run.dataset_version, "models": run.models,
                             "summary": run.summary, "created_at": run.created_at.isoformat()}
    # Satisfaction across all users (aggregated, no per-user data): share of thumbs-up among rated replies.
    rows = db.execute(
        select(Message.provider, Message.model, func.count(),
               func.sum(case((Message.rating > 0, 1), else_=0)))
        .where(Message.rating.is_not(None), Message.provider.is_not(None))
        .group_by(Message.provider, Message.model)
    ).all()
    satisfaction = [{"model": f"{p}:{m}", "ratings": n, "thumbs_up_rate": round(int(up or 0) / n, 3)}
                    for p, m, n, up in rows]
    return {"latest": latest, "satisfaction": satisfaction,
            "note": "Small internal datasets; results are indicative, not a published benchmark."}
