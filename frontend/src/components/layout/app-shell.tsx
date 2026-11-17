"use client";

import * as React from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import * as DialogPrimitive from "@radix-ui/react-dialog";
import { LineChart, LogOut, Menu, X } from "lucide-react";
import { useAuth } from "@/components/auth/auth-provider";
import { Skeleton } from "@/components/ui/feedback";
import { MarketStreamProvider } from "@/lib/market-stream";
import { cn } from "@/lib/utils";
import { NAV, isActive } from "./nav";
import { TopBarStatus } from "./topbar-status";

function Sidebar({ onNavigate }: { onNavigate?: () => void }) {
  const pathname = usePathname();
  const { isAdmin } = useAuth();
  return (
    <nav className="flex h-full flex-col gap-4 overflow-y-auto px-2 py-3" aria-label="Main">
      <Link href="/" className="flex items-center gap-2 px-2 pb-1" onClick={onNavigate}>
        <LineChart className="size-5 text-accent" />
        <span className="text-sm font-semibold tracking-wide">MarketIntel</span>
      </Link>
      {NAV.map((group) => {
        const items = group.items.filter((i) => !i.adminOnly || isAdmin);
        if (!items.length) return null;
        return (
          <div key={group.label}>
            <p className="px-2 pb-1 text-[10px] font-semibold uppercase tracking-wider text-subtle">{group.label}</p>
            <ul className="space-y-0.5">
              {items.map((item) => {
                const active = isActive(pathname, item.href);
                const Icon = item.icon;
                return (
                  <li key={item.href}>
                    <Link
                      href={item.href}
                      onClick={onNavigate}
                      aria-current={active ? "page" : undefined}
                      className={cn(
                        "flex items-center gap-2.5 rounded-md px-2 py-1.5 text-[13px] transition-colors",
                        active ? "bg-surface-3 text-foreground" : "text-muted hover:bg-surface-2 hover:text-foreground",
                      )}
                    >
                      <Icon className={cn("size-4", active ? "text-accent" : "")} />
                      {item.label}
                    </Link>
                  </li>
                );
              })}
            </ul>
          </div>
        );
      })}
    </nav>
  );
}

function UserMenu() {
  const { user, logout } = useAuth();
  const router = useRouter();
  if (!user) return null;
  return (
    <div className="flex items-center gap-2">
      <div className="hidden text-right sm:block">
        <p className="text-xs font-medium leading-tight">{user.full_name || user.email}</p>
        <p className="text-[10px] uppercase tracking-wider text-subtle">{user.role}</p>
      </div>
      <button
        type="button"
        onClick={async () => {
          await logout();
          router.replace("/login");
        }}
        className="rounded-md p-1.5 text-muted hover:bg-surface-2 hover:text-foreground"
        aria-label="Sign out"
        title="Sign out"
      >
        <LogOut className="size-4" />
      </button>
    </div>
  );
}

export function AppShell({ children }: { children: React.ReactNode }) {
  const { user, loading } = useAuth();
  const router = useRouter();
  const pathname = usePathname();
  const [mobileOpen, setMobileOpen] = React.useState(false);

  React.useEffect(() => {
    if (!loading && !user) router.replace(`/login?next=${encodeURIComponent(pathname)}`);
  }, [loading, user, router, pathname]);

  if (loading || !user) {
    return (
      <div className="flex min-h-screen">
        <div className="hidden w-60 border-r border-border bg-surface p-4 lg:block">
          <Skeleton className="h-6 w-32" />
        </div>
        <div className="flex-1 space-y-4 p-6">
          <Skeleton className="h-8 w-64" />
          <Skeleton className="h-40 w-full" />
        </div>
      </div>
    );
  }

  return (
    <MarketStreamProvider>
    <div className="flex min-h-screen">
      <aside className="sticky top-0 hidden h-screen w-60 shrink-0 border-r border-border bg-surface lg:block">
        <Sidebar />
      </aside>
      <DialogPrimitive.Root open={mobileOpen} onOpenChange={setMobileOpen}>
        <DialogPrimitive.Portal>
          <DialogPrimitive.Overlay className="fixed inset-0 z-40 bg-black/60 lg:hidden" />
          <DialogPrimitive.Content className="fixed inset-y-0 left-0 z-50 w-64 border-r border-border bg-surface lg:hidden">
            <DialogPrimitive.Title className="sr-only">Navigation</DialogPrimitive.Title>
            <DialogPrimitive.Description className="sr-only">Main navigation menu</DialogPrimitive.Description>
            <DialogPrimitive.Close className="absolute right-2 top-2 rounded p-1 text-muted hover:bg-surface-2" aria-label="Close menu">
              <X className="size-4" />
            </DialogPrimitive.Close>
            <Sidebar onNavigate={() => setMobileOpen(false)} />
          </DialogPrimitive.Content>
        </DialogPrimitive.Portal>
      </DialogPrimitive.Root>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-30 flex h-12 items-center gap-3 border-b border-border bg-background/95 px-3 backdrop-blur sm:px-4">
          <button
            type="button"
            className="rounded-md p-1.5 text-muted hover:bg-surface-2 lg:hidden"
            onClick={() => setMobileOpen(true)}
            aria-label="Open menu"
          >
            <Menu className="size-5" />
          </button>
          <div className="min-w-0 flex-1">
            <TopBarStatus />
          </div>
          <UserMenu />
        </header>
        <main className="min-w-0 flex-1 p-3 sm:p-5">{children}</main>
        <footer className="border-t border-border px-4 py-2 text-[10px] text-subtle">
          Information only — not investment advice. Every data point is labelled LIVE, DELAYED, HISTORICAL or SIMULATED with its source and timestamp.
        </footer>
      </div>
    </div>
    </MarketStreamProvider>
  );
}
