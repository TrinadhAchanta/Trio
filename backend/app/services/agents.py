"""System prompt assembly for agents and memory."""

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Agent, Conversation, MemoryItem, User
from .artifacts import ARTIFACT_SYSTEM_PROMPT

MAX_MEMORY_ITEMS = 50
MAX_MEMORY_CHARS = 6000


def memory_enabled(user: User) -> bool:
    return bool((user.preferences or {}).get("memory_enabled", True))


def memory_block(db: Session, user: User) -> str:
    items = db.scalars(select(MemoryItem).where(MemoryItem.user_id == user.id)
                       .order_by(MemoryItem.created_at.desc()).limit(MAX_MEMORY_ITEMS)).all()
    if not items:
        return ""
    lines, total = [], 0
    for m in items:
        if total + len(m.content) > MAX_MEMORY_CHARS:
            break
        lines.append(f"- {m.content}")
        total += len(m.content)
    return ("\n\n<user_memory>\nThings the user asked you to remember about them. Use them when relevant; "
            "do not mention this list unless asked.\n" + "\n".join(lines) + "\n</user_memory>")


def conversation_agent(db: Session, user: User, conv: Conversation) -> Agent | None:
    if not conv.agent_id:
        return None
    a = db.get(Agent, conv.agent_id)
    return a if a and a.user_id == user.id else None


def system_prompt(db: Session, user: User, agent: Agent | None) -> str:
    s = ARTIFACT_SYSTEM_PROMPT
    if agent and agent.instructions.strip():
        s += (f"\n\n<agent_instructions name=\"{agent.name}\">\nThe user configured you as this assistant. Follow these "
              f"instructions unless they conflict with safety rules.\n{agent.instructions.strip()}\n</agent_instructions>")
    if memory_enabled(user) and (agent is None or agent.use_memory):
        s += memory_block(db, user)
    return s


def agent_kb_ids(agent: Agent | None) -> list[uuid.UUID]:
    out = []
    for i in (agent.knowledge_base_ids if agent else []):
        try:
            out.append(uuid.UUID(str(i)))
        except ValueError:
            continue
    return out
