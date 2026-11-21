"""Google Gemini adapter using the official Gemini REST API (``streamGenerateContent`` over SSE).

Uses httpx directly to keep dependencies small; swapping to the ``google-genai`` SDK only
changes this file.
"""

import json
from collections.abc import AsyncIterator

import httpx

from .base import ChatMessage, ModelInfo, Provider, ProviderError, ReasoningMode, StreamEvent, status_to_code


class GeminiProvider(Provider):
    name = "gemini"
    label = "Google Gemini"

    def __init__(self, api_key: str, catalog: list[ModelInfo], base_url: str, transport=None):
        self._transport = transport  # injectable for tests
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._models = [m for m in catalog if m.provider == self.name]

    def configured(self) -> bool:
        return bool(self._api_key)

    def models(self) -> list[ModelInfo]:
        return self._models

    @staticmethod
    def _to_wire(messages: list[ChatMessage]) -> list[dict]:
        contents = []
        for m in messages:
            parts: list[dict] = [{"inline_data": {"mime_type": i.media_type, "data": i.data_b64}} for i in m.images]
            parts.append({"text": m.content or "(image)"})
            contents.append({"role": "model" if m.role == "assistant" else "user", "parts": parts})
        return contents

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
            raise ProviderError("provider_not_configured", "GEMINI_API_KEY is not set on the server.")
        body: dict = {
            "contents": self._to_wire(messages),
            "generationConfig": {"maxOutputTokens": max_tokens},
        }
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}
        url = f"{self._base_url}/models/{model}:streamGenerateContent"
        in_tok = out_tok = 0
        finish = None
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=10.0), transport=self._transport) as client:
                async with client.stream(
                    "POST", url, params={"alt": "sse"}, json=body, headers={"x-goog-api-key": self._api_key}
                ) as resp:
                    if resp.status_code >= 400:
                        code, retryable = status_to_code(resp.status_code)
                        raise ProviderError(code, f"Gemini API error ({resp.status_code})", retryable=retryable,
                                            status=resp.status_code)
                    async for line in resp.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        data = json.loads(line[5:].strip() or "{}")
                        for cand in data.get("candidates", []):
                            for part in cand.get("content", {}).get("parts", []):
                                if part.get("text") and not part.get("thought"):
                                    yield StreamEvent(type="text", text=part["text"])
                            finish = cand.get("finishReason") or finish
                        um = data.get("usageMetadata") or {}
                        in_tok = um.get("promptTokenCount", in_tok)
                        out_tok = um.get("candidatesTokenCount", out_tok)
        except httpx.HTTPError as e:
            raise ProviderError("provider_unavailable", "Could not reach the Gemini API.", retryable=True) from e
        yield StreamEvent(type="usage", input_tokens=in_tok, output_tokens=out_tok, stop_reason=finish)

    async def list_remote_models(self) -> list[str] | None:
        if not self.configured():
            return None
        try:
            async with httpx.AsyncClient(timeout=15.0, transport=self._transport) as client:
                r = await client.get(f"{self._base_url}/models", headers={"x-goog-api-key": self._api_key})
                r.raise_for_status()
                return [m["name"].removeprefix("models/") for m in r.json().get("models", [])]
        except (httpx.HTTPError, KeyError, ValueError):
            return None
