"use client";

import * as React from "react";
import Link from "next/link";
import useSWR from "swr";
import { Bar, BarChart, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { ChangeCell, LiveBadge, PriceCell, useLiveQuote } from "@/components/market/quote-row-cells";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { EmptyState, Skeleton } from "@/components/ui/feedback";
import { Select } from "@/components/ui/input";
import { fetcher } from "@/lib/api";
import { useWatch } from "@/lib/market-stream";
import type { DataStatus, Instrument, Quote } from "@/lib/types";
import { changeColor, cn, fmtCompact, fmtPct, fmtPrice, timeAgo } from "@/lib/utils";
import { WatchlistTable } from "@/components/market/watchlist-table";

interface IndicesResponse {
  data_mode: string;
  items: { code: string; label: string; instrument: Instrument | null; quote: Quote | null }[];
}

function IndexCard({ item }: { item: IndicesResponse["items"][number] }) {
  const q = useLiveQuote(item.instrument?.id ?? 0, item.quote);
  return (
    <Link href={item.instrument ? `/stocks/${encodeURIComponent(item.instrument.symbol)}` : "#"} className="block rounded-lg border border-border bg-surface p-3 transition-colors hover:border-border-strong">
      <div className="flex items-start justify-between gap-2">
        <p className="text-[11px] font-semibold uppercase tracking-wider text-muted">{item.instrument?.is_simulated ? item.instrument.name : item.label}</p>
        {item.instrument ? <LiveBadge quote={q} /> : null}
      </div>
      {item.instrument ? (
        <>
          <PriceCell value={q?.ltp} className="mt-1 block text-lg font-semibold" />
          <div className="text-xs">
            <ChangeCell change={q?.change} pct={q?.change_pct} />
          </div>
        </>
      ) : (
        <p className="mt-2 text-[11px] text-subtle">Not found in the instrument master</p>
      )}
    </Link>
  );
}

export function IndicesWidget() {
  const { data } = useSWR<IndicesResponse>("/market/indices", fetcher, { refreshInterval: 60_000 });
  useWatch((data?.items ?? []).flatMap((i) => (i.instrument ? [i.instrument.id] : [])));
  if (!data) return <Skeleton className="h-24" />;
  return (
    <div className="space-y-2">
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4 xl:grid-cols-7">
        {data.items.map((i) => (
          <IndexCard key={i.code} item={i} />
        ))}
      </div>
      <p className="text-[11px] text-subtle">Global indices: none of the supported Indian broker feeds carries them; add a licensed global-data vendor as a custom provider to include them.</p>
    </div>
  );
}

export interface OverviewResponse {
  universe_size: number;
  priced: number;
  data_statuses: DataStatus[];
  breadth: { advances: number; declines: number; unchanged: number; advance_decline_ratio: number | null; pct_above_ema200: number | null };
  total_volume: number;
  total_turnover: number;
  gainers: Mover[];
  losers: Mover[];
  most_active: Mover[];
  sectors: { sector: string; count: number; avg_change_pct: number; advancers: number; avg_ret_1m: number | null }[];
  metrics_refresh?: { state: string; finished_at?: string } | null;
}
interface Mover {
  instrument_id: number;
  symbol: string;
  name: string;
  price: number | null;
  change_pct: number | null;
  volume: number | null;
  data_status: DataStatus | null;
}

export function useOverview() {
  return useSWR<OverviewResponse>("/market/overview", fetcher, { refreshInterval: 15_000 });
}

export function BreadthWidget() {
  const { data } = useOverview();
  if (!data) return <Skeleton className="h-40" />;
  const b = data.breadth;
  const total = b.advances + b.declines + b.unchanged || 1;
  return (
    <Card>
      <CardHeader>
        <div>
          <CardTitle>Market statistics</CardTitle>
          <p className="mt-0.5 text-xs text-muted">Tracked universe: {data.universe_size} stocks · {data.data_statuses.join(", ") || "no data"}</p>
        </div>
      </CardHeader>
      <CardContent className="space-y-3">
        <div>
          <div className="mb-1 flex justify-between text-xs">
            <span className="text-up">{b.advances} advances</span>
            <span className="text-muted">{b.unchanged} unchanged</span>
            <span className="text-down">{b.declines} declines</span>
          </div>
          <div className="flex h-2.5 overflow-hidden rounded bg-surface-3">
            <div className="bg-up" style={{ width: `${(b.advances / total) * 100}%` }} />
            <div className="bg-subtle" style={{ width: `${(b.unchanged / total) * 100}%` }} />
            <div className="bg-down" style={{ width: `${(b.declines / total) * 100}%` }} />
          </div>
        </div>
        <div className="grid grid-cols-2 gap-2 text-xs sm:grid-cols-4">
          {[
            ["A/D ratio", b.advance_decline_ratio?.toFixed(2) ?? "—"],
            ["Above 200 EMA", b.pct_above_ema200 != null ? `${b.pct_above_ema200}%` : "—"],
            ["Total volume", fmtCompact(data.total_volume)],
            ["Turnover", `₹${fmtCompact(data.total_turnover)}`],
          ].map(([k, v]) => (
            <div key={k} className="rounded border border-border bg-surface-2 px-2 py-1.5">
              <p className="text-[10px] uppercase text-subtle">{k}</p>
              <p className="num text-sm">{v}</p>
            </div>
          ))}
        </div>
        {data.metrics_refresh?.finished_at ? <p className="text-[10px] text-subtle">Daily metrics refreshed {timeAgo(data.metrics_refresh.finished_at)}</p> : null}
      </CardContent>
    </Card>
  );
}

function MoverList({ title, items, metric }: { title: string; items: Mover[]; metric: "change" | "volume" }) {
  return (
    <div>
      <p className="mb-1 text-[11px] font-semibold uppercase tracking-wider text-subtle">{title}</p>
      {items.length === 0 ? <p className="text-xs text-muted">—</p> : null}
      <ul className="space-y-0.5">
        {items.map((m) => (
          <li key={m.instrument_id} className="flex items-center justify-between gap-2 text-xs">
            <Link href={`/stocks/${encodeURIComponent(m.symbol)}`} className="min-w-0 truncate hover:text-accent" title={m.name}>
              {m.symbol}
            </Link>
            <span className="flex shrink-0 gap-2">
              <span className="num hidden text-muted 2xl:inline">{fmtPrice(m.price)}</span>
              <span className={cn("num w-16 text-right", metric === "change" ? changeColor(m.change_pct) : "")}>{metric === "change" ? fmtPct(m.change_pct) : fmtCompact(m.volume)}</span>
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

export function MoversWidget() {
  const { data } = useOverview();
  if (!data) return <Skeleton className="h-40" />;
  return (
    <Card>
      <CardHeader>
        <CardTitle>Top movers</CardTitle>
      </CardHeader>
      <CardContent className="grid gap-4 sm:grid-cols-3">
        <MoverList title="Top gainers" items={data.gainers} metric="change" />
        <MoverList title="Top losers" items={data.losers} metric="change" />
        <MoverList title="Most active" items={data.most_active} metric="volume" />
      </CardContent>
    </Card>
  );
}

export function SectorsWidget() {
  const { data } = useOverview();
  if (!data) return <Skeleton className="h-40" />;
  const rows = data.sectors.map((s) => ({ ...s, v: +s.avg_change_pct.toFixed(2) }));
  return (
    <Card>
      <CardHeader>
        <CardTitle>Sector performance</CardTitle>
        <Link href="/heatmap" className="text-xs text-accent hover:underline">
          Heatmap →
        </Link>
      </CardHeader>
      <CardContent className="h-64">
        {rows.length === 0 ? (
          <EmptyState title="No sector data" description="Import index constituents to assign sectors." />
        ) : (
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={rows} layout="vertical" margin={{ left: 10, right: 20 }}>
              <XAxis type="number" tick={{ fill: "#8b98a9", fontSize: 10 }} unit="%" />
              <YAxis type="category" dataKey="sector" width={130} tick={{ fill: "#8b98a9", fontSize: 10 }} />
              <Tooltip contentStyle={{ background: "#131a23", border: "1px solid #2f3c4f", fontSize: 12 }} formatter={(v) => [`${v}%`, "Avg day change"]} />
              <Bar dataKey="v" radius={[0, 2, 2, 0]}>
                {rows.map((r) => (
                  <Cell key={r.sector} fill={r.v >= 0 ? "#22c55e" : "#ef4444"} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        )}
      </CardContent>
    </Card>
  );
}

export function WatchlistWidget() {
  const { data } = useSWR<{ id: number; name: string }[]>("/watchlists", fetcher);
  const [selected, setSelected] = React.useState<number | null>(null);
  const id = selected ?? data?.[0]?.id ?? null;
  return (
    <Card>
      <CardHeader>
        <CardTitle>Watchlist</CardTitle>
        {data && data.length > 1 ? (
          <Select className="h-7 w-40 text-xs" value={id ?? ""} onChange={(e) => setSelected(Number(e.target.value))} aria-label="Watchlist">
            {data.map((w) => (
              <option key={w.id} value={w.id}>
                {w.name}
              </option>
            ))}
          </Select>
        ) : null}
      </CardHeader>
      <CardContent className="p-0">
        {id ? (
          <WatchlistTable id={id} compact />
        ) : (
          <EmptyState title="No watchlist" description={<Link href="/watchlists" className="text-accent hover:underline">Create a watchlist</Link>} />
        )}
      </CardContent>
    </Card>
  );
}
