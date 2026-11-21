"""Knowledge bases: create, add documents (uploaded files or pasted text), search."""

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import FileObject, KBDocument, KnowledgeBase, User
from ..security import audit, get_current_user, primary_org_id
from ..services import knowledge as kb_svc

router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])


class KBIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)


class KBPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)


class DocIn(BaseModel):
    file_id: uuid.UUID | None = None
    title: str | None = Field(default=None, max_length=255)
    text: str | None = Field(default=None, max_length=kb_svc.MAX_DOC_CHARS)

    @model_validator(mode="after")
    def one_source(self):
        if bool(self.file_id) == bool(self.text):
            raise ValueError("Give either file_id or text.")
        if self.text and not (self.title or "").strip():
            raise ValueError("A title is required for pasted text.")
        return self


class SearchIn(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    k: int = Field(default=6, ge=1, le=20)


def _doc_out(d: KBDocument) -> dict:
    return {"id": str(d.id), "title": d.title, "source": d.source, "file_id": str(d.file_id) if d.file_id else None,
            "chars": d.chars, "chunk_count": d.chunk_count, "created_at": d.created_at.isoformat()}


def _kb_out(kb: KnowledgeBase, docs: bool = False) -> dict:
    out = {"id": str(kb.id), "name": kb.name, "description": kb.description,
           "document_count": len(kb.documents), "chunk_count": sum(d.chunk_count for d in kb.documents),
           "created_at": kb.created_at.isoformat(), "updated_at": kb.updated_at.isoformat()}
    if docs:
        out["documents"] = [_doc_out(d) for d in kb.documents]
    return out


def _get(db: Session, user: User, kb_id: uuid.UUID) -> KnowledgeBase:
    kb = kb_svc.owned_kb(db, user, kb_id)
    if not kb:
        raise HTTPException(status_code=404, detail="Knowledge base not found.")
    return kb


@router.get("")
def list_kbs(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    kbs = db.scalars(select(KnowledgeBase).where(KnowledgeBase.user_id == user.id)
                     .order_by(KnowledgeBase.updated_at.desc())).all()
    return [_kb_out(k) for k in kbs]


@router.post("", status_code=201)
def create_kb(body: KBIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    kb = KnowledgeBase(user_id=user.id, org_id=primary_org_id(db, user), name=body.name.strip(),
                       description=body.description)
    db.add(kb)
    db.commit()
    db.refresh(kb)
    return _kb_out(kb, docs=True)


@router.get("/{kb_id}")
def get_kb(kb_id: uuid.UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return _kb_out(_get(db, user, kb_id), docs=True)


@router.patch("/{kb_id}")
def patch_kb(kb_id: uuid.UUID, body: KBPatch, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    kb = _get(db, user, kb_id)
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(kb, k, v)
    db.commit()
    return _kb_out(kb, docs=True)


@router.delete("/{kb_id}", status_code=204)
def delete_kb(kb_id: uuid.UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    kb = _get(db, user, kb_id)
    audit(db, "knowledge.deleted", user_id=user.id, org_id=kb.org_id, target_type="knowledge_base", target_id=str(kb.id))
    db.delete(kb)
    db.commit()


@router.post("/{kb_id}/documents", status_code=201)
def add_document(kb_id: uuid.UUID, body: DocIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    kb = _get(db, user, kb_id)
    if body.file_id:
        f = db.get(FileObject, body.file_id)
        if not f or f.user_id != user.id:
            raise HTTPException(status_code=404, detail="File not found.")
        if not f.extracted_text:
            raise HTTPException(status_code=400, detail="Only text, code, CSV, JSON and PDF files with readable text "
                                                        "can be added to a knowledge base.")
        doc = kb_svc.add_document(db, kb, body.title or f.filename, f.extracted_text, "upload", f.id)
    else:
        doc = kb_svc.add_document(db, kb, body.title.strip(), body.text, "text")  # type: ignore[union-attr,arg-type]
    kb.updated_at = datetime.now(kb.updated_at.tzinfo)
    db.commit()
    return _doc_out(doc)


@router.delete("/{kb_id}/documents/{doc_id}", status_code=204)
def delete_document(kb_id: uuid.UUID, doc_id: uuid.UUID, user: User = Depends(get_current_user),
                    db: Session = Depends(get_db)):
    kb = _get(db, user, kb_id)
    doc = db.get(KBDocument, doc_id)
    if not doc or doc.kb_id != kb.id:
        raise HTTPException(status_code=404, detail="Document not found.")
    db.delete(doc)
    db.commit()


@router.post("/{kb_id}/search")
def search_kb(kb_id: uuid.UUID, body: SearchIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _get(db, user, kb_id)
    return {"results": kb_svc.search(db, user, [kb_id], body.query, body.k)}
