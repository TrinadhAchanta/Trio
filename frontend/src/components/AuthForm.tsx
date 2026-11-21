"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { api } from "@/lib/api";
import { Logo } from "./Logo";

export function AuthForm({ mode }: { mode: "login" | "register" }) {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      if (mode === "register") {
        await api("/api/auth/register", { method: "POST", json: { email, password, display_name: name } });
      } else {
        await api("/api/auth/login", { method: "POST", json: { email, password } });
      }
      router.replace("/chat");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong.");
    } finally {
      setBusy(false);
    }
  }

  const input =
    "w-full rounded-lg border border-line bg-panel px-3 py-2.5 text-fg placeholder:text-muted focus:border-accent outline-none";

  return (
    <main className="min-h-full flex items-center justify-center p-4">
      <div className="w-full max-w-sm">
        <div className="mb-8 flex flex-col items-center gap-3 text-center">
          <Logo size={44} withText={false} />
          <h1 className="text-2xl font-semibold">{mode === "login" ? "Welcome back to Trio" : "Create your Trio account"}</h1>
          <p className="text-sm text-muted">Chat with, compare and build with multiple AI models in one place.</p>
        </div>
        <form onSubmit={submit} className="space-y-3 rounded-2xl border border-line bg-panel p-6 shadow-sm" noValidate>
          {mode === "register" && (
            <label className="block text-sm">
              <span className="mb-1 block font-medium">Name</span>
              <input className={input} value={name} onChange={(e) => setName(e.target.value)} required autoComplete="name" />
            </label>
          )}
          <label className="block text-sm">
            <span className="mb-1 block font-medium">Email</span>
            <input className={input} type="email" value={email} onChange={(e) => setEmail(e.target.value)} required autoComplete="email" />
          </label>
          <label className="block text-sm">
            <span className="mb-1 block font-medium">Password</span>
            <input
              className={input}
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
              minLength={mode === "register" ? 10 : 1}
              autoComplete={mode === "register" ? "new-password" : "current-password"}
            />
            {mode === "register" && <span className="mt-1 block text-xs text-muted">At least 10 characters, mixing letters and numbers.</span>}
          </label>
          {error && (
            <p role="alert" className="rounded-lg bg-danger/10 px-3 py-2 text-sm text-danger">
              {error}
            </p>
          )}
          <button
            type="submit"
            disabled={busy}
            className="w-full rounded-lg bg-accent px-3 py-2.5 font-medium text-accent-contrast transition hover:opacity-90 disabled:opacity-60"
          >
            {busy ? "Please wait..." : mode === "login" ? "Sign in" : "Create account"}
          </button>
        </form>
        <p className="mt-4 text-center text-sm text-muted">
          {mode === "login" ? (
            <>
              New to Trio?{" "}
              <Link className="text-accent underline" href="/register">
                Create an account
              </Link>
            </>
          ) : (
            <>
              Already have an account?{" "}
              <Link className="text-accent underline" href="/login">
                Sign in
              </Link>
            </>
          )}
        </p>
      </div>
    </main>
  );
}
