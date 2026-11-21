"use client";

import clsx from "clsx";
import { FileText, Trash2 } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { useApp } from "./AppShell";
import { DOC_FORMATS } from "./DownloadMenu";
import { Markdown } from "./Markdown";
import { ModelPicker } from "./ModelPicker";

interface Doc {
  id: string;
  title: string;
  kind: string;
  model: string | null;
  created_at: string;
  updated_at: string;
  prompt?: string | null;
  markdown?: string;
  notices?: string[];
}

const KINDS = [
  { id: "report", label: "Report" },
  { id: "memo", label: "Memo" },
  { id: "letter", label: "Letter" },
  { id: "slides", label: "Slide deck" },
  { id: "spreadsheet", label: "Spreadsheet" },
  { id: "other", label: "Other" },
];

export function DocumentsPanel() {
  const { models } = useApp();
  const [docs, setDocs] = useState<Doc[]>([]);
  const [active, setActive] = useState<Doc | null>(null);
  const [draft, setDraft] = useState("");
  const [tab, setTab] = useState<"preview" | "edit">("preview");
  const [prompt, setPrompt] = useState("");
  const [kind, setKind] = useState("report");
  const [model, setModel] = useState("auto");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  const load = useCallback(() => api<Doc[]>("/api/documents").then(setDocs), []);
  useEffect(() => {
    load().catch((e) => setError(e.message));
  }, [load]);

  function show(d: Doc) {
    setActive(d);
    setDraft(d.markdown ?? "");
    setTab("preview");
  }

  async function generate() {
    setBusy(true);
    setError(null);
    try {
      const d = await api<Doc>("/api/documents/generate", { method: "POST", json: { prompt, kind, model } });
      show(d);
      setPrompt("");
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not generate the document");
    } finally {
      setBusy(false);
    }
  }

  async function save() {
    if (!active) return;
    const d = await api<Doc>(`/api/documents/${active.id}`, { method: "PATCH", json: { markdown: draft } });
    setActive(d);
    setSaved(true);
    setTimeout(() => setSaved(false), 1200);
    await load();
  }

  async function remove(id: string) {
    if (!confirm("Delete this document?")) return;
    await api(`/api/documents/${id}`, { method: "DELETE" });
    if (active?.id === id) setActive(null);
    await load();
  }

  const dirty = active && draft !== active.markdown;

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-5xl space-y-4 p-4 md:p-6">
        <div>
          <h1 className="text-xl font-semibold">Documents</h1>
          <p className="mt-1 text-sm text-muted">
            Describe a document and Trio drafts it. Edit it here, then download it as Word, PDF, PowerPoint, Excel or Markdown. Any chat reply can
            also be downloaded from its download button.
          </p>
        </div>
        {error && (
          <p role="alert" className="rounded-lg bg-danger/10 p-3 text-sm text-danger">
            {error}
          </p>
        )}
        <form
          className="rounded-2xl border border-line bg-panel p-3"
          onSubmit={(e) => {
            e.preventDefault();
            if (prompt.trim().length >= 3) void generate();
          }}
        >
          <label htmlFor="doc-prompt" className="text-sm font-medium">
            What should the document say?
          </label>
          <textarea
            id="doc-prompt"
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            rows={3}
            placeholder="e.g. A one-page project update for the board: we shipped the beta, hired 2 engineers, next milestone is March."
            className="mt-1 w-full resize-none rounded-lg border border-line bg-bg px-2 py-1.5 text-sm"
          />
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <label className="inline-flex items-center gap-1.5 text-xs">
              Type
              <select value={kind} onChange={(e) => setKind(e.target.value)} className="rounded-md border border-line bg-panel px-2 py-1 text-sm">
                {KINDS.map((k) => (
                  <option key={k.id} value={k.id}>
                    {k.label}
                  </option>
                ))}
              </select>
            </label>
            <ModelPicker models={models} value={model} onChange={setModel} />
            <button disabled={busy || prompt.trim().length < 3} className="ml-auto rounded-lg bg-accent px-4 py-1.5 text-sm font-medium text-accent-contrast disabled:opacity-40">
              {busy ? "Writing..." : "Generate"}
            </button>
          </div>
        </form>

        <div className="grid gap-4 md:grid-cols-[16rem_1fr]">
          <ul className="space-y-1" data-testid="doc-list">
            {docs.length === 0 && <li className="text-sm text-muted">No documents yet.</li>}
            {docs.map((d) => (
              <li key={d.id} className="group flex items-start gap-1">
                <button
                  onClick={() => void api<Doc>(`/api/documents/${d.id}`).then(show)}
                  className={clsx("flex min-w-0 flex-1 items-start gap-2 rounded-lg px-3 py-2 text-left text-sm", d.id === active?.id ? "bg-accent/10" : "hover:bg-panel-2")}
                >
                  <FileText size={14} className="mt-0.5 shrink-0 text-muted" />
                  <span className="min-w-0">
                    <span className="block truncate">{d.title}</span>
                    <span className="text-xs capitalize text-muted">{d.kind}</span>
                  </span>
                </button>
                <button onClick={() => void remove(d.id)} className="rounded p-1.5 text-muted opacity-0 hover:text-danger group-hover:opacity-100 focus:opacity-100" aria-label={`Delete ${d.title}`}>
                  <Trash2 size={14} />
                </button>
              </li>
            ))}
          </ul>

          <section className="min-w-0" data-testid="doc-view">
            {!active ? (
              <p className="rounded-2xl border border-dashed border-line p-8 text-center text-sm text-muted">Generate a document or pick one from the list.</p>
            ) : (
              <div className="space-y-3">
                {active.notices?.map((n) => (
                  <p key={n} className="rounded-lg border border-accent-3/40 bg-accent-3/10 px-3 py-1.5 text-xs">
                    {n}
                  </p>
                ))}
                <div className="flex flex-wrap items-center gap-2">
                  <div role="tablist" className="inline-flex overflow-hidden rounded-md border border-line text-sm">
                    {(["preview", "edit"] as const).map((t) => (
                      <button key={t} role="tab" aria-selected={tab === t} onClick={() => setTab(t)} className={clsx("px-3 py-1 capitalize", tab === t ? "bg-accent text-accent-contrast" : "hover:bg-panel-2")}>
                        {t}
                      </button>
                    ))}
                  </div>
                  {dirty && (
                    <button onClick={() => void save()} className="rounded-lg bg-accent px-3 py-1 text-sm text-accent-contrast">
                      Save changes
                    </button>
                  )}
                  {saved && <span className="text-xs text-accent-2">Saved</span>}
                  <span className="ml-auto text-xs text-muted">Download:</span>
                  {DOC_FORMATS.map((f) => (
                    <a
                      key={f.id}
                      href={dirty ? undefined : `/api/documents/${active.id}/download?format=${f.id}`}
                      aria-disabled={!!dirty}
                      title={dirty ? "Save your changes first" : f.label}
                      className={clsx("rounded border border-line px-2 py-0.5 text-xs uppercase", dirty ? "opacity-40" : "hover:bg-panel-2")}
                    >
                      {f.id}
                    </a>
                  ))}
                </div>
                {tab === "edit" ? (
                  <textarea
                    aria-label="Document Markdown"
                    value={draft}
                    onChange={(e) => setDraft(e.target.value)}
                    rows={24}
                    className="w-full rounded-xl border border-line bg-panel p-3 font-mono text-sm"
                  />
                ) : (
                  <div className="rounded-2xl border border-line bg-panel p-4">
                    <Markdown text={draft} />
                  </div>
                )}
                {active.model && <p className="text-xs text-muted">Drafted by {active.model}. Check facts before you send it.</p>}
              </div>
            )}
          </section>
        </div>
      </div>
    </div>
  );
}
