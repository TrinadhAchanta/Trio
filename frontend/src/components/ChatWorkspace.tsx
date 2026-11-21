"use client";

import clsx from "clsx";
import {
  AlertTriangle,
  ArrowUp,
  Check,
  ChevronLeft,
  ChevronRight,
  Copy,
  Download,
  FileText,
  Image as ImageIcon,
  LayoutPanelLeft,
  Paperclip,
  Pencil,
  RefreshCw,
  Square,
  ThumbsDown,
  ThumbsUp,
  Trash2,
  X,
} from "lucide-react";
import { useParams, useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, streamSSE, uploadFile } from "@/lib/api";
import type { Artifact, ConversationDetail, FileInfo, Message, Reasoning } from "@/lib/types";
import { useApp } from "./AppShell";
import { ArtifactPanel } from "./ArtifactPanel";
import { Markdown } from "./Markdown";
import { ModelPicker, ReasoningPicker, modelLabel } from "./ModelPicker";

interface PathItem {
  msg: Message;
  siblings: Message[];
}

interface StreamState {
  parentKey: string;
  user?: Message;
  text: string;
  modelLabel?: string;
  mock?: boolean;
  routeReason?: string;
  error?: string;
}

const ROOT = "root";

function computePath(messages: Message[], selection: Record<string, string>): PathItem[] {
  const children = new Map<string, Message[]>();
  for (const m of [...messages].sort((a, b) => a.created_at.localeCompare(b.created_at))) {
    const key = m.parent_id ?? ROOT;
    if (!children.has(key)) children.set(key, []);
    children.get(key)!.push(m);
  }
  const path: PathItem[] = [];
  let key = ROOT;
  const guard = new Set<string>();
  for (;;) {
    const kids = children.get(key);
    if (!kids?.length || guard.has(key)) break;
    guard.add(key);
    const pick = kids.find((k) => k.id === selection[key]) ?? kids[kids.length - 1];
    path.push({ msg: pick, siblings: kids });
    key = pick.id;
  }
  return path;
}

const SUGGESTIONS = [
  "Build an interactive tip calculator as a web page",
  "Explain the difference between TCP and UDP with a table",
  "Write a Python function that checks if a string is a palindrome",
  "Summarize the key ideas of supply and demand",
];

export function ChatWorkspace() {
  const params = useParams<{ id?: string }>();
  const router = useRouter();
  const { models, refreshConversations, user } = useApp();
  const [activeId, setActiveId] = useState<string | null>(params?.id ?? null);
  const [detail, setDetail] = useState<ConversationDetail | null>(null);
  const [artifacts, setArtifacts] = useState<Artifact[]>([]);
  const [selection, setSelection] = useState<Record<string, string>>({});
  const [stream, setStream] = useState<StreamState | null>(null);
  const [model, setModel] = useState<string>(user.preferences.default_model ?? "auto");
  const [mode, setMode] = useState<Reasoning>(user.preferences.reasoning_mode ?? "balanced");
  const [input, setInput] = useState("");
  const [files, setFiles] = useState<FileInfo[]>([]);
  const [uploading, setUploading] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [openArtifact, setOpenArtifact] = useState<string | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const realConfigured = models?.providers.some((p) => p.configured && !p.mock) ?? true;

  // Follow URL changes (sidebar navigation), but not while a reply is streaming.
  // Done as a state adjustment during render rather than in an effect.
  const urlId = params?.id ?? null;
  const [seenUrlId, setSeenUrlId] = useState(urlId);
  if (urlId !== seenUrlId && !stream) {
    setSeenUrlId(urlId);
    if (urlId !== activeId) {
      setActiveId(urlId);
      setOpenArtifact(null);
      setSelection({});
      setDetail(null);
      setArtifacts([]);
    }
  }

  const apply = useCallback((d: ConversationDetail, arts: Artifact[]) => {
    setDetail(d);
    setArtifacts(arts);
    setLoadError(null);
    if (d.default_model) setModel(d.default_model);
    setMode(d.reasoning_mode as Reasoning);
  }, []);

  const load = useCallback(
    async (id: string) => {
      try {
        const [d, arts] = await fetchConversation(id);
        apply(d, arts);
      } catch (e) {
        setLoadError(e instanceof Error ? e.message : "Could not load conversation");
      }
    },
    [apply],
  );

  useEffect(() => {
    if (!activeId || detail?.id === activeId) return;
    let cancelled = false;
    fetchConversation(activeId)
      .then(([d, arts]) => !cancelled && apply(d, arts))
      .catch((e) => !cancelled && setLoadError(e instanceof Error ? e.message : "Could not load conversation"));
    return () => {
      cancelled = true;
    };
  }, [activeId]); // eslint-disable-line react-hooks/exhaustive-deps

  const path = useMemo(() => computePath(detail?.messages ?? [], selection), [detail, selection]);

  useEffect(() => {
    const el = scrollRef.current;
    if (el && el.scrollHeight - el.scrollTop - el.clientHeight < 200) el.scrollTop = el.scrollHeight;
  }, [stream?.text, path.length]);

  async function run(kind: "send" | "regenerate", opts: { content?: string; parentId?: string | null; userMessageId?: string }) {
    setNotice(null);
    let convId = activeId;
    if (!convId) {
      const c = await api<{ id: string }>("/api/conversations", { method: "POST", json: { reasoning_mode: mode } });
      convId = c.id;
      setActiveId(convId);
      window.history.replaceState(null, "", `/chat/${convId}`);
    }
    const parentKey = kind === "regenerate" ? opts.userMessageId! : (opts.parentId ?? ROOT);
    const optimisticUser: Message | undefined =
      kind === "send"
        ? {
            id: "pending-user",
            conversation_id: convId,
            parent_id: opts.parentId ?? null,
            role: "user",
            content: opts.content ?? "",
            provider: null,
            model: null,
            status: "complete",
            error: null,
            attachments: files.map((f) => f.id),
            input_tokens: null,
            output_tokens: null,
            latency_ms: null,
            rating: null,
            created_at: new Date().toISOString(),
          }
        : undefined;
    // Hide the old branch while the new one streams by selecting nothing below parentKey.
    setStream({ parentKey, user: optimisticUser, text: "" });
    const controller = new AbortController();
    abortRef.current = controller;
    const url = kind === "send" ? `/api/conversations/${convId}/messages` : `/api/conversations/${convId}/regenerate`;
    const body =
      kind === "send"
        ? {
            content: opts.content,
            model,
            reasoning_mode: mode,
            attachments: files.map((f) => f.id),
            ...(opts.parentId ? { parent_id: opts.parentId } : { branch_from_root: true }),
          }
        : { user_message_id: opts.userMessageId, model, reasoning_mode: mode };
    if (kind === "send") {
      setInput("");
      setFiles([]);
    }
    try {
      await streamSSE(
        url,
        body,
        (event, data) => {
          if (event === "meta") setStream((s) => s && { ...s, modelLabel: data.model_label, mock: data.mock, routeReason: data.route_reason });
          else if (event === "delta") setStream((s) => s && { ...s, text: s.text + data.text });
          else if (event === "error") setStream((s) => s && { ...s, error: data.message });
          else if (event === "done" && data.artifacts?.length) {
            setArtifacts((a) => [...data.artifacts, ...a]);
            setOpenArtifact(data.artifacts[0].id);
          }
        },
        controller.signal,
      );
    } catch (e) {
      if (!(e instanceof DOMException && e.name === "AbortError")) {
        setNotice(e instanceof Error ? e.message : "The request failed.");
        if (kind === "send" && opts.content) setInput(opts.content);
      }
    } finally {
      abortRef.current = null;
      // A stopped stream is saved by the server as soon as it notices the disconnect.
      if (controller.signal.aborted) await new Promise((r) => setTimeout(r, 400));
      setSelection((sel) => {
        const next = { ...sel };
        delete next[parentKey];
        return next;
      });
      await load(convId);
      setStream(null);
      void refreshConversations();
    }
  }

  function submit() {
    const text = input.trim();
    if (!text || stream || uploading) return;
    const last = path[path.length - 1]?.msg;
    void run("send", { content: text, parentId: last?.id ?? null });
  }

  async function attach(list: FileList | File[]) {
    setUploading(true);
    setNotice(null);
    try {
      for (const f of Array.from(list).slice(0, 10)) {
        const info = await uploadFile(f);
        setFiles((xs) => [...xs, info]);
      }
    } catch (e) {
      setNotice(e instanceof Error ? e.message : "Upload failed");
    } finally {
      setUploading(false);
    }
  }

  async function rate(msg: Message, value: 1 | -1) {
    const updated = await api<Message>(`/api/messages/${msg.id}/rating`, { method: "POST", json: { rating: msg.rating === value ? null : value } });
    setDetail((d) => d && { ...d, messages: d.messages.map((m) => (m.id === updated.id ? updated : m)) });
  }

  async function deleteConversation() {
    if (!detail || !confirm("Delete this conversation? This cannot be undone.")) return;
    await api(`/api/conversations/${detail.id}`, { method: "DELETE" });
    await refreshConversations();
    router.push("/chat");
  }

  async function rename() {
    if (!detail) return;
    const title = prompt("Rename conversation", detail.title)?.trim();
    if (!title) return;
    await api(`/api/conversations/${detail.id}`, { method: "PATCH", json: { title } });
    setDetail({ ...detail, title });
    void refreshConversations();
  }

  // While streaming, cut the visible path at the branch point and show the live turn.
  const visible = stream ? cutPath(path, stream.parentKey) : path;

  return (
    <div className="flex h-full min-h-0">
      <div className={clsx("flex min-w-0 flex-1 flex-col", openArtifact && "hidden lg:flex")}>
        {detail && (
          <div className="flex items-center gap-2 border-b border-line px-4 py-2">
            <h1 className="mr-auto truncate text-sm font-semibold" title={detail.title}>
              {detail.title}
            </h1>
            <button className="rounded p-1.5 text-muted hover:bg-panel-2" onClick={rename} aria-label="Rename conversation" title="Rename">
              <Pencil size={15} />
            </button>
            <a className="rounded p-1.5 text-muted hover:bg-panel-2" href={`/api/conversations/${detail.id}/export?format=md`} aria-label="Export as Markdown" title="Export Markdown">
              <Download size={15} />
            </a>
            {artifacts.length > 0 && !openArtifact && (
              <button className="rounded p-1.5 text-muted hover:bg-panel-2" onClick={() => setOpenArtifact(artifacts[0].id)} aria-label="Open artifacts" title="Artifacts">
                <LayoutPanelLeft size={15} />
              </button>
            )}
            <button className="rounded p-1.5 text-muted hover:bg-panel-2" onClick={deleteConversation} aria-label="Delete conversation" title="Delete">
              <Trash2 size={15} />
            </button>
          </div>
        )}
        {!realConfigured && (
          <div className="border-b border-line bg-accent-3/10 px-4 py-2 text-xs" role="note" data-testid="demo-banner">
            <strong>Demo mode:</strong> no AI provider API keys are configured on the server, so replies come from the built-in Mock provider, not a real AI
            model. Add a key to <code>backend/.env</code> to enable OpenAI, Anthropic, Gemini or xAI.
          </div>
        )}
        <div ref={scrollRef} className="min-h-0 flex-1 overflow-y-auto" data-testid="messages">
          <div className="mx-auto w-full max-w-3xl px-4 py-6">
            {loadError && <p className="rounded-lg bg-danger/10 p-3 text-sm text-danger">{loadError}</p>}
            {!activeId && !stream && (
              <div className="mt-[10vh] text-center">
                <h1 className="text-2xl font-semibold">What can Trio help you with?</h1>
                <p className="mt-2 text-sm text-muted">Pick a model or let Auto choose. Ask for a web page or app to get a live preview.</p>
                <div className="mx-auto mt-6 grid max-w-xl gap-2 sm:grid-cols-2">
                  {SUGGESTIONS.map((s) => (
                    <button key={s} className="rounded-xl border border-line bg-panel p-3 text-left text-sm hover:bg-panel-2" onClick={() => setInput(s)}>
                      {s}
                    </button>
                  ))}
                </div>
              </div>
            )}
            {visible.map(({ msg, siblings }) => (
              <MessageRow
                key={msg.id}
                msg={msg}
                siblings={siblings}
                modelsLabel={(id) => modelLabel(models, id)}
                artifacts={artifacts.filter((a) => a.message_id === msg.id)}
                busy={!!stream}
                onSelectSibling={(id) => setSelection((s) => ({ ...s, [msg.parent_id ?? ROOT]: id }))}
                onEdit={(content) => void run("send", { content, parentId: msg.parent_id })}
                onRegenerate={() => msg.parent_id && void run("regenerate", { userMessageId: msg.parent_id })}
                onRate={(v) => void rate(msg, v)}
                onOpenArtifact={setOpenArtifact}
              />
            ))}
            {stream?.user && <MessageRow msg={stream.user} siblings={[stream.user]} busy modelsLabel={(id) => id} artifacts={[]} />}
            {stream && (
              <div className="mb-6" data-testid="streaming-reply" aria-live="polite">
                <AssistantHeader label={stream.modelLabel ?? "Thinking..."} mock={stream.mock} routeReason={stream.routeReason} />
                {stream.text ? (
                  <div className={clsx(!stream.error && "cursor-blink")}>
                    <Markdown text={stream.text} />
                  </div>
                ) : (
                  !stream.error && <p className="cursor-blink text-sm text-muted">Waiting for the model</p>
                )}
                {stream.error && <ErrorBox text={stream.error} />}
              </div>
            )}
          </div>
        </div>
        <div className="border-t border-line bg-bg px-4 py-3">
          <div className="mx-auto w-full max-w-3xl">
            {notice && (
              <p role="alert" className="mb-2 flex items-center justify-between rounded-lg bg-danger/10 px-3 py-2 text-sm text-danger">
                <span>
                  {notice}
                  {/credits/i.test(notice) && (
                    <a href="/billing" className="ml-2 font-medium underline">
                      View plans
                    </a>
                  )}
                </span>
                <button onClick={() => setNotice(null)} aria-label="Dismiss">
                  <X size={14} />
                </button>
              </p>
            )}
            {files.length > 0 && (
              <div className="mb-2 flex flex-wrap gap-2" data-testid="pending-attachments">
                {files.map((f) => (
                  <span key={f.id} className="inline-flex items-center gap-1 rounded-full border border-line bg-panel px-2 py-0.5 text-xs">
                    {f.kind === "image" ? <ImageIcon size={12} /> : <FileText size={12} />}
                    {f.filename}
                    <button onClick={() => setFiles((xs) => xs.filter((x) => x.id !== f.id))} aria-label={`Remove ${f.filename}`}>
                      <X size={12} />
                    </button>
                  </span>
                ))}
              </div>
            )}
            <div
              className="rounded-2xl border border-line bg-panel p-2 shadow-sm focus-within:border-accent"
              onDragOver={(e) => e.preventDefault()}
              onDrop={(e) => {
                e.preventDefault();
                if (e.dataTransfer.files.length) void attach(e.dataTransfer.files);
              }}
            >
              <label htmlFor="composer" className="sr-only">
                Message
              </label>
              <textarea
                id="composer"
                ref={textareaRef}
                data-testid="composer"
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onPaste={(e) => {
                  if (e.clipboardData.files.length) {
                    e.preventDefault();
                    void attach(e.clipboardData.files);
                  }
                }}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                    e.preventDefault();
                    submit();
                  }
                }}
                rows={Math.min(8, Math.max(2, input.split("\n").length))}
                placeholder="Message Trio (Enter to send, Shift+Enter for a new line)"
                className="w-full resize-none bg-transparent px-2 py-1 outline-none placeholder:text-muted"
              />
              <div className="flex flex-wrap items-center gap-2 px-1">
                <input
                  ref={fileInputRef}
                  type="file"
                  multiple
                  hidden
                  data-testid="file-input"
                  accept=".txt,.md,.csv,.tsv,.json,.py,.js,.ts,.tsx,.jsx,.html,.css,.xml,.yaml,.yml,.sql,.pdf,.png,.jpg,.jpeg,.gif,.webp"
                  onChange={(e) => {
                    if (e.target.files) void attach(e.target.files);
                    e.target.value = "";
                  }}
                />
                <button
                  type="button"
                  className="rounded-md p-1.5 text-muted hover:bg-panel-2 disabled:opacity-50"
                  onClick={() => fileInputRef.current?.click()}
                  disabled={uploading}
                  aria-label="Attach files"
                  title="Attach files (text, code, CSV, PDF, images)"
                >
                  <Paperclip size={16} />
                </button>
                <ModelPicker models={models} value={model} onChange={setModel} id="model-picker" />
                <ReasoningPicker value={mode} onChange={setMode} />
                <span className="ml-auto text-xs text-muted">{uploading ? "Uploading..." : ""}</span>
                {stream ? (
                  <button
                    type="button"
                    onClick={() => abortRef.current?.abort()}
                    className="rounded-full bg-fg p-2 text-bg"
                    aria-label="Stop generating"
                    data-testid="stop"
                  >
                    <Square size={14} />
                  </button>
                ) : (
                  <button
                    type="button"
                    onClick={submit}
                    disabled={!input.trim() || uploading}
                    className="rounded-full bg-accent p-2 text-accent-contrast disabled:opacity-40"
                    aria-label="Send message"
                    data-testid="send"
                  >
                    <ArrowUp size={16} />
                  </button>
                )}
              </div>
            </div>
          </div>
        </div>
      </div>
      {openArtifact && (
        <div className="w-full min-w-0 lg:w-[48%] lg:max-w-[900px]">
          <ArtifactPanel key={openArtifact} artifactId={openArtifact} onClose={() => setOpenArtifact(null)} />
        </div>
      )}
    </div>
  );
}

// Artifact source is shown in the artifact panel, so the chat shows a chip instead of the code.
const ARTIFACT_FENCE = /```(?:html|svg)(?:[ \t]+title="[^"\n]{1,200}")?[ \t]*\n[\s\S]*?\n```/gi;
function stripArtifactBlocks(text: string) {
  return text.replace(ARTIFACT_FENCE, "").replace(/\n{3,}/g, "\n\n");
}

function fetchConversation(id: string) {
  return Promise.all([api<ConversationDetail>(`/api/conversations/${id}`), api<Artifact[]>(`/api/artifacts?conversation_id=${id}`)]);
}

function cutPath(path: PathItem[], parentKey: string): PathItem[] {
  if (parentKey === ROOT) return [];
  const idx = path.findIndex((p) => p.msg.id === parentKey);
  return idx === -1 ? path : path.slice(0, idx + 1);
}

function AssistantHeader({ label, mock, routeReason }: { label: string; mock?: boolean; routeReason?: string }) {
  return (
    <div className="mb-1 flex items-center gap-2 text-xs text-muted">
      <span className="font-medium text-fg">{label}</span>
      {mock && <span className="rounded bg-accent-3/15 px-1.5 py-0.5 font-medium text-accent-3">Mock, not a real AI</span>}
      {routeReason && <span title={routeReason} className="hidden truncate sm:inline">{routeReason}</span>}
    </div>
  );
}

function ErrorBox({ text }: { text: string }) {
  return (
    <p role="alert" className="mt-2 flex items-start gap-2 rounded-lg border border-danger/30 bg-danger/10 px-3 py-2 text-sm text-danger">
      <AlertTriangle size={16} className="mt-0.5 shrink-0" /> {text}
    </p>
  );
}

function MessageRow({
  msg,
  siblings,
  modelsLabel,
  artifacts,
  busy,
  onSelectSibling,
  onEdit,
  onRegenerate,
  onRate,
  onOpenArtifact,
}: {
  msg: Message;
  siblings: Message[];
  modelsLabel: (id: string) => string;
  artifacts: Artifact[];
  busy: boolean;
  onSelectSibling?: (id: string) => void;
  onEdit?: (content: string) => void;
  onRegenerate?: () => void;
  onRate?: (v: 1 | -1) => void;
  onOpenArtifact?: (id: string) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(msg.content);
  const [copied, setCopied] = useState(false);
  const idx = siblings.findIndex((s) => s.id === msg.id);
  const iconBtn = "rounded p-1 text-muted hover:bg-panel-2 disabled:opacity-40";

  const nav = siblings.length > 1 && onSelectSibling && (
    <span className="inline-flex items-center gap-0.5 text-xs text-muted" aria-label="Versions">
      <button className={iconBtn} disabled={busy || idx <= 0} onClick={() => onSelectSibling(siblings[idx - 1].id)} aria-label="Previous version">
        <ChevronLeft size={14} />
      </button>
      {idx + 1}/{siblings.length}
      <button className={iconBtn} disabled={busy || idx >= siblings.length - 1} onClick={() => onSelectSibling(siblings[idx + 1].id)} aria-label="Next version">
        <ChevronRight size={14} />
      </button>
    </span>
  );

  function copy() {
    navigator.clipboard?.writeText(msg.content).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1200);
    });
  }

  if (msg.role === "user") {
    return (
      <div className="group mb-6 flex flex-col items-end" data-testid="user-message">
        {editing ? (
          <div className="w-full max-w-[85%] rounded-2xl border border-accent bg-panel p-2">
            <textarea aria-label="Edit message" value={draft} onChange={(e) => setDraft(e.target.value)} rows={3} className="w-full resize-none bg-transparent p-1 outline-none" />
            <div className="flex justify-end gap-2">
              <button className="rounded-md px-2 py-1 text-xs hover:bg-panel-2" onClick={() => setEditing(false)}>
                Cancel
              </button>
              <button
                className="rounded-md bg-accent px-2 py-1 text-xs text-accent-contrast"
                disabled={!draft.trim()}
                onClick={() => {
                  setEditing(false);
                  onEdit?.(draft.trim());
                }}
              >
                Send edit
              </button>
            </div>
          </div>
        ) : (
          <div className="max-w-[85%] whitespace-pre-wrap rounded-2xl bg-accent/10 px-4 py-2.5 text-[15px]">{msg.content}</div>
        )}
        {msg.attachments.length > 0 && (
          <span className="mt-1 inline-flex items-center gap-1 text-xs text-muted">
            <Paperclip size={12} /> {msg.attachments.length} attachment{msg.attachments.length > 1 ? "s" : ""}
          </span>
        )}
        {onEdit && !editing && (
          <div className="mt-1 flex items-center gap-1 opacity-100 transition sm:opacity-0 sm:group-hover:opacity-100 sm:focus-within:opacity-100">
            {nav}
            <button className={iconBtn} onClick={copy} aria-label="Copy message">
              {copied ? <Check size={14} /> : <Copy size={14} />}
            </button>
            <button className={iconBtn} disabled={busy} onClick={() => setEditing(true)} aria-label="Edit message">
              <Pencil size={14} />
            </button>
          </div>
        )}
      </div>
    );
  }

  return (
    <div className="group mb-6" data-testid="assistant-message">
      <AssistantHeader label={msg.model ? modelsLabel(`${msg.provider}:${msg.model}`) : "Assistant"} mock={msg.provider === "mock"} />
      {msg.content && <Markdown text={artifacts.length ? stripArtifactBlocks(msg.content) : msg.content} />}
      {msg.status === "stopped" && <p className="mt-1 text-xs text-muted">Stopped early.</p>}
      {msg.status === "error" && <ErrorBox text={`This reply failed${msg.content ? " partway through" : ""}. ${msg.error?.split(": ").slice(1).join(": ") ?? ""}`} />}
      {artifacts.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-2">
          {artifacts.map((a) => (
            <button
              key={a.id}
              onClick={() => onOpenArtifact?.(a.id)}
              className="inline-flex items-center gap-2 rounded-xl border border-line bg-panel px-3 py-2 text-sm hover:bg-panel-2"
              data-testid="artifact-chip"
            >
              <LayoutPanelLeft size={16} className="text-accent" />
              <span className="font-medium">{a.title}</span>
              <span className="text-xs text-muted">Open preview</span>
            </button>
          ))}
        </div>
      )}
      <div className="mt-1 flex flex-wrap items-center gap-1 text-xs text-muted">
        {nav}
        <button className={iconBtn} onClick={copy} aria-label="Copy reply">
          {copied ? <Check size={14} /> : <Copy size={14} />}
        </button>
        <button className={iconBtn} disabled={busy} onClick={onRegenerate} aria-label="Regenerate reply" title="Regenerate with the selected model">
          <RefreshCw size={14} />
        </button>
        <button className={clsx(iconBtn, msg.rating === 1 && "text-accent-2")} onClick={() => onRate?.(1)} aria-label="Good response" aria-pressed={msg.rating === 1}>
          <ThumbsUp size={14} />
        </button>
        <button className={clsx(iconBtn, msg.rating === -1 && "text-danger")} onClick={() => onRate?.(-1)} aria-label="Bad response" aria-pressed={msg.rating === -1}>
          <ThumbsDown size={14} />
        </button>
        {msg.output_tokens != null && (
          <span className="ml-1 opacity-0 transition group-hover:opacity-100">
            {msg.input_tokens} in / {msg.output_tokens} out tokens
            {msg.latency_ms != null ? `, ${(msg.latency_ms / 1000).toFixed(1)}s` : ""}
          </span>
        )}
      </div>
    </div>
  );
}
