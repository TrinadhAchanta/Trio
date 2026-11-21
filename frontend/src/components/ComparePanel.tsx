"use client";

import clsx from "clsx";
import { Plus, Sparkles, Star, X } from "lucide-react";
import { useMemo, useRef, useState } from "react";
import { api, streamSSE } from "@/lib/api";
import type { Reasoning } from "@/lib/types";
import { useApp } from "./AppShell";
import { Markdown } from "./Markdown";
import { ModelPicker, ReasoningPicker, modelLabel } from "./ModelPicker";

interface Column {
  model: string;
  text: string;
  status: "idle" | "streaming" | "done" | "error";
  error?: string;
  mock?: boolean;
  stats?: { latency_ms: number; ttft_ms: number | null; input_tokens: number; output_tokens: number; cost_usd: number | null };
}

function fmtCost(c: number | null | undefined) {
  if (c == null) return "n/a";
  if (c === 0) return "$0";
  return c < 0.01 ? `$${c.toFixed(5)}` : `$${c.toFixed(3)}`;
}

export function ComparePanel() {
  const { models } = useApp();
  const available = useMemo(() => models?.providers.filter((p) => p.configured).flatMap((p) => p.models.map((m) => m.id)) ?? [], [models]);
  const [selected, setSelected] = useState<string[]>(() => available.slice(0, 2));
  const [prompt, setPrompt] = useState("");
  const [mode, setMode] = useState<Reasoning>("balanced");
  const [cols, setCols] = useState<Column[]>([]);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [synth, setSynth] = useState<{ text: string; status: string; model?: string } | null>(null);
  const [voted, setVoted] = useState<string | null>(null);
  const lastPrompt = useRef("");

  // Pick defaults once the model list arrives.
  if (selected.length === 0 && available.length >= 2) setSelected(available.slice(0, 2));

  async function run() {
    if (running || !prompt.trim()) return;
    setError(null);
    setSynth(null);
    setVoted(null);
    setRunning(true);
    lastPrompt.current = prompt.trim();
    setCols(selected.map((m) => ({ model: m, text: "", status: "streaming" })));
    const update = (model: string, f: (c: Column) => Column) => setCols((cs) => cs.map((c) => (c.model === model ? f(c) : c)));
    try {
      await streamSSE("/api/compare", { prompt: prompt.trim(), models: selected, reasoning_mode: mode }, (ev, d) => {
        if (ev === "start") update(d.model, (c) => ({ ...c, mock: d.mock }));
        if (ev === "delta") update(d.model, (c) => ({ ...c, text: c.text + d.text }));
        if (ev === "done") update(d.model, (c) => ({ ...c, status: "done", stats: d }));
        if (ev === "error") update(d.model, (c) => ({ ...c, status: "error", error: d.message }));
      });
    } catch (e) {
      setError(e instanceof Error ? e.message : "Comparison failed");
      setCols((cs) => cs.map((c) => (c.status === "streaming" ? { ...c, status: "error", error: "Interrupted" } : c)));
    } finally {
      setRunning(false);
    }
  }

  async function synthesize() {
    const answers = cols.filter((c) => c.status === "done").map((c) => ({ model: modelLabel(models, c.model), text: c.text }));
    if (answers.length < 2) return;
    setSynth({ text: "", status: "streaming" });
    try {
      await streamSSE("/api/compare/synthesize", { prompt: lastPrompt.current, answers, model: "auto" }, (ev, d) => {
        if (ev === "start") setSynth((s) => s && { ...s, model: d.label });
        if (ev === "delta") setSynth((s) => s && { ...s, text: s.text + d.text });
        if (ev === "done") setSynth((s) => s && { ...s, status: "done" });
        if (ev === "error") setSynth((s) => s && { ...s, status: "error", text: s.text || d.message });
      });
    } catch (e) {
      setSynth({ text: e instanceof Error ? e.message : "Synthesis failed", status: "error" });
    }
  }

  async function vote(model: string) {
    setVoted(model);
    await api("/api/compare/vote", { method: "POST", json: { prompt: lastPrompt.current, preferred_model: model, models: cols.map((c) => c.model) } }).catch(() => {});
  }

  const doneCols = cols.filter((c) => c.status === "done");
  const fastest = doneCols.length > 1 ? doneCols.reduce((a, b) => (a.stats!.latency_ms <= b.stats!.latency_ms ? a : b)).model : null;

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-7xl p-4 md:p-6">
        <h1 className="text-xl font-semibold">Compare models</h1>
        <p className="mt-1 text-sm text-muted">
          Send one prompt to several models and see their answers side by side with speed, token use and estimated cost. Your preference vote is
          personal feedback, not a benchmark.
        </p>

        <div className="mt-4 rounded-2xl border border-line bg-panel p-3">
          <div className="mb-2 flex flex-wrap items-center gap-2">
            {selected.map((m, i) => (
              <span key={i} className="inline-flex items-center gap-1">
                <ModelPicker
                  models={models}
                  value={m}
                  allowAuto={false}
                  id={`compare-model-${i}`}
                  label={`Model ${i + 1}`}
                  onChange={(v) => setSelected((s) => s.map((x, j) => (j === i ? v : x)))}
                />
                {selected.length > 2 && (
                  <button onClick={() => setSelected((s) => s.filter((_, j) => j !== i))} aria-label={`Remove model ${i + 1}`} className="rounded p-1 text-muted hover:bg-panel-2">
                    <X size={14} />
                  </button>
                )}
              </span>
            ))}
            {selected.length < 4 && (
              <button
                className="inline-flex items-center gap-1 rounded-md border border-dashed border-line px-2 py-1 text-xs text-muted hover:bg-panel-2"
                onClick={() => setSelected((s) => [...s, available.find((a) => !s.includes(a)) ?? available[0]])}
              >
                <Plus size={12} /> Add model
              </button>
            )}
            <ReasoningPicker value={mode} onChange={setMode} />
          </div>
          <label htmlFor="compare-prompt" className="sr-only">
            Prompt
          </label>
          <textarea
            id="compare-prompt"
            data-testid="compare-prompt"
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) void run();
            }}
            rows={3}
            placeholder="Ask something to compare (Ctrl+Enter to run)"
            className="w-full resize-y rounded-lg bg-transparent p-2 outline-none placeholder:text-muted"
          />
          <div className="flex items-center justify-between">
            <span className="text-xs text-muted">{new Set(selected).size !== selected.length ? "Pick different models for each column." : ""}</span>
            <button
              onClick={run}
              disabled={running || !prompt.trim() || new Set(selected).size !== selected.length || selected.length < 2}
              className="rounded-lg bg-accent px-4 py-1.5 text-sm font-medium text-accent-contrast disabled:opacity-50"
              data-testid="compare-run"
            >
              {running ? "Running..." : "Compare"}
            </button>
          </div>
        </div>
        {error && <p className="mt-3 rounded-lg bg-danger/10 p-3 text-sm text-danger">{error}</p>}

        {cols.length > 0 && (
          <div className={clsx("mt-4 grid gap-3", cols.length === 2 ? "md:grid-cols-2" : cols.length === 3 ? "md:grid-cols-3" : "md:grid-cols-2 xl:grid-cols-4")}>
            {cols.map((c) => (
              <article key={c.model} className="flex min-w-0 flex-col rounded-2xl border border-line bg-panel" data-testid="compare-column">
                <header className="flex items-center gap-2 border-b border-line px-3 py-2 text-sm">
                  <span className="font-semibold">{modelLabel(models, c.model)}</span>
                  {c.mock && <span className="rounded bg-accent-3/15 px-1.5 py-0.5 text-xs text-accent-3">Mock</span>}
                  <button
                    className={clsx("ml-auto rounded p-1 hover:bg-panel-2", voted === c.model ? "text-accent-3" : "text-muted")}
                    onClick={() => vote(c.model)}
                    disabled={c.status !== "done"}
                    aria-label={`Prefer ${modelLabel(models, c.model)}`}
                    aria-pressed={voted === c.model}
                    title="I prefer this answer"
                  >
                    <Star size={15} fill={voted === c.model ? "currentColor" : "none"} />
                  </button>
                </header>
                <div className={clsx("min-h-24 flex-1 px-3 py-2 text-sm", c.status === "streaming" && "cursor-blink")}>
                  {c.text && <Markdown text={c.text} />}
                  {c.status === "error" && <p className="text-danger">{c.error}</p>}
                </div>
                <footer className="grid grid-cols-2 gap-x-3 gap-y-0.5 border-t border-line px-3 py-2 text-xs text-muted">
                  {c.stats ? (
                    <>
                      <span>
                        Total: {(c.stats.latency_ms / 1000).toFixed(2)}s {fastest === c.model && <strong className="text-accent-2">fastest</strong>}
                      </span>
                      <span>First token: {c.stats.ttft_ms != null ? `${(c.stats.ttft_ms / 1000).toFixed(2)}s` : "n/a"}</span>
                      <span>
                        Tokens: {c.stats.input_tokens} in / {c.stats.output_tokens} out
                      </span>
                      <span>Est. cost: {fmtCost(c.stats.cost_usd)}</span>
                    </>
                  ) : (
                    <span>{c.status === "streaming" ? "Generating..." : c.status === "error" ? "Failed" : ""}</span>
                  )}
                </footer>
              </article>
            ))}
          </div>
        )}

        {!running && doneCols.length >= 2 && (
          <div className="mt-4">
            {!synth && (
              <button onClick={synthesize} className="inline-flex items-center gap-2 rounded-lg border border-line bg-panel px-3 py-2 text-sm hover:bg-panel-2" data-testid="synthesize">
                <Sparkles size={16} className="text-accent" /> Combine into one answer
              </button>
            )}
            {synth && (
              <section className="rounded-2xl border border-line bg-panel p-4" data-testid="synthesis">
                <h2 className="mb-1 text-sm font-semibold">Combined answer</h2>
                <p className="mb-2 text-xs text-muted">
                  Written by {synth.model ?? "..."} from the answers above. Claims are attributed to the model that made them; check important facts.
                </p>
                <div className={clsx("text-sm", synth.status === "streaming" && "cursor-blink")}>
                  <Markdown text={synth.text} />
                </div>
              </section>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
