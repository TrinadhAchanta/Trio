"""Billing: plans, subscription status, Stripe Checkout, customer portal, webhooks, history.

Nothing here charges money by itself: payments only happen when a user completes Stripe's hosted
Checkout page. With no STRIPE_SECRET_KEY the endpoints report billing as unconfigured.
"""

import json
import logging
import uuid

import stripe
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from ..models import BillingRecord, Membership, Subscription, User, WebhookEvent
from ..security import audit, client_ip, get_current_user, primary_org_id
from ..services.billing import (
    _ts,
    entitlement,
    get_subscription,
    load_plan_config,
    stripe_client,
    stripe_mode,
    subscription_fields_from_stripe,
)

log = logging.getLogger("trio.billing")
router = APIRouter(prefix="/api/billing", tags=["billing"])


class CheckoutIn(BaseModel):
    plan_id: str


def require_billing_admin(db: Session, user: User) -> uuid.UUID:
    org_id = primary_org_id(db, user)
    m = db.query(Membership).filter(Membership.user_id == user.id, Membership.org_id == org_id).first()
    if not m or m.role not in ("owner", "admin"):
        raise HTTPException(status_code=403, detail="Only workspace owners and admins can manage billing.")
    return org_id


@router.get("/plans")
def list_plans():
    cfg = load_plan_config()
    return {
        "stripe_mode": stripe_mode(),
        "credits_per_usd": cfg.credits_per_usd,
        "plans": [
            {"id": p.id, "name": p.name, "description": p.description, "price_display": p.price_display,
             "interval": p.interval, "monthly_credits": p.monthly_credits, "features": p.features, "paid": p.paid,
             "checkout": p.checkout, "purchasable": p.checkout == "stripe" and bool(p.stripe_price_id)}
            for p in cfg.plans.values()
        ],
    }


@router.get("/subscription")
def get_subscription_status(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    org_id = primary_org_id(db, user)
    ent = entitlement(db, org_id)
    db.commit()  # persists the default free subscription row on first visit
    sub: Subscription = ent["subscription"]
    return {
        "plan_id": ent["plan"].id,
        "plan_name": ent["plan"].name,
        "status": sub.status,
        "cancel_at_period_end": sub.cancel_at_period_end,
        "has_stripe_customer": bool(sub.stripe_customer_id),
        "period_start": ent["period_start"].isoformat(),
        "period_end": ent["period_end"].isoformat(),
        "credits_limit": ent["credits_limit"],
        "credits_used": ent["credits_used"],
        "credits_remaining": ent["credits_remaining"],
        "stripe_mode": stripe_mode(),
    }


@router.post("/checkout")
def create_checkout(body: CheckoutIn, request: Request, user: User = Depends(get_current_user),
                    db: Session = Depends(get_db)):
    org_id = require_billing_admin(db, user)
    plan = load_plan_config().plans.get(body.plan_id)
    if not plan or not plan.paid:
        raise HTTPException(status_code=400, detail="Choose a paid plan.")
    if plan.checkout == "contact":
        raise HTTPException(status_code=400, detail=f"The {plan.name} plan is arranged with our sales team, not online checkout.")
    if not plan.stripe_price_id:
        raise HTTPException(status_code=503, detail=f"The {plan.name} plan has no Stripe price configured ({plan.stripe_price_env}).")
    sub = get_subscription(db, org_id)
    if sub.stripe_subscription_id and sub.status in ("active", "trialing", "past_due"):
        raise HTTPException(status_code=409, detail="You already have a subscription. Use Manage subscription to change plans.")
    client = stripe_client()
    app_url = get_settings().public_app_url.rstrip("/")
    meta = {"org_id": str(org_id), "plan_id": plan.id}
    params: dict = {
        "mode": "subscription",
        "line_items": [{"price": plan.stripe_price_id, "quantity": 1}],
        "client_reference_id": str(org_id),
        "metadata": meta,
        "subscription_data": {"metadata": meta},
        "allow_promotion_codes": True,
        "success_url": f"{app_url}/billing?checkout=success",
        "cancel_url": f"{app_url}/billing?checkout=cancelled",
    }
    if sub.stripe_customer_id:
        params["customer"] = sub.stripe_customer_id
    else:
        params["customer_email"] = user.email
    try:
        session = client.v1.checkout.sessions.create(params=params)
    except stripe.StripeError as e:
        log.warning("Stripe checkout failed: %s", e)
        raise HTTPException(status_code=502, detail="Stripe could not start checkout. Please try again.") from None
    audit(db, "billing.checkout_started", user_id=user.id, org_id=org_id, ip=client_ip(request),
          details={"plan_id": plan.id})
    db.commit()
    return {"url": session.url}


@router.post("/portal")
def create_portal(request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    org_id = require_billing_admin(db, user)
    sub = get_subscription(db, org_id)
    if not sub.stripe_customer_id:
        raise HTTPException(status_code=400, detail="There is no paid subscription to manage yet.")
    client = stripe_client()
    try:
        session = client.v1.billing_portal.sessions.create(params={
            "customer": sub.stripe_customer_id,
            "return_url": f"{get_settings().public_app_url.rstrip('/')}/billing",
        })
    except stripe.StripeError as e:
        log.warning("Stripe portal failed: %s", e)
        raise HTTPException(status_code=502, detail="Stripe could not open the billing portal.") from None
    audit(db, "billing.portal_opened", user_id=user.id, org_id=org_id, ip=client_ip(request))
    db.commit()
    return {"url": session.url}


@router.get("/history")
def billing_history(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    org_id = primary_org_id(db, user)
    rows = (db.query(BillingRecord).filter(BillingRecord.org_id == org_id)
            .order_by(BillingRecord.created_at.desc()).limit(100).all())
    return [
        {"id": str(r.id), "status": r.status, "amount_cents": r.amount_cents, "currency": r.currency,
         "description": r.description, "hosted_invoice_url": r.hosted_invoice_url,
         "period_start": r.period_start.isoformat() if r.period_start else None,
         "period_end": r.period_end.isoformat() if r.period_end else None,
         "created_at": r.created_at.isoformat()}
        for r in rows
    ]


# ---------- webhooks ----------

def _find_subscription(db: Session, *, sub_id=None, customer=None, org_id=None) -> Subscription | None:
    if sub_id:
        s = db.query(Subscription).filter(Subscription.stripe_subscription_id == sub_id).first()
        if s:
            return s
    if org_id:
        try:
            s = get_subscription(db, uuid.UUID(str(org_id)))
            return s
        except (ValueError, IntegrityError):
            return None
    if customer:
        return db.query(Subscription).filter(Subscription.stripe_customer_id == customer).first()
    return None


def _handle_event(db: Session, event: dict) -> str:
    etype = event["type"]
    obj = event["data"]["object"]

    if etype == "checkout.session.completed":
        if obj.get("mode") != "subscription":
            return "ignored"
        meta = obj.get("metadata") or {}
        sub = _find_subscription(db, org_id=obj.get("client_reference_id") or meta.get("org_id"))
        if not sub:
            return "no-org"
        sub.stripe_customer_id = obj.get("customer") or sub.stripe_customer_id
        sub.stripe_subscription_id = obj.get("subscription") or sub.stripe_subscription_id
        if meta.get("plan_id") in load_plan_config().plans:
            sub.plan_id = meta["plan_id"]
        sub.status = "active"
        audit(db, "billing.subscription_started", org_id=sub.org_id, details={"plan_id": sub.plan_id})
        return "ok"

    if etype in ("customer.subscription.created", "customer.subscription.updated"):
        fields = subscription_fields_from_stripe(obj)
        sub = _find_subscription(db, sub_id=obj.get("id"), org_id=(obj.get("metadata") or {}).get("org_id"),
                                 customer=obj.get("customer"))
        if not sub:
            return "no-org"
        for k, v in fields.items():
            if v is not None or k in ("current_period_start", "current_period_end"):
                setattr(sub, k, v)
        audit(db, "billing.subscription_updated", org_id=sub.org_id,
              details={"plan_id": sub.plan_id, "status": sub.status, "cancel_at_period_end": sub.cancel_at_period_end})
        return "ok"

    if etype == "customer.subscription.deleted":
        sub = _find_subscription(db, sub_id=obj.get("id"), customer=obj.get("customer"))
        if not sub:
            return "no-org"
        sub.plan_id = load_plan_config().default_plan
        sub.status = "active"
        sub.stripe_subscription_id = None
        sub.current_period_start = sub.current_period_end = None
        sub.cancel_at_period_end = False
        audit(db, "billing.subscription_ended", org_id=sub.org_id)
        return "ok"

    if etype in ("invoice.paid", "invoice.payment_failed"):
        sub = _find_subscription(db, customer=obj.get("customer"))
        if not sub:
            return "no-org"
        rec = db.query(BillingRecord).filter(BillingRecord.stripe_invoice_id == obj["id"]).first()
        if not rec:
            rec = BillingRecord(org_id=sub.org_id, stripe_invoice_id=obj["id"])
            db.add(rec)
        paid = etype == "invoice.paid"
        rec.status = "paid" if paid else "payment_failed"
        rec.amount_cents = int(obj.get("amount_paid") if paid else obj.get("amount_due") or 0)
        rec.currency = (obj.get("currency") or "usd").lower()
        lines = ((obj.get("lines") or {}).get("data") or [{}])
        rec.description = (lines[0] or {}).get("description") or obj.get("description")
        rec.hosted_invoice_url = obj.get("hosted_invoice_url")
        period = (lines[0] or {}).get("period") or {}
        rec.period_start = _ts(period.get("start") or obj.get("period_start"))
        rec.period_end = _ts(period.get("end") or obj.get("period_end"))
        if not paid:
            audit(db, "billing.payment_failed", org_id=sub.org_id, details={"invoice": obj["id"]})
        return "ok"

    return "ignored"


@router.post("/webhook")
async def stripe_webhook(request: Request, db: Session = Depends(get_db)):
    secret = get_settings().stripe_webhook_secret
    if not secret:
        raise HTTPException(status_code=503, detail="Stripe webhooks are not configured (STRIPE_WEBHOOK_SECRET).")
    payload = await request.body()
    sig = request.headers.get("stripe-signature")
    try:
        stripe.WebhookSignature.verify_header(payload.decode("utf-8"), sig or "", secret, tolerance=300)
    except (stripe.SignatureVerificationError, UnicodeDecodeError):
        log.warning("Rejected Stripe webhook with an invalid signature from %s", client_ip(request))
        raise HTTPException(status_code=400, detail="Invalid signature") from None
    event = json.loads(payload)
    if db.get(WebhookEvent, event["id"]):
        return {"received": True, "duplicate": True}
    db.add(WebhookEvent(id=event["id"], type=event["type"]))
    try:
        result = _handle_event(db, event)
        db.commit()
    except IntegrityError:
        # A concurrent delivery of the same event won the race.
        db.rollback()
        return {"received": True, "duplicate": True}
    return {"received": True, "result": result}
