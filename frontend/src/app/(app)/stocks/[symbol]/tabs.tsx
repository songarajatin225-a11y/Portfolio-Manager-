"use client";

import useSWR from "swr";
import { Bar, BarChart, CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { EmptyState, ErrorNotice, Skeleton } from "@/components/ui/feedback";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { DataBadge } from "@/components/market/data-badge";
import { fetcher } from "@/lib/api";
import type { DataStatus } from "@/lib/types";
import { changeColor, cn, fmtDate, fmtDateTime, fmtNum, fmtPct, fmtPrice } from "@/lib/utils";

export interface TechnicalsResponse {
  data_status: DataStatus;
  source: string;
  as_of?: string;
  warnings: string[];
  series: Record<string, (number | null)[] | string[]>;
  latest: Record<string, number | null>;
  flags?: Record<string, boolean>;
  signals?: { indicator: string; reading: string; tone: "bullish" | "bearish" | "neutral" }[];
  levels: {
    fibonacci?: { swing_high: number; swing_low: number; trend: string; levels: Record<string, number> } | null;
    support?: { price: number; touches: number }[];
    resistance?: { price: number; touches: number }[];
  };
  volume_profile?: { price_low: number; price_high: number; volume: number; share: number }[];
}

const TONE = { bullish: "up", bearish: "down", neutral: "default" } as const;
const tooltipStyle = { background: "#131a23", border: "1px solid #2f3c4f", fontSize: 12 };

export function TechnicalsTab({ data }: { data: TechnicalsResponse | undefined }) {
  if (!data) return <Skeleton className="h-48" />;
  const l = data.latest;
  const fib = data.levels.fibonacci;
  return (
    <div className="grid gap-4 lg:grid-cols-3">
      <Card className="lg:col-span-2">
        <CardHeader>
          <CardTitle>Indicator readings</CardTitle>
          <DataBadge status={data.data_status} source={data.source} asOf={data.as_of} />
        </CardHeader>
        <CardContent className="space-y-3">
          {data.signals?.length ? (
            <ul className="divide-y divide-border/60">
              {data.signals.map((s) => (
                <li key={s.indicator} className="flex items-center justify-between py-1.5 text-sm">
                  <span className="text-muted">{s.indicator}</span>
                  <span className="flex items-center gap-2">
                    <span className="num text-xs">{s.reading}</span>
                    <Badge variant={TONE[s.tone]}>{s.tone}</Badge>
                  </span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-xs text-muted">Signals are computed on daily bars.</p>
          )}
          <div className="grid grid-cols-2 gap-2 text-xs sm:grid-cols-4">
            {[
              ["EMA 20", l.ema_20],
              ["EMA 50", l.ema_50],
              ["EMA 200", l.ema_200],
              ["SMA 200", l.sma_200],
              ["ATR 14", l.atr_14],
              ["ADX 14", l.adx_14],
              ["MACD hist", l.macd_hist],
              ["Volatility (ann.)", l.volatility_20 != null ? `${fmtNum(l.volatility_20)}%` : null],
            ].map(([k, v]) => (
              <div key={k as string} className="rounded border border-border bg-surface-2 px-2 py-1.5">
                <p className="text-[10px] uppercase text-subtle">{k}</p>
                <p className="num">{typeof v === "number" ? fmtNum(v) : (v ?? "—")}</p>
              </div>
            ))}
          </div>
          <p className="text-[11px] text-subtle">Descriptive readings of standard indicators, not trading recommendations.</p>
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle>Key levels</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3 text-xs">
          <div>
            <p className="mb-1 font-medium text-down">Resistance</p>
            {data.levels.resistance?.length ? data.levels.resistance.map((r) => <p key={r.price} className="num">{fmtPrice(r.price)} <span className="text-subtle">({r.touches} touches)</span></p>) : <p className="text-muted">None identified</p>}
          </div>
          <div>
            <p className="mb-1 font-medium text-up">Support</p>
            {data.levels.support?.length ? data.levels.support.map((s) => <p key={s.price} className="num">{fmtPrice(s.price)} <span className="text-subtle">({s.touches} touches)</span></p>) : <p className="text-muted">None identified</p>}
          </div>
          {fib ? (
            <div>
              <p className="mb-1 font-medium">Fibonacci retracement ({fib.trend === "up" ? "from swing low to high" : "from swing high to low"})</p>
              {Object.entries(fib.levels).map(([k, v]) => (
                <p key={k} className="num flex justify-between">
                  <span className="text-muted">{k}</span>
                  <span>{fmtPrice(v)}</span>
                </p>
              ))}
            </div>
          ) : null}
          <p className="text-[10px] text-subtle">Levels come from confirmed swing points (±5 bars) over the last 250 bars.</p>
        </CardContent>
      </Card>
      {data.volume_profile?.length ? (
        <Card className="lg:col-span-3">
          <CardHeader>
            <CardTitle>Volume profile (last 120 bars)</CardTitle>
          </CardHeader>
          <CardContent className="h-56">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={data.volume_profile.map((b) => ({ price: fmtPrice((b.price_low + b.price_high) / 2), share: +(b.share * 100).toFixed(2) }))}>
                <CartesianGrid stroke="#222c3a" vertical={false} />
                <XAxis dataKey="price" tick={{ fill: "#8b98a9", fontSize: 10 }} interval={2} />
                <YAxis tick={{ fill: "#8b98a9", fontSize: 10 }} unit="%" />
                <Tooltip contentStyle={tooltipStyle} formatter={(v) => [`${v}%`, "Share of volume"]} />
                <Bar dataKey="share" fill="#3b82f6" radius={[2, 2, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </CardContent>
        </Card>
      ) : null}
    </div>
  );
}

interface PerformanceResponse {
  data_status: DataStatus;
  source: string;
  as_of?: string;
  returns: Record<string, number | null>;
  cagr?: number | null;
  max_drawdown_pct?: number;
  history_start?: string;
  calendar_returns?: Record<string, number>;
}

export function PerformanceTab({ symbol }: { symbol: string }) {
  const { data, error } = useSWR<PerformanceResponse>(`/stocks/${encodeURIComponent(symbol)}/performance`, fetcher);
  if (error) return <ErrorNotice error={error} />;
  if (!data) return <Skeleton className="h-40" />;
  return (
    <Card>
      <CardHeader>
        <div>
          <CardTitle>Historical performance</CardTitle>
          <p className="mt-0.5 text-xs text-muted">From daily closes since {fmtDate(data.history_start)}. Past performance does not indicate future results.</p>
        </div>
        <DataBadge status={data.data_status} source={data.source} asOf={data.as_of} />
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="grid grid-cols-4 gap-2 sm:grid-cols-8">
          {Object.entries(data.returns).map(([k, v]) => (
            <div key={k} className="rounded border border-border bg-surface-2 px-2 py-1.5 text-center">
              <p className="text-[10px] text-subtle">{k}</p>
              <p className={cn("num text-sm", changeColor(v))}>{fmtPct(v)}</p>
            </div>
          ))}
        </div>
        <div className="flex flex-wrap gap-6 text-xs">
          <p>
            <span className="text-muted">CAGR: </span>
            <span className="num">{fmtPct(data.cagr)}</span>
          </p>
          <p>
            <span className="text-muted">Maximum drawdown: </span>
            <span className="num text-down">{fmtPct(data.max_drawdown_pct)}</span>
          </p>
        </div>
        {data.calendar_returns && Object.keys(data.calendar_returns).length ? (
          <div className="h-48">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={Object.entries(data.calendar_returns).map(([year, r]) => ({ year, r }))}>
                <CartesianGrid stroke="#222c3a" vertical={false} />
                <XAxis dataKey="year" tick={{ fill: "#8b98a9", fontSize: 11 }} />
                <YAxis tick={{ fill: "#8b98a9", fontSize: 11 }} unit="%" />
                <Tooltip contentStyle={tooltipStyle} formatter={(v) => [`${v}%`, "Calendar-year return"]} />
                <Bar dataKey="r" fill="#3b82f6" radius={[2, 2, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}

interface Ratio {
  value: number | null;
  formula: string;
  period: string;
  source: string;
  reported_at: string | null;
  reason: string | null;
  unit: string;
}
interface FundamentalsResponse {
  ratios: { price: number | null; price_source: string | null; price_as_of: string | null; periods_used: number; periods_rejected: number; note?: string; ratios: Record<string, Ratio> };
  periods: Record<string, unknown>[];
}

const RATIO_ORDER: [string, string][] = [
  ["pe", "P/E"],
  ["pb", "P/B"],
  ["roe", "ROE"],
  ["roce", "ROCE"],
  ["debt_to_equity", "Debt / Equity"],
  ["dividend_yield", "Dividend yield"],
  ["eps", "EPS (TTM/FY)"],
  ["market_cap", "Market cap"],
  ["revenue", "Revenue"],
  ["ebitda", "EBITDA"],
  ["net_profit", "Net profit"],
  ["free_cash_flow", "Free cash flow"],
  ["ebitda_margin", "EBITDA margin"],
  ["net_margin", "Net margin"],
  ["revenue_growth", "Revenue growth"],
  ["profit_growth", "Profit growth"],
  ["earnings_quality", "Earnings quality"],
  ["book_value_per_share", "Book value / share"],
];

function fmtRatio(r: Ratio): string {
  if (r.value == null) return "—";
  if (r.unit === "%") return `${fmtNum(r.value)}%`;
  if (r.unit === "x") return `${fmtNum(r.value)}×`;
  if (r.unit === "INR cr") return `₹${fmtNum(r.value, 0)} cr`;
  if (r.unit === "INR") return `₹${fmtNum(r.value)}`;
  return fmtNum(r.value);
}

export function FundamentalsTab({ symbol, isAdmin }: { symbol: string; isAdmin: boolean }) {
  const { data, error } = useSWR<FundamentalsResponse>(`/stocks/${encodeURIComponent(symbol)}/fundamentals`, fetcher);
  if (error) return <ErrorNotice error={error} />;
  if (!data) return <Skeleton className="h-40" />;
  const r = data.ratios;
  if (!data.periods.length) {
    return (
      <Card>
        <EmptyState
          title="No financial statements on record"
          description={
            <>
              Ratios are only computed from validated statements with a stated source.{" "}
              {isAdmin ? "Import them under System Administration → Data Imports (fundamentals CSV)." : "Ask an administrator to import them."}
            </>
          }
        />
      </Card>
    );
  }
  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <div>
            <CardTitle>Ratios</CardTitle>
            <p className="mt-0.5 text-xs text-muted">
              Price {fmtPrice(r.price)} ({r.price_source ?? "n/a"}{r.price_as_of ? `, ${fmtDateTime(r.price_as_of)}` : ""}) · {r.periods_used} validated period(s)
              {r.periods_rejected ? `, ${r.periods_rejected} excluded by validation` : ""}
            </p>
          </div>
        </CardHeader>
        <CardContent className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
          {RATIO_ORDER.filter(([k]) => r.ratios[k]).map(([k, label]) => {
            const ratio = r.ratios[k];
            return (
              <div key={k} className="rounded-md border border-border bg-surface-2 px-3 py-2" title={`${ratio.formula}\nPeriod: ${ratio.period}\nSource: ${ratio.source}\nReported: ${ratio.reported_at ?? "—"}`}>
                <div className="flex items-baseline justify-between gap-2">
                  <span className="text-xs text-muted">{label}</span>
                  <span className="num text-sm font-semibold">{fmtRatio(ratio)}</span>
                </div>
                <p className="mt-0.5 truncate text-[10px] text-subtle">{ratio.value == null ? ratio.reason : `${ratio.period} · ${ratio.source}${ratio.reported_at ? ` · reported ${fmtDate(ratio.reported_at)}` : ""}`}</p>
              </div>
            );
          })}
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle>Reported statements (INR crore)</CardTitle>
        </CardHeader>
        <CardContent className="p-0">
          <Table>
            <THead>
              <tr>
                <TH>Period</TH>
                <TH className="text-right">Revenue</TH>
                <TH className="text-right">EBITDA</TH>
                <TH className="text-right">Net profit</TH>
                <TH className="text-right">EPS</TH>
                <TH className="text-right">Equity</TH>
                <TH className="text-right">Debt</TH>
                <TH>Source</TH>
              </tr>
            </THead>
            <TBody>
              {data.periods.map((p, i) => {
                const errs = (p.validation_errors as string[]) ?? [];
                return (
                  <TR key={i} className={errs.length ? "opacity-60" : undefined}>
                    <TD className="text-xs">
                      {p.period_type === "Q" ? "Quarter" : "FY"} {fmtDate(p.period_end as string)}
                      {errs.length ? <Badge variant="warn" className="ml-2" title={errs.join("\n")}>excluded</Badge> : null}
                    </TD>
                    {["revenue", "ebitda", "net_profit", "eps", "total_equity", "total_debt"].map((f) => (
                      <TD key={f} className="num text-right text-xs">{p[f] == null ? "—" : fmtNum(p[f] as number, f === "eps" ? 2 : 0)}</TD>
                    ))}
                    <TD className="text-[11px] text-muted">
                      {p.source as string}
                      {p.reported_at ? ` · ${fmtDate(p.reported_at as string)}` : ""}
                    </TD>
                  </TR>
                );
              })}
            </TBody>
          </Table>
        </CardContent>
      </Card>
    </div>
  );
}

interface InstitutionalResponse {
  shareholding: { period_end: string; promoter_pct: number | null; fii_pct: number | null; dii_pct: number | null; public_pct: number | null; source: string; reported_at: string | null }[];
  deals: { date: string; deal_type: string; client_name: string; side: string; quantity: number; price: number; source: string }[];
}

export function InstitutionalTab({ symbol }: { symbol: string }) {
  const { data, error } = useSWR<InstitutionalResponse>(`/stocks/${encodeURIComponent(symbol)}/institutional`, fetcher);
  if (error) return <ErrorNotice error={error} />;
  if (!data) return <Skeleton className="h-40" />;
  if (!data.shareholding.length && !data.deals.length) {
    return <Card><EmptyState title="No shareholding or deal data on record" description="Import shareholding patterns and bulk/block deals (CSV) under System Administration → Data Imports." /></Card>;
  }
  const latest = data.shareholding[data.shareholding.length - 1];
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      {data.shareholding.length ? (
        <Card>
          <CardHeader>
            <div>
              <CardTitle>Shareholding pattern</CardTitle>
              <p className="mt-0.5 text-xs text-muted">Latest: {fmtDate(latest.period_end)} · {latest.source}</p>
            </div>
          </CardHeader>
          <CardContent className="h-64">
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={data.shareholding.map((s) => ({ ...s, period: fmtDate(s.period_end) }))}>
                <CartesianGrid stroke="#222c3a" />
                <XAxis dataKey="period" tick={{ fill: "#8b98a9", fontSize: 10 }} />
                <YAxis tick={{ fill: "#8b98a9", fontSize: 10 }} unit="%" />
                <Tooltip contentStyle={tooltipStyle} />
                <Legend wrapperStyle={{ fontSize: 11 }} />
                <Line dataKey="promoter_pct" name="Promoter" stroke="#3b82f6" dot={false} />
                <Line dataKey="fii_pct" name="FII" stroke="#22c55e" dot={false} />
                <Line dataKey="dii_pct" name="DII" stroke="#f59e0b" dot={false} />
                <Line dataKey="public_pct" name="Public" stroke="#a855f7" dot={false} />
              </LineChart>
            </ResponsiveContainer>
          </CardContent>
        </Card>
      ) : null}
      {data.deals.length ? (
        <Card>
          <CardHeader>
            <CardTitle>Bulk & block deals</CardTitle>
          </CardHeader>
          <CardContent className="p-0">
            <Table>
              <THead>
                <tr>
                  <TH>Date</TH>
                  <TH>Client</TH>
                  <TH>Side</TH>
                  <TH className="text-right">Qty</TH>
                  <TH className="text-right">Price</TH>
                </tr>
              </THead>
              <TBody>
                {data.deals.map((d, i) => (
                  <TR key={i}>
                    <TD className="text-xs">{fmtDate(d.date)} <Badge>{d.deal_type}</Badge></TD>
                    <TD className="text-xs" title={`Source: ${d.source}`}>{d.client_name}</TD>
                    <TD><Badge variant={d.side === "BUY" ? "up" : "down"}>{d.side}</Badge></TD>
                    <TD className="num text-right text-xs">{d.quantity.toLocaleString("en-IN")}</TD>
                    <TD className="num text-right text-xs">{fmtPrice(d.price)}</TD>
                  </TR>
                ))}
              </TBody>
            </Table>
          </CardContent>
        </Card>
      ) : null}
    </div>
  );
}
