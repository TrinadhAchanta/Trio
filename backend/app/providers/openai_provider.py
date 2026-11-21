"""OpenAI adapter (official ``openai`` SDK, Chat Completions streaming).

xAI's API is documented as OpenAI-compatible, so ``XAIProvider`` reuses this adapter with
xAI's base URL and key.
"""

from collections.abc import AsyncIterator

import openai

from .base import ChatMessage, ModelInfo, Provider, ProviderError, ReasoningMode, StreamEvent, status_to_code

REASONING_EFFORT = {"fast": "low", "balanced": "medium", "deep": "high"}


class OpenAICompatibleProvider(Provider):
    name = "openai"
    label = "OpenAI"
    env_var = "OPENAI_API_KEY"
    supports_reasoning_effort = True

    def __init__(self, api_key: str, catalog: list[ModelInfo], base_url: str | None = None, http_client=None):
        self._http_client = http_client  # injectable for tests
        self._api_key = api_key
        self._base_url = base_url
        self._models = [m for m in catalog if m.provider == self.name]
        self._client: openai.AsyncOpenAI | None = None

    def configured(self) -> bool:
        return bool(self._api_key)

    def models(self) -> list[ModelInfo]:
        return self._models

    def _get_client(self) -> openai.AsyncOpenAI:
        if self._client is None:
            self._client = openai.AsyncOpenAI(api_key=self._api_key, base_url=self._base_url, max_retries=2,
                                            http_client=self._http_client)
        return self._client

    @staticmethod
    def _to_wire(messages: list[ChatMessage], system: str | None) -> list[dict]:
        wire: list[dict] = []
        if system:
            wire.append({"role": "system", "content": system})
        for m in messages:
            if m.images and m.role == "user":
                parts: list[dict] = [{"type": "text", "text": m.content or "(image)"}]
                parts += [
                    {"type": "image_url", "image_url": {"url": f"data:{i.media_type};base64,{i.data_b64}"}}
                    for i in m.images
                ]
                wire.append({"role": "user", "content": parts})
            else:
                wire.append({"role": m.role, "content": m.content})
        return wire

    async def stream(
        self,
        model: str,
        messages: list[ChatMessage],
        *,
        system: str | None,
        max_tokens: int,
        reasoning: ReasoningMode,
    ) -> AsyncIterator[StreamEvent]:
        if not self.configured():
            raise ProviderError("provider_not_configured", f"{self.env_var} is not set on the server.")
        kwargs: dict = {
            "model": model,
            "messages": self._to_wire(messages, system),
            "stream": True,
            "stream_options": {"include_usage": True},
            "max_completion_tokens": max_tokens,
        }
        if self.supports_reasoning_effort and model.startswith(("gpt-5", "o")):
            kwargs["reasoning_effort"] = REASONING_EFFORT[reasoning]
        usage = None
        finish = None
        try:
            stream = await self._get_client().chat.completions.create(**kwargs)
            async for chunk in stream:
                if chunk.usage:
                    usage = chunk.usage
                for choice in chunk.choices or []:
                    if choice.delta and choice.delta.content:
                        yield StreamEvent(type="text", text=choice.delta.content)
                    if choice.finish_reason:
                        finish = choice.finish_reason
        except openai.APIStatusError as e:
            code, retryable = status_to_code(e.status_code)
            raise ProviderError(code, f"{self.label} API error ({e.status_code})", retryable=retryable,
                                status=e.status_code) from e
        except openai.APIConnectionError as e:
            raise ProviderError("provider_unavailable", f"Could not reach the {self.label} API.",
                                retryable=True) from e
        yield StreamEvent(
            type="usage",
            input_tokens=(usage.prompt_tokens if usage else 0) or 0,
            output_tokens=(usage.completion_tokens if usage else 0) or 0,
            stop_reason=finish,
        )

    async def list_remote_models(self) -> list[str] | None:
        if not self.configured():
            return None
        try:
            return [m.id async for m in self._get_client().models.list()]
        except openai.APIError:
            return None


class OpenAIProvider(OpenAICompatibleProvider):
    pass


class XAIProvider(OpenAICompatibleProvider):
    name = "xai"
    label = "xAI"
    env_var = "XAI_API_KEY"
    supports_reasoning_effort = False
