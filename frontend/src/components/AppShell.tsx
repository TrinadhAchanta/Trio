"use client";

import clsx from "clsx";
import { BarChart3, Columns3, CreditCard, LogOut, Menu, MessageSquarePlus, PanelLeftClose, Search, Settings, X } from "lucide-react";
import Link from "next/link";
import { useParams, usePathname, useRouter } from "next/navigation";
import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { api, ApiError } from "@/lib/api";
import type { Conversation, ModelsResponse, User } from "@/lib/types";
import { CommandPalette } from "./CommandPalette";
import { Logo } from "./Logo";

interface AppCtx {
  user: User;
  models: ModelsResponse | null;
  conversations: Conversation[];
  refreshConversations: () => Promise<void>;
  setUser: (u: User) => void;
}

const Ctx = createContext<AppCtx | null>(null);

export function useApp() {
  const v = useContext(Ctx);
  if (!v) throw new Error("useApp outside AppShell");
  return v;
}

function groupByDate(convs: Conversation[]) {
  const now = new Date();
  const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
  const groups: Record<string, Conversation[]> = {};
  for (const c of convs) {
    const t = new Date(c.updated_at).getTime();
    const key = c.folder
      ? `Folder: ${c.folder}`
      : t >= startOfToday
        ? "Today"
        : t >= startOfToday - 86400000
          ? "Yesterday"
          : t >= startOfToday - 7 * 86400000
            ? "Previous 7 days"
            : "Older";
    (groups[key] ??= []).push(c);
  }
  return Object.entries(groups);
}

export function AppShell({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const params = useParams<{ id?: string }>();
  const [user, setUser] = useState<User | null>(null);
  const [models, setModels] = useState<ModelsResponse | null>(null);
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [query, setQuery] = useState("");
  const [navOpen, setNavOpen] = useState(false); // mobile drawer
  const [collapsed, setCollapsed] = useState(false); // desktop
  const [paletteOpen, setPaletteOpen] = useState(false);

  const refreshConversations = useCallback(async () => {
    const q = query.trim();
    const list = await api<Conversation[]>(`/api/conversations${q ? `?q=${encodeURIComponent(q)}` : ""}`);
    setConversations(list);
  }, [query]);

  useEffect(() => {
    api<User>("/api/auth/me")
      .then(setUser)
      .catch((e) => {
        if (e instanceof ApiError && e.status === 401) router.replace("/login");
      });
    api<ModelsResponse>("/api/models").then(setModels).catch(() => setModels(null));
  }, [router]);

  useEffect(() => {
    if (!user) return;
    const t = setTimeout(() => void refreshConversations().catch(() => {}), query ? 250 : 0);
    return () => clearTimeout(t);
  }, [user, query, refreshConversations]);

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setPaletteOpen((o) => !o);
      }
      if ((e.metaKey || e.ctrlKey) && e.shiftKey && e.key.toLowerCase() === "o") {
        e.preventDefault();
        router.push("/chat");
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [router]);

  // Close the mobile drawer after navigation (adjusting state during render, not in an effect).
  const [lastPath, setLastPath] = useState(pathname);
  if (pathname !== lastPath) {
    setLastPath(pathname);
    setNavOpen(false);
  }

  const ctx = useMemo<AppCtx | null>(
    () => (user ? { user, models, conversations, refreshConversations, setUser } : null),
    [user, models, conversations, refreshConversations],
  );

  async function logout() {
    await api("/api/auth/logout", { method: "POST" }).catch(() => {});
    router.replace("/login");
  }

  if (!ctx) {
    return (
      <div className="flex h-full items-center justify-center text-muted" aria-busy="true">
        <Logo size={36} withText={false} />
      </div>
    );
  }

  const nav = [
    { href: "/compare", label: "Compare models", icon: Columns3 },
    { href: "/usage", label: "Usage", icon: BarChart3 },
    { href: "/billing", label: "Plan and billing", icon: CreditCard },
    { href: "/settings", label: "Settings", icon: Settings },
  ];

  const sidebar = (
    <nav aria-label="Main" className="flex h-full w-72 flex-col border-r border-line bg-panel">
      <div className="flex items-center justify-between px-4 py-3">
        <Link href="/chat" aria-label="Trio home">
          <Logo />
        </Link>
        <button className="hidden rounded p-1.5 text-muted hover:bg-panel-2 md:block" onClick={() => setCollapsed(true)} aria-label="Collapse sidebar">
          <PanelLeftClose size={18} />
        </button>
        <button className="rounded p-1.5 text-muted hover:bg-panel-2 md:hidden" onClick={() => setNavOpen(false)} aria-label="Close menu">
          <X size={18} />
        </button>
      </div>
      <div className="px-3">
        <Link
          href="/chat"
          className="flex w-full items-center gap-2 rounded-lg bg-accent px-3 py-2 text-sm font-medium text-accent-contrast hover:opacity-90"
        >
          <MessageSquarePlus size={16} /> New chat
        </Link>
        <label className="mt-3 flex items-center gap-2 rounded-lg border border-line px-2.5 py-1.5 text-sm">
          <Search size={14} className="text-muted" />
          <span className="sr-only">Search conversations</span>
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search conversations"
            className="w-full bg-transparent outline-none placeholder:text-muted"
          />
        </label>
      </div>
      <div className="mt-3 flex-1 overflow-y-auto px-2" data-testid="conversation-list">
        {conversations.length === 0 && <p className="px-2 py-4 text-sm text-muted">{query ? "No matches." : "No conversations yet."}</p>}
        {groupByDate(conversations).map(([group, items]) => (
          <div key={group} className="mb-3">
            <p className="px-2 pb-1 text-xs font-medium uppercase tracking-wide text-muted">{group}</p>
            {items.map((c) => (
              <Link
                key={c.id}
                href={`/chat/${c.id}`}
                className={clsx(
                  "block truncate rounded-lg px-2 py-1.5 text-sm hover:bg-panel-2",
                  params?.id === c.id && "bg-panel-2 font-medium",
                )}
                aria-current={params?.id === c.id ? "page" : undefined}
              >
                {c.title}
              </Link>
            ))}
          </div>
        ))}
      </div>
      <div className="border-t border-line p-2">
        {nav.map(({ href, label, icon: Icon }) => (
          <Link
            key={href}
            href={href}
            className={clsx("flex items-center gap-2 rounded-lg px-2 py-1.5 text-sm hover:bg-panel-2", pathname === href && "bg-panel-2 font-medium")}
          >
            <Icon size={16} /> {label}
          </Link>
        ))}
        <div className="mt-2 flex items-center justify-between px-2 text-sm">
          <span className="truncate text-muted" title={ctx.user.email}>
            {ctx.user.display_name}
          </span>
          <button onClick={logout} className="rounded p-1.5 text-muted hover:bg-panel-2" aria-label="Sign out" title="Sign out">
            <LogOut size={16} />
          </button>
        </div>
        <p className="px-2 pt-1 text-[11px] text-muted">
          Press <kbd className="rounded border border-line px-1">Ctrl</kbd>+<kbd className="rounded border border-line px-1">K</kbd> for commands
        </p>
      </div>
    </nav>
  );

  return (
    <Ctx.Provider value={ctx}>
      <div className="flex h-dvh overflow-hidden">
        <div className={clsx("hidden md:block", collapsed && "md:hidden")}>{sidebar}</div>
        {navOpen && (
          <div className="fixed inset-0 z-40 flex md:hidden">
            <div className="h-full">{sidebar}</div>
            <button className="flex-1 bg-black/40" aria-label="Close menu" onClick={() => setNavOpen(false)} />
          </div>
        )}
        <div className="flex min-w-0 flex-1 flex-col">
          <div className={clsx("flex items-center gap-2 border-b border-line px-3 py-2", !collapsed && "md:hidden")}>
            <button
              className="rounded p-1.5 hover:bg-panel-2"
              onClick={() => (window.matchMedia("(min-width: 768px)").matches ? setCollapsed(false) : setNavOpen(true))}
              aria-label="Open menu"
            >
              <Menu size={18} />
            </button>
            <Logo size={22} />
          </div>
          <div className="min-h-0 flex-1">{children}</div>
        </div>
      </div>
      {paletteOpen && <CommandPalette onClose={() => setPaletteOpen(false)} />}
    </Ctx.Provider>
  );
}
