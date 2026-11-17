"use client";

import { Area, AreaChart, Bar, BarChart, CartesianGrid, Cell, Legend, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { DataBadge } from "@/components/market/data-badge";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Notice } from "@/components/ui/feedback";
import { Stat } from "@/components/ui/stat";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { AXIS_TICK, DOWN, GRID, SERIES, TOOLTIP_LABEL, TOOLTIP_STYLE, UP } from "@/lib/chart-theme";
import type { DataStatus } from "@/lib/types";
import { changeColor, fmtINR, fmtNum, fmtPct, fmtPrice } from "@/lib/utils";

export interface BacktestResult {
  metrics: {
    initial_capital: number;
    final_equity: number;
    total_return_pct: number;
    cagr_pct: number | null;
    annual_volatility_pct: number | null;
    sharpe: number | null;
    sortino: number | null;
    max_drawdown_pct: number;
    calmar: number | null;
    trades: number;
    win_rate: number | null;
    profit_factor: number | null;
    avg_trade_pct: number | null;
    avg_win: number | null;
    avg_loss: number | null;
    avg_holding_days: number | null;
    exposure_pct: number;
    total_costs: number;
    risk_free_rate_pct: number;
    benchmark_return_pct?: number;
    benchmark_cagr_pct?: number | null;
  };
  equity_curve: { date: string; equity: number; drawdown_pct: number; benchmark: number | null }[];
  trades: { symbol: string; entry_date: string; exit_date: string; entry_price: number; exit_price: number; quantity: number; net_pnl: number; return_pct: number; holding_days: number; costs: number; exit_reason: string }[];
  monthly_returns: { month: string; return_pct: number }[];
  skipped_entries: number;
  period: { start: string; end: string; bars: number };
  data_status: DataStatus;
  data_sources: string[];
  warnings: string[];
  assumptions: string[];
  universe_size: number;
}

export function BacktestResults({ r, name }: { r: BacktestResult; name: string }) {
  const m = r.metrics;
  const hasBench = r.equity_curve.some((p) => p.benchmark != null);
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h2 className="text-sm font-semibold">{name}</h2>
          <p className="text-xs text-muted">
            {r.period.start} → {r.period.end} · {r.period.bars} sessions · {r.universe_size} instruments
          </p>
        </div>
        <DataBadge status={r.data_status} source={r.data_sources.join(", ")} asOf={r.period.end} />
      </div>
      {r.data_status === "SIMULATED" ? <Notice tone="warn">This backtest ran on SIMULATED prices. Results say nothing about real market behaviour.</Notice> : null}
      {r.warnings.map((w) => (
        <Notice key={w} tone="warn">{w}</Notice>
      ))}
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4 xl:grid-cols-8">
        <Stat label="Total return" value={fmtPct(m.total_return_pct)} valueClassName={changeColor(m.total_return_pct)} sub={`${fmtINR(m.initial_capital)} → ${fmtINR(m.final_equity)}`} />
        <Stat label="CAGR" value={m.cagr_pct != null ? fmtPct(m.cagr_pct) : "—"} valueClassName={changeColor(m.cagr_pct)} sub={m.benchmark_cagr_pct != null ? `benchmark ${fmtPct(m.benchmark_cagr_pct)}` : undefined} />
        <Stat label="Max drawdown" value={fmtPct(m.max_drawdown_pct, 2, false)} valueClassName="text-down" />
        <Stat label="Sharpe" value={m.sharpe != null ? fmtNum(m.sharpe, 2) : "—"} sub={`rf ${m.risk_free_rate_pct}%`} />
        <Stat label="Sortino" value={m.sortino != null ? fmtNum(m.sortino, 2) : "—"} />
        <Stat label="Win rate" value={m.win_rate != null ? `${fmtNum(m.win_rate * 100, 1)}%` : "—"} sub={`${m.trades} trades`} />
        <Stat label="Profit factor" value={m.profit_factor != null ? fmtNum(m.profit_factor, 2) : "—"} />
        <Stat label="Exposure" value={`${fmtNum(m.exposure_pct, 0)}%`} sub={`costs ${fmtINR(m.total_costs)}`} />
      </div>
      <Card>
        <CardHeader>
          <div>
            <CardTitle>Equity curve</CardTitle>
            <CardDescription className="mt-0.5">{hasBench ? `Strategy vs benchmark buy-and-hold with the same starting capital (benchmark return ${fmtPct(m.benchmark_return_pct)}).` : "Benchmark history not available for this period."}</CardDescription>
          </div>
        </CardHeader>
        <CardContent className="space-y-2">
          <div className="h-72" role="img" aria-label="Strategy equity versus benchmark over the test period">
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={r.equity_curve} margin={{ top: 4, right: 8, bottom: 0, left: 8 }}>
                <CartesianGrid stroke={GRID} vertical={false} />
                <XAxis dataKey="date" tick={AXIS_TICK} minTickGap={50} />
                <YAxis tick={AXIS_TICK} width={72} domain={["auto", "auto"]} tickFormatter={(v) => fmtNum(Number(v), 0)} />
                <Tooltip contentStyle={TOOLTIP_STYLE} labelStyle={TOOLTIP_LABEL} formatter={(v, n) => [fmtINR(Number(v)), n]} />
                <Legend wrapperStyle={{ fontSize: 11 }} />
                <Line name="Strategy" dataKey="equity" stroke={SERIES[0]} strokeWidth={2} dot={false} />
                {hasBench ? <Line name="Benchmark" dataKey="benchmark" stroke={SERIES[1]} strokeWidth={2} dot={false} /> : null}
              </LineChart>
            </ResponsiveContainer>
          </div>
          <div className="h-28" role="img" aria-label="Strategy drawdown from peak">
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={r.equity_curve} margin={{ top: 0, right: 8, bottom: 0, left: 8 }}>
                <CartesianGrid stroke={GRID} vertical={false} />
                <XAxis dataKey="date" hide />
                <YAxis tick={AXIS_TICK} width={72} unit="%" />
                <Tooltip contentStyle={TOOLTIP_STYLE} labelStyle={TOOLTIP_LABEL} formatter={(v) => [`${fmtNum(Number(v), 2)}%`, "Drawdown"]} />
                <Area dataKey="drawdown_pct" stroke={DOWN} fill={DOWN} fillOpacity={0.2} strokeWidth={1.5} />
              </AreaChart>
            </ResponsiveContainer>
          </div>
        </CardContent>
      </Card>
      <div className="grid gap-4 2xl:grid-cols-[minmax(0,1fr)_minmax(0,1.6fr)]">
        <Card>
          <CardHeader><CardTitle>Monthly returns</CardTitle></CardHeader>
          <CardContent className="h-64" role="img" aria-label="Monthly strategy returns">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={r.monthly_returns} margin={{ top: 4, right: 8, bottom: 0, left: 0 }}>
                <CartesianGrid stroke={GRID} vertical={false} />
                <XAxis dataKey="month" tick={AXIS_TICK} minTickGap={20} />
                <YAxis tick={AXIS_TICK} unit="%" width={44} />
                <ReferenceLine y={0} stroke="#383835" />
                <Tooltip contentStyle={TOOLTIP_STYLE} labelStyle={TOOLTIP_LABEL} cursor={{ fill: "rgba(255,255,255,0.04)" }} formatter={(v) => [fmtPct(Number(v)), "Return"]} />
                <Bar dataKey="return_pct" radius={[2, 2, 0, 0]}>
                  {r.monthly_returns.map((x) => (
                    <Cell key={x.month} fill={x.return_pct >= 0 ? UP : DOWN} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <div>
              <CardTitle>Trades ({r.trades.length})</CardTitle>
              <CardDescription className="mt-0.5">
                Avg trade {fmtPct(m.avg_trade_pct)} · avg win {fmtINR(m.avg_win)} · avg loss {fmtINR(m.avg_loss)} · avg hold {fmtNum(m.avg_holding_days, 1)} days
                {r.skipped_entries ? ` · ${r.skipped_entries} entry signals skipped (no free slot or cash)` : ""}
              </CardDescription>
            </div>
          </CardHeader>
          <CardContent className="max-h-80 overflow-auto">
            <Table>
              <THead>
                <TR>
                  <TH>Symbol</TH>
                  <TH>Entry</TH>
                  <TH>Exit</TH>
                  <TH className="text-right">Qty</TH>
                  <TH className="text-right">Entry ₹</TH>
                  <TH className="text-right">Exit ₹</TH>
                  <TH className="text-right">Net P&amp;L</TH>
                  <TH>Exit reason</TH>
                </TR>
              </THead>
              <TBody>
                {[...r.trades].reverse().map((t, i) => (
                  <TR key={`${t.symbol}-${t.entry_date}-${i}`}>
                    <TD className="font-medium">{t.symbol}</TD>
                    <TD className="whitespace-nowrap text-xs">{t.entry_date}</TD>
                    <TD className="whitespace-nowrap text-xs">{t.exit_date}</TD>
                    <TD className="num text-right">{t.quantity}</TD>
                    <TD className="num text-right">{fmtPrice(t.entry_price)}</TD>
                    <TD className="num text-right">{fmtPrice(t.exit_price)}</TD>
                    <TD className={`num whitespace-nowrap text-right ${changeColor(t.net_pnl)}`}>
                      {fmtINR(t.net_pnl)}
                      <span className="block text-[11px]">{fmtPct(t.return_pct)}</span>
                    </TD>
                    <TD className="whitespace-nowrap"><Badge>{t.exit_reason}</Badge></TD>
                  </TR>
                ))}
              </TBody>
            </Table>
          </CardContent>
        </Card>
      </div>
      <Card>
        <CardHeader><CardTitle>Assumptions</CardTitle></CardHeader>
        <CardContent>
          <ul className="list-disc space-y-1 pl-4 text-xs text-muted">
            {r.assumptions.map((a) => <li key={a}>{a}</li>)}
          </ul>
        </CardContent>
      </Card>
    </div>
  );
}
