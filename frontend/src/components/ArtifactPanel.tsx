"use client";

import clsx from "clsx";
import { AlertTriangle, Code2, Download, Eye, Maximize2, Minimize2, RotateCcw, Save, X } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "@/lib/api";
import type { ArtifactDetail } from "@/lib/types";

// Everything the artifact runs is confined to an opaque-origin sandboxed iframe (no
// allow-same-origin), so it cannot read Trio cookies or call the API. The CSP also blocks all
// network access, so generated code cannot load remote scripts or exfiltrate data.
const CSP =
  "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data: blob:; font-src data:; media-src data: blob:; form-action 'none'; base-uri 'none'";

const REPORTER = `<script>(function(){function send(m){try{parent.postMessage({__trio:'artifact-error',message:String(m)},'*')}catch(e){}}
window.addEventListener('error',function(e){send((e.message||'Error')+(e.lineno?' (line '+e.lineno+')':''))});
window.addEventListener('unhandledrejection',function(e){send('Unhandled promise rejection: '+(e.reason&&e.reason.message||e.reason))});
document.addEventListener('securitypolicyviolation',function(e){send('Blocked by sandbox: '+e.violatedDirective+' '+(e.blockedURI||''))});})();</script>`;

export function buildSrcDoc(kind: string, content: string): string {
  const head = `<meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="${CSP}"><meta name="viewport" content="width=device-width, initial-scale=1">${REPORTER}`;
  if (kind === "svg") {
    return `<!doctype html><html><head>${head}<style>html,body{margin:0;height:100%;display:grid;place-items:center;background:#fff}svg{max-width:100%;max-height:100vh}</style></head><body>${content}</body></html>`;
  }
  // Put CSP + reporter first so they apply before any artifact code runs.
  const headMatch = content.match(/<head[^>]*>/i);
  if (headMatch && headMatch.index !== undefined) {
    const at = headMatch.index + headMatch[0].length;
    return content.slice(0, at) + head + content.slice(at);
  }
  return `<!doctype html><html><head>${head}</head><body>${content}</body></html>`;
}

export function ArtifactPanel({ artifactId, onClose }: { artifactId: string; onClose: () => void }) {
  const [art, setArt] = useState<ArtifactDetail | null>(null);
  const [version, setVersion] = useState<number | null>(null);
  const [tab, setTab] = useState<"preview" | "code">("preview");
  const [draft, setDraft] = useState("");
  const [errors, setErrors] = useState<string[]>([]);
  const [full, setFull] = useState(false);
  const [busy, setBusy] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const frameRef = useRef<HTMLIFrameElement>(null);

  useEffect(() => {
    api<ArtifactDetail>(`/api/artifacts/${artifactId}`)
      .then((a) => {
        setArt(a);
        setVersion(a.current_version);
      })
      .catch((e) => setLoadError(e instanceof Error ? e.message : "Could not load artifact"));
  }, [artifactId]);

  const current = useMemo(() => art?.versions.find((v) => v.version === version) ?? null, [art, version]);

  // Reset the editor when the shown version changes (state adjustment during render).
  const [draftFor, setDraftFor] = useState(current);
  if (current !== draftFor) {
    setDraftFor(current);
    setDraft(current?.content ?? "");
    setErrors([]);
  }

  useEffect(() => {
    function onMsg(e: MessageEvent) {
      if (e.source !== frameRef.current?.contentWindow) return;
      if (e.data?.__trio === "artifact-error") setErrors((xs) => [...xs.slice(-9), String(e.data.message)]);
    }
    window.addEventListener("message", onMsg);
    return () => window.removeEventListener("message", onMsg);
  }, []);

  const dirty = current ? draft !== current.content : false;
  const srcDoc = useMemo(() => (art ? buildSrcDoc(art.kind, tab === "code" || dirty ? draft : (current?.content ?? "")) : ""), [art, current, draft, dirty, tab]);

  async function saveVersion() {
    if (!art) return;
    setBusy(true);
    try {
      const a = await api<ArtifactDetail>(`/api/artifacts/${art.id}/versions`, { method: "POST", json: { content: draft } });
      setArt(a);
      setVersion(a.current_version);
    } finally {
      setBusy(false);
    }
  }

  async function restore() {
    if (!art || version === null) return;
    setBusy(true);
    try {
      const a = await api<ArtifactDetail>(`/api/artifacts/${art.id}/restore`, { method: "POST", json: { version } });
      setArt(a);
      setVersion(a.current_version);
    } finally {
      setBusy(false);
    }
  }

  const btn = "inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs hover:bg-panel-2 disabled:opacity-50";

  return (
    <section
      aria-label="Artifact workspace"
      data-testid="artifact-panel"
      className={clsx("flex min-h-0 flex-col border-l border-line bg-panel", full ? "fixed inset-0 z-40" : "h-full")}
    >
      <header className="flex flex-wrap items-center gap-1 border-b border-line px-3 py-2">
        <h2 className="mr-auto truncate text-sm font-semibold" title={art?.title}>
          {art?.title ?? "Loading..."}
        </h2>
        <div className="inline-flex overflow-hidden rounded-md border border-line">
          <button className={clsx(btn, "rounded-none", tab === "preview" && "bg-panel-2 font-medium")} onClick={() => setTab("preview")} aria-pressed={tab === "preview"}>
            <Eye size={14} /> Preview
          </button>
          <button className={clsx(btn, "rounded-none", tab === "code" && "bg-panel-2 font-medium")} onClick={() => setTab("code")} aria-pressed={tab === "code"}>
            <Code2 size={14} /> Code
          </button>
        </div>
        {art && (
          <label className="text-xs">
            <span className="sr-only">Version</span>
            <select value={version ?? ""} onChange={(e) => setVersion(Number(e.target.value))} className="rounded-md border border-line bg-panel px-1.5 py-1">
              {[...art.versions].reverse().map((v) => (
                <option key={v.version} value={v.version}>
                  v{v.version} ({v.source === "ai" ? "AI" : v.source === "restore" ? "restored" : "edited"})
                </option>
              ))}
            </select>
          </label>
        )}
        {art && version !== art.current_version && (
          <button className={btn} onClick={restore} disabled={busy} title="Make this version the latest">
            <RotateCcw size={14} /> Restore
          </button>
        )}
        {dirty && (
          <>
            <button className={btn} onClick={() => setDraft(current?.content ?? "")} title="Undo your edits">
              <RotateCcw size={14} /> Undo
            </button>
            <button className={clsx(btn, "bg-accent text-accent-contrast hover:bg-accent")} onClick={saveVersion} disabled={busy}>
              <Save size={14} /> Save version
            </button>
          </>
        )}
        {art && (
          <a className={btn} href={`/api/artifacts/${art.id}/download?version=${version}`} download>
            <Download size={14} /> Download
          </a>
        )}
        <button className={btn} onClick={() => setFull((f) => !f)} aria-label={full ? "Exit full screen" : "Full screen"}>
          {full ? <Minimize2 size={14} /> : <Maximize2 size={14} />}
        </button>
        <button className={btn} onClick={onClose} aria-label="Close artifact">
          <X size={14} />
        </button>
      </header>
      {loadError && <p className="p-4 text-sm text-danger">{loadError}</p>}
      <div className={clsx("grid min-h-0 flex-1", tab === "code" ? "grid-rows-2" : "grid-rows-1")}>
        {tab === "code" && (
          <textarea
            aria-label="Artifact source code"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            spellCheck={false}
            className="min-h-0 w-full resize-none border-b border-line bg-[var(--code-bg)] p-3 font-mono text-xs leading-relaxed outline-none"
          />
        )}
        <iframe
          ref={frameRef}
          title={`Preview of ${art?.title ?? "artifact"}`}
          sandbox="allow-scripts allow-modals"
          srcDoc={srcDoc}
          className="h-full min-h-0 w-full bg-white"
          data-testid="artifact-frame"
        />
      </div>
      {errors.length > 0 && (
        <div role="status" className="max-h-28 overflow-y-auto border-t border-line bg-danger/10 px-3 py-2 text-xs text-danger">
          <p className="mb-1 flex items-center gap-1 font-medium">
            <AlertTriangle size={14} /> {errors.length} problem{errors.length > 1 ? "s" : ""} in the preview
          </p>
          {errors.map((e, i) => (
            <p key={i} className="font-mono">
              {e}
            </p>
          ))}
        </div>
      )}
    </section>
  );
}
