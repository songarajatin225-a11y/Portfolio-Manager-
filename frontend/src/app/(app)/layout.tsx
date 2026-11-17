import { Suspense } from "react";
import { AppShell } from "@/components/layout/app-shell";

function ShellFallback() {
  return (
    <div className="flex min-h-screen">
      <div className="hidden w-60 border-r border-border bg-surface lg:block" />
      <div className="flex-1 space-y-4 p-6">
        <div className="h-8 w-64 animate-pulse rounded bg-surface-3/60" />
        <div className="h-40 w-full animate-pulse rounded bg-surface-3/60" />
      </div>
    </div>
  );
}

export default function AuthenticatedLayout({ children }: { children: React.ReactNode }) {
  // The shell reads the current URL (usePathname), which is only known at request time.
  return (
    <Suspense fallback={<ShellFallback />}>
      <AppShell>{children}</AppShell>
    </Suspense>
  );
}
