"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { api } from "@/lib/api";
import { applyTheme, getTheme, type Theme } from "@/lib/theme";
import type { ModelsResponse, User } from "@/lib/types";
import { useApp } from "./AppShell";
import { MemorySettings } from "./MemorySettings";
import { ModelPicker, ReasoningPicker } from "./ModelPicker";

export function SettingsPanel() {
  const { user, setUser, models } = useApp();
  const router = useRouter();
  const [theme, setTheme] = useState<Theme>(() => getTheme());
  const [saved, setSaved] = useState(false);
  const [verified, setVerified] = useState<ModelsResponse | null>(null);
  const [checking, setChecking] = useState(false);

  async function savePrefs(p: Partial<User["preferences"]>) {
    const u = await api<User>("/api/auth/me/preferences", { method: "PATCH", json: p });
    setUser(u);
    setSaved(true);
    setTimeout(() => setSaved(false), 1200);
  }

  async function verify() {
    setChecking(true);
    try {
      setVerified(await api<ModelsResponse>("/api/models?verify=true"));
    } finally {
      setChecking(false);
    }
  }

  async function logoutAll() {
    if (!confirm("Sign out of Trio on every device?")) return;
    await api("/api/auth/logout-all", { method: "POST" });
    router.replace("/login");
  }

  const shown = verified ?? models;
  const realProviders = (models?.providers ?? []).filter((p) => p.configured && !p.mock);
  const prefs = user.preferences;
  const toggle = (list: string[] | undefined, name: string) =>
    (list ?? []).includes(name) ? (list ?? []).filter((x) => x !== name) : [...(list ?? []), name];

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-3xl space-y-6 p-4 md:p-6">
        <h1 className="text-xl font-semibold">Settings {saved && <span className="ml-2 text-xs font-normal text-accent-2">Saved</span>}</h1>

        <section className="rounded-2xl border border-line bg-panel p-4">
          <h2 className="mb-3 text-sm font-semibold">Appearance</h2>
          <div role="radiogroup" aria-label="Theme" className="inline-flex overflow-hidden rounded-md border border-line text-sm">
            {(["light", "dark", "system"] as Theme[]).map((t) => (
              <button
                key={t}
                role="radio"
                aria-checked={theme === t}
                onClick={() => {
                  setTheme(t);
                  applyTheme(t);
                }}
                className={`px-3 py-1.5 capitalize ${theme === t ? "bg-accent text-accent-contrast" : "hover:bg-panel-2"}`}
              >
                {t}
              </button>
            ))}
          </div>
        </section>

        <section className="rounded-2xl border border-line bg-panel p-4">
          <h2 className="mb-3 text-sm font-semibold">Defaults for new chats</h2>
          <div className="flex flex-wrap items-center gap-3">
            <ModelPicker models={models} value={user.preferences.default_model ?? "auto"} onChange={(v) => void savePrefs({ default_model: v })} />
            <ReasoningPicker value={user.preferences.reasoning_mode ?? "balanced"} onChange={(v) => void savePrefs({ reasoning_mode: v })} />
          </div>
        </section>

        <MemorySettings enabled={prefs.memory_enabled ?? true} onToggle={(on) => savePrefs({ memory_enabled: on })} />

        <section className="rounded-2xl border border-line bg-panel p-4" data-testid="routing-settings">
          <h2 className="mb-1 text-sm font-semibold">Auto routing</h2>
          <p className="mb-3 text-xs text-muted">
            With the model set to Auto, the Trio Router picks a model for each message from the task type, the reasoning mode, expected
            quality, cost, speed and provider health.
          </p>
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={prefs.allow_fallback ?? true}
              onChange={(e) => void savePrefs({ allow_fallback: e.target.checked })}
              data-testid="allow-fallback"
            />
            If a model is unavailable, retry automatically with the next best model
          </label>
          {realProviders.length > 0 ? (
            <div className="mt-3 grid gap-3 sm:grid-cols-2">
              <fieldset>
                <legend className="mb-1 text-xs font-medium">Prefer these providers</legend>
                {realProviders.map((p) => (
                  <label key={p.name} className="flex items-center gap-2 text-sm">
                    <input
                      type="checkbox"
                      checked={(prefs.preferred_providers ?? []).includes(p.name)}
                      onChange={() => void savePrefs({ preferred_providers: toggle(prefs.preferred_providers, p.name) })}
                    />
                    {p.label}
                  </label>
                ))}
              </fieldset>
              <fieldset>
                <legend className="mb-1 text-xs font-medium">Only use these providers (none ticked = all)</legend>
                {realProviders.map((p) => (
                  <label key={p.name} className="flex items-center gap-2 text-sm">
                    <input
                      type="checkbox"
                      checked={(prefs.allowed_providers ?? []).includes(p.name)}
                      onChange={() => void savePrefs({ allowed_providers: toggle(prefs.allowed_providers, p.name) })}
                    />
                    {p.label}
                  </label>
                ))}
              </fieldset>
            </div>
          ) : (
            <p className="mt-3 text-xs text-muted">Provider preferences appear here once an AI provider key is configured.</p>
          )}
        </section>

        <section className="rounded-2xl border border-line bg-panel p-4">
          <div className="mb-3 flex items-center justify-between">
            <h2 className="text-sm font-semibold">AI providers</h2>
            <button onClick={verify} disabled={checking} className="rounded-md border border-line px-2 py-1 text-xs hover:bg-panel-2 disabled:opacity-50">
              {checking ? "Checking..." : "Check live model lists"}
            </button>
          </div>
          <p className="mb-3 text-xs text-muted">
            API keys are stored only on the server (in <code>backend/.env</code>) and never sent to the browser. &quot;Check live model lists&quot; asks
            each configured provider which models your key can use.
          </p>
          <ul className="space-y-3 text-sm">
            {shown?.providers.map((p) => (
              <li key={p.name}>
                <p className="font-medium">
                  {p.label}{" "}
                  <span className={`ml-1 rounded px-1.5 py-0.5 text-xs ${p.configured ? "bg-accent-2/15 text-accent-2" : "bg-panel-2 text-muted"}`}>
                    {p.mock ? "demo only" : p.configured ? "configured" : "no API key"}
                  </span>
                  {p.configured && p.health && p.health !== "unknown" && (
                    <span
                      className={`ml-1 rounded px-1.5 py-0.5 text-xs ${p.health === "healthy" ? "bg-accent-2/15 text-accent-2" : "bg-danger/10 text-danger"}`}
                      title="Based on recent requests to this server"
                    >
                      {p.health}
                    </span>
                  )}
                </p>
                <p className="text-xs text-muted">
                  {p.models
                    .map((m) => `${m.label}${m.verified === true ? " ✓" : m.verified === false ? " (not available for this key)" : ""}`)
                    .join(", ")}
                </p>
              </li>
            ))}
          </ul>
        </section>

        <section className="rounded-2xl border border-line bg-panel p-4">
          <h2 className="mb-1 text-sm font-semibold">Account</h2>
          <p className="mb-3 text-sm text-muted">
            Signed in as {user.display_name} ({user.email})
          </p>
          <button onClick={logoutAll} className="rounded-md border border-danger/40 px-3 py-1.5 text-sm text-danger hover:bg-danger/10">
            Sign out everywhere
          </button>
        </section>
      </div>
    </div>
  );
}
