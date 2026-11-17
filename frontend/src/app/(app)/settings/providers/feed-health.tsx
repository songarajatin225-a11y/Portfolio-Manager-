"use client";

import useSWR from "swr";
import { toast } from "sonner";
import { RefreshCw } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { api, errorMessage, fetcher } from "@/lib/api";
import { useMarketStream, useNow, type FeedStatus } from "@/lib/market-stream";
import { fmtDateTime, timeAgo } from "@/lib/utils";

const STATE_VARIANT: Record<string, "up" | "warn" | "down" | "default"> = {
  connected: "up",
  polling: "up",
  connecting: "warn",
  authenticating: "warn",
  reconnecting: "warn",
  degraded: "warn",
  idle: "warn",
  auth_required: "down",
  error: "down",
  not_configured: "default",
};

function Stat({ label, value, title }: { label: string; value: React.ReactNode; title?: string }) {
  return (
    <div className="rounded-md border border-border bg-surface-2 px-3 py-2" title={title}>
      <p className="text-[10px] uppercase tracking-wider text-subtle">{label}</p>
      <p className="num mt-0.5 truncate text-sm">{value}</p>
    </div>
  );
}

export function FeedHealthCard() {
  const { feed: live } = useMarketStream();
  const { data: polled } = useSWR<FeedStatus>("/admin/providers/feed-health", fetcher, { refreshInterval: 10_000 });
  const now = useNow(2000);
  const h = live ?? polled;
  const state = h?.state ?? "unknown";
  const statuses = Object.entries(h?.market_status ?? {});

  async function reload() {
    try {
      await api.post("/admin/providers/feed/reload");
      toast.success("Feed reload requested");
    } catch (err) {
      toast.error(errorMessage(err));
    }
  }

  return (
    <Card>
      <CardHeader>
        <div>
          <CardTitle>Feed health</CardTitle>
          <p className="mt-0.5 text-xs text-muted">Reported by the worker every few seconds{h?.updated_at ? ` · ${fmtDateTime(h.updated_at)}` : ""}</p>
        </div>
        <div className="flex items-center gap-2">
          <Badge variant={STATE_VARIANT[state] ?? "default"}>{state.replace("_", " ")}</Badge>
          <Button size="sm" variant="secondary" onClick={reload}>
            <RefreshCw /> Reconnect
          </Button>
        </div>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-6">
          <Stat label="Provider" value={h?.provider_name ?? "—"} />
          <Stat label="Mode" value={h?.streaming ? "WebSocket stream" : h?.provider_name ? "REST polling" : "—"} />
          <Stat label="Subscribed instruments" value={h?.subscribed ?? 0} />
          <Stat label="Last successful update" value={h?.last_message_at ? timeAgo(h.last_message_at, now || undefined) : "never"} title={fmtDateTime(h?.last_message_at)} />
          <Stat label="Updates / min" value={h?.messages_last_minute ?? 0} />
          <Stat label="Feed latency p50 / p95" value={h?.latency_ms_p50 != null ? `${h.latency_ms_p50} / ${h.latency_ms_p95 ?? "—"} ms` : "not measurable"} title="Exchange timestamp to receipt, when the provider sends exchange timestamps" />
          <Stat label="Reconnects" value={h?.reconnects ?? 0} />
          <Stat label="Rejected quotes" value={h?.invalid_quotes ?? 0} title="Quotes failing validation (non-positive prices, high<low, future timestamps)" />
          <Stat label="Stale after" value={`${h?.stale_after_seconds ?? 30}s`} />
          <Stat label="Session expires" value={h?.token_expires_at ? fmtDateTime(h.token_expires_at) : "—"} />
          {statuses.map(([ex, s]) => (
            <Stat key={ex} label={`${ex} status`} value={`${s.status} (${s.source})`} title={s.detail} />
          ))}
        </div>
        {h?.last_error ? (
          <p className="rounded-md border border-down/40 bg-down/10 px-3 py-2 text-xs text-down">
            Last error: {h.last_error}
          </p>
        ) : null}
      </CardContent>
    </Card>
  );
}
