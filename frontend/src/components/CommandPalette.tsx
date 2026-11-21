"use client";

import { useRouter } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";
import { applyTheme, getTheme } from "@/lib/theme";
import { useApp } from "./AppShell";

interface Cmd {
  id: string;
  label: string;
  hint?: string;
  run: () => void;
}

export function CommandPalette({ onClose }: { onClose: () => void }) {
  const router = useRouter();
  const { conversations } = useApp();
  const [q, setQ] = useState("");
  const [active, setActive] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);

  const commands = useMemo<Cmd[]>(() => {
    const go = (href: string) => () => router.push(href);
    const base: Cmd[] = [
      { id: "new", label: "New chat", hint: "Ctrl+Shift+O", run: go("/chat") },
      { id: "compare", label: "Compare models", run: go("/compare") },
      { id: "usage", label: "View usage and errors", run: go("/usage") },
      { id: "billing", label: "Plan and billing", run: go("/billing") },
      { id: "settings", label: "Settings", run: go("/settings") },
      {
        id: "theme",
        label: "Toggle light/dark theme",
        run: () => applyTheme(document.documentElement.classList.contains("dark") ? "light" : getTheme() === "dark" ? "light" : "dark"),
      },
    ];
    const convs = conversations.slice(0, 50).map((c) => ({ id: c.id, label: c.title, hint: "Conversation", run: go(`/chat/${c.id}`) }));
    return [...base, ...convs];
  }, [conversations, router]);

  const filtered = commands.filter((c) => c.label.toLowerCase().includes(q.toLowerCase()));

  useEffect(() => inputRef.current?.focus(), []);

  function onKeyDown(e: React.KeyboardEvent) {
    if (e.key === "Escape") onClose();
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActive((a) => Math.min(a + 1, filtered.length - 1));
    }
    if (e.key === "ArrowUp") {
      e.preventDefault();
      setActive((a) => Math.max(a - 1, 0));
    }
    if (e.key === "Enter" && filtered[active]) {
      filtered[active].run();
      onClose();
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center bg-black/40 p-4 pt-[15vh]" onMouseDown={onClose}>
      <div
        role="dialog"
        aria-modal="true"
        aria-label="Command palette"
        className="w-full max-w-lg overflow-hidden rounded-xl border border-line bg-panel shadow-2xl"
        onMouseDown={(e) => e.stopPropagation()}
      >
        <input
          ref={inputRef}
          value={q}
          onChange={(e) => {
            setQ(e.target.value);
            setActive(0);
          }}
          onKeyDown={onKeyDown}
          placeholder="Type a command or search conversations..."
          className="w-full border-b border-line bg-transparent px-4 py-3 outline-none"
          aria-label="Command"
        />
        <ul className="max-h-80 overflow-y-auto py-1" role="listbox">
          {filtered.length === 0 && <li className="px-4 py-3 text-sm text-muted">No results</li>}
          {filtered.map((c, i) => (
            <li
              key={c.id}
              role="option"
              aria-selected={i === active}
              className={`flex cursor-pointer items-center justify-between px-4 py-2 text-sm ${i === active ? "bg-panel-2" : ""}`}
              onMouseEnter={() => setActive(i)}
              onClick={() => {
                c.run();
                onClose();
              }}
            >
              <span className="truncate">{c.label}</span>
              {c.hint && <span className="ml-3 shrink-0 text-xs text-muted">{c.hint}</span>}
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
