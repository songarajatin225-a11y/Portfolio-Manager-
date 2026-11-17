"use client";

import * as React from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import useSWR from "swr";
import { toast } from "sonner";
import { Calculator } from "lucide-react";
import { PageHeader } from "@/components/layout/page-header";
import { DataBadge } from "@/components/market/data-badge";
import { PortfolioPicker } from "@/components/portfolio/portfolio-picker";
import type { PortfolioLite } from "@/components/portfolio/types";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { EmptyState, ErrorNotice, Notice, Skeleton } from "@/components/ui/feedback";
import { Field, Input, Select } from "@/components/ui/input";
import { ShareBars, Stat } from "@/components/ui/stat";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { api, errorMessage, fetcher } from "@/lib/api";
import { SERIES, divergingColor } from "@/lib/chart-theme";
import type { DataStatus } from "@/lib/types";
import { changeColor, fmtINR, fmtNum } from "@/lib/utils";

interface VarRow {
  confidence: number;
  horizon_days: number;
  historical_pct: number;
  historical_value: number;
  parametric_pct: number;
  parametric_value: number;
  expected_shortfall_pct: number;
  expected_shortfall_value: number;
}

interface RiskReport {
  available: boolean;
  reason?: string;
  as_of: string;
  value: number;
  lookback_days: number;
  benchmark: string | null;
  data_status: DataStatus;
  var: { available: boolean; reason?: string; observations?: number; annual_vol_pct?: number; daily_vol_pct?: number; table?: VarRow[]; methodology?: string };
  volatility: { portfolio_annual_pct: number | null; by_holding: Record<string, number> };
  max_drawdown_pct: number | null;
  max_drawdown_note: string;
  beta: number | null;
  betas: Record<string, number>;
  correlation: { labels: string[]; matrix: (number | null)[][]; average: number | null };
  diversification_ratio: number | null;
  concentration: { hhi: number | null; effective_holdings: number | null; top5_weight: number | null; largest: { name: string; weight: number } | null };
  sector_exposure: { name: string; weight: number; value: number }[];
  position_exposure: { symbol: string; weight: number; value: number }[];
  scenarios: {
    market_decline: { market_move_pct: number; estimated_move_pct: number | null; estimated_value_change: number | null; largest_contributors?: { symbol: string; value_change: number }[] }[];
    volatility_shock: { volatility_multiplier: number; one_day_var99_pct: number; one_day_var99_value: number }[];
    assumptions: string;
  };
  warnings: string[];
  disclaimer: string;
}

function CorrelationHeatmap({ c }: { c: RiskReport["correlation"] }) {
  if (c.labels.length < 2) return <EmptyState title="Needs at least two holdings with 30+ days of history" />;
  return (
    <div className="overflow-x-auto">
      <table className="border-separate border-spacing-0.5 text-[10px]" aria-label="Correlation matrix of daily returns">
        <thead>
          <tr>
            <th />
            {c.labels.map((l) => (
              <th key={l} scope="col" className="max-w-16 truncate px-1 pb-1 font-medium text-muted" title={l}>
                {l}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {c.matrix.map((row, i) => (
            <tr key={c.labels[i]}>
              <th scope="row" className="pr-1 text-right font-medium text-muted">
                {c.labels[i]}
              </th>
              {row.map((v, j) => (
                <td key={c.labels[j]} className="num h-8 min-w-12 rounded-sm text-center text-white" style={{ background: divergingColor(v) }} title={`${c.labels[i]} / ${c.labels[j]}: ${v ?? "n/a"}`}>
                  {v != null ? v.toFixed(2) : "—"}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      <p className="mt-2 text-[11px] text-muted">
        Blue = move together, red = move oppositely, grey = unrelated. Average pairwise correlation: {c.average != null ? fmtNum(c.average, 2) : "—"}.
      </p>
    </div>
  );
}

function PositionSizer() {
  const [f, setF] = React.useState({ capital: "1000000", risk_pct: "1", entry: "", stop: "", target: "", lot_size: "1", max_position_pct: "20" });
  const [res, setRes] = React.useState<null | { quantity: number; position_value: number; risk_amount: number; risk_per_share: number; position_pct_of_capital: number; direction: string; capped_by_max_position: boolean; risk_reward?: number; reward_amount?: number }>(null);
  const set = (k: keyof typeof f) => (e: React.ChangeEvent<HTMLInputElement>) => setF((x) => ({ ...x, [k]: e.target.value }));
  async function calc(e: React.FormEvent) {
    e.preventDefault();
    try {
      setRes(
        await api.post("/risk/position-size", {
          capital: Number(f.capital),
          risk_pct: Number(f.risk_pct),
          entry: Number(f.entry),
          stop: Number(f.stop),
          target: f.target ? Number(f.target) : null,
          lot_size: Number(f.lot_size) || 1,
          max_position_pct: f.max_position_pct ? Number(f.max_position_pct) : null,
        }),
      );
    } catch (err) {
      setRes(null);
      toast.error(errorMessage(err));
    }
  }
  return (
    <Card>
      <CardHeader>
        <div>
          <CardTitle>Position size calculator</CardTitle>
          <CardDescription className="mt-0.5">Fixed-fractional sizing: risk a set share of capital between entry and stop.</CardDescription>
        </div>
      </CardHeader>
      <CardContent>
        <form onSubmit={calc} className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <Field label="Capital (₹)"><Input type="number" min="1" step="any" required value={f.capital} onChange={set("capital")} /></Field>
          <Field label="Risk per trade (%)"><Input type="number" min="0.01" max="100" step="any" required value={f.risk_pct} onChange={set("risk_pct")} /></Field>
          <Field label="Entry (₹)"><Input type="number" min="0.01" step="any" required value={f.entry} onChange={set("entry")} /></Field>
          <Field label="Stop-loss (₹)"><Input type="number" min="0.01" step="any" required value={f.stop} onChange={set("stop")} /></Field>
          <Field label="Target (₹, optional)"><Input type="number" min="0.01" step="any" value={f.target} onChange={set("target")} /></Field>
          <Field label="Lot size"><Input type="number" min="1" step="1" value={f.lot_size} onChange={set("lot_size")} /></Field>
          <Field label="Max position (% of capital)"><Input type="number" min="1" max="100" step="any" value={f.max_position_pct} onChange={set("max_position_pct")} /></Field>
          <div className="flex items-end">
            <Button type="submit" className="w-full"><Calculator /> Calculate</Button>
          </div>
        </form>
        {res ? (
          <div className="mt-4 grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-6">
            <Stat label="Quantity" value={res.quantity.toLocaleString("en-IN")} sub={res.direction} />
            <Stat label="Position value" value={fmtINR(res.position_value)} sub={`${fmtNum(res.position_pct_of_capital, 1)}% of capital`} />
            <Stat label="Amount at risk" value={fmtINR(res.risk_amount)} valueClassName="text-down" />
            <Stat label="Risk per share" value={fmtINR(res.risk_per_share)} />
            <Stat label="Reward / risk" value={res.risk_reward != null ? `${fmtNum(res.risk_reward, 2)} : 1` : "—"} />
            <Stat label="Potential reward" value={res.reward_amount != null ? fmtINR(res.reward_amount) : "—"} valueClassName="text-up" />
            {res.capped_by_max_position ? <Notice tone="warn" className="col-span-full">Quantity was reduced to respect the maximum position size.</Notice> : null}
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}

function RiskReportView({ id, lookback }: { id: number; lookback: number }) {
  const { data: r, error } = useSWR<RiskReport>(`/portfolios/${id}/risk?lookback_days=${lookback}`, fetcher);
  if (error) return <ErrorNotice error={error} />;
  if (!r) return <Skeleton className="h-96" />;
  if (!r.available) return <Card><EmptyState title="Risk analysis unavailable" description={r.reason} /></Card>;
  const vols = Object.entries(r.volatility.by_holding).sort((a, b) => b[1] - a[1]);
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <DataBadge status={r.data_status} source={`daily closes, ${r.lookback_days}-day window`} asOf={r.as_of} />
        <span className="text-xs text-muted">Benchmark: {r.benchmark ?? "not available"}</span>
      </div>
      {r.warnings.map((w) => (
        <Notice key={w} tone="warn">{w}</Notice>
      ))}
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 xl:grid-cols-6">
        <Stat label="Portfolio value" value={fmtINR(r.value)} />
        <Stat label="Volatility (ann.)" value={r.volatility.portfolio_annual_pct != null ? `${fmtNum(r.volatility.portfolio_annual_pct, 1)}%` : "—"} />
        <Stat label="Beta" value={r.beta != null ? fmtNum(r.beta, 2) : "—"} sub={r.benchmark ? `vs ${r.benchmark}` : undefined} />
        <Stat label="Max drawdown" value={r.max_drawdown_pct != null ? `${fmtNum(r.max_drawdown_pct, 1)}%` : "—"} valueClassName="text-down" title={r.max_drawdown_note} />
        <Stat label="Diversification ratio" value={r.diversification_ratio != null ? fmtNum(r.diversification_ratio, 2) : "—"} title="Weighted average volatility ÷ portfolio volatility; higher means more diversification benefit" />
        <Stat label="Effective holdings" value={r.concentration.effective_holdings != null ? fmtNum(r.concentration.effective_holdings, 1) : "—"} sub={r.concentration.largest ? `largest ${r.concentration.largest.name} ${fmtNum(r.concentration.largest.weight * 100, 1)}%` : undefined} />
      </div>

      <Card>
        <CardHeader>
          <div>
            <CardTitle>Value at Risk &amp; expected shortfall</CardTitle>
            <CardDescription className="mt-0.5">{r.var.available ? `${r.var.observations} daily observations` : r.var.reason}</CardDescription>
          </div>
        </CardHeader>
        {r.var.available && r.var.table ? (
          <CardContent className="space-y-3">
            <div className="overflow-x-auto">
              <Table>
                <THead>
                  <TR>
                    <TH>Confidence</TH>
                    <TH>Horizon</TH>
                    <TH className="text-right">Historical VaR</TH>
                    <TH className="text-right">Parametric VaR</TH>
                    <TH className="text-right">Expected shortfall</TH>
                  </TR>
                </THead>
                <TBody>
                  {r.var.table.map((row) => (
                    <TR key={`${row.confidence}-${row.horizon_days}`}>
                      <TD>{(row.confidence * 100).toFixed(0)}%</TD>
                      <TD>{row.horizon_days} day{row.horizon_days > 1 ? "s" : ""}</TD>
                      <TD className="num text-right">{fmtINR(row.historical_value)} <span className="text-muted">({fmtNum(row.historical_pct, 2)}%)</span></TD>
                      <TD className="num text-right">{fmtINR(row.parametric_value)} <span className="text-muted">({fmtNum(row.parametric_pct, 2)}%)</span></TD>
                      <TD className="num text-right">{fmtINR(row.expected_shortfall_value)} <span className="text-muted">({fmtNum(row.expected_shortfall_pct, 2)}%)</span></TD>
                    </TR>
                  ))}
                </TBody>
              </Table>
            </div>
            <p className="text-xs text-muted"><span className="font-medium text-foreground">Methodology: </span>{r.var.methodology}</p>
          </CardContent>
        ) : null}
      </Card>

      <div className="grid items-start gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader><CardTitle>Correlation of daily returns</CardTitle></CardHeader>
          <CardContent><CorrelationHeatmap c={r.correlation} /></CardContent>
        </Card>
        <Card>
          <CardHeader>
            <div>
              <CardTitle>Scenario analysis</CardTitle>
              <CardDescription className="mt-0.5">{r.scenarios.assumptions}</CardDescription>
            </div>
          </CardHeader>
          <CardContent className="space-y-4">
            <Table>
              <THead>
                <TR>
                  <TH>Market move</TH>
                  <TH className="text-right">Est. portfolio move</TH>
                  <TH className="text-right">Est. value change</TH>
                  <TH>Largest contributors</TH>
                </TR>
              </THead>
              <TBody>
                {r.scenarios.market_decline.map((s) => (
                  <TR key={s.market_move_pct}>
                    <TD className="num text-down">{fmtNum(s.market_move_pct, 0)}%</TD>
                    <TD className={`num text-right ${changeColor(s.estimated_move_pct)}`}>{s.estimated_move_pct != null ? `${fmtNum(s.estimated_move_pct, 1)}%` : "—"}</TD>
                    <TD className={`num text-right ${changeColor(s.estimated_value_change)}`}>{s.estimated_value_change != null ? fmtINR(s.estimated_value_change) : "—"}</TD>
                    <TD className="text-xs text-muted">{s.largest_contributors?.map((c) => c.symbol).join(", ") ?? "beta unavailable"}</TD>
                  </TR>
                ))}
              </TBody>
            </Table>
            {r.scenarios.volatility_shock.length ? (
              <Table>
                <THead>
                  <TR>
                    <TH>Volatility shock</TH>
                    <TH className="text-right">1-day 99% VaR</TH>
                  </TR>
                </THead>
                <TBody>
                  {r.scenarios.volatility_shock.map((s) => (
                    <TR key={s.volatility_multiplier}>
                      <TD>{s.volatility_multiplier}× current volatility</TD>
                      <TD className="num text-right">{fmtINR(s.one_day_var99_value)} <span className="text-muted">({fmtNum(s.one_day_var99_pct, 2)}%)</span></TD>
                    </TR>
                  ))}
                </TBody>
              </Table>
            ) : null}
          </CardContent>
        </Card>
      </div>

      <div className="grid gap-4 lg:grid-cols-3">
        <Card>
          <CardHeader><CardTitle>Position exposure</CardTitle></CardHeader>
          <CardContent><ShareBars rows={r.position_exposure.map((p) => ({ name: p.symbol, weight: p.weight, value: p.value }))} format={fmtINR} /></CardContent>
        </Card>
        <Card>
          <CardHeader><CardTitle>Sector exposure</CardTitle></CardHeader>
          <CardContent><ShareBars rows={r.sector_exposure} color={SERIES[2]} format={fmtINR} /></CardContent>
        </Card>
        <Card>
          <CardHeader><CardTitle>Volatility &amp; beta by holding</CardTitle></CardHeader>
          <CardContent>
            <Table>
              <THead>
                <TR>
                  <TH>Symbol</TH>
                  <TH className="text-right">Volatility</TH>
                  <TH className="text-right">Beta</TH>
                </TR>
              </THead>
              <TBody>
                {vols.map(([sym, v]) => (
                  <TR key={sym}>
                    <TD>{sym}</TD>
                    <TD className="num text-right">{fmtNum(v, 1)}%</TD>
                    <TD className="num text-right">{r.betas[sym] != null ? fmtNum(r.betas[sym], 2) : "—"}</TD>
                  </TR>
                ))}
              </TBody>
            </Table>
          </CardContent>
        </Card>
      </div>
      <Notice tone="warn">{r.disclaimer}</Notice>
    </div>
  );
}

export default function RiskPage() {
  const params = useSearchParams();
  const { data, error } = useSWR<PortfolioLite[]>("/portfolios", fetcher);
  const [picked, setPicked] = React.useState<number | null>(() => (params.get("portfolio") ? Number(params.get("portfolio")) : null));
  const [lookback, setLookback] = React.useState(365);
  const current = data?.find((p) => p.id === picked)?.id ?? data?.[0]?.id ?? null;
  return (
    <>
      <PageHeader
        title="Risk Analytics"
        description="Concentration, volatility, drawdown, Value at Risk, correlation and scenario analysis for your portfolios, plus a position size calculator."
        actions={
          data?.length ? (
            <div className="flex gap-2">
              <PortfolioPicker portfolios={data} value={current} onChange={setPicked} className="w-48" />
              <Select aria-label="Look-back window" className="w-32" value={lookback} onChange={(e) => setLookback(Number(e.target.value))}>
                <option value={180}>6 months</option>
                <option value={365}>1 year</option>
                <option value={730}>2 years</option>
                <option value={1095}>3 years</option>
              </Select>
            </div>
          ) : null
        }
      />
      <div className="space-y-4">
        <ErrorNotice error={error} />
        {!data && !error ? <Skeleton className="h-64" /> : null}
        {data && !data.length ? (
          <Card>
            <EmptyState title="No portfolios" description={<>Create one in the <Link className="text-accent hover:underline" href="/portfolios">Portfolio Manager</Link> to see its risk profile.</>} />
          </Card>
        ) : null}
        {current != null ? <RiskReportView key={`${current}-${lookback}`} id={current} lookback={lookback} /> : null}
        <PositionSizer />
      </div>
    </>
  );
}
