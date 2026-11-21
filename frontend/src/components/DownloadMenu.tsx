"use client";

import { Download } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { downloadPost } from "@/lib/api";

export const DOC_FORMATS = [
  { id: "docx", label: "Word (.docx)" },
  { id: "pdf", label: "PDF" },
  { id: "pptx", label: "PowerPoint (.pptx)" },
  { id: "xlsx", label: "Excel (.xlsx, tables)" },
  { id: "md", label: "Markdown" },
] as const;

/** Small menu that converts a Markdown text to an office file and downloads it. */
export function DownloadMenu({ markdown, title, className }: { markdown: string; title?: string; className?: string }) {
  const [open, setOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => ref.current && !ref.current.contains(e.target as Node) && setOpen(false);
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, [open]);

  return (
    <div className="relative inline-block" ref={ref}>
      <button className={className} onClick={() => setOpen((o) => !o)} aria-label="Download as a file" aria-expanded={open} title="Download as Word, PDF and more">
        <Download size={14} />
      </button>
      {open && (
        <div role="menu" className="absolute bottom-full left-0 z-20 mb-1 w-48 rounded-lg border border-line bg-panel p-1 text-sm shadow-lg">
          {DOC_FORMATS.map((f) => (
            <button
              key={f.id}
              role="menuitem"
              className="block w-full rounded px-2 py-1.5 text-left hover:bg-panel-2"
              onClick={() => {
                setOpen(false);
                setError(null);
                downloadPost("/api/documents/export", { markdown, format: f.id, title }).catch((e) => setError(e.message));
              }}
            >
              {f.label}
            </button>
          ))}
        </div>
      )}
      {error && (
        <span role="alert" className="ml-2 text-xs text-danger">
          {error}
        </span>
      )}
    </div>
  );
}
