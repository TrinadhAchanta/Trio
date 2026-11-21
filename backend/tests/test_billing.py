"""Billing tests. Stripe is never called: checkout/portal calls are replaced with fakes and webhook
payloads are signed locally with the same scheme Stripe uses."""

import hashlib
import hmac
import json
import time
from types import SimpleNamespace

import pytest

from app.config import get_settings
from app.db import SessionLocal
from app.models import AuditEvent, BillingRecord, Subscription, UsageRecord
from app.services import billing as billing_svc

from .conftest import make_client, parse_sse, register

WEBHOOK_SECRET = "whsec_test_secret"


def sign(payload: str, secret: str = WEBHOOK_SECRET, ts: int | None = None) -> str:
    ts = ts or int(time.time())
    mac = hmac.new(secret.encode(), f"{ts}.{payload}".encode(), hashlib.sha256).hexdigest()
    return f"t={ts},v1={mac}"


def post_event(client, event: dict, secret: str = WEBHOOK_SECRET, ts: int | None = None):
    payload = json.dumps(event)
    return client.post("/api/billing/webhook", content=payload,
                       headers={"stripe-signature": sign(payload, secret, ts), "content-type": "application/json"})


@pytest.fixture
def stripe_on(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "stripe_secret_key", "sk_test_123")
    monkeypatch.setattr(s, "stripe_webhook_secret", WEBHOOK_SECRET)
    monkeypatch.setenv("STRIPE_PRICE_PRO", "price_pro_test")
    monkeypatch.setenv("STRIPE_PRICE_BUSINESS", "price_business_test")
    calls = {}

    class FakeSessions:
        def __init__(self, kind):
            self.kind = kind

        def create(self, params=None, options=None):
            calls[self.kind] = params
            return SimpleNamespace(url=f"https://checkout.stripe.test/{self.kind}")

    class FakeClient:
        def __init__(self, key):
            calls["key"] = key
            self.v1 = SimpleNamespace(checkout=SimpleNamespace(sessions=FakeSessions("checkout")),
                                      billing_portal=SimpleNamespace(sessions=FakeSessions("portal")))

    monkeypatch.setattr(billing_svc.stripe, "StripeClient", FakeClient)
    return calls


def org_id_of(client) -> str:
    me = client.get("/api/billing/subscription")
    assert me.status_code == 200
    with SessionLocal() as db:
        return str(db.query(Subscription).one().org_id)


def test_plans_come_from_config(client):
    data = client.get("/api/billing/plans").json()
    ids = [p["id"] for p in data["plans"]]
    assert ids == ["free", "plus", "pro", "business", "enterprise", "developer"]
    assert data["stripe_mode"] == "unconfigured"
    assert not any(p["purchasable"] for p in data["plans"])


def test_new_user_is_on_free_plan_with_credits(authed):
    s = authed.get("/api/billing/subscription").json()
    assert s["plan_id"] == "free" and s["credits_limit"] == 200 and s["credits_remaining"] == 200
    assert s["stripe_mode"] == "unconfigured"


def test_chat_consumes_credits_and_failures_are_free(authed):
    cid = authed.post("/api/conversations", json={}).json()["id"]
    parse_sse(authed.post(f"/api/conversations/{cid}/messages", json={"content": "hi", "model": "mock:mock-echo"}).text)
    parse_sse(authed.post(f"/api/conversations/{cid}/messages", json={"content": "x [[mock-error]]", "model": "mock:mock-echo"}).text)
    s = authed.get("/api/billing/subscription").json()
    assert s["credits_used"] == 1  # min 1 credit for the successful call, 0 for the failed one
    with SessionLocal() as db:
        # the failed request also tried a fallback model; every failed attempt costs 0
        credits = sorted(u.credits for u in db.query(UsageRecord).all())
        assert credits[-1] == 1 and set(credits[:-1]) == {0}


def test_credit_formula():
    assert billing_svc.credits_for(0.0, "ok") == 1
    assert billing_svc.credits_for(0.0123, "ok") == 13  # 1000 credits per USD, rounded up
    assert billing_svc.credits_for(0.5, "error") == 0
    assert billing_svc.credits_for(0.002, "cancelled") == 2


def test_out_of_credits_blocks_chat_and_compare(authed):
    cid = authed.post("/api/conversations", json={}).json()["id"]
    parse_sse(authed.post(f"/api/conversations/{cid}/messages", json={"content": "hi", "model": "mock:mock-echo"}).text)
    with SessionLocal() as db:
        u = db.query(UsageRecord).one()
        u.credits = 200
        db.commit()
    r = authed.post(f"/api/conversations/{cid}/messages", json={"content": "again", "model": "mock:mock-echo"})
    assert r.status_code == 402 and "credits" in r.json()["detail"]
    r = authed.post("/api/compare", json={"prompt": "x", "models": ["mock:mock-echo", "mock:mock-deep"]})
    assert r.status_code == 402


def test_checkout_unconfigured_is_clear(authed):
    r = authed.post("/api/billing/checkout", json={"plan_id": "pro"})
    assert r.status_code == 503


def test_live_keys_refused_without_explicit_opt_in(authed, monkeypatch):
    monkeypatch.setattr(get_settings(), "stripe_secret_key", "sk_live_abc")
    monkeypatch.setenv("STRIPE_PRICE_PRO", "price_pro_live")
    assert authed.get("/api/billing/plans").json()["stripe_mode"] == "live-blocked"
    r = authed.post("/api/billing/checkout", json={"plan_id": "pro"})
    assert r.status_code == 503 and "STRIPE_ALLOW_LIVE" in r.json()["detail"]


def test_checkout_creates_subscription_session(authed, stripe_on):
    r = authed.post("/api/billing/checkout", json={"plan_id": "pro"})
    assert r.status_code == 200 and r.json()["url"] == "https://checkout.stripe.test/checkout"
    params = stripe_on["checkout"]
    assert stripe_on["key"] == "sk_test_123"
    assert params["mode"] == "subscription"
    assert params["line_items"] == [{"price": "price_pro_test", "quantity": 1}]
    assert params["customer_email"] == "ada@example.com"
    assert params["metadata"]["plan_id"] == "pro" and params["client_reference_id"] == params["metadata"]["org_id"]
    assert authed.post("/api/billing/checkout", json={"plan_id": "free"}).status_code == 400
    assert authed.post("/api/billing/checkout", json={"plan_id": "nope"}).status_code == 400
    r = authed.post("/api/billing/checkout", json={"plan_id": "enterprise"})
    assert r.status_code == 400 and "sales" in r.json()["detail"]


def test_portal_requires_customer(authed, stripe_on):
    assert authed.post("/api/billing/portal").status_code == 400


def test_webhook_rejects_bad_signatures(client, stripe_on):
    event = {"id": "evt_1", "type": "invoice.paid", "data": {"object": {}}}
    assert post_event(client, event, secret="whsec_wrong").status_code == 400
    assert post_event(client, event, ts=int(time.time()) - 3600).status_code == 400  # replayed/old
    r = client.post("/api/billing/webhook", content=json.dumps(event), headers={"content-type": "application/json"})
    assert r.status_code == 400


def test_webhook_unconfigured(client):
    r = client.post("/api/billing/webhook", content="{}", headers={"stripe-signature": "t=1,v1=x"})
    assert r.status_code == 503


def test_full_subscription_lifecycle_via_webhooks(authed, stripe_on):
    org = org_id_of(authed)
    now = int(time.time())

    # 1. Checkout completed
    r = post_event(authed, {"id": "evt_co", "type": "checkout.session.completed", "data": {"object": {
        "mode": "subscription", "client_reference_id": org, "customer": "cus_1", "subscription": "sub_1",
        "metadata": {"org_id": org, "plan_id": "pro"}}}})
    assert r.json()["result"] == "ok"
    s = authed.get("/api/billing/subscription").json()
    assert s["plan_id"] == "pro" and s["credits_limit"] == 20000 and s["has_stripe_customer"]

    # Same event delivered twice is processed once
    r = post_event(authed, {"id": "evt_co", "type": "checkout.session.completed", "data": {"object": {}}})
    assert r.json()["duplicate"] is True

    # 2. Subscription updated (new API shape: period on the item) -> upgrade to team, cancel at period end
    r = post_event(authed, {"id": "evt_up", "type": "customer.subscription.updated", "data": {"object": {
        "id": "sub_1", "customer": "cus_1", "status": "active", "cancel_at_period_end": True, "metadata": {},
        "items": {"data": [{"price": {"id": "price_business_test"}, "current_period_start": now - 10,
                            "current_period_end": now + 86400 * 30}]}}}})
    assert r.json()["result"] == "ok"
    s = authed.get("/api/billing/subscription").json()
    assert s["plan_id"] == "business" and s["cancel_at_period_end"] is True
    assert abs(__import__("datetime").datetime.fromisoformat(s["period_start"]).timestamp() - (now - 10)) < 2

    # Checkout again while subscribed -> told to use the portal; portal works now
    assert authed.post("/api/billing/checkout", json={"plan_id": "pro"}).status_code == 409
    r = authed.post("/api/billing/portal")
    assert r.status_code == 200 and stripe_on["portal"]["customer"] == "cus_1"

    # 3. Invoices -> billing history
    post_event(authed, {"id": "evt_inv1", "type": "invoice.paid", "data": {"object": {
        "id": "in_1", "customer": "cus_1", "amount_paid": 6000, "currency": "usd",
        "hosted_invoice_url": "https://invoice.stripe.test/in_1",
        "lines": {"data": [{"description": "Trio Team", "period": {"start": now, "end": now + 100}}]}}}})
    post_event(authed, {"id": "evt_inv2", "type": "invoice.payment_failed", "data": {"object": {
        "id": "in_2", "customer": "cus_1", "amount_due": 6000, "currency": "usd", "lines": {"data": []}}}})
    hist = authed.get("/api/billing/history").json()
    assert {(h["status"], h["amount_cents"]) for h in hist} == {("paid", 6000), ("payment_failed", 6000)}

    # 4. Past due keeps access; unpaid drops to free limits
    post_event(authed, {"id": "evt_pd", "type": "customer.subscription.updated", "data": {"object": {
        "id": "sub_1", "customer": "cus_1", "status": "unpaid", "metadata": {},
        "items": {"data": [{"price": {"id": "price_business_test"}}]}}}})
    assert authed.get("/api/billing/subscription").json()["credits_limit"] == 200

    # 5. Deleted -> back to free
    post_event(authed, {"id": "evt_del", "type": "customer.subscription.deleted", "data": {"object": {
        "id": "sub_1", "customer": "cus_1"}}})
    s = authed.get("/api/billing/subscription").json()
    assert s["plan_id"] == "free" and s["status"] == "active"
    with SessionLocal() as db:
        actions = {a.action for a in db.query(AuditEvent).all()}
        assert {"billing.subscription_started", "billing.subscription_ended", "billing.payment_failed"} <= actions


def test_billing_history_is_tenant_isolated(stripe_on):
    alice = make_client()
    register(alice, "alice@example.com")
    org = org_id_of(alice)
    post_event(alice, {"id": "evt_a", "type": "checkout.session.completed", "data": {"object": {
        "mode": "subscription", "client_reference_id": org, "customer": "cus_a", "subscription": "sub_a",
        "metadata": {"plan_id": "pro"}}}})
    post_event(alice, {"id": "evt_b", "type": "invoice.paid", "data": {"object": {
        "id": "in_a", "customer": "cus_a", "amount_paid": 2000, "currency": "usd", "lines": {"data": []}}}})
    bob = make_client()
    register(bob, "bob@example.com")
    assert bob.get("/api/billing/history").json() == []
    assert bob.get("/api/billing/subscription").json()["plan_id"] == "free"
    assert len(alice.get("/api/billing/history").json()) == 1
    with SessionLocal() as db:
        assert db.query(BillingRecord).count() == 1


def test_unknown_events_are_acknowledged(client, stripe_on):
    r = post_event(client, {"id": "evt_x", "type": "charge.refunded", "data": {"object": {}}})
    assert r.status_code == 200 and r.json()["result"] == "ignored"
