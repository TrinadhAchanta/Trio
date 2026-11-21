"""Provider abstraction shared by every model vendor.

Each adapter turns our neutral ``ChatMessage`` list into the vendor's official API request
and yields neutral ``StreamEvent`` objects back. Routers never import vendor SDKs directly.
"""

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Literal

ReasoningMode = Literal["fast", "balanced", "deep"]


@dataclass
class ImageInput:
    media_type: str  # image/png, image/jpeg, image/gif, image/webp
    data_b64: str


@dataclass
class ChatMessage:
    role: Literal["user", "assistant"]
    content: str
    images: list[ImageInput] = field(default_factory=list)


@dataclass
class StreamEvent:
    type: Literal["text", "usage"]
    text: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    stop_reason: str | None = None


@dataclass
class ModelInfo:
    provider: str
    model: str
    label: str
    tier: Literal["fast", "balanced", "deep"]
    vision: bool = False
    context_window: int | None = None
    input_price_per_mtok: float | None = None  # USD; None = unknown, cost shown as n/a
    output_price_per_mtok: float | None = None
    # "documented" = taken from vendor docs/skill reference; "catalog-default" = editable default
    # that should be checked against the vendor's live model list once a key is configured.
    source: str = "catalog-default"

    @property
    def id(self) -> str:
        return f"{self.provider}:{self.model}"

    def cost(self, input_tokens: int, output_tokens: int) -> float | None:
        if self.input_price_per_mtok is None or self.output_price_per_mtok is None:
            return None
        return (input_tokens * self.input_price_per_mtok + output_tokens * self.output_price_per_mtok) / 1_000_000


class ProviderError(Exception):
    """Normalized provider failure. ``code`` is stable and safe to show to users."""

    def __init__(self, code: str, message: str, *, retryable: bool = False, status: int | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.status = status


def status_to_code(status: int | None) -> tuple[str, bool]:
    if status is None:
        return "provider_unavailable", True
    if status in (401, 403):
        return "provider_auth_failed", False
    if status == 404:
        return "model_not_found", False
    if status == 429:
        return "provider_rate_limited", True
    if status == 400 or status == 422:
        return "provider_bad_request", False
    if status >= 500:
        return "provider_unavailable", True
    return "provider_error", False


class Provider(ABC):
    name: str
    label: str

    @abstractmethod
    def configured(self) -> bool: ...

    @abstractmethod
    def models(self) -> list[ModelInfo]: ...

    @abstractmethod
    def stream(
        self,
        model: str,
        messages: list[ChatMessage],
        *,
        system: str | None,
        max_tokens: int,
        reasoning: ReasoningMode,
    ) -> AsyncIterator[StreamEvent]: ...

    async def list_remote_models(self) -> list[str] | None:
        """Return model ids the vendor reports for this key, or None if unsupported/unavailable."""
        return None
