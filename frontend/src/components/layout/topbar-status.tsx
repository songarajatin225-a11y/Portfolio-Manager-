"use client";

import Link from "next/link";
import useSWR from "swr";
import { fetcher } from "@/lib/api";
import { useMarketStream, useNow, type FeedStatus } from "@/lib/market-stream";
import { cn, timeAgo } from "@/lib/utils";

export interface MarketStatusResponse {
  data_mode: "real" | "simulated";
  session: { state: string; source: string; calendar_state: string; as_of: string };
  feed: FeedStatus;
}

const FEED_DOT: Record<string, string> = {
  connected: "bg-up",
  polling: "bg-up",
  connecting: "bg-warn",
  authenticating: "bg-warn",
  reconnecting: "bg-warn",
  degraded: "bg-warn",
  idle: "bg-warn",
  auth_required: "bg-down",
  error: "bg-down",
  not_configured: "bg-subtle",
};

const SESSION_LABEL: Record<string, string> = {
  open: "Market open",
  pre_open: "Pre-open",
  post_close: "Post-close",
  closed: "Market closed",
  weekend: "Weekend — closed",
  holiday: "Holiday — closed",
};

export function useMarketStatus() {
  return useSWR<MarketStatusResponse>("/market/status", fetcher, { refreshInterval: 30_000 });
}

export function TopBarStatus() {
  const { data } = useMarketStatus();
  const { feed: liveFeed, connection } = useMarketStream();
  const now = useNow(5000);
  const feed = liveFeed ?? data?.feed;
  const state = feed?.state ?? "unknown";
  const session = data?.session;
  return (
    <div className="flex min-w-0 items-center gap-3 text-xs">
      <Link href="/settings/providers" className="flex min-w-0 items-center gap-1.5 text-muted hover:text-foreground" title={feed?.last_error ?? undefined}>
        <span className={cn("size-2 shrink-0 rounded-full", FEED_DOT[state] ?? "bg-subtle")} />
        <span className="truncate">
          {state === "not_configured" || !feed?.provider_name ? "Feed: not configured" : `${feed.provider_name}: ${state.replace("_", " ")}`}
          {feed?.last_message_at ? <span className="hidden text-subtle sm:inline"> · last tick {timeAgo(feed.last_message_at, now || undefined)}</span> : null}
        </span>
      </Link>
      {session ? (
        <span className="hidden items-center gap-1.5 text-muted md:flex" title={`Source: ${session.source}`}>
          <span className={cn("size-2 rounded-full", session.state === "open" ? "bg-up" : "bg-subtle")} />
          {SESSION_LABEL[session.state] ?? session.state}
        </span>
      ) : null}
      {connection === "closed" ? <span className="hidden text-warn lg:inline">Stream reconnecting…</span> : null}
      {data?.data_mode === "simulated" ? (
        <span className="rounded bg-simulated px-2 py-0.5 text-[10px] font-bold uppercase tracking-wider text-white" title="All data on this instance is simulated">
          Simulated data — not real prices
        </span>
      ) : null}
    </div>
  );
}
