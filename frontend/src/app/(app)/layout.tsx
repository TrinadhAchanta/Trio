import { Suspense } from "react";
import { AppShell } from "@/components/AppShell";
import { Logo } from "@/components/Logo";

// The workspace is fully client-rendered after sign-in, so its URL-dependent shell streams in
// behind a lightweight placeholder.
export default function AppLayout({ children }: { children: React.ReactNode }) {
  return (
    <Suspense
      fallback={
        <div className="flex h-full items-center justify-center" aria-busy="true">
          <Logo size={36} withText={false} />
        </div>
      }
    >
      <AppShell>{children}</AppShell>
    </Suspense>
  );
}
