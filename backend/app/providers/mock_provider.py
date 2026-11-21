"""Mock provider for local demos and automated tests.

It never calls a network service and every response is labeled as mock output. Control
markers (for tests): include ``[[mock-error]]`` to raise a provider error, ``[[mock-error-mid]]``
to fail mid-stream.
"""

import asyncio
from collections.abc import AsyncIterator

from .base import ChatMessage, ModelInfo, Provider, ProviderError, ReasoningMode, StreamEvent

ARTIFACT_HTML = """<!doctype html>
<html>
<head>
<meta charset="utf-8">
<style>
  body { font-family: system-ui, sans-serif; display: grid; place-items: center; min-height: 100vh; margin: 0;
         background: linear-gradient(135deg, #0f766e, #4338ca); color: white; }
  .card { background: rgba(255,255,255,.12); padding: 2rem 2.5rem; border-radius: 16px; text-align: center; }
  button { font-size: 1.1rem; padding: .6rem 1.4rem; border-radius: 999px; border: 0; cursor: pointer; }
</style>
</head>
<body>
  <div class="card">
    <h1>Counter</h1>
    <p id="count" style="font-size:3rem;margin:.5rem 0">0</p>
    <button id="inc">Add one</button>
  </div>
  <script>
    let n = 0;
    document.getElementById('inc').onclick = () => {
      n += 1; document.getElementById('count').textContent = n;
    };
  </script>
</body>
</html>"""

ARTIFACT_TRIGGERS = ("html", "web page", "webpage", "website", "app", "artifact", "component", "counter", "landing")


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def build_reply(model: str, prompt: str, history_len: int) -> str:
    lower = prompt.lower()
    header = f"**[Mock response from `{model}`: no real AI provider was called]**\n\n"
    if any(t in lower for t in ARTIFACT_TRIGGERS):
        return (
            header
            + "Here is a small interactive page you can preview in the artifact panel.\n\n"
            + '```html title="Counter app"\n'
            + ARTIFACT_HTML
            + "\n```\n\nClick **Add one** in the preview to try it."
        )
    snippet = prompt.strip().replace("\n", " ")
    if len(snippet) > 160:
        snippet = snippet[:157] + "..."
    return (
        header
        + f"You said: _{snippet}_\n\n"
        + f"This conversation has {history_len} earlier message(s). Configure an API key for OpenAI, "
        + "Anthropic, Google Gemini or xAI on the server to get real answers.\n\n"
        + "| Feature | Status |\n|---|---|\n| Streaming | working |\n| Markdown | working |\n\n"
        + "```python\nprint('syntax highlighting works')\n```"
    )


class MockProvider(Provider):
    name = "mock"
    label = "Mock (demo)"

    def __init__(self, enabled: bool, catalog: list[ModelInfo], delay_s: float = 0.01):
        self._enabled = enabled
        self._models = [m for m in catalog if m.provider == self.name]
        self.delay_s = delay_s

    def configured(self) -> bool:
        return self._enabled

    def models(self) -> list[ModelInfo]:
        return self._models

    async def stream(
        self,
        model: str,
        messages: list[ChatMessage],
        *,
        system: str | None,
        max_tokens: int,
        reasoning: ReasoningMode,
    ) -> AsyncIterator[StreamEvent]:
        if not self._enabled:
            raise ProviderError("provider_not_configured", "The mock provider is disabled.")
        prompt = messages[-1].content if messages else ""
        # Earlier failed turns get merged into the last user message; only test markers in the
        # newest part should trigger simulated failures.
        newest = prompt.rsplit("\n\n", 1)[-1]
        if "[[mock-error]]" in newest or f"[[mock-error:{model}]]" in newest:
            raise ProviderError("provider_unavailable", "Simulated provider outage (mock).", retryable=True,
                                status=503)
        reply = build_reply(model, prompt, len(messages) - 1)
        if model == "mock-deep":
            reply = reply.replace("Mock response", "Mock deep response")
        words = reply.split(" ")
        emitted = 0
        for i, w in enumerate(words):
            if "[[mock-error-mid]]" in newest and i == len(words) // 2:
                raise ProviderError("provider_unavailable", "Simulated mid-stream failure (mock).", retryable=True)
            chunk = w if i == 0 else " " + w
            emitted += len(chunk)
            yield StreamEvent(type="text", text=chunk)
            if self.delay_s:
                await asyncio.sleep(self.delay_s)
        in_text = (system or "") + "".join(m.content for m in messages)
        yield StreamEvent(
            type="usage",
            input_tokens=estimate_tokens(in_text),
            output_tokens=min(max_tokens, estimate_tokens(reply)),
            stop_reason="end_turn",
        )
