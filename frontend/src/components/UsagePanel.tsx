"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";

interface Summary {
  spent_last_24h_usd: number;
  daily_limit_usd: number;
  by_model: {
    provider: string;
    model: string;
    status: string;
    requests: number;
    input_tokens: number;
    output_tokens: number;
    cost_usd: number;
    avg_latency_ms: number | null;
  }[];
  recent: {
    id: string;
    feature: string;
    provider: string;
    model: string;
    status: string;
    input_tokens: number;
    output_tokens: number;
    cost_usd: number;
    latency_ms: number | null;
    error_code: string | null;
    error_message: string | null;
    created_at: string;
  }[];
}

export function UsagePanel() {
  const [data, setData] = useState<Summary | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api<Summary>("/api/usage/summary")
      .then(setData)
      .catch((e) => setError(e.message));
  }, []);

  const totals = data?.by_model.reduce(
    (t, r) => ({ requests: t.requests + r.requests, tokens: t.tokens + r.input_tokens + r.output_tokens, errors: t.errors + (r.status === "error" ? r.requests : 0) }),
    { requests: 0, tokens: 0, errors: 0 },
  );
  const pct = data ? Math.min(100, (data.spent_last_24h_usd / Math.max(data.daily_limit_usd, 0.0001)) * 100) : 0;

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-5xl p-4 md:p-6">
        <h1 className="text-xl font-semibold">Usage and errors</h1>
        <p className="mt-1 text-sm text-muted">Every model call is recorded here, including failures. Costs are estimates from the model price list.</p>
        {error && <p className="mt-4 text-danger">{error}</p>}
        {data && totals && (
          <>
            <div className="mt-4 grid gap-3 sm:grid-cols-4">
              <Stat label="Requests" value={totals.requests.toLocaleString()} />
              <Stat label="Tokens" value={totals.tokens.toLocaleString()} />
              <Stat label="Errors" value={totals.errors.toLocaleString()} />
              <div className="rounded-xl border border-line bg-panel p-3">
                <p className="text-xs text-muted">Spent in last 24h</p>
                <p className="text-lg font-semibold">
                  ${data.spent_last_24h_usd.toFixed(4)} <span className="text-xs font-normal text-muted">of ${data.daily_limit_usd.toFixed(2)}</span>
                </p>
                <div className="mt-1 h-1.5 overflow-hidden rounded bg-panel-2" role="progressbar" aria-valuenow={Math.round(pct)} aria-valuemin={0} aria-valuemax={100}>
                  <div className="h-full bg-accent" style={{ width: `${pct}%` }} />
                </div>
              </div>
            </div>

            <h2 className="mt-6 mb-2 text-sm font-semibold">By model</h2>
            <div className="overflow-x-auto rounded-xl border border-line bg-panel">
              <table className="w-full text-sm">
                <thead className="text-left text-xs text-muted">
                  <tr>
                    {["Model", "Status", "Requests", "Input tokens", "Output tokens", "Est. cost", "Avg latency"].map((h) => (
                      <th key={h} className="px-3 py-2 font-medium">
                        {h}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {data.by_model.length === 0 && (
                    <tr>
                      <td colSpan={7} className="px-3 py-4 text-muted">
                        No usage yet.
                      </td>
                    </tr>
                  )}
                  {data.by_model.map((r) => (
                    <tr key={`${r.provider}:${r.model}:${r.status}`} className="border-t border-line">
                      <td className="px-3 py-2">
                        {r.provider}:{r.model}
                      </td>
                      <td className={`px-3 py-2 ${r.status === "error" ? "text-danger" : ""}`}>{r.status}</td>
                      <td className="px-3 py-2">{r.requests}</td>
                      <td className="px-3 py-2">{r.input_tokens.toLocaleString()}</td>
                      <td className="px-3 py-2">{r.output_tokens.toLocaleString()}</td>
                      <td className="px-3 py-2">${r.cost_usd.toFixed(5)}</td>
                      <td className="px-3 py-2">{r.avg_latency_ms != null ? `${r.avg_latency_ms} ms` : "n/a"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            <h2 className="mt-6 mb-2 text-sm font-semibold">Recent calls</h2>
            <div className="overflow-x-auto rounded-xl border border-line bg-panel">
              <table className="w-full text-sm">
                <thead className="text-left text-xs text-muted">
                  <tr>
                    {["Time", "Feature", "Model", "Status", "Tokens", "Latency", "Error"].map((h) => (
                      <th key={h} className="px-3 py-2 font-medium">
                        {h}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {data.recent.map((r) => (
                    <tr key={r.id} className="border-t border-line align-top">
                      <td className="whitespace-nowrap px-3 py-2">{new Date(r.created_at).toLocaleString()}</td>
                      <td className="px-3 py-2">{r.feature}</td>
                      <td className="px-3 py-2">
                        {r.provider}:{r.model}
                      </td>
                      <td className={`px-3 py-2 ${r.status === "error" ? "text-danger" : ""}`}>{r.status}</td>
                      <td className="px-3 py-2">
                        {r.input_tokens}/{r.output_tokens}
                      </td>
                      <td className="px-3 py-2">{r.latency_ms != null ? `${r.latency_ms} ms` : ""}</td>
                      <td className="px-3 py-2 text-danger">{r.error_code ? `${r.error_code}: ${r.error_message ?? ""}` : ""}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-xl border border-line bg-panel p-3">
      <p className="text-xs text-muted">{label}</p>
      <p className="text-lg font-semibold">{value}</p>
    </div>
  );
}
