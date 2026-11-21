"""Trio Router, provider health / circuit breaking, evidence and model fallback."""

from app.config import Settings
from app.db import SessionLocal
from app.models import Message, UsageRecord
from app.providers.registry import Registry
from app.services import health as health_mod
from app.services.health import HealthMonitor
from app.services.router import Evidence, classify_task, rank_models

from .conftest import parse_sse


def real_registry():
    return Registry(Settings(anthropic_api_key="k", openai_api_key="k", gemini_api_key="", xai_api_key=""))


def models():
    return real_registry().available_models()


# ---- classifier ----
def test_classify_task():
    assert classify_task("Fix this Python function, it throws an exception")[0] == "code"
    assert classify_task("Solve the equation 3x + 2 = 11")[0] == "math"
    assert classify_task("Write a cover letter for a design job")[0] == "writing"
    assert classify_task("Extract the names as JSON")[0] == "extraction"
    assert classify_task("Summarize and compare these two plans")[0] == "analysis"
    assert classify_task("hey there")[0] == "chat"
    assert classify_task("what is this?", needs_vision=True)[0] == "analysis"


# ---- ranking ----
def test_mode_changes_the_choice():
    fast = rank_models(models(), "hey there", "fast")
    deep = rank_models(models(), "Prove that the square root of 2 is irrational", "deep")
    assert fast.model.tier == "fast"
    assert deep.model.tier == "deep" and deep.task_type == "math"
    assert all(c.model.provider != "mock" for c in deep.ranked[:3])  # mock is always last-resort
    assert deep.reason.startswith("Math task: chose ")


def test_allowed_and_preferred_providers():
    d = rank_models(models(), "refactor this function", "balanced", allowed_providers=["openai"])
    assert {c.model.provider for c in d.ranked} <= {"openai", "mock"}
    d1 = rank_models(models(), "hey", "balanced")
    other = "openai" if d1.model.provider == "anthropic" else "anthropic"
    d2 = rank_models(models(), "hey", "balanced", preferred_providers=[other])
    assert d2.ranked[0].utility >= d1.ranked[0].utility - 0.2  # bonus applied without errors
    assert any(c.model.provider == other for c in d2.ranked[:2])


def test_evidence_moves_quality_but_not_from_a_few_votes():
    ms = models()
    target = next(m for m in ms if m.tier == "fast" and m.provider == "openai")
    base = rank_models(ms, "hey", "balanced")
    q0 = next(c.quality for c in base.ranked if c.model.id == target.id)
    ev = Evidence()
    ev.add(target.id, "chat", 1.0)
    one = next(c.quality for c in rank_models(ms, "hey", "balanced", evidence=ev).ranked if c.model.id == target.id)
    assert q0 < one < q0 + 0.1  # one thumbs-up nudges, does not swing
    for _ in range(50):
        ev.add(target.id, "chat", 1.0)
    many = next(c.quality for c in rank_models(ms, "hey", "balanced", evidence=ev).ranked if c.model.id == target.id)
    assert many > 0.9


def test_down_provider_is_skipped_and_degraded_penalized():
    t = [0.0]
    h = HealthMonitor(clock=lambda: t[0])
    for _ in range(3):
        h.record("anthropic", "claude-opus-5-5", False, retryable=True, error="provider_unavailable")
    assert h.state("anthropic") == "down"
    d = rank_models(models(), "prove this theorem", "deep", health=h)
    assert all(c.model.provider != "anthropic" for c in d.ranked)
    t[0] = 61.0
    assert h.state("anthropic") == "recovering"
    d = rank_models(models(), "prove this theorem", "deep", health=h)
    assert any(c.model.provider == "anthropic" and c.state == "recovering" for c in d.ranked)
    h.record("anthropic", "claude-opus-5-5", True, 900)
    assert h.state("anthropic") == "degraded"  # 3 of the last 4 failed
    for _ in range(10):
        h.record("anthropic", "claude-opus-5-5", True, 900)
    assert h.state("anthropic") == "healthy"
    assert h.p50_latency("anthropic", "claude-opus-5-5") == 900


def test_non_retryable_errors_do_not_open_the_circuit():
    h = HealthMonitor()
    for _ in range(5):
        h.record("openai", "gpt-5", False, retryable=False, error="invalid_request")
    assert h.state("openai") != "down"


# ---- fallback end to end (mock provider) ----
def _send(c, content, model="mock:mock-echo"):
    cid = c.post("/api/conversations", json={}).json()["id"]
    r = c.post(f"/api/conversations/{cid}/messages", json={"content": content, "model": model})
    assert r.status_code == 200
    return parse_sse(r.text)


def test_fallback_to_next_model_when_first_fails(authed):
    events = _send(authed, "hello [[mock-error:mock-echo]]")
    kinds = [e for e, _ in events]
    assert "fallback" in kinds and kinds[-1] == "done"
    fb = dict(events)["fallback"]
    assert fb["from"] == "mock:mock-echo" and fb["to"] == "mock:mock-deep" and fb["code"] == "provider_unavailable"
    with SessionLocal() as db:
        msg = db.query(Message).filter(Message.role == "assistant").one()
        assert msg.status == "complete" and msg.model == "mock-deep" and msg.task_type == "chat"
        assert msg.notice and "Mock Deep" in msg.notice
        assert sorted(u.status for u in db.query(UsageRecord).all()) == ["error", "ok"]
    snap = health_mod.health.snapshot()["mock"]
    assert snap["samples"] == 2


def test_fallback_can_be_turned_off(authed):
    authed.patch("/api/auth/me/preferences", json={"allow_fallback": False})
    events = _send(authed, "hello [[mock-error:mock-echo]]")
    assert "fallback" not in [e for e, _ in events] and events[-1][0] == "error"


def test_no_fallback_after_text_was_streamed(authed):
    events = _send(authed, "go [[mock-error-mid]]")
    assert "fallback" not in [e for e, _ in events] and events[-1][0] == "error"


def test_status_endpoint(authed):
    _send(authed, "hi")
    data = authed.get("/api/status").json()
    assert data["providers"]["mock"]["state"] == "healthy"
    assert data["providers"]["mock"]["success_rate"] == 1.0
