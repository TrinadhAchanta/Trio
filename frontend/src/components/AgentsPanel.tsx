"use client";

import { Bot, MessageSquarePlus, Pencil, Plus, Trash2 } from "lucide-react";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { AgentInfo, KnowledgeBase, Reasoning } from "@/lib/types";
import { useApp } from "./AppShell";
import { ModelPicker, ReasoningPicker } from "./ModelPicker";

type Draft = Omit<AgentInfo, "id" | "created_at" | "updated_at"> & { id?: string };

const EMPTY: Draft = {
  name: "",
  description: "",
  instructions: "",
  model: "auto",
  reasoning_mode: "balanced",
  knowledge_base_ids: [],
  starters: [],
  use_memory: true,
};

export function AgentsPanel() {
  const { models, refreshConversations } = useApp();
  const router = useRouter();
  const [agents, setAgents] = useState<AgentInfo[]>([]);
  const [kbs, setKbs] = useState<KnowledgeBase[]>([]);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => api<AgentInfo[]>("/api/agents").then(setAgents), []);
  useEffect(() => {
    Promise.all([load(), api<KnowledgeBase[]>("/api/knowledge").then(setKbs)]).catch((e) => setError(e.message));
  }, [load]);

  async function save() {
    if (!draft) return;
    setBusy(true);
    setError(null);
    try {
      const body = { ...draft, starters: draft.starters.filter((s) => s.trim()) };
      if (draft.id) await api(`/api/agents/${draft.id}`, { method: "PUT", json: body });
      else await api("/api/agents", { method: "POST", json: body });
      setDraft(null);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not save the agent");
    } finally {
      setBusy(false);
    }
  }

  async function deleteAgent(a: AgentInfo) {
    if (!confirm(`Delete the agent "${a.name}"? Its past chats are kept.`)) return;
    await api(`/api/agents/${a.id}`, { method: "DELETE" });
    await load();
  }

  async function chat(a: AgentInfo) {
    const c = await api<{ id: string }>("/api/conversations", { method: "POST", json: { agent_id: a.id } });
    await refreshConversations();
    router.push(`/chat/${c.id}`);
  }

  const set = (patch: Partial<Draft>) => setDraft((d) => (d ? { ...d, ...patch } : d));

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-5xl space-y-4 p-4 md:p-6">
        <div className="flex flex-wrap items-start gap-2">
          <div className="mr-auto">
            <h1 className="text-xl font-semibold">Agents</h1>
            <p className="mt-1 text-sm text-muted">
              An agent is a reusable assistant with your own instructions, default model and knowledge bases. Start a chat with it any time.
            </p>
          </div>
          <button onClick={() => setDraft({ ...EMPTY })} className="inline-flex items-center gap-1 rounded-lg bg-accent px-3 py-1.5 text-sm font-medium text-accent-contrast">
            <Plus size={16} /> New agent
          </button>
        </div>
        {error && (
          <p role="alert" className="rounded-lg bg-danger/10 p-3 text-sm text-danger">
            {error}
          </p>
        )}

        {draft && (
          <form
            className="space-y-3 rounded-2xl border border-line bg-panel p-4"
            aria-label={draft.id ? "Edit agent" : "New agent"}
            onSubmit={(e) => {
              e.preventDefault();
              if (draft.name.trim()) void save();
            }}
          >
            <div className="grid gap-3 sm:grid-cols-2">
              <label className="block text-sm">
                Name
                <input value={draft.name} onChange={(e) => set({ name: e.target.value })} maxLength={80} required className="mt-1 w-full rounded-lg border border-line bg-bg px-2 py-1.5" />
              </label>
              <label className="block text-sm">
                Short description
                <input value={draft.description ?? ""} onChange={(e) => set({ description: e.target.value })} maxLength={300} className="mt-1 w-full rounded-lg border border-line bg-bg px-2 py-1.5" />
              </label>
            </div>
            <label className="block text-sm">
              Instructions
              <textarea
                value={draft.instructions}
                onChange={(e) => set({ instructions: e.target.value })}
                rows={6}
                placeholder="e.g. You are my writing coach. Point out unclear sentences, suggest shorter alternatives, and keep my tone."
                className="mt-1 w-full rounded-lg border border-line bg-bg px-2 py-1.5"
              />
            </label>
            <div className="flex flex-wrap items-center gap-3">
              <ModelPicker models={models} value={draft.model} onChange={(v) => set({ model: v })} />
              <ReasoningPicker value={draft.reasoning_mode} onChange={(v: Reasoning) => set({ reasoning_mode: v })} />
              <label className="inline-flex items-center gap-2 text-sm">
                <input type="checkbox" checked={draft.use_memory} onChange={(e) => set({ use_memory: e.target.checked })} />
                Use my memory
              </label>
            </div>
            <fieldset>
              <legend className="text-sm">Knowledge bases to search</legend>
              {kbs.length === 0 && <p className="text-xs text-muted">No knowledge bases yet. Create one on the Knowledge page.</p>}
              <div className="mt-1 flex flex-wrap gap-3">
                {kbs.map((kb) => (
                  <label key={kb.id} className="inline-flex items-center gap-2 text-sm">
                    <input
                      type="checkbox"
                      checked={draft.knowledge_base_ids.includes(kb.id)}
                      onChange={() =>
                        set({
                          knowledge_base_ids: draft.knowledge_base_ids.includes(kb.id)
                            ? draft.knowledge_base_ids.filter((x) => x !== kb.id)
                            : [...draft.knowledge_base_ids, kb.id],
                        })
                      }
                    />
                    {kb.name}
                  </label>
                ))}
              </div>
            </fieldset>
            <label className="block text-sm">
              Conversation starters (one per line, up to 6)
              <textarea
                value={draft.starters.join("\n")}
                onChange={(e) => set({ starters: e.target.value.split("\n").slice(0, 6) })}
                rows={3}
                className="mt-1 w-full rounded-lg border border-line bg-bg px-2 py-1.5"
              />
            </label>
            <div className="flex gap-2">
              <button disabled={busy || !draft.name.trim()} className="rounded-lg bg-accent px-4 py-1.5 text-sm font-medium text-accent-contrast disabled:opacity-40">
                Save agent
              </button>
              <button type="button" onClick={() => setDraft(null)} className="rounded-lg border border-line px-4 py-1.5 text-sm hover:bg-panel-2">
                Cancel
              </button>
            </div>
          </form>
        )}

        <ul className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3" data-testid="agent-list">
          {agents.length === 0 && !draft && (
            <li className="col-span-full rounded-2xl border border-dashed border-line p-8 text-center text-sm text-muted">No agents yet.</li>
          )}
          {agents.map((a) => (
            <li key={a.id} className="flex flex-col rounded-2xl border border-line bg-panel p-4">
              <div className="flex items-center gap-2">
                <Bot size={18} className="text-accent" />
                <h2 className="min-w-0 flex-1 truncate font-semibold">{a.name}</h2>
              </div>
              <p className="mt-1 line-clamp-2 flex-1 text-sm text-muted">{a.description || a.instructions || "No instructions yet."}</p>
              <p className="mt-2 text-xs text-muted">
                {a.model === "auto" ? "Auto model" : a.model}
                {a.knowledge_base_ids.length > 0 && ` · ${a.knowledge_base_ids.length} knowledge base(s)`}
              </p>
              <div className="mt-3 flex items-center gap-1">
                <button onClick={() => void chat(a)} className="inline-flex items-center gap-1 rounded-lg bg-accent px-3 py-1 text-sm text-accent-contrast">
                  <MessageSquarePlus size={14} /> Chat
                </button>
                <button onClick={() => setDraft({ ...a })} className="ml-auto rounded p-1.5 text-muted hover:bg-panel-2" aria-label={`Edit ${a.name}`}>
                  <Pencil size={14} />
                </button>
                <button onClick={() => void deleteAgent(a)} className="rounded p-1.5 text-muted hover:bg-panel-2 hover:text-danger" aria-label={`Delete ${a.name}`}>
                  <Trash2 size={14} />
                </button>
              </div>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
