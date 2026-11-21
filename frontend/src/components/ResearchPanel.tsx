"use client";

import clsx from "clsx";
import { CheckCircle2, Circle, Download, ExternalLink, Loader2, Telescope, Trash2, XCircle } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { useApp } from "./AppShell";
import { Markdown } from "./Markdown";
import { ModelPicker } from "./ModelPicker";

interface Step {
  at: string;
  message: string;
}
interface RSource {
  n: number;
  title: string;
  url: string;
  excerpt: string;
  cited?: boolean;
}
interface Job {
  id: string;
  question: string;
  status: "queued" | "running" | "done" | "error" | "cancelled";
  used_model: string | null;
  search_provider: string | null;
  demo: boolean;
  error: string | null;
  created_at: string;
  steps?: Step[];
  queries?: string[];
  sources?: RSource[];
  report?: string | null;
  stats?: { citation_coverage?: number; cited_sources?: number[]; invalid_citations?: number[]; notices?: string[]; skipped?: { url: string; reason: string }[] };
}

const ACTIVE = new Set(["queued", "running"]);

export function ResearchPanel() {
  const { models } = useApp();
  const [config, setConfig] = useState<{ available: boolean; provider: string | null; demo: boolean } | null>(null);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [active, setActive] = useState<Job | null>(null);
  const [question, setQuestion] = useState("");
  const [model, setModel] = useState("auto");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const loadJobs = useCallback(() => api<Job[]>("/api/research").then(setJobs), []);

  useEffect(() => {
    Promise.all([api<typeof config>("/api/research/config").then(setConfig), loadJobs()]).catch((e) => setError(e.message));
  }, [loadJobs]);

  const activeId = active?.id;
  const activeRunning = active ? ACTIVE.has(active.status) : false;
  useEffect(() => {
    if (!activeId || !activeRunning) return;
    const t = setInterval(() => {
      api<Job>(`/api/research/${activeId}`)
        .then((j) => {
          setActive(j);
          if (!ACTIVE.has(j.status)) void loadJobs();
        })
        .catch(() => undefined);
    }, 1200);
    return () => clearInterval(t);
  }, [activeId, activeRunning, loadJobs]);

  async function start() {
    setBusy(true);
    setError(null);
    try {
      const j = await api<Job>("/api/research", { method: "POST", json: { question, model } });
      setActive(j);
      setQuestion("");
      await loadJobs();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not start research");
    } finally {
      setBusy(false);
    }
  }

  async function open(id: string) {
    setActive(await api<Job>(`/api/research/${id}`));
  }

  async function cancel() {
    if (!active) return;
    setActive(await api<Job>(`/api/research/${active.id}/cancel`, { method: "POST" }));
    await loadJobs();
  }

  async function remove(id: string) {
    if (!confirm("Delete this research report?")) return;
    await api(`/api/research/${id}`, { method: "DELETE" });
    if (active?.id === id) setActive(null);
    await loadJobs();
  }

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-5xl space-y-4 p-4 md:p-6">
        <div>
          <h1 className="flex items-center gap-2 text-xl font-semibold">
            <Telescope size={20} /> Deep research
          </h1>
          <p className="mt-1 text-sm text-muted">
            Trio plans searches, reads the pages it finds and writes a report where every claim cites a numbered source. Citations are checked
            against the source list afterwards. Always open the sources for anything important.
          </p>
        </div>
        {config?.demo && (
          <p className="rounded-lg border border-accent-3/40 bg-accent-3/10 p-3 text-sm" data-testid="research-demo-note">
            Demo search: no web search key is set on this server, so research uses a few built-in example pages instead of the real web.
          </p>
        )}
        {config && !config.available && (
          <p className="rounded-lg bg-panel-2 p-3 text-sm">Web search is not configured on this server, so research is unavailable.</p>
        )}
        {error && (
          <p role="alert" className="rounded-lg bg-danger/10 p-3 text-sm text-danger">
            {error}
          </p>
        )}

        <form
          className="rounded-2xl border border-line bg-panel p-3"
          onSubmit={(e) => {
            e.preventDefault();
            if (question.trim().length >= 5) void start();
          }}
        >
          <label htmlFor="research-question" className="text-sm font-medium">
            What should Trio research?
          </label>
          <textarea
            id="research-question"
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            rows={2}
            placeholder="e.g. Are heat pumps cheaper to run than gas boilers?"
            className="mt-1 w-full resize-none rounded-lg border border-line bg-bg px-2 py-1.5 text-sm"
          />
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <ModelPicker models={models} value={model} onChange={setModel} />
            <button
              disabled={busy || !config?.available || question.trim().length < 5}
              className="ml-auto rounded-lg bg-accent px-4 py-1.5 text-sm font-medium text-accent-contrast disabled:opacity-40"
            >
              Start research
            </button>
          </div>
        </form>

        <div className="grid gap-4 md:grid-cols-[16rem_1fr]">
          <ul className="space-y-1" data-testid="research-list">
            {jobs.length === 0 && <li className="text-sm text-muted">No research yet.</li>}
            {jobs.map((j) => (
              <li key={j.id} className="group flex items-start gap-1">
                <button
                  onClick={() => void open(j.id)}
                  className={clsx("min-w-0 flex-1 rounded-lg px-3 py-2 text-left text-sm", j.id === active?.id ? "bg-accent/10" : "hover:bg-panel-2")}
                >
                  <span className="line-clamp-2">{j.question}</span>
                  <span className={clsx("text-xs", j.status === "error" ? "text-danger" : "text-muted")}>{j.status}</span>
                </button>
                <button onClick={() => void remove(j.id)} className="rounded p-1.5 text-muted opacity-0 hover:text-danger group-hover:opacity-100 focus:opacity-100" aria-label="Delete research">
                  <Trash2 size={14} />
                </button>
              </li>
            ))}
          </ul>

          <section className="min-w-0" data-testid="research-view">
            {!active ? (
              <p className="rounded-2xl border border-dashed border-line p-8 text-center text-sm text-muted">Start a research task or pick one from the list.</p>
            ) : (
              <article className="space-y-4">
                <header className="flex flex-wrap items-start gap-2">
                  <h2 className="min-w-0 flex-1 text-lg font-semibold">{active.question}</h2>
                  {ACTIVE.has(active.status) && (
                    <button onClick={() => void cancel()} className="rounded-lg border border-line px-3 py-1 text-sm hover:bg-panel-2">
                      Cancel
                    </button>
                  )}
                  {active.status === "done" && (
                    <a href={`/api/research/${active.id}/export.md`} className="inline-flex items-center gap-1 rounded-lg border border-line px-3 py-1 text-sm hover:bg-panel-2">
                      <Download size={14} /> Markdown
                    </a>
                  )}
                </header>

                <ol className="space-y-1 text-sm" aria-label="Progress" data-testid="research-steps">
                  {(active.steps ?? []).map((s, i) => {
                    const last = i === (active.steps?.length ?? 0) - 1;
                    return (
                      <li key={i} className="flex items-start gap-2">
                        {last && ACTIVE.has(active.status) ? (
                          <Loader2 size={16} className="mt-0.5 shrink-0 animate-spin text-accent" />
                        ) : last && active.status === "error" ? (
                          <XCircle size={16} className="mt-0.5 shrink-0 text-danger" />
                        ) : (
                          <CheckCircle2 size={16} className="mt-0.5 shrink-0 text-accent-2" />
                        )}
                        <span>{s.message}</span>
                      </li>
                    );
                  })}
                  {active.status === "queued" && (
                    <li className="flex items-center gap-2 text-muted">
                      <Circle size={16} /> Waiting to start
                    </li>
                  )}
                </ol>
                {active.error && <p className="rounded-lg bg-danger/10 p-3 text-sm text-danger">{active.error}</p>}
                {active.stats?.notices?.map((n) => (
                  <p key={n} className="rounded-lg border border-accent-3/40 bg-accent-3/10 px-3 py-1.5 text-xs">
                    {n}
                  </p>
                ))}

                {active.report && (
                  <div className="rounded-2xl border border-line bg-panel p-4" data-testid="research-report">
                    <p className="mb-3 text-xs text-muted">
                      Written by {active.used_model} using {active.search_provider}
                      {typeof active.stats?.citation_coverage === "number" &&
                        `. ${Math.round(active.stats.citation_coverage * 100)}% of paragraphs cite a source`}
                      {active.stats?.invalid_citations?.length ? `; ${active.stats.invalid_citations.length} invalid citation(s) were marked [?]` : ""}.
                    </p>
                    <Markdown text={active.report} />
                  </div>
                )}

                {(active.sources?.length ?? 0) > 0 && (
                  <div>
                    <h3 className="mb-2 text-sm font-semibold">Sources</h3>
                    <ol className="space-y-2 text-sm" data-testid="research-sources">
                      {active.sources!.map((s) => (
                        <li key={s.n} className="rounded-lg border border-line bg-panel p-2">
                          <div className="flex items-start gap-2">
                            <span className="font-medium">[{s.n}]</span>
                            <a href={s.url} target="_blank" rel="noopener noreferrer nofollow" className="min-w-0 flex-1 truncate text-accent underline">
                              {s.title}
                            </a>
                            <ExternalLink size={12} className="mt-1 shrink-0 text-muted" />
                            {!s.cited && <span className="text-xs text-muted">not cited</span>}
                          </div>
                          <p className="mt-1 line-clamp-2 text-xs text-muted">{s.excerpt}</p>
                        </li>
                      ))}
                    </ol>
                  </div>
                )}
              </article>
            )}
          </section>
        </div>
      </div>
    </div>
  );
}
