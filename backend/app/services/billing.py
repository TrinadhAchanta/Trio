"""Plans, credits and Stripe integration.

Plans come from a JSON file (``PLANS_FILE``, default ``plans.json``) so prices, credit allowances
and limits change without code changes. Stripe price ids are read from the environment variables
each plan names (``stripe_price_env``), so test and live prices never live in the repo.

Credits: every successful or stopped model call costs
``max(min_credits_per_request, ceil(estimated_cost_usd * credits_per_usd))`` credits. Failed calls
cost nothing. Credits are counted per organization per billing period.
"""

import json
import math
import os
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path

import stripe
from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import Subscription, UsageRecord

ACTIVE_STATUSES = {"active", "trialing", "past_due"}  # past_due keeps access during Stripe's retries


@dataclass
class Plan:
    id: str
    name: str
    description: str
    price_display: str
    interval: str
    monthly_credits: int
    daily_cost_limit_usd: float
    stripe_price_env: str | None = None
    checkout: str = "stripe"  # stripe | contact (sales-led) | none (free)
    features: list[str] = field(default_factory=list)

    @property
    def stripe_price_id(self) -> str | None:
        return os.environ.get(self.stripe_price_env) if self.stripe_price_env else None

    @property
    def paid(self) -> bool:
        return self.checkout != "none"


@dataclass
class PlanConfig:
    credits_per_usd: float
    min_credits_per_request: int
    default_plan: str
    plans: dict[str, Plan]

    def get(self, plan_id: str | None) -> Plan:
        return self.plans.get(plan_id or "", self.plans[self.default_plan])

    def by_price_id(self, price_id: str | None) -> Plan | None:
        if not price_id:
            return None
        return next((p for p in self.plans.values() if p.stripe_price_id == price_id), None)


@lru_cache
def load_plan_config() -> PlanConfig:
    path = Path(get_settings().plans_file)
    if not path.is_absolute():
        path = Path(__file__).resolve().parents[2] / path
    raw = json.loads(path.read_text(encoding="utf-8"))
    plans = {p["id"]: Plan(**p) for p in raw["plans"]}
    if raw["default_plan"] not in plans:
        raise ValueError("default_plan must be one of the configured plans")
    return PlanConfig(raw["credits_per_usd"], raw["min_credits_per_request"], raw["default_plan"], plans)


def credits_for(cost_usd: float | None, status: str) -> int:
    if status not in ("ok", "cancelled"):
        return 0
    cfg = load_plan_config()
    return max(cfg.min_credits_per_request, math.ceil((cost_usd or 0.0) * cfg.credits_per_usd))


# ---------- subscriptions & entitlements ----------

def get_subscription(db: Session, org_id: uuid.UUID) -> Subscription:
    sub = db.query(Subscription).filter(Subscription.org_id == org_id).first()
    if not sub:
        sub = Subscription(org_id=org_id, plan_id=load_plan_config().default_plan, status="active")
        db.add(sub)
        db.flush()
    return sub


def effective_plan(sub: Subscription) -> Plan:
    cfg = load_plan_config()
    if sub.status not in ACTIVE_STATUSES:
        return cfg.get(cfg.default_plan)
    return cfg.get(sub.plan_id)


def current_period(sub: Subscription, now: datetime | None = None) -> tuple[datetime, datetime]:
    now = now or datetime.now(UTC)
    if sub.current_period_start and sub.current_period_end and sub.status in ACTIVE_STATUSES:
        start, end = sub.current_period_start, sub.current_period_end
        if start.tzinfo is None:
            start, end = start.replace(tzinfo=UTC), end.replace(tzinfo=UTC)
        if start <= now < end:
            return start, end
    start = datetime(now.year, now.month, 1, tzinfo=UTC)
    end = datetime(now.year + (now.month == 12), now.month % 12 + 1, 1, tzinfo=UTC)
    return start, end


def credits_used(db: Session, org_id: uuid.UUID, since: datetime) -> int:
    total = (
        db.query(func.coalesce(func.sum(UsageRecord.credits), 0))
        .filter(UsageRecord.org_id == org_id, UsageRecord.created_at >= since)
        .scalar()
    )
    return int(total or 0)


def entitlement(db: Session, org_id: uuid.UUID) -> dict:
    sub = get_subscription(db, org_id)
    plan = effective_plan(sub)
    start, end = current_period(sub)
    used = credits_used(db, org_id, start)
    return {
        "plan": plan,
        "subscription": sub,
        "period_start": start,
        "period_end": end,
        "credits_limit": plan.monthly_credits,
        "credits_used": used,
        "credits_remaining": max(0, plan.monthly_credits - used),
    }


def enforce_credits(db: Session, org_id: uuid.UUID) -> Plan:
    ent = entitlement(db, org_id)
    if ent["credits_remaining"] <= 0:
        raise HTTPException(
            status_code=402,
            detail=f"You've used all {ent['credits_limit']:,} credits on the {ent['plan'].name} plan for this period. "
            "Upgrade on the Billing page or wait for your credits to reset.",
        )
    return ent["plan"]


# ---------- Stripe ----------

class BillingUnavailable(HTTPException):
    def __init__(self, detail: str):
        super().__init__(status_code=503, detail=detail)


def stripe_mode() -> str:
    """'unconfigured' | 'test' | 'live-blocked' | 'live'."""
    s = get_settings()
    key = s.stripe_secret_key
    if not key:
        return "unconfigured"
    if key.startswith(("sk_live_", "rk_live_")):
        return "live" if s.stripe_allow_live else "live-blocked"
    return "test"


def stripe_client() -> stripe.StripeClient:
    mode = stripe_mode()
    if mode == "unconfigured":
        raise BillingUnavailable("Billing is not configured on this server (STRIPE_SECRET_KEY is not set).")
    if mode == "live-blocked":
        raise BillingUnavailable("A live Stripe key is configured but live billing is disabled (set STRIPE_ALLOW_LIVE=true).")
    return stripe.StripeClient(get_settings().stripe_secret_key)


def _ts(value) -> datetime | None:
    return datetime.fromtimestamp(int(value), UTC) if value else None


def subscription_fields_from_stripe(obj: dict) -> dict:
    """Extract what we store from a Stripe Subscription object (handles old and new API shapes)."""
    items = (obj.get("items") or {}).get("data") or [{}]
    item = items[0] or {}
    price_id = (item.get("price") or {}).get("id")
    cfg = load_plan_config()
    plan = cfg.by_price_id(price_id) or cfg.plans.get((obj.get("metadata") or {}).get("plan_id", ""))
    return {
        "status": obj.get("status", "active"),
        "plan_id": plan.id if plan else None,
        "stripe_subscription_id": obj.get("id"),
        "stripe_customer_id": obj.get("customer"),
        # Newer Stripe API versions put the period on the subscription item.
        "current_period_start": _ts(obj.get("current_period_start") or item.get("current_period_start")),
        "current_period_end": _ts(obj.get("current_period_end") or item.get("current_period_end")),
        "cancel_at_period_end": bool(obj.get("cancel_at_period_end")),
    }
