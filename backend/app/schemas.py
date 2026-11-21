import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

Reasoning = Literal["fast", "balanced", "deep"]


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ---- auth ----
class RegisterIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=10, max_length=200)
    display_name: str = Field(min_length=1, max_length=120)

    @field_validator("password")
    @classmethod
    def strong_enough(cls, v: str) -> str:
        if v.isdigit() or v.isalpha() or v.lower() == v and not any(c.isdigit() for c in v):
            raise ValueError("Use at least 10 characters with a mix of letters and numbers.")
        return v


class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=200)


class UserOut(ORM):
    id: uuid.UUID
    email: str
    display_name: str
    preferences: dict
    created_at: datetime


class PreferencesIn(BaseModel):
    default_model: str | None = None
    reasoning_mode: Reasoning | None = None
    theme: Literal["light", "dark", "system"] | None = None
    allowed_providers: list[str] | None = None  # empty list = all providers allowed
    preferred_providers: list[str] | None = None
    allow_fallback: bool | None = None
    memory_enabled: bool | None = None


# ---- conversations ----
class ConversationCreate(BaseModel):
    title: str | None = Field(default=None, max_length=300)
    folder: str | None = Field(default=None, max_length=120)
    default_model: str | None = None
    reasoning_mode: Reasoning = "balanced"
    agent_id: uuid.UUID | None = None


class ConversationUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=300)
    folder: str | None = Field(default=None, max_length=120)
    archived: bool | None = None
    default_model: str | None = None
    reasoning_mode: Reasoning | None = None


class ConversationOut(ORM):
    id: uuid.UUID
    title: str
    folder: str | None
    default_model: str | None
    reasoning_mode: str
    archived: bool
    agent_id: uuid.UUID | None = None
    created_at: datetime
    updated_at: datetime


class MessageOut(ORM):
    id: uuid.UUID
    conversation_id: uuid.UUID
    parent_id: uuid.UUID | None
    role: str
    content: str
    provider: str | None
    model: str | None
    status: str
    error: str | None
    attachments: list
    sources: list = Field(default_factory=list)
    input_tokens: int | None
    output_tokens: int | None
    latency_ms: int | None
    rating: int | None
    notice: str | None = None
    created_at: datetime


class ConversationDetail(ConversationOut):
    messages: list[MessageOut]


class SendMessageIn(BaseModel):
    content: str = Field(min_length=1, max_length=100_000)
    model: str = "auto"
    reasoning_mode: Reasoning = "balanced"
    attachments: list[uuid.UUID] = Field(default_factory=list, max_length=10)
    knowledge_base_ids: list[uuid.UUID] = Field(default_factory=list, max_length=10)
    # For edits/branches: the message the new user message should follow. None = after the
    # latest message on the current path (computed server-side).
    parent_id: uuid.UUID | None = None
    branch_from_root: bool = False


class RegenerateIn(BaseModel):
    user_message_id: uuid.UUID
    model: str = "auto"
    reasoning_mode: Reasoning = "balanced"


class RatingIn(BaseModel):
    rating: Literal[-1, 1] | None


# ---- files ----
class FileOut(ORM):
    id: uuid.UUID
    filename: str
    content_type: str
    kind: str
    size_bytes: int
    created_at: datetime


# ---- artifacts ----
class ArtifactVersionOut(ORM):
    version: int
    content: str
    source: str
    created_at: datetime


class ArtifactOut(ORM):
    id: uuid.UUID
    conversation_id: uuid.UUID | None
    message_id: uuid.UUID | None
    title: str
    kind: str
    current_version: int
    created_at: datetime
    updated_at: datetime


class ArtifactDetail(ArtifactOut):
    versions: list[ArtifactVersionOut]


class ArtifactEditIn(BaseModel):
    content: str = Field(min_length=1, max_length=500_000)
    title: str | None = Field(default=None, max_length=300)


class ArtifactRestoreIn(BaseModel):
    version: int = Field(ge=1)


# ---- compare ----
class CompareIn(BaseModel):
    prompt: str = Field(min_length=1, max_length=50_000)
    models: list[str] = Field(min_length=2, max_length=4)
    reasoning_mode: Reasoning = "balanced"


class CompareAnswer(BaseModel):
    model: str
    text: str = Field(max_length=200_000)


class SynthesizeIn(BaseModel):
    prompt: str = Field(min_length=1, max_length=50_000)
    answers: list[CompareAnswer] = Field(min_length=2, max_length=4)
    model: str = "auto"


class CompareVoteIn(BaseModel):
    prompt: str = Field(max_length=50_000)
    preferred_model: str
    models: list[str]
