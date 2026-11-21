"""Documents: generate with AI, edit, and download as Word, PDF, PowerPoint, Excel or Markdown."""

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import GeneratedDocument, User
from ..providers.base import ChatMessage, ProviderError
from ..security import get_current_user, primary_org_id
from ..services import documents as doc_svc
from ..services.llm import complete
from .chat import _precheck, resolve_model

router = APIRouter(prefix="/api/documents", tags=["documents"])

Fmt = Literal["docx", "pdf", "pptx", "xlsx", "md"]
Kind = Literal["report", "memo", "letter", "slides", "spreadsheet", "other"]
MAX_MD = 500_000

KIND_GUIDE = {
    "report": "a structured report with a title (# heading), a short summary, sections (## headings) and a conclusion",
    "memo": "a business memo with a title, To/From/Date/Subject lines, and concise sections",
    "letter": "a formal letter with date, greeting, body paragraphs and sign-off",
    "slides": "a slide deck: '# Title' for the first slide, then one '## Slide title' per slide with 3-6 short bullets each",
    "spreadsheet": "one or more Markdown tables with a header row (plain numbers in numeric columns) and a one-line title",
    "other": "a well-structured document with a title and headings",
}
GEN_SYSTEM = """You write documents in Markdown that will be converted to office files.
Write {guide}. Use only Markdown headings, paragraphs, bold/italic, lists, tables and code blocks (no HTML, no images).
Do not invent specific facts, figures, names or quotes the user did not provide; use clearly marked placeholders
like [Company name] instead. Output only the document."""


class ExportIn(BaseModel):
    markdown: str = Field(min_length=1, max_length=MAX_MD)
    format: Fmt
    title: str | None = Field(default=None, max_length=200)


class GenerateIn(BaseModel):
    prompt: str = Field(min_length=3, max_length=20_000)
    kind: Kind = "report"
    model: str = "auto"


class DocPatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    markdown: str | None = Field(default=None, max_length=MAX_MD)


def _file(md: str, fmt: str, title: str | None) -> Response:
    data, media, name = doc_svc.render(md, fmt, title)
    return Response(data, media_type=media, headers={"Content-Disposition": f'attachment; filename="{name}"',
                                                     "Cache-Control": "no-store"})


def _out(d: GeneratedDocument, full: bool = True) -> dict:
    out = {"id": str(d.id), "title": d.title, "kind": d.kind, "model": d.model,
           "created_at": d.created_at.isoformat(), "updated_at": d.updated_at.isoformat()}
    if full:
        out.update({"prompt": d.prompt, "markdown": d.markdown})
    return out


def _get(db: Session, user: User, doc_id: uuid.UUID) -> GeneratedDocument:
    d = db.get(GeneratedDocument, doc_id)
    if not d or d.user_id != user.id:
        raise HTTPException(status_code=404, detail="Document not found.")
    return d


@router.post("/export")
def export(body: ExportIn, user: User = Depends(get_current_user)):
    """Convert any Markdown (e.g. a chat reply) to a file. No AI call, no credits."""
    return _file(body.markdown, body.format, body.title)


@router.post("/generate", status_code=201)
async def generate(body: GenerateIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _precheck(db, user)
    plan = resolve_model(body.model, body.prompt, "balanced", False, user, db)
    org_id = primary_org_id(db, user)
    try:
        out = await complete(plan.attempts, [ChatMessage("user", body.prompt)],
                             system=GEN_SYSTEM.format(guide=KIND_GUIDE[body.kind]), mode="balanced", max_tokens=4096,
                             user_id=user.id, org_id=org_id, feature="documents", task_type="writing")
    except ProviderError as e:
        raise HTTPException(status_code=502, detail=f"The AI model failed: {e.message}") from e
    md = out.text.strip()
    if md.startswith("```") and md.endswith("```"):  # some models wrap the whole document in a fence
        md = md.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    d = GeneratedDocument(user_id=user.id, org_id=org_id, title=doc_svc.title_from(md, body.prompt[:80]),
                          kind=body.kind, prompt=body.prompt, markdown=md, model=out.info.id)
    db.add(d)
    db.commit()
    db.refresh(d)
    return {**_out(d), "notices": out.notices}


@router.get("")
def list_docs(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    docs = db.scalars(select(GeneratedDocument).where(GeneratedDocument.user_id == user.id)
                      .order_by(GeneratedDocument.updated_at.desc()).limit(200)).all()
    return [_out(d, full=False) for d in docs]


@router.get("/{doc_id}")
def get_doc(doc_id: uuid.UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return _out(_get(db, user, doc_id))


@router.patch("/{doc_id}")
def patch_doc(doc_id: uuid.UUID, body: DocPatch, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    d = _get(db, user, doc_id)
    if body.markdown is not None:
        d.markdown = body.markdown
        if body.title is None:  # keep the title in step with the document's first heading
            d.title = doc_svc.title_from(body.markdown, d.title)
    if body.title is not None:
        d.title = body.title
    db.commit()
    return _out(d)


@router.delete("/{doc_id}", status_code=204)
def delete_doc(doc_id: uuid.UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    db.delete(_get(db, user, doc_id))
    db.commit()


@router.get("/{doc_id}/download")
def download(doc_id: uuid.UUID, format: Fmt = "docx", user: User = Depends(get_current_user),
             db: Session = Depends(get_db)):
    d = _get(db, user, doc_id)
    return _file(d.markdown or f"# {d.title}", format, d.title)
