"use client";

import clsx from "clsx";
import { BookOpen, FileText, Plus, Search, Trash2, Upload } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { api, uploadFile } from "@/lib/api";
import type { KnowledgeBase, Source } from "@/lib/types";

export function KnowledgePanel() {
  const [kbs, setKbs] = useState<KnowledgeBase[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [active, setActive] = useState<KnowledgeBase | null>(null);
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [pasteTitle, setPasteTitle] = useState("");
  const [pasteText, setPasteText] = useState("");
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<Source[] | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  const loadList = useCallback(() => api<KnowledgeBase[]>("/api/knowledge").then(setKbs), []);
  const loadActive = useCallback((id: string) => api<KnowledgeBase>(`/api/knowledge/${id}`).then(setActive), []);

  useEffect(() => {
    loadList().catch((e) => setError(e.message));
  }, [loadList]);

  useEffect(() => {
    if (activeId) loadActive(activeId).catch((e) => setError(e.message));
  }, [activeId, loadActive]);

  async function wrap(fn: () => Promise<unknown>) {
    setBusy(true);
    setError(null);
    try {
      await fn();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Something went wrong");
    } finally {
      setBusy(false);
    }
  }

  const create = () =>
    wrap(async () => {
      const kb = await api<KnowledgeBase>("/api/knowledge", { method: "POST", json: { name } });
      setName("");
      await loadList();
      setActiveId(kb.id);
      setResults(null);
    });

  const addFiles = (files: FileList) =>
    wrap(async () => {
      for (const f of Array.from(files)) {
        const up = await uploadFile(f);
        await api(`/api/knowledge/${activeId}/documents`, { method: "POST", json: { file_id: up.id } });
      }
      await Promise.all([loadActive(activeId!), loadList()]);
    });

  const addText = () =>
    wrap(async () => {
      await api(`/api/knowledge/${activeId}/documents`, { method: "POST", json: { title: pasteTitle, text: pasteText } });
      setPasteTitle("");
      setPasteText("");
      await Promise.all([loadActive(activeId!), loadList()]);
    });

  const removeDoc = (docId: string) =>
    wrap(async () => {
      await api(`/api/knowledge/${activeId}/documents/${docId}`, { method: "DELETE" });
      await Promise.all([loadActive(activeId!), loadList()]);
    });

  const removeKb = () => {
    if (!active || !confirm(`Delete the knowledge base "${active.name}" and all its documents?`)) return;
    void wrap(async () => {
      await api(`/api/knowledge/${active.id}`, { method: "DELETE" });
      setActiveId(null);
      setActive(null);
      await loadList();
    });
  };

  const search = () =>
    wrap(async () => {
      const r = await api<{ results: Source[] }>(`/api/knowledge/${activeId}/search`, { method: "POST", json: { query } });
      setResults(r.results.map((x, i) => ({ ...x, n: i + 1 })));
    });

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-5xl space-y-4 p-4 md:p-6">
        <div>
          <h1 className="text-xl font-semibold">Knowledge</h1>
          <p className="mt-1 text-sm text-muted">
            Add documents to a knowledge base, then pick it with the <BookOpen size={14} className="inline" /> button in chat. Trio finds the
            most relevant passages for each message and the answer cites them. Search is keyword-based (full-text) for now.
          </p>
        </div>
        {error && (
          <p role="alert" className="rounded-lg bg-danger/10 p-3 text-sm text-danger">
            {error}
          </p>
        )}
        <div className="grid gap-4 md:grid-cols-[16rem_1fr]">
          <aside className="space-y-2">
            <form
              className="flex gap-2"
              onSubmit={(e) => {
                e.preventDefault();
                if (name.trim()) void create();
              }}
            >
              <label htmlFor="kb-name" className="sr-only">
                New knowledge base name
              </label>
              <input
                id="kb-name"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="New knowledge base"
                className="min-w-0 flex-1 rounded-lg border border-line bg-panel px-2 py-1.5 text-sm"
              />
              <button disabled={busy || !name.trim()} className="rounded-lg bg-accent p-2 text-accent-contrast disabled:opacity-40" aria-label="Create knowledge base">
                <Plus size={16} />
              </button>
            </form>
            <ul className="space-y-1" data-testid="kb-list">
              {kbs.length === 0 && <li className="text-sm text-muted">No knowledge bases yet.</li>}
              {kbs.map((kb) => (
                <li key={kb.id}>
                  <button
                    onClick={() => {
                      setActiveId(kb.id);
                      setResults(null);
                    }}
                    className={clsx("w-full rounded-lg px-3 py-2 text-left text-sm", kb.id === activeId ? "bg-accent/10" : "hover:bg-panel-2")}
                  >
                    <span className="block truncate font-medium">{kb.name}</span>
                    <span className="text-xs text-muted">
                      {kb.document_count} documents, {kb.chunk_count} passages
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          </aside>

          <section className="min-w-0 space-y-4">
            {!active ? (
              <p className="rounded-2xl border border-dashed border-line p-8 text-center text-sm text-muted">Create or pick a knowledge base.</p>
            ) : (
              <>
                <div className="flex items-center gap-2">
                  <h2 className="text-lg font-semibold">{active.name}</h2>
                  <button onClick={removeKb} className="ml-auto rounded-lg p-2 text-muted hover:bg-panel-2 hover:text-danger" aria-label="Delete knowledge base">
                    <Trash2 size={16} />
                  </button>
                </div>

                <div className="rounded-2xl border border-line bg-panel p-4">
                  <div className="flex flex-wrap items-center gap-2">
                    <h3 className="text-sm font-semibold">Documents</h3>
                    <input
                      ref={fileRef}
                      type="file"
                      multiple
                      hidden
                      data-testid="kb-file-input"
                      accept=".txt,.md,.csv,.tsv,.json,.py,.js,.ts,.tsx,.jsx,.html,.css,.xml,.yaml,.yml,.sql,.pdf"
                      onChange={(e) => {
                        if (e.target.files?.length) void addFiles(e.target.files);
                        e.target.value = "";
                      }}
                    />
                    <button
                      onClick={() => fileRef.current?.click()}
                      disabled={busy}
                      className="ml-auto inline-flex items-center gap-1 rounded-lg border border-line px-3 py-1.5 text-sm hover:bg-panel-2 disabled:opacity-50"
                    >
                      <Upload size={14} /> Upload files
                    </button>
                  </div>
                  <ul className="mt-3 divide-y divide-line text-sm" data-testid="kb-documents">
                    {active.documents?.length === 0 && <li className="py-2 text-muted">No documents yet. Upload text, Markdown, CSV, JSON, code or PDF files, or paste text below.</li>}
                    {active.documents?.map((d) => (
                      <li key={d.id} className="flex items-center gap-2 py-2">
                        <FileText size={14} className="shrink-0 text-muted" />
                        <span className="min-w-0 flex-1 truncate">{d.title}</span>
                        <span className="text-xs text-muted">
                          {d.chunk_count} passages, {Math.round(d.chars / 1000)}k chars
                        </span>
                        <button onClick={() => void removeDoc(d.id)} className="rounded p-1 text-muted hover:text-danger" aria-label={`Remove ${d.title}`}>
                          <Trash2 size={14} />
                        </button>
                      </li>
                    ))}
                  </ul>
                  <details className="mt-3">
                    <summary className="cursor-pointer text-sm text-accent">Paste text instead</summary>
                    <div className="mt-2 space-y-2">
                      <label className="block text-xs">
                        Title
                        <input value={pasteTitle} onChange={(e) => setPasteTitle(e.target.value)} className="mt-1 w-full rounded-lg border border-line bg-bg px-2 py-1.5 text-sm" />
                      </label>
                      <label className="block text-xs">
                        Text
                        <textarea value={pasteText} onChange={(e) => setPasteText(e.target.value)} rows={6} className="mt-1 w-full rounded-lg border border-line bg-bg px-2 py-1.5 text-sm" />
                      </label>
                      <button
                        onClick={() => void addText()}
                        disabled={busy || !pasteTitle.trim() || !pasteText.trim()}
                        className="rounded-lg bg-accent px-3 py-1.5 text-sm text-accent-contrast disabled:opacity-40"
                      >
                        Add text
                      </button>
                    </div>
                  </details>
                </div>

                <div className="rounded-2xl border border-line bg-panel p-4">
                  <h3 className="text-sm font-semibold">Test a search</h3>
                  <form
                    className="mt-2 flex gap-2"
                    onSubmit={(e) => {
                      e.preventDefault();
                      if (query.trim()) void search();
                    }}
                  >
                    <label htmlFor="kb-query" className="sr-only">
                      Search query
                    </label>
                    <input id="kb-query" value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Ask a question" className="min-w-0 flex-1 rounded-lg border border-line bg-bg px-2 py-1.5 text-sm" />
                    <button disabled={busy || !query.trim()} className="inline-flex items-center gap-1 rounded-lg border border-line px-3 py-1.5 text-sm hover:bg-panel-2 disabled:opacity-50">
                      <Search size={14} /> Search
                    </button>
                  </form>
                  {results && (
                    <ol className="mt-3 space-y-2 text-sm" data-testid="kb-results">
                      {results.length === 0 && <li className="text-muted">No matching passages.</li>}
                      {results.map((r) => (
                        <li key={r.chunk_id} className="rounded-lg bg-panel-2 p-2">
                          <p className="text-xs font-medium">
                            [{r.n}] {r.title}
                          </p>
                          <p className="mt-1 line-clamp-4 whitespace-pre-wrap text-xs text-muted">{r.content}</p>
                        </li>
                      ))}
                    </ol>
                  )}
                </div>
              </>
            )}
          </section>
        </div>
      </div>
    </div>
  );
}
