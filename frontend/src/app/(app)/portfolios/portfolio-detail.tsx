"use client";

import * as React from "react";
import Link from "next/link";
import useSWR from "swr";
import { toast } from "sonner";
import { Area, AreaChart, CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import type { Holding, Performance, PortfolioSummary } from "@/components/portfolio/types";
import { DataBadge } from "@/components/market/data-badge";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { EmptyState, ErrorNotice, Notice, Skeleton } from "@/components/ui/feedback";
import { Field, Input } from "@/components/ui/input";
import { ShareBars, Stat } from "@/components/ui/stat";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { api, errorMessage, fetcher } from "@/lib/api";
import { AXIS_TICK, DOWN, GRID, SERIES, TOOLTIP_LABEL, TOOLTIP_STYLE } from "@/lib/chart-theme";
import { changeColor, fmtDate, fmtINR, fmtNum, fmtPct, fmtPrice } from "@/lib/utils";
import { TransactionsPanel } from "./transactions";

function PerformanceCard({ id }: { id: number }) {
  const { data, error } = useSWR<Performance>(`/portfolios/${id}/performance`, fetcher);
  if (error) return <ErrorNotice error={error} />;
  if (!data) return <Skeleton className="h-72" />;
  if (!data.available) return <Card><EmptyState title="No performance history" description={data.reason} /></Card>;
  const hasBench = data.series.some((s) => s.benchmark != null);
  return (
    <Card>
      <CardHeader>
        <div>
          <CardTitle>Performance (time-weighted, indexed to 100)</CardTitle>
          <CardDescription className="mt-0.5">{data.methodology}</CardDescription>
        </div>
        <DataBadge status={data.data_status} source="stored daily closes" asOf={data.series.at(-1)?.date} />
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-6">
          <Stat label="Time-weighted return" value={fmtPct(data.twr_pct)} valueClassName={changeColor(data.twr_pct)} />
          <Stat label="CAGR" value={data.cagr_pct != null ? fmtPct(data.cagr_pct) : "—"} sub={data.cagr_pct == null ? "needs 1 year" : undefined} />
          <Stat label="Volatility (ann.)" value={data.volatility_pct != null ? `${fmtNum(data.volatility_pct, 1)}%` : "—"} />
          <Stat label="Max drawdown" value={fmtPct(data.max_drawdown_pct, 2, false)} valueClassName="text-down" />
          <Stat label={`Benchmark (${data.benchmark ?? "—"})`} value={data.benchmark_return_pct != null ? fmtPct(data.benchmark_return_pct) : "—"} />
          <Stat label="Beta" value={data.beta != null ? fmtNum(data.beta, 2) : "—"} />
        </div>
        <div className="h-64" role="img" aria-label="Portfolio index versus benchmark index over time">
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={data.series} margin={{ top: 4, right: 8, bottom: 0, left: 0 }}>
              <CartesianGrid stroke={GRID} vertical={false} />
              <XAxis dataKey="date" tick={AXIS_TICK} minTickGap={40} />
              <YAxis tick={AXIS_TICK} domain={["auto", "auto"]} width={44} />
              <Tooltip contentStyle={TOOLTIP_STYLE} labelStyle={TOOLTIP_LABEL} formatter={(v, n) => [fmtNum(Number(v), 2), n]} />
              <Legend wrapperStyle={{ fontSize: 11 }} />
              <Line name="Portfolio" dataKey="index" stroke={SERIES[0]} strokeWidth={2} dot={false} />
              {hasBench ? <Line name={data.benchmark ?? "Benchmark"} dataKey="benchmark" stroke={SERIES[1]} strokeWidth={2} dot={false} /> : null}
            </LineChart>
          </ResponsiveContainer>
        </div>
        <div className="h-28" role="img" aria-label="Drawdown from peak over time">
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart data={data.series} margin={{ top: 0, right: 8, bottom: 0, left: 0 }}>
              <CartesianGrid stroke={GRID} vertical={false} />
              <XAxis dataKey="date" hide />
              <YAxis tick={AXIS_TICK} width={44} unit="%" />
              <Tooltip contentStyle={TOOLTIP_STYLE} labelStyle={TOOLTIP_LABEL} formatter={(v) => [`${fmtNum(Number(v), 2)}%`, "Drawdown"]} />
              <Area dataKey="drawdown" stroke={DOWN} fill={DOWN} fillOpacity={0.2} strokeWidth={1.5} />
            </AreaChart>
          </ResponsiveContainer>
        </div>
        {data.missing_history.length ? <Notice tone="warn">No stored history for {data.missing_history.join(", ")}; these are excluded from the chart.</Notice> : null}
      </CardContent>
    </Card>
  );
}

function HoldingsTable({ holdings }: { holdings: Holding[] }) {
  if (!holdings.length) return <EmptyState title="No open holdings" />;
  return (
    <div className="overflow-x-auto">
      <Table>
        <THead>
          <TR>
            <TH>Symbol</TH>
            <TH>Sector</TH>
            <TH className="text-right">Qty</TH>
            <TH className="text-right">Avg cost</TH>
            <TH className="text-right">Price</TH>
            <TH className="text-right">Value</TH>
            <TH className="text-right">Unrealised</TH>
            <TH className="text-right">Realised</TH>
            <TH className="text-right">Weight</TH>
            <TH>Price data</TH>
          </TR>
        </THead>
        <TBody>
          {holdings.map((h) => (
            <TR key={h.instrument_id}>
              <TD>
                <Link className="font-medium hover:text-accent" href={`/stocks/${encodeURIComponent(h.symbol)}`}>
                  {h.symbol}
                </Link>
                <span className="block max-w-40 truncate text-[11px] text-subtle">{h.name}</span>
              </TD>
              <TD className="text-muted">{h.sector}</TD>
              <TD className="num text-right">{fmtNum(h.quantity, 0)}</TD>
              <TD className="num text-right">{fmtPrice(h.avg_cost)}</TD>
              <TD className="num text-right">{fmtPrice(h.price)}</TD>
              <TD className="num text-right">{fmtPrice(h.market_value)}</TD>
              <TD className={`num text-right ${changeColor(h.unrealized_pnl)}`}>
                {fmtPrice(h.unrealized_pnl)}
                <span className="block text-[11px]">{fmtPct(h.unrealized_pct)}</span>
              </TD>
              <TD className={`num text-right ${changeColor(h.realized_pnl)}`}>{fmtPrice(h.realized_pnl)}</TD>
              <TD className="num text-right">{fmtNum(h.weight * 100, 1)}%</TD>
              <TD>{h.price_status ? <DataBadge status={h.price_status} source={h.price_source} asOf={h.price_as_of} /> : <Badge variant="warn">no price · at cost</Badge>}</TD>
            </TR>
          ))}
        </TBody>
      </Table>
    </div>
  );
}

interface Rebalance {
  basis: string;
  threshold_pp?: number;
  suggestions: { symbol: string; current_weight: number; target_weight: number; drift_pp: number; action: "BUY" | "SELL"; value: number; quantity: number }[];
  warnings: string[];
  note?: string;
}

function RebalancePanel({ summary, canWrite, onSaved }: { summary: PortfolioSummary; canWrite: boolean; onSaved: () => void }) {
  const id = summary.portfolio.id;
  const [threshold, setThreshold] = React.useState(5);
  const { data, mutate } = useSWR<Rebalance>(`/portfolios/${id}/rebalance?threshold_pp=${threshold}`, fetcher);
  const [targets, setTargets] = React.useState<Record<string, string>>(() =>
    Object.fromEntries(summary.holdings.map((h) => [h.symbol, summary.portfolio.target_allocations[h.symbol] != null ? String(+(summary.portfolio.target_allocations[h.symbol] * 100).toFixed(2)) : ""])),
  );
  const total = Object.values(targets).reduce((s, v) => s + (Number(v) || 0), 0);

  async function save(clear = false) {
    const body = clear ? {} : Object.fromEntries(Object.entries(targets).filter(([, v]) => Number(v) > 0).map(([k, v]) => [k, Number(v) / 100]));
    try {
      await api.patch(`/portfolios/${id}`, { target_allocations: body });
      toast.success(clear ? "Targets cleared (equal weight)" : "Targets saved");
      onSaved();
      void mutate();
    } catch (err) {
      toast.error(errorMessage(err));
    }
  }

  return (
    <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.3fr)]">
      <Card>
        <CardHeader>
          <div>
            <CardTitle>Target allocation</CardTitle>
            <CardDescription className="mt-0.5">Leave all blank for equal weight. Targets must add up to 100%.</CardDescription>
          </div>
        </CardHeader>
        <CardContent className="space-y-2">
          {summary.holdings.map((h) => (
            <div key={h.symbol} className="flex items-center gap-2 text-sm">
              <span className="w-28 truncate">{h.symbol}</span>
              <span className="num w-16 text-right text-xs text-muted">{fmtNum(h.weight * 100, 1)}%</span>
              <Input aria-label={`Target weight for ${h.symbol}`} type="number" min="0" max="100" step="0.1" className="h-8 w-24" disabled={!canWrite} value={targets[h.symbol] ?? ""} onChange={(e) => setTargets((t) => ({ ...t, [h.symbol]: e.target.value }))} />
              <span className="text-xs text-subtle">%</span>
            </div>
          ))}
          <p className={`text-xs ${total && Math.abs(total - 100) > 1 ? "text-down" : "text-muted"}`}>Total: {fmtNum(total, 1)}%</p>
          {canWrite ? (
            <div className="flex gap-2">
              <Button size="sm" onClick={() => save()} disabled={!!total && Math.abs(total - 100) > 1}>
                Save targets
              </Button>
              <Button size="sm" variant="ghost" onClick={() => save(true)}>
                Use equal weight
              </Button>
            </div>
          ) : null}
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <div>
            <CardTitle>Rebalancing suggestions</CardTitle>
            <CardDescription className="mt-0.5">Basis: {data?.basis ?? "…"}. Positions drifting more than the threshold from target.</CardDescription>
          </div>
          <Field label="Threshold (pp)" className="w-28">
            <Input type="number" min="0.5" max="50" step="0.5" value={threshold} onChange={(e) => setThreshold(Math.max(0.5, Number(e.target.value) || 5))} />
          </Field>
        </CardHeader>
        <CardContent className="space-y-3">
          {data?.warnings.map((w) => (
            <Notice key={w} tone="warn">{w}</Notice>
          ))}
          {data && !data.suggestions.length ? <EmptyState title="Within tolerance" description="No position drifts beyond the threshold." /> : null}
          {data?.suggestions.length ? (
            <Table>
              <THead>
                <TR>
                  <TH>Symbol</TH>
                  <TH className="text-right">Current</TH>
                  <TH className="text-right">Target</TH>
                  <TH>Action</TH>
                  <TH className="text-right">Approx. value</TH>
                  <TH className="text-right">Qty</TH>
                </TR>
              </THead>
              <TBody>
                {data.suggestions.map((s) => (
                  <TR key={s.symbol}>
                    <TD className="font-medium">{s.symbol}</TD>
                    <TD className="num text-right">{fmtNum(s.current_weight * 100, 1)}%</TD>
                    <TD className="num text-right">{fmtNum(s.target_weight * 100, 1)}%</TD>
                    <TD>
                      <Badge variant={s.action === "BUY" ? "up" : "down"}>{s.action}</Badge>
                    </TD>
                    <TD className="num text-right">{fmtINR(s.value)}</TD>
                    <TD className="num text-right">{s.quantity}</TD>
                  </TR>
                ))}
              </TBody>
            </Table>
          ) : null}
          {data?.note ? <p className="text-xs text-muted">{data.note}</p> : null}
        </CardContent>
      </Card>
    </div>
  );
}

export function PortfolioDetail({ id, canWrite }: { id: number; canWrite: boolean }) {
  const { data: s, error, mutate } = useSWR<PortfolioSummary>(`/portfolios/${id}`, fetcher, { refreshInterval: 30_000 });
  if (error) return <ErrorNotice error={error} />;
  if (!s) return <Skeleton className="h-96" />;
  return (
    <div className="space-y-4">
      {s.data_mode === "simulated" ? <Notice tone="warn">Prices on this instance are SIMULATED. Values, P&amp;L and returns below are not real market values.</Notice> : null}
      {s.note ? <Notice tone="warn">{s.note}</Notice> : null}
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4 xl:grid-cols-8">
        <Stat label="Current value" value={fmtINR(s.current_value)} sub={s.price_statuses.join(", ") || undefined} />
        <Stat label="Total invested" value={fmtINR(s.total_invested)} />
        <Stat label="Unrealised P&L" value={fmtINR(s.unrealized_pnl)} valueClassName={changeColor(s.unrealized_pnl)} />
        <Stat label="Realised P&L" value={fmtINR(s.realized_pnl)} valueClassName={changeColor(s.realized_pnl)} />
        <Stat label="Dividends" value={fmtINR(s.dividends)} />
        <Stat label="Total P&L" value={fmtINR(s.total_pnl)} valueClassName={changeColor(s.total_pnl)} sub={s.absolute_return_pct != null ? `${fmtPct(s.absolute_return_pct)} absolute` : undefined} />
        <Stat label="XIRR" value={s.xirr_pct != null ? fmtPct(s.xirr_pct) : "—"} valueClassName={changeColor(s.xirr_pct)} title="Money-weighted annual return of all cash flows and today's value" />
        <Stat label="Holdings" value={s.holdings.length} sub={s.first_trade_date ? `since ${fmtDate(s.first_trade_date)}` : undefined} />
      </div>
      <Tabs defaultValue="overview">
        <TabsList>
          <TabsTrigger value="overview">Overview</TabsTrigger>
          <TabsTrigger value="holdings">Holdings</TabsTrigger>
          <TabsTrigger value="transactions">Transactions ({s.transaction_count})</TabsTrigger>
          <TabsTrigger value="rebalance">Rebalance</TabsTrigger>
        </TabsList>
        <TabsContent value="overview" className="space-y-4">
          <PerformanceCard id={id} />
          <div className="grid gap-4 lg:grid-cols-3">
            <Card>
              <CardHeader>
                <CardTitle>Sector allocation</CardTitle>
              </CardHeader>
              <CardContent>{s.allocation_by_sector.length ? <ShareBars rows={s.allocation_by_sector} format={fmtINR} /> : <EmptyState title="No holdings" />}</CardContent>
            </Card>
            <Card>
              <CardHeader>
                <CardTitle>Asset allocation</CardTitle>
              </CardHeader>
              <CardContent className="space-y-4">
                {s.holdings.length ? <ShareBars rows={s.holdings.map((h) => ({ name: h.symbol, weight: h.weight, value: h.market_value }))} color={SERIES[2]} format={fmtINR} /> : <EmptyState title="No holdings" />}
                {s.allocation_by_category.length > 1 ? (
                  <div>
                    <p className="mb-1 text-[10px] font-semibold uppercase tracking-wider text-subtle">By category</p>
                    <ShareBars rows={s.allocation_by_category} color={SERIES[6]} />
                  </div>
                ) : null}
              </CardContent>
            </Card>
            <Card>
              <CardHeader>
                <div>
                  <CardTitle>Contribution &amp; concentration</CardTitle>
                  <CardDescription className="mt-0.5">P&amp;L contribution by holding (unrealised + realised + dividends)</CardDescription>
                </div>
              </CardHeader>
              <CardContent className="space-y-3 text-xs">
                <ul className="space-y-1">
                  {s.contribution.map((c) => (
                    <li key={c.symbol} className="flex justify-between">
                      <span>{c.symbol}</span>
                      <span className={`num ${changeColor(c.pnl)}`}>
                        {fmtINR(c.pnl)}
                        {c.share != null ? <span className="text-muted"> ({fmtNum(c.share * 100, 0)}%)</span> : null}
                      </span>
                    </li>
                  ))}
                </ul>
                <div className="grid grid-cols-2 gap-2">
                  <Stat label="Largest position" value={s.concentration.largest ? `${fmtNum(s.concentration.largest.weight * 100, 1)}%` : "—"} sub={s.concentration.largest?.name} />
                  <Stat label="Top-5 weight" value={s.concentration.top5_weight != null ? `${fmtNum(s.concentration.top5_weight * 100, 1)}%` : "—"} />
                  <Stat label="HHI" value={s.concentration.hhi != null ? fmtNum(s.concentration.hhi, 3) : "—"} title="Herfindahl-Hirschman index: sum of squared weights (1 = single stock)" />
                  <Stat label="Effective holdings" value={s.concentration.effective_holdings != null ? fmtNum(s.concentration.effective_holdings, 1) : "—"} title="1 / HHI: the number of equal-weight holdings with the same concentration" />
                </div>
                <Link href={`/risk?portfolio=${id}`} className="block text-accent hover:underline">
                  Full risk analytics →
                </Link>
              </CardContent>
            </Card>
          </div>
        </TabsContent>
        <TabsContent value="holdings">
          <Card>
            <CardContent className="pt-4">
              <HoldingsTable holdings={s.holdings} />
            </CardContent>
          </Card>
        </TabsContent>
        <TabsContent value="transactions">
          <Card>
            <CardContent className="pt-4">
              <TransactionsPanel portfolioId={id} canWrite={canWrite} />
            </CardContent>
          </Card>
        </TabsContent>
        <TabsContent value="rebalance">
          {s.holdings.length ? <RebalancePanel key={JSON.stringify(s.portfolio.target_allocations)} summary={s} canWrite={canWrite} onSaved={() => void mutate()} /> : <Card><EmptyState title="No holdings to rebalance" /></Card>}
        </TabsContent>
      </Tabs>
    </div>
  );
}
