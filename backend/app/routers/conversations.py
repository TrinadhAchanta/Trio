import json
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Agent, Conversation, Message, User
from ..schemas import (
    ConversationCreate,
    ConversationDetail,
    ConversationOut,
    ConversationUpdate,
    MessageOut,
    RatingIn,
)
from ..security import audit, client_ip, get_current_user, primary_org_id

router = APIRouter(prefix="/api", tags=["conversations"])


def get_owned_conversation(db: Session, user: User, conversation_id: uuid.UUID) -> Conversation:
    conv = db.get(Conversation, conversation_id)
    # Same 404 for "missing" and "someone else's" so ids can't be probed.
    if not conv or conv.user_id != user.id:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return conv


@router.get("/conversations", response_model=list[ConversationOut])
def list_conversations(
    q: str | None = Query(default=None, max_length=200),
    folder: str | None = Query(default=None, max_length=120),
    archived: bool = False,
    limit: int = Query(default=100, ge=1, le=500),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    stmt = select(Conversation).where(Conversation.user_id == user.id, Conversation.archived == archived)
    if folder:
        stmt = stmt.where(Conversation.folder == folder)
    if q:
        like = f"%{q.replace('%', r'\%').replace('_', r'\_')}%"
        matching = select(Message.conversation_id).where(Message.content.ilike(like, escape="\\"))
        stmt = stmt.where(or_(Conversation.title.ilike(like, escape="\\"), Conversation.id.in_(matching)))
    stmt = stmt.order_by(Conversation.updated_at.desc()).limit(limit)
    return db.scalars(stmt).all()


@router.post("/conversations", response_model=ConversationOut, status_code=201)
def create_conversation(body: ConversationCreate, user: User = Depends(get_current_user),
                        db: Session = Depends(get_db)):
    agent = None
    if body.agent_id:
        agent = db.get(Agent, body.agent_id)
        if not agent or agent.user_id != user.id:
            raise HTTPException(status_code=404, detail="Agent not found.")
    conv = Conversation(
        user_id=user.id,
        org_id=primary_org_id(db, user),
        title=(body.title or "New conversation").strip() or "New conversation",
        folder=body.folder,
        default_model=body.default_model or (agent.model if agent else None),
        reasoning_mode=agent.reasoning_mode if agent else body.reasoning_mode,
        agent_id=agent.id if agent else None,
    )
    db.add(conv)
    db.commit()
    return conv


@router.get("/conversations/{conversation_id}", response_model=ConversationDetail)
def get_conversation(conversation_id: uuid.UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return get_owned_conversation(db, user, conversation_id)


@router.patch("/conversations/{conversation_id}", response_model=ConversationOut)
def update_conversation(conversation_id: uuid.UUID, body: ConversationUpdate,
                        user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    conv = get_owned_conversation(db, user, conversation_id)
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(conv, k, v)
    db.commit()
    return conv


@router.delete("/conversations/{conversation_id}", status_code=204)
def delete_conversation(conversation_id: uuid.UUID, request: Request, user: User = Depends(get_current_user),
                        db: Session = Depends(get_db)):
    conv = get_owned_conversation(db, user, conversation_id)
    audit(db, "conversation.delete", user_id=user.id, org_id=conv.org_id, target_type="conversation",
          target_id=str(conv.id), ip=client_ip(request))
    db.delete(conv)
    db.commit()
    return Response(status_code=204)


@router.get("/conversations/{conversation_id}/export")
def export_conversation(conversation_id: uuid.UUID, format: str = Query(default="md", pattern="^(md|json)$"),
                        user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    conv = get_owned_conversation(db, user, conversation_id)
    safe_title = "".join(c if c.isalnum() or c in "-_ " else "_" for c in conv.title)[:60] or "conversation"
    if format == "json":
        payload = ConversationDetail.model_validate(conv).model_dump(mode="json")
        body, media, ext = json.dumps(payload, indent=2), "application/json", "json"
    else:
        lines = [f"# {conv.title}", ""]
        for m in conv.messages:
            who = "You" if m.role == "user" else f"Assistant ({m.provider}:{m.model})"
            lines += [f"### {who}", "", m.content, ""]
        body, media, ext = "\n".join(lines), "text/markdown", "md"
    return Response(body, media_type=media,
                    headers={"Content-Disposition": f'attachment; filename="{safe_title}.{ext}"'})


@router.post("/messages/{message_id}/rating", response_model=MessageOut)
def rate_message(message_id: uuid.UUID, body: RatingIn, user: User = Depends(get_current_user),
                 db: Session = Depends(get_db)):
    msg = db.get(Message, message_id)
    if not msg or msg.conversation.user_id != user.id or msg.role != "assistant":
        raise HTTPException(status_code=404, detail="Message not found")
    msg.rating = body.rating
    db.commit()
    return msg
