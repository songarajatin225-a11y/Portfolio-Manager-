"use client";

import * as React from "react";
import { DataBadge, isStale } from "@/components/market/data-badge";
import { useMarketStream, useNow, useTick, type Tick } from "@/lib/market-stream";
import type { Quote } from "@/lib/types";
import { changeColor, cn } from "@/lib/utils";

export type LiveQuote = Pick<
  Tick,
  "ltp" | "change" | "change_pct" | "open" | "high" | "low" | "prev_close" | "volume" | "vwap" | "bid" | "ask" | "received_at" | "exchange_ts" | "source" | "data_status" | "is_simulated"
> & { is_stale?: boolean };

/** Merge the REST snapshot with the latest streamed tick (whichever is newer). */
export function useLiveQuote(instrumentId: number, snapshot: Quote | null | undefined): LiveQuote | null {
  const tick = useTick(instrumentId);
  if (tick && (!snapshot || tick.received_at >= snapshot.received_at)) return tick;
  return snapshot ?? null;
}

/** Briefly flashes green/red when the value changes. */
export function useFlash(value: number | null | undefined): string {
  const [flash, setFlash] = React.useState<{ dir: "" | "up" | "down"; seen: number | null | undefined }>({ dir: "", seen: value });
  if (value !== flash.seen) {
    const dir = value != null && flash.seen != null ? (value > flash.seen ? "up" : value < flash.seen ? "down" : "") : "";
    setFlash({ dir, seen: value });
  }
  React.useEffect(() => {
    if (!flash.dir) return;
    const t = setTimeout(() => setFlash((f) => ({ ...f, dir: "" })), 700);
    return () => clearTimeout(t);
  }, [flash]);
  return flash.dir === "up" ? "bg-up/20" : flash.dir === "down" ? "bg-down/20" : "";
}

export function LiveBadge({ quote }: { quote: LiveQuote | null }) {
  const { feed } = useMarketStream();
  const now = useNow(5000);
  if (!quote) return <DataBadge status={null} />;
  const stale = quote.is_stale || isStale(quote.data_status, quote.received_at, feed?.stale_after_seconds, now);
  return <DataBadge status={quote.data_status} source={quote.source} asOf={quote.exchange_ts ?? quote.received_at} stale={stale} />;
}

export function PriceCell({ value, className }: { value: number | null | undefined; className?: string }) {
  const flash = useFlash(value);
  return <span className={cn("num rounded px-1 transition-colors", flash, className)}>{value == null ? "—" : value.toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</span>;
}

export function ChangeCell({ change, pct }: { change: number | null | undefined; pct: number | null | undefined }) {
  return (
    <span className={cn("num", changeColor(pct))}>
      {change == null ? "—" : `${change > 0 ? "+" : ""}${change.toFixed(2)}`}
      <span className="ml-1.5 text-[11px]">{pct == null ? "" : `(${pct > 0 ? "+" : ""}${pct.toFixed(2)}%)`}</span>
    </span>
  );
}
