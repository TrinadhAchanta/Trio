"""Personal agents (reusable assistants) and the user's memory."""

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Agent, KnowledgeBase, MemoryItem, Message, User
from ..schemas import Reasoning
from ..security import audit, get_current_user, primary_org_id

router = APIRouter(prefix="/api", tags=["agents"])


class AgentIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    description: str | None = Field(default=None, max_length=300)
    instructions: str = Field(default="", max_length=20_000)
    model: str = Field(default="auto", max_length=160)
    reasoning_mode: Reasoning = "balanced"
    knowledge_base_ids: list[uuid.UUID] = Field(default_factory=list, max_length=10)
    starters: list[str] = Field(default_factory=list, max_length=6)
    use_memory: bool = True


class MemoryIn(BaseModel):
    content: str | None = Field(default=None, min_length=1, max_length=1000)
    message_id: uuid.UUID | None = None


def _agent_out(a: Agent) -> dict:
    return {"id": str(a.id), "name": a.name, "description": a.description, "instructions": a.instructions,
            "model": a.model, "reasoning_mode": a.reasoning_mode, "knowledge_base_ids": a.knowledge_base_ids,
            "starters": a.starters, "use_memory": a.use_memory, "created_at": a.created_at.isoformat(),
            "updated_at": a.updated_at.isoformat()}


def _owned_agent(db: Session, user: User, agent_id: uuid.UUID) -> Agent:
    a = db.get(Agent, agent_id)
    if not a or a.user_id != user.id:
        raise HTTPException(status_code=404, detail="Agent not found.")
    return a


def _apply(db: Session, user: User, a: Agent, body: AgentIn) -> None:
    kb_ids = [str(i) for i in body.knowledge_base_ids]
    if kb_ids:
        owned = {str(i) for i in db.scalars(select(KnowledgeBase.id).where(KnowledgeBase.id.in_(body.knowledge_base_ids),
                                                                            KnowledgeBase.user_id == user.id))}
        if set(kb_ids) - owned:
            raise HTTPException(status_code=400, detail="One or more knowledge bases were not found.")
    a.name, a.description, a.instructions = body.name.strip(), body.description, body.instructions
    a.model, a.reasoning_mode, a.knowledge_base_ids = body.model, body.reasoning_mode, kb_ids
    a.starters = [s.strip()[:200] for s in body.starters if s.strip()]
    a.use_memory = body.use_memory


@router.get("/agents")
def list_agents(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return [_agent_out(a) for a in db.scalars(select(Agent).where(Agent.user_id == user.id).order_by(Agent.name)).all()]


@router.post("/agents", status_code=201)
def create_agent(body: AgentIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    a = Agent(user_id=user.id, org_id=primary_org_id(db, user))
    _apply(db, user, a, body)
    db.add(a)
    db.commit()
    db.refresh(a)
    return _agent_out(a)


@router.get("/agents/{agent_id}")
def get_agent(agent_id: uuid.UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return _agent_out(_owned_agent(db, user, agent_id))


@router.put("/agents/{agent_id}")
def update_agent(agent_id: uuid.UUID, body: AgentIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    a = _owned_agent(db, user, agent_id)
    _apply(db, user, a, body)
    db.commit()
    return _agent_out(a)


@router.delete("/agents/{agent_id}", status_code=204)
def delete_agent(agent_id: uuid.UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    db.delete(_owned_agent(db, user, agent_id))
    db.commit()


# ---- memory ----
@router.get("/memory")
def list_memory(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    items = db.scalars(select(MemoryItem).where(MemoryItem.user_id == user.id).order_by(MemoryItem.created_at.desc())).all()
    return {"enabled": bool((user.preferences or {}).get("memory_enabled", True)),
            "items": [{"id": str(m.id), "content": m.content, "source": m.source, "created_at": m.created_at.isoformat()}
                      for m in items]}


@router.post("/memory", status_code=201)
def add_memory(body: MemoryIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if bool(body.content) == bool(body.message_id):
        raise HTTPException(status_code=422, detail="Give either content or message_id.")
    content, source = body.content, "manual"
    if body.message_id:
        msg = db.get(Message, body.message_id)
        if not msg or msg.conversation.user_id != user.id or msg.role != "user":
            raise HTTPException(status_code=404, detail="Message not found.")
        content, source = msg.content[:1000], "message"
    if db.query(MemoryItem).filter(MemoryItem.user_id == user.id).count() >= 200:
        raise HTTPException(status_code=400, detail="Memory is full (200 items). Delete some first.")
    m = MemoryItem(user_id=user.id, content=content.strip(), source=source)  # type: ignore[union-attr]
    db.add(m)
    db.commit()
    return {"id": str(m.id), "content": m.content, "source": m.source, "created_at": m.created_at.isoformat()}


@router.delete("/memory/{item_id}", status_code=204)
def delete_memory(item_id: uuid.UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    m = db.get(MemoryItem, item_id)
    if not m or m.user_id != user.id:
        raise HTTPException(status_code=404, detail="Memory item not found.")
    db.delete(m)
    db.commit()


@router.delete("/memory", status_code=204)
def clear_memory(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    db.execute(delete(MemoryItem).where(MemoryItem.user_id == user.id))
    audit(db, "memory.cleared", user_id=user.id)
    db.commit()
