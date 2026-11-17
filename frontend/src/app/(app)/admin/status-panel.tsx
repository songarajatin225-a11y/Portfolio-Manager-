"use client";

import useSWR from "swr";
import { RefreshCw } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ErrorNotice, Skeleton } from "@/components/ui/feedback";
import { fetcher } from "@/lib/api";
import { fmtDateTime } from "@/lib/utils";

interface Service {
  name: string;
  status: "ok" | "warning" | "error" | "not_configured";
  detail: string;
  health?: Record<string, unknown>;
  tasks?: Record<string, { status: string; since: string; error?: string }>;
}
interface SystemStatus {
  overall: string;
  environment: string;
  simulated_feed_allowed: boolean;
  checked_at: string;
  services: Service[];
}

const LABELS: Record<string, string> = {
  database: "Database",
  message_bus: "Message bus",
  worker: "Background worker",
  market_data: "Market data feed",
  ai: "AI (Claude API)",
  ml: "ML models",
  news: "News ingestion",
};

const VARIANT = { ok: "up", warning: "warn", error: "down", not_configured: "default" } as const;

export function SystemStatusPanel() {
  const { data, error, isLoading, mutate, isValidating } = useSWR<SystemStatus>("/system/status", fetcher, { refreshInterval: 15_000 });
  return (
    <Card>
      <CardHeader>
        <div>
          <CardTitle>Service status</CardTitle>
          <p className="mt-0.5 text-xs text-muted">
            {data ? `Environment: ${data.environment} · checked ${fmtDateTime(data.checked_at)}` : "Loading…"}
          </p>
        </div>
        <Button variant="secondary" size="sm" onClick={() => mutate()} disabled={isValidating}>
          <RefreshCw className={isValidating ? "animate-spin" : ""} /> Refresh
        </Button>
      </CardHeader>
      <CardContent className="space-y-2">
        <ErrorNotice error={error} />
        {isLoading ? <Skeleton className="h-40" /> : null}
        {data?.simulated_feed_allowed ? (
          <p className="rounded-md border border-simulated/40 bg-simulated/10 px-3 py-2 text-xs text-simulated">
            ALLOW_SIMULATED_FEED is enabled on this server. Simulated data is always labelled SIMULATED and stored separately from real data.
          </p>
        ) : null}
        <ul className="divide-y divide-border/60">
          {data?.services.map((s) => (
            <li key={s.name} className="flex flex-col gap-1 py-2.5 sm:flex-row sm:items-start sm:justify-between">
              <div className="min-w-0">
                <p className="text-sm font-medium">{LABELS[s.name] ?? s.name}</p>
                <p className="text-xs text-muted">{s.detail}</p>
                {s.tasks ? (
                  <div className="mt-1 flex flex-wrap gap-1">
                    {Object.entries(s.tasks).map(([name, t]) => (
                      <Badge key={name} variant={t.status === "running" ? "up" : "warn"} title={t.error ?? t.since}>
                        {name}: {t.status}
                      </Badge>
                    ))}
                  </div>
                ) : null}
              </div>
              <Badge variant={VARIANT[s.status]}>{s.status.replace("_", " ")}</Badge>
            </li>
          ))}
        </ul>
      </CardContent>
    </Card>
  );
}
