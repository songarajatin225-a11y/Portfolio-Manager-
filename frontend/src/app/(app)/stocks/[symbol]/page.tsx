"use client";

import * as React from "react";
import { useParams } from "next/navigation";
import useSWR from "swr";
import { useAuth } from "@/components/auth/auth-provider";
import { AddToWatchlist } from "@/components/market/add-to-watchlist";
import { DataBadge } from "@/components/market/data-badge";
import { OVERLAY_COLORS, PriceChart, type OverlayKey } from "@/components/market/price-chart";
import { ChangeCell, LiveBadge, PriceCell, useLiveQuote } from "@/components/market/quote-row-cells";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { ErrorNotice, Notice, Skeleton } from "@/components/ui/feedback";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { fetcher } from "@/lib/api";
import { useWatch } from "@/lib/market-stream";
import type { CandleResponse, Instrument, Quote } from "@/lib/types";
import { cn } from "@/lib/utils";
import { QuotePanel } from "./quote-panel";
import { FundamentalsTab, InstitutionalTab, PerformanceTab, TechnicalsTab, type TechnicalsResponse } from "./tabs";
import { StockExtraTabs, StockExtraTriggers } from "./extra-tabs";

const INTERVALS: [string, string][] = [
  ["1m", "1m"],
  ["5m", "5m"],
  ["15m", "15m"],
  ["1h", "1H"],
  ["1d", "1D"],
  ["1w", "1W"],
  ["1mo", "1M"],
];
const OVERLAYS: [OverlayKey, string][] = [
  ["sma_20", "SMA 20"],
  ["sma_50", "SMA 50"],
  ["sma_200", "SMA 200"],
  ["ema_20", "EMA 20"],
  ["ema_50", "EMA 50"],
  ["ema_200", "EMA 200"],
  ["bb", "Bollinger"],
  ["supertrend", "Supertrend"],
  ["vwap", "VWAP"],
];

function Toggle({ on, onClick, children, color }: { on: boolean; onClick: () => void; children: React.ReactNode; color?: string }) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={on}
      className={cn("rounded border px-2 py-0.5 text-[11px] transition-colors", on ? "border-border-strong bg-surface-3 text-foreground" : "border-border text-muted hover:text-foreground")}
    >
      {color ? <span className="mr-1 inline-block size-2 rounded-full" style={{ background: color }} /> : null}
      {children}
    </button>
  );
}

export default function StockPage() {
  const params = useParams<{ symbol: string }>();
  const symbol = decodeURIComponent(params.symbol);
  const { isAdmin } = useAuth();
  const [interval, setInterval] = React.useState("1d");
  const [overlays, setOverlays] = React.useState<OverlayKey[]>(["ema_20", "ema_50"]);
  const [showRsi, setShowRsi] = React.useState(true);
  const [showMacd, setShowMacd] = React.useState(false);
  const [showLevels, setShowLevels] = React.useState(false);

  const enc = encodeURIComponent(symbol);
  const { data: info, error: infoError } = useSWR<{ instrument: Instrument; quote: Quote | null }>(`/instruments/${enc}`, fetcher, { refreshInterval: 5000 });
  const { data: candles, error: candleError, isLoading } = useSWR<CandleResponse>(`/stocks/${enc}/candles?interval=${interval}`, fetcher);
  const { data: tech } = useSWR<TechnicalsResponse>(`/stocks/${enc}/technicals?interval=${interval}`, fetcher);
  const inst = info?.instrument;
  useWatch(inst ? [inst.id] : []);
  const live = useLiveQuote(inst?.id ?? 0, info?.quote);

  const levels = React.useMemo(() => {
    if (!showLevels || !tech) return [];
    return [
      ...(tech.levels.support ?? []).map((s) => ({ price: s.price, label: "S", color: "#22c55e" })),
      ...(tech.levels.resistance ?? []).map((r) => ({ price: r.price, label: "R", color: "#ef4444" })),
    ];
  }, [showLevels, tech]);
  const series = tech?.series;
  const chartLive = React.useMemo(() => (live ? { ltp: live.ltp, received_at: live.received_at } : null), [live]);

  if (infoError) return <ErrorNotice error={infoError} />;
  if (!inst) return <Skeleton className="h-96" />;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <h1 className="text-xl font-semibold">{inst.symbol}</h1>
            <Badge>{inst.exchange}</Badge>
            {inst.sector ? <Badge variant="info">{inst.sector}</Badge> : null}
            {inst.is_simulated ? <Badge variant="simulated">simulated instrument</Badge> : null}
          </div>
          <p className="truncate text-sm text-muted">
            {inst.name}
            {inst.isin ? ` · ${inst.isin}` : ""}
          </p>
        </div>
        <div className="flex items-end gap-4">
          <div className="text-right">
            <PriceCell value={live?.ltp} className="text-2xl font-semibold" />
            <div className="text-sm">
              <ChangeCell change={live?.change} pct={live?.change_pct} />
            </div>
          </div>
          <div className="flex flex-col items-end gap-2">
            <LiveBadge quote={live} />
            <AddToWatchlist instrumentId={inst.id} symbol={inst.symbol} />
          </div>
        </div>
      </div>

      <div className="grid gap-4 xl:grid-cols-[1fr_300px]">
        <Card>
          <CardContent className="space-y-2 p-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="flex gap-1" role="group" aria-label="Interval">
                {INTERVALS.map(([v, label]) => (
                  <Toggle key={v} on={interval === v} onClick={() => setInterval(v)}>
                    {label}
                  </Toggle>
                ))}
              </div>
              {candles ? <DataBadge status={candles.data_status} source={candles.source} asOf={candles.as_of} /> : null}
            </div>
            <div className="flex flex-wrap gap-1">
              {OVERLAYS.filter(([k]) => k !== "vwap" || ["1m", "5m", "15m", "1h"].includes(interval)).map(([k, label]) => (
                <Toggle key={k} color={OVERLAY_COLORS[k]} on={overlays.includes(k)} onClick={() => setOverlays((o) => (o.includes(k) ? o.filter((x) => x !== k) : [...o, k]))}>
                  {label}
                </Toggle>
              ))}
              <Toggle on={showRsi} onClick={() => setShowRsi((v) => !v)}>RSI</Toggle>
              <Toggle on={showMacd} onClick={() => setShowMacd((v) => !v)}>MACD</Toggle>
              <Toggle on={showLevels} onClick={() => setShowLevels((v) => !v)}>Support / resistance</Toggle>
            </div>
            {candleError ? <ErrorNotice error={candleError} /> : null}
            {candles?.warnings?.length ? <Notice tone="warn">{candles.warnings.join(" · ")}</Notice> : null}
            {isLoading ? <Skeleton className="h-[460px]" /> : null}
            {candles && candles.candles.length ? (
              <PriceChart candles={candles.candles} interval={interval} series={series} overlays={overlays} showRsi={showRsi} showMacd={showMacd} levels={levels} live={chartLive} height={showRsi || showMacd ? 560 : 460} />
            ) : candles ? (
              <p className="py-20 text-center text-sm text-muted">No candles for this interval. Configure a provider with historical data or import candles.</p>
            ) : null}
            <p className="text-[10px] text-subtle">Times shown in IST. Indicators use only completed bars up to each point.</p>
          </CardContent>
        </Card>
        <QuotePanel quote={info?.quote} live={live} isIndex={inst.is_index} />
      </div>

      <Tabs defaultValue="technicals">
        <TabsList>
          <TabsTrigger value="technicals">Technicals</TabsTrigger>
          <TabsTrigger value="performance">Performance</TabsTrigger>
          {!inst.is_index ? <TabsTrigger value="fundamentals">Fundamentals</TabsTrigger> : null}
          {!inst.is_index ? <TabsTrigger value="institutional">Institutional</TabsTrigger> : null}
          <StockExtraTriggers isIndex={inst.is_index} />
        </TabsList>
        <TabsContent value="technicals">
          <TechnicalsTab data={tech} />
        </TabsContent>
        <TabsContent value="performance">
          <PerformanceTab symbol={symbol} />
        </TabsContent>
        {!inst.is_index ? (
          <TabsContent value="fundamentals">
            <FundamentalsTab symbol={symbol} isAdmin={isAdmin} />
          </TabsContent>
        ) : null}
        {!inst.is_index ? (
          <TabsContent value="institutional">
            <InstitutionalTab symbol={symbol} />
          </TabsContent>
        ) : null}
        <StockExtraTabs symbol={symbol} instrument={inst} />
      </Tabs>
    </div>
  );
}
