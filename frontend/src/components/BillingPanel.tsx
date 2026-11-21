"use client";

import clsx from "clsx";
import { Check, ExternalLink } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";

interface Plan {
  id: string;
  name: string;
  description: string;
  price_display: string;
  interval: string;
  monthly_credits: number;
  features: string[];
  paid: boolean;
  checkout: "stripe" | "contact" | "none";
  purchasable: boolean;
}

interface Sub {
  plan_id: string;
  plan_name: string;
  status: string;
  cancel_at_period_end: boolean;
  has_stripe_customer: boolean;
  period_start: string;
  period_end: string;
  credits_limit: number;
  credits_used: number;
  credits_remaining: number;
  stripe_mode: "unconfigured" | "test" | "live-blocked" | "live";
}

interface Invoice {
  id: string;
  status: string;
  amount_cents: number;
  currency: string;
  description: string | null;
  hosted_invoice_url: string | null;
  period_start: string | null;
  period_end: string | null;
  created_at: string;
}

const MODE_NOTE: Record<Sub["stripe_mode"], string | null> = {
  unconfigured: "Billing is not configured on this server: no Stripe key is set, so upgrades are unavailable. Everyone stays on the Free plan.",
  "live-blocked": "A live Stripe key is configured but live billing is switched off, so no real payments can be taken.",
  test: "Stripe test mode: no real money is charged. Use Stripe's test card 4242 4242 4242 4242.",
  live: null,
};

export function BillingPanel() {
  const [plans, setPlans] = useState<Plan[]>([]);
  const [sub, setSub] = useState<Sub | null>(null);
  const [history, setHistory] = useState<Invoice[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [banner] = useState(() => {
    if (typeof window === "undefined") return null;
    const c = new URLSearchParams(window.location.search).get("checkout");
    return c === "success"
      ? "Thanks! Your payment went through. Your plan updates as soon as Stripe confirms it (usually a few seconds)."
      : c === "cancelled"
        ? "Checkout was cancelled. You have not been charged."
        : null;
  });

  useEffect(() => {
    Promise.all([
      api<{ plans: Plan[] }>("/api/billing/plans"),
      api<Sub>("/api/billing/subscription"),
      api<Invoice[]>("/api/billing/history"),
    ])
      .then(([p, s, h]) => {
        setPlans(p.plans);
        setSub(s);
        setHistory(h);
      })
      .catch((e) => setError(e.message));
  }, []);

  async function go(kind: "checkout" | "portal", planId?: string) {
    setBusy(planId ?? kind);
    setError(null);
    try {
      const r = await api<{ url: string }>(`/api/billing/${kind}`, { method: "POST", json: kind === "checkout" ? { plan_id: planId } : {} });
      window.location.assign(r.url);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Billing request failed");
      setBusy(null);
    }
  }

  const pct = sub ? Math.min(100, (sub.credits_used / Math.max(1, sub.credits_limit)) * 100) : 0;
  const note = sub ? MODE_NOTE[sub.stripe_mode] : null;

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-5xl space-y-6 p-4 md:p-6">
        <div>
          <h1 className="text-xl font-semibold">Plan and billing</h1>
          <p className="mt-1 text-sm text-muted">Credits measure AI usage: each request costs at least 1 credit, more for long or expensive model calls.</p>
        </div>
        {banner && <p className="rounded-lg bg-accent-2/10 p-3 text-sm">{banner}</p>}
        {note && (
          <p className="rounded-lg border border-accent-3/40 bg-accent-3/10 p-3 text-sm" data-testid="billing-mode-note">
            {note}
          </p>
        )}
        {error && (
          <p role="alert" className="rounded-lg bg-danger/10 p-3 text-sm text-danger">
            {error}
          </p>
        )}

        {sub && (
          <section className="rounded-2xl border border-line bg-panel p-4" data-testid="current-plan">
            <div className="flex flex-wrap items-center gap-3">
              <h2 className="text-lg font-semibold">{sub.plan_name} plan</h2>
              <span className="rounded bg-panel-2 px-2 py-0.5 text-xs">{sub.status.replace("_", " ")}</span>
              {sub.cancel_at_period_end && <span className="text-xs text-accent-3">Ends {new Date(sub.period_end).toLocaleDateString()}</span>}
              {sub.has_stripe_customer && (
                <button
                  onClick={() => go("portal")}
                  disabled={!!busy}
                  className="ml-auto inline-flex items-center gap-1 rounded-lg border border-line px-3 py-1.5 text-sm hover:bg-panel-2 disabled:opacity-50"
                >
                  Manage subscription <ExternalLink size={14} />
                </button>
              )}
            </div>
            <p className="mt-3 text-sm">
              <strong>{sub.credits_used.toLocaleString()}</strong> of {sub.credits_limit.toLocaleString()} credits used this period
              <span className="text-muted"> (resets {new Date(sub.period_end).toLocaleDateString()})</span>
            </p>
            <div className="mt-2 h-2 overflow-hidden rounded bg-panel-2" role="progressbar" aria-label="Credits used" aria-valuenow={Math.round(pct)} aria-valuemin={0} aria-valuemax={100}>
              <div className={clsx("h-full", pct >= 90 ? "bg-danger" : "bg-accent")} style={{ width: `${pct}%` }} />
            </div>
          </section>
        )}

        <section className="grid gap-3 md:grid-cols-3">
          {plans.map((p) => {
            const current = sub?.plan_id === p.id;
            return (
              <article key={p.id} className={clsx("flex flex-col rounded-2xl border bg-panel p-4", current ? "border-accent" : "border-line")} data-testid={`plan-${p.id}`}>
                <h3 className="font-semibold">{p.name}</h3>
                <p className="text-sm text-muted">{p.description}</p>
                <p className="mt-3 text-2xl font-semibold">
                  {p.price_display}
                  <span className="text-sm font-normal text-muted">/{p.interval}</span>
                </p>
                <ul className="mt-3 flex-1 space-y-1 text-sm">
                  {p.features.map((f) => (
                    <li key={f} className="flex gap-2">
                      <Check size={16} className="mt-0.5 shrink-0 text-accent-2" /> {f}
                    </li>
                  ))}
                </ul>
                <div className="mt-4">
                  {current ? (
                    <span className="block rounded-lg bg-panel-2 px-3 py-2 text-center text-sm">Current plan</span>
                  ) : p.checkout === "contact" ? (
                    <span className="block rounded-lg border border-line px-3 py-2 text-center text-sm text-muted">Arranged with our sales team</span>
                  ) : p.paid ? (
                    <button
                      onClick={() => (sub?.has_stripe_customer && sub.plan_id !== "free" ? go("portal") : go("checkout", p.id))}
                      disabled={!p.purchasable || !!busy}
                      title={p.purchasable ? undefined : "Billing is not configured on this server"}
                      className="w-full rounded-lg bg-accent px-3 py-2 text-sm font-medium text-accent-contrast disabled:opacity-40"
                    >
                      {busy === p.id ? "Opening checkout..." : sub?.plan_id !== "free" ? `Switch to ${p.name}` : `Upgrade to ${p.name}`}
                    </button>
                  ) : null}
                </div>
              </article>
            );
          })}
        </section>

        <section>
          <h2 className="mb-2 text-sm font-semibold">Billing history</h2>
          <div className="overflow-x-auto rounded-xl border border-line bg-panel">
            <table className="w-full text-sm">
              <thead className="text-left text-xs text-muted">
                <tr>
                  {["Date", "Description", "Amount", "Status", "Invoice"].map((h) => (
                    <th key={h} className="px-3 py-2 font-medium">
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {history.length === 0 && (
                  <tr>
                    <td colSpan={5} className="px-3 py-4 text-muted">
                      No invoices yet.
                    </td>
                  </tr>
                )}
                {history.map((h) => (
                  <tr key={h.id} className="border-t border-line">
                    <td className="px-3 py-2">{new Date(h.created_at).toLocaleDateString()}</td>
                    <td className="px-3 py-2">{h.description ?? "Subscription"}</td>
                    <td className="px-3 py-2">
                      {(h.amount_cents / 100).toLocaleString(undefined, { style: "currency", currency: h.currency.toUpperCase() })}
                    </td>
                    <td className={clsx("px-3 py-2", h.status !== "paid" && "text-danger")}>{h.status.replace("_", " ")}</td>
                    <td className="px-3 py-2">
                      {h.hosted_invoice_url && (
                        <a href={h.hosted_invoice_url} target="_blank" rel="noopener noreferrer" className="text-accent underline">
                          View
                        </a>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      </div>
    </div>
  );
}
