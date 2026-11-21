"""Deep research jobs: start, poll, cancel, delete, export."""

import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import ResearchJob, User
from ..security import get_current_user, primary_org_id
from ..services.research import run_research
from ..services.search import get_search_provider
from .chat import _precheck, resolve_model

router = APIRouter(prefix="/api/research", tags=["research"])


class ResearchIn(BaseModel):
    question: str = Field(min_length=5, max_length=2000)
    model: str = "auto"


def _out(j: ResearchJob, full: bool = True) -> dict:
    out = {"id": str(j.id), "question": j.question, "status": j.status, "model": j.model, "used_model": j.used_model,
           "search_provider": j.search_provider, "demo": j.demo, "error": j.error,
           "created_at": j.created_at.isoformat(), "updated_at": j.updated_at.isoformat()}
    if full:
        out.update({"steps": j.steps, "queries": j.queries, "sources": j.sources, "report": j.report, "stats": j.stats})
    return out


def _get(db: Session, user: User, job_id: uuid.UUID) -> ResearchJob:
    j = db.get(ResearchJob, job_id)
    if not j or j.user_id != user.id:
        raise HTTPException(status_code=404, detail="Research not found.")
    return j


@router.get("/config")
def research_config(user: User = Depends(get_current_user)):
    s = get_search_provider()
    return {"available": s is not None, "provider": s.label if s else None, "demo": bool(s and s.demo)}


@router.post("", status_code=201)
async def start(body: ResearchIn, background: BackgroundTasks, user: User = Depends(get_current_user),
                db: Session = Depends(get_db)):
    _precheck(db, user)
    if not get_search_provider():
        raise HTTPException(status_code=503, detail="Web search is not configured on this server. "
                                                    "Set BRAVE_SEARCH_API_KEY or TAVILY_API_KEY.")
    plan = resolve_model(body.model, body.question, "deep", False, user, db)
    job = ResearchJob(user_id=user.id, org_id=primary_org_id(db, user), question=body.question.strip(), model=body.model)
    db.add(job)
    db.commit()
    db.refresh(job)
    background.add_task(run_research, job.id, plan.attempts)
    return _out(job)


@router.get("")
def list_jobs(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    jobs = db.scalars(select(ResearchJob).where(ResearchJob.user_id == user.id)
                      .order_by(ResearchJob.created_at.desc()).limit(100)).all()
    return [_out(j, full=False) for j in jobs]


@router.get("/{job_id}")
def get_job(job_id: uuid.UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return _out(_get(db, user, job_id))


@router.post("/{job_id}/cancel")
def cancel(job_id: uuid.UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    j = _get(db, user, job_id)
    if j.status in ("queued", "running"):
        j.status = "cancelled"
        j.steps = [*j.steps, {"at": j.updated_at.isoformat(), "message": "Cancelled"}]
        db.commit()
    return _out(j)


@router.delete("/{job_id}", status_code=204)
def delete(job_id: uuid.UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    db.delete(_get(db, user, job_id))
    db.commit()


@router.get("/{job_id}/export.md", response_class=PlainTextResponse)
def export_markdown(job_id: uuid.UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    j = _get(db, user, job_id)
    if j.status != "done":
        raise HTTPException(status_code=409, detail="The report is not finished yet.")
    lines = [f"# {j.question}", "", j.report or "", "", "## Sources", ""]
    lines += [f"{s['n']}. [{s['title']}]({s['url']})" for s in j.sources]
    if j.demo:
        lines += ["", "_Demo search: these sources are built-in example pages, not real web results._"]
    return PlainTextResponse("\n".join(lines), headers={
        "Content-Disposition": f'attachment; filename="research-{str(j.id)[:8]}.md"'})
