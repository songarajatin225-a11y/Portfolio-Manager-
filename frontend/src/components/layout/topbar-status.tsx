"use client";

import Link from "next/link";
import useSWR from "swr";
import { fetcher } from "@/lib/api";
import { cn } from "@/lib/utils";

interface Service {
  name: string;
  status: "ok" | "warning" | "error" | "not_configured";
  detail: string;
}
interface SystemStatus {
  overall: string;
  simulated_feed_allowed: boolean;
  services: Service[];
}

const DOT: Record<string, string> = {
  ok: "bg-up",
  warning: "bg-warn",
  error: "bg-down",
  not_configured: "bg-subtle",
};

export function TopBarStatus() {
  const { data } = useSWR<SystemStatus>("/system/status", fetcher, { refreshInterval: 30_000 });
  if (!data) return <span className="text-xs text-subtle">Checking services…</span>;
  const market = data.services.find((s) => s.name === "market_data");
  const worker = data.services.find((s) => s.name === "worker");
  return (
    <div className="flex min-w-0 items-center gap-3 text-xs">
      {market ? (
        <Link href="/admin?tab=status" className="flex min-w-0 items-center gap-1.5 text-muted hover:text-foreground" title={market.detail}>
          <span className={cn("size-2 shrink-0 rounded-full", DOT[market.status])} />
          <span className="truncate">Feed: {market.status === "not_configured" ? "not configured" : market.detail}</span>
        </Link>
      ) : null}
      {worker && worker.status !== "ok" ? (
        <span className="hidden items-center gap-1.5 text-down md:flex" title={worker.detail}>
          <span className="size-2 rounded-full bg-down" /> Worker offline
        </span>
      ) : null}
    </div>
  );
}
