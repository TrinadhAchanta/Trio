"use client";

import { Trash2 } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { User } from "@/lib/types";

interface Item {
  id: string;
  content: string;
  source: string;
  created_at: string;
}

/** Settings section: the facts the user asked Trio to remember. Nothing is added without a user action. */
export function MemorySettings({ enabled, onToggle }: { enabled: boolean; onToggle: (on: boolean) => Promise<User | void> }) {
  const [items, setItems] = useState<Item[]>([]);
  const [text, setText] = useState("");

  const load = useCallback(() => api<{ items: Item[] }>("/api/memory").then((r) => setItems(r.items)), []);
  useEffect(() => {
    load().catch(() => undefined);
  }, [load]);

  async function add() {
    await api("/api/memory", { method: "POST", json: { content: text } });
    setText("");
    await load();
  }

  async function forget(id: string) {
    await api(`/api/memory/${id}`, { method: "DELETE" });
    await load();
  }

  async function clearAll() {
    if (!confirm("Forget everything in your memory?")) return;
    await api("/api/memory", { method: "DELETE" });
    await load();
  }

  return (
    <section className="rounded-2xl border border-line bg-panel p-4" data-testid="memory-settings">
      <h2 className="mb-1 text-sm font-semibold">Memory</h2>
      <p className="mb-3 text-xs text-muted">
        Facts you ask Trio to remember are added to every chat so you don&apos;t have to repeat them. Trio only remembers what you add here or
        save with the bookmark button on your messages.
      </p>
      <label className="flex items-center gap-2 text-sm">
        <input type="checkbox" checked={enabled} onChange={(e) => void onToggle(e.target.checked)} data-testid="memory-enabled" />
        Use memory in chats
      </label>
      <form
        className="mt-3 flex gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          if (text.trim()) void add();
        }}
      >
        <label htmlFor="memory-new" className="sr-only">
          Something to remember
        </label>
        <input
          id="memory-new"
          value={text}
          onChange={(e) => setText(e.target.value)}
          maxLength={1000}
          placeholder="e.g. I prefer metric units"
          className="min-w-0 flex-1 rounded-lg border border-line bg-bg px-2 py-1.5 text-sm"
        />
        <button disabled={!text.trim()} className="rounded-lg bg-accent px-3 py-1.5 text-sm text-accent-contrast disabled:opacity-40">
          Remember
        </button>
      </form>
      <ul className="mt-3 divide-y divide-line text-sm" data-testid="memory-items">
        {items.length === 0 && <li className="py-2 text-muted">Nothing remembered yet.</li>}
        {items.map((m) => (
          <li key={m.id} className="flex items-start gap-2 py-2">
            <span className="min-w-0 flex-1">{m.content}</span>
            <button onClick={() => void forget(m.id)} className="rounded p-1 text-muted hover:text-danger" aria-label={`Forget: ${m.content.slice(0, 40)}`}>
              <Trash2 size={14} />
            </button>
          </li>
        ))}
      </ul>
      {items.length > 0 && (
        <button onClick={() => void clearAll()} className="mt-2 text-xs text-danger underline">
          Forget everything
        </button>
      )}
    </section>
  );
}
