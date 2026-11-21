"""Adapter tests against recorded wire formats (no network, no API keys).

These check that each adapter sends a well-formed request to the vendor's official endpoint and
parses the vendor's streaming format. They do NOT prove a live key works; that needs a real key.
"""

import json

import httpx
import httpx2
import pytest

from app.providers.anthropic_provider import AnthropicProvider
from app.providers.base import ChatMessage, ImageInput, ProviderError
from app.providers.catalog import load_catalog
from app.providers.gemini_provider import GeminiProvider
from app.providers.openai_provider import OpenAIProvider, XAIProvider

CATALOG = load_catalog()


def anthropic_sse(text_chunks, model="claude-haiku-5-5", stop="end_turn"):
    events = [
        ("message_start", {"type": "message_start", "message": {
            "id": "msg_1", "type": "message", "role": "assistant", "model": model, "content": [],
            "stop_reason": None, "stop_sequence": None, "usage": {"input_tokens": 12, "output_tokens": 1}}}),
        ("content_block_start", {"type": "content_block_start", "index": 0,
                                 "content_block": {"type": "text", "text": ""}}),
    ]
    for t in text_chunks:
        events.append(("content_block_delta", {"type": "content_block_delta", "index": 0,
                                               "delta": {"type": "text_delta", "text": t}}))
    events += [
        ("content_block_stop", {"type": "content_block_stop", "index": 0}),
        ("message_delta", {"type": "message_delta", "delta": {"stop_reason": stop, "stop_sequence": None},
                           "usage": {"output_tokens": 7}}),
        ("message_stop", {"type": "message_stop"}),
    ]
    return "".join(f"event: {e}\ndata: {json.dumps(d)}\n\n" for e, d in events)


async def collect(gen):
    text, usage = "", None
    async for ev in gen:
        if ev.type == "text":
            text += ev.text
        else:
            usage = ev
    return text, usage


async def test_anthropic_streams_and_reports_usage():
    seen = {}

    def handler(req: httpx2.Request):
        seen["url"] = str(req.url)
        seen["headers"] = dict(req.headers)
        seen["body"] = json.loads(req.content)
        return httpx2.Response(200, headers={"content-type": "text/event-stream"},
                               content=anthropic_sse(["Hel", "lo"]).encode())

    p = AnthropicProvider("sk-test", CATALOG, "https://api.anthropic.com", http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)))
    msgs = [ChatMessage("user", "Describe", images=[ImageInput("image/png", "aGVsbG8=")])]
    text, usage = await collect(p.stream("claude-haiku-5-5", msgs, system="sys", max_tokens=100, reasoning="fast"))
    assert text == "Hello"
    assert (usage.input_tokens, usage.output_tokens, usage.stop_reason) == (12, 7, "end_turn")
    assert seen["url"].endswith("/v1/messages")
    # SDK 2.x sends the key as a bearer token; older SDKs used x-api-key.
    assert seen["headers"].get("x-api-key") == "sk-test" or seen["headers"].get("authorization") == "Bearer sk-test"
    body = seen["body"]
    assert body["model"] == "claude-haiku-5-5" and body["system"] == "sys" and body["stream"] is True
    assert body["output_config"] == {"effort": "low"}
    assert body["messages"][0]["content"][0]["type"] == "image"
    assert "fallbacks" not in body


async def test_anthropic_opus_uses_server_side_fallbacks():
    seen = {}

    def handler(req):
        seen["headers"] = dict(req.headers)
        seen["body"] = json.loads(req.content)
        return httpx2.Response(200, headers={"content-type": "text/event-stream"},
                               content=anthropic_sse(["ok"], model="claude-opus-5-5").encode())

    p = AnthropicProvider("sk-test", CATALOG, "https://api.anthropic.com", http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)))
    text, _ = await collect(p.stream("claude-opus-5-5", [ChatMessage("user", "hi")], system=None, max_tokens=50,
                                     reasoning="deep"))
    assert text == "ok"
    assert seen["body"]["fallbacks"] == "default"
    assert "server-side-fallback-2026-07-01" in seen["headers"].get("anthropic-beta", "")
    assert seen["body"]["output_config"] == {"effort": "high"}


@pytest.mark.parametrize("status,code", [(401, "provider_auth_failed"), (429, "provider_rate_limited"),
                                         (404, "model_not_found")])
async def test_anthropic_errors_are_normalized(status, code):
    def handler(req):
        return httpx2.Response(status, json={"type": "error", "error": {"type": "x", "message": "nope"}},
                               headers={"x-should-retry": "false"})

    p = AnthropicProvider("sk-test", CATALOG, "https://api.anthropic.com", http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)))
    with pytest.raises(ProviderError) as ei:
        await collect(p.stream("claude-haiku-5-5", [ChatMessage("user", "hi")], system=None, max_tokens=10,
                               reasoning="fast"))
    assert ei.value.code == code


def openai_sse(chunks):
    lines = []
    for t in chunks:
        lines.append({"id": "c1", "object": "chat.completion.chunk", "created": 1, "model": "m",
                      "choices": [{"index": 0, "delta": {"content": t}, "finish_reason": None}]})
    lines.append({"id": "c1", "object": "chat.completion.chunk", "created": 1, "model": "m",
                  "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]})
    lines.append({"id": "c1", "object": "chat.completion.chunk", "created": 1, "model": "m", "choices": [],
                  "usage": {"prompt_tokens": 9, "completion_tokens": 3, "total_tokens": 12}})
    return "".join(f"data: {json.dumps(x)}\n\n" for x in lines) + "data: [DONE]\n\n"


@pytest.mark.parametrize("cls,model,base", [(OpenAIProvider, "gpt-5-mini", None),
                                            (XAIProvider, "grok-4", "https://api.x.ai/v1")])
async def test_openai_compatible_streaming(cls, model, base):
    seen = {}

    def handler(req):
        seen["url"] = str(req.url)
        seen["auth"] = req.headers.get("authorization")
        seen["body"] = json.loads(req.content)
        return httpx2.Response(200, headers={"content-type": "text/event-stream"},
                               content=openai_sse(["Hi", " there"]).encode())

    p = cls("key-1", CATALOG, base_url=base or "https://api.openai.com/v1", http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)))
    text, usage = await collect(p.stream(model, [ChatMessage("user", "hello")], system="be nice", max_tokens=64,
                                         reasoning="balanced"))
    assert text == "Hi there"
    assert (usage.input_tokens, usage.output_tokens, usage.stop_reason) == (9, 3, "stop")
    assert seen["url"].endswith("/chat/completions")
    assert seen["auth"] == "Bearer key-1"
    assert seen["body"]["messages"][0] == {"role": "system", "content": "be nice"}
    assert seen["body"]["stream_options"] == {"include_usage": True}
    if cls is OpenAIProvider:
        assert seen["body"]["reasoning_effort"] == "medium"
        assert seen["url"].startswith("https://api.openai.com/")
    else:
        assert "reasoning_effort" not in seen["body"]
        assert seen["url"].startswith("https://api.x.ai/v1/")


async def test_gemini_streaming():
    seen = {}

    def handler(req: httpx.Request):
        seen["url"] = str(req.url)
        seen["key"] = req.headers.get("x-goog-api-key")
        seen["body"] = json.loads(req.content)
        chunks = [
            {"candidates": [{"content": {"parts": [{"text": "Bon"}], "role": "model"}}]},
            {"candidates": [{"content": {"parts": [{"text": "jour"}], "role": "model"}, "finishReason": "STOP"}],
             "usageMetadata": {"promptTokenCount": 5, "candidatesTokenCount": 2}},
        ]
        body = "".join(f"data: {json.dumps(c)}\r\n\r\n" for c in chunks)
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body.encode())

    p = GeminiProvider("g-key", CATALOG, "https://generativelanguage.googleapis.com/v1beta",
                       transport=httpx.MockTransport(handler))
    history = [ChatMessage("user", "hi"), ChatMessage("assistant", "hello"), ChatMessage("user", "French?")]
    text, usage = await collect(p.stream("gemini-2.5-flash", history, system="sys", max_tokens=32,
                                         reasoning="fast"))
    assert text == "Bonjour"
    assert (usage.input_tokens, usage.output_tokens) == (5, 2)
    assert "models/gemini-2.5-flash:streamGenerateContent" in seen["url"] and "alt=sse" in seen["url"]
    assert seen["key"] == "g-key"
    assert [c["role"] for c in seen["body"]["contents"]] == ["user", "model", "user"]
    assert seen["body"]["systemInstruction"]["parts"][0]["text"] == "sys"


async def test_gemini_http_error():
    p = GeminiProvider("g-key", CATALOG, "https://x.test/v1beta",
                       transport=httpx.MockTransport(lambda r: httpx.Response(403, json={})))
    with pytest.raises(ProviderError) as ei:
        await collect(p.stream("gemini-2.5-pro", [ChatMessage("user", "x")], system=None, max_tokens=5,
                               reasoning="fast"))
    assert ei.value.code == "provider_auth_failed"


async def test_unconfigured_provider_raises():
    p = OpenAIProvider("", CATALOG)
    assert not p.configured()
    with pytest.raises(ProviderError) as ei:
        await collect(p.stream("gpt-5", [ChatMessage("user", "x")], system=None, max_tokens=5, reasoning="fast"))
    assert ei.value.code == "provider_not_configured"
