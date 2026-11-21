"""Anthropic adapter (official ``anthropic`` Python SDK, Messages API with streaming)."""

from collections.abc import AsyncIterator

import anthropic

from .base import ChatMessage, ModelInfo, Provider, ProviderError, ReasoningMode, StreamEvent, status_to_code

EFFORT = {"fast": "low", "balanced": "medium", "deep": "high"}
# Models that accept server-side refusal fallbacks (beta). Others are called without it.
FALLBACK_MODELS = {"claude-opus-5-5", "claude-sonnet-5-5"}
FALLBACK_BETA = "server-side-fallback-2026-07-01"


class AnthropicProvider(Provider):
    name = "anthropic"
    label = "Anthropic"

    def __init__(self, api_key: str, catalog: list[ModelInfo], base_url: str | None = None, http_client=None):
        self._http_client = http_client  # injectable for tests
        self._api_key = api_key
        self._base_url = base_url
        self._models = [m for m in catalog if m.provider == self.name]
        self._client: anthropic.AsyncAnthropic | None = None

    def configured(self) -> bool:
        return bool(self._api_key)

    def models(self) -> list[ModelInfo]:
        return self._models

    def _get_client(self) -> anthropic.AsyncAnthropic:
        if self._client is None:
            # api_key passed explicitly so the app never picks up ambient developer credentials.
            self._client = anthropic.AsyncAnthropic(api_key=self._api_key, base_url=self._base_url, max_retries=2,
                                                  http_client=self._http_client)
        return self._client

    @staticmethod
    def _to_wire(messages: list[ChatMessage]) -> list[dict]:
        wire = []
        for m in messages:
            if m.images and m.role == "user":
                blocks: list[dict] = [
                    {"type": "image", "source": {"type": "base64", "media_type": i.media_type, "data": i.data_b64}}
                    for i in m.images
                ]
                blocks.append({"type": "text", "text": m.content or "(image)"})
                wire.append({"role": m.role, "content": blocks})
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
            raise ProviderError("provider_not_configured", "ANTHROPIC_API_KEY is not set on the server.")
        client = self._get_client()
        kwargs: dict = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": self._to_wire(messages),
            "output_config": {"effort": EFFORT[reasoning]},
        }
        if system:
            kwargs["system"] = system
        try:
            if model in FALLBACK_MODELS:
                cm = client.beta.messages.stream(betas=[FALLBACK_BETA], fallbacks="default", **kwargs)
            else:
                cm = client.messages.stream(**kwargs)
            async with cm as stream:
                async for event in stream:
                    if event.type == "text":
                        yield StreamEvent(type="text", text=event.text)
                final = await stream.get_final_message()
            if final.stop_reason == "refusal":
                yield StreamEvent(type="text", text="\n\n_The model declined to answer this request._")
            yield StreamEvent(
                type="usage",
                input_tokens=final.usage.input_tokens or 0,
                output_tokens=final.usage.output_tokens or 0,
                stop_reason=final.stop_reason,
            )
        except anthropic.APIStatusError as e:
            code, retryable = status_to_code(e.status_code)
            raise ProviderError(code, f"Anthropic API error ({e.status_code})", retryable=retryable,
                                status=e.status_code) from e
        except anthropic.APIConnectionError as e:
            raise ProviderError("provider_unavailable", "Could not reach the Anthropic API.", retryable=True) from e

    async def list_remote_models(self) -> list[str] | None:
        if not self.configured():
            return None
        try:
            return [m.id async for m in self._get_client().models.list()]
        except anthropic.APIError:
            return None
