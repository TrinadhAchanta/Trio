"use client";

import clsx from "clsx";
import { BookOpen } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import type { KnowledgeBase } from "@/lib/types";

/** Composer button: choose which knowledge bases Trio searches for each message. */
export function KnowledgePicker({ value, onChange }: { value: string[]; onChange: (ids: string[]) => void }) {
  const [open, setOpen] = useState(false);
  const [kbs, setKbs] = useState<KnowledgeBase[] | null>(null);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    let live = true;
    api<KnowledgeBase[]>("/api/knowledge")
      .then((r) => live && setKbs(r))
      .catch(() => live && setKbs([]));
    const close = (e: MouseEvent) => ref.current && !ref.current.contains(e.target as Node) && setOpen(false);
    document.addEventListener("mousedown", close);
    return () => {
      live = false;
      document.removeEventListener("mousedown", close);
    };
  }, [open]);

  return (
    <div className="relative" ref={ref}>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        aria-label="Knowledge bases"
        title="Answer from your knowledge bases"
        data-testid="kb-picker"
        className={clsx("inline-flex items-center gap-1 rounded-md p-1.5 text-sm hover:bg-panel-2", value.length ? "text-accent" : "text-muted")}
      >
        <BookOpen size={16} />
        {value.length > 0 && <span className="text-xs">{value.length}</span>}
      </button>
      {open && (
        <div className="absolute bottom-full left-0 z-20 mb-2 w-64 rounded-xl border border-line bg-panel p-2 shadow-lg" role="dialog" aria-label="Choose knowledge bases">
          <p className="px-1 pb-1 text-xs font-medium">Search these knowledge bases</p>
          {kbs === null && <p className="px-1 text-xs text-muted">Loading...</p>}
          {kbs?.length === 0 && (
            <p className="px-1 text-xs text-muted">
              No knowledge bases yet.{" "}
              <a href="/knowledge" className="text-accent underline">
                Create one
              </a>
            </p>
          )}
          {kbs?.map((kb) => (
            <label key={kb.id} className="flex items-center gap-2 rounded px-1 py-1 text-sm hover:bg-panel-2">
              <input
                type="checkbox"
                checked={value.includes(kb.id)}
                onChange={() => onChange(value.includes(kb.id) ? value.filter((x) => x !== kb.id) : [...value, kb.id])}
              />
              <span className="truncate">{kb.name}</span>
              <span className="ml-auto text-xs text-muted">{kb.document_count} docs</span>
            </label>
          ))}
        </div>
      )}
    </div>
  );
}
