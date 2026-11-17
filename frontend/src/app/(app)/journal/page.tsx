"use client";

import * as React from "react";
import useSWR from "swr";
import { toast } from "sonner";
import { Bot, Download, Pencil, Plus, Trash2, XCircle } from "lucide-react";
import { CartesianGrid, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { useAuth } from "@/components/auth/auth-provider";
import { PageHeader } from "@/components/layout/page-header";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { EmptyState, ErrorNotice, Notice, Skeleton, Spinner } from "@/components/ui/feedback";
import { Select } from "@/components/ui/input";
import { Stat } from "@/components/ui/stat";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { api, errorMessage, fetcher } from "@/lib/api";
import { AXIS_TICK, GRID, SERIES, TOOLTIP_LABEL, TOOLTIP_STYLE } from "@/lib/chart-theme";
import { changeColor, fmtDateTime, fmtINR, fmtNum, fmtPct, fmtPrice } from "@/lib/utils";
import { CloseDialog, TradeDialog, type JournalTrade } from "./trade-dialog";

interface Block {
  trades: number;
  net_pnl: number;
  win_rate: number | null;
  avg_win: number | null;
  avg_loss: number | null;
  payoff_ratio: number | null;
  profit_factor: number | null;
  expectancy: number | null;
  avg_r: number | null;
}
interface Stats extends Block {
  open_trades: number;
  largest_win: number | null;
  largest_loss: number | null;
  max_drawdown: number | null;
  max_consecutive_wins: number;
  max_consecutive_losses: number;
  avg_holding_days: number | null;
  avg_holding_days_winners: number | null;
  avg_holding_days_losers: number | null;
  total_costs: number;
  equity_curve: { date: string; symbol: string; pnl: number; cumulative: number }[];
  by_strategy: (Block & { strategy: string })[];
  by_month: (Block & { month: string })[];
  by_emotion: (Block & { emotion: string })[];
  by_direction: (Block & { direction: string })[];
}
interface Finding {
  code: string;
  severity: "critical" | "warning" | "info";
  title: string;
  detail: string;
  trade_ids: number[];
}
interface CoachReport {
  id: number;
  model: string;
  created_at: string;
  content: {
    summary: string;
    strengths: string[];
    weaknesses: string[];
    behavioural_patterns: { pattern: string; evidence: string; suggestion: string }[];
    strategy_review: { strategy: string; assessment: string }[];
    rules_to_follow: string[];
    sample_size_note: string;
    disclaimer: string;
  };
}

function BlockTable<T extends Block>({ rows, keyOf, label }: { rows: T[]; keyOf: (r: T) => string; label: string }) {
  if (!rows.length) return <EmptyState title="No closed trades" />;
  return (
    <div className="overflow-x-auto">
      <Table>
        <THead>
          <TR>
            <TH>{label}</TH>
            <TH className="text-right">Trades</TH>
            <TH className="text-right">Net P&amp;L</TH>
            <TH className="text-right">Win rate</TH>
            <TH className="text-right">Profit factor</TH>
            <TH className="text-right">Expectancy</TH>
            <TH className="text-right">Avg R</TH>
          </TR>
        </THead>
        <TBody>
          {rows.map((r) => (
            <TR key={keyOf(r)}>
              <TD className="whitespace-nowrap font-medium">{keyOf(r)}</TD>
              <TD className="num text-right">{r.trades}</TD>
              <TD className={`num text-right ${changeColor(r.net_pnl)}`}>{fmtINR(r.net_pnl)}</TD>
              <TD className="num text-right">{r.win_rate != null ? `${fmtNum(r.win_rate * 100, 0)}%` : "—"}</TD>
              <TD className="num text-right">{r.profit_factor != null ? fmtNum(r.profit_factor, 2) : "—"}</TD>
              <TD className={`num text-right ${changeColor(r.expectancy)}`}>{r.expectancy != null ? fmtINR(r.expectancy) : "—"}</TD>
              <TD className="num text-right">{r.avg_r != null ? `${fmtNum(r.avg_r, 2)}R` : "—"}</TD>
            </TR>
          ))}
        </TBody>
      </Table>
    </div>
  );
}

function CoachCard({ canWrite, closedTrades }: { canWrite: boolean; closedTrades: number }) {
  const { data: status } = useSWR<{ configured: boolean; enabled: boolean }>("/ai/status", fetcher);
  const { data: report, mutate } = useSWR<CoachReport | null>("/journal/coach", fetcher);
  const [busy, setBusy] = React.useState(false);
  async function run() {
    setBusy(true);
    try {
      await mutate(await api.post<CoachReport>("/journal/coach"), { revalidate: false });
      toast.success("Coaching review ready");
    } catch (err) {
      toast.error(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }
  const can = canWrite && status?.configured && status.enabled && closedTrades >= 5;
  const c = report?.content;
  return (
    <Card>
      <CardHeader>
        <div>
          <CardTitle>AI journal coach</CardTitle>
          <CardDescription className="mt-0.5">Claude reviews your statistics, the rule-based findings and recent trades for behavioural patterns. It sees only your journal.</CardDescription>
        </div>
        {canWrite ? (
          <Button size="sm" onClick={run} disabled={!can || busy}>
            {busy ? <Spinner className="text-white" /> : <Bot />} {report ? "Run new review" : "Review my trading"}
          </Button>
        ) : null}
      </CardHeader>
      <CardContent className="space-y-3 text-sm">
        {status && !status.configured ? <Notice tone="warn">AI is not configured on this server (ANTHROPIC_API_KEY). The rule-based findings above still work.</Notice> : null}
        {closedTrades < 5 ? <Notice>The coach needs at least 5 closed trades.</Notice> : null}
        {busy ? <p className="flex items-center gap-2 text-xs text-muted"><Spinner /> Reviewing your journal…</p> : null}
        {c ? (
          <div className="space-y-3">
            <p>{c.summary}</p>
            <div className="grid gap-3 md:grid-cols-2">
              <div>
                <p className="mb-1 text-xs font-semibold text-up">Strengths</p>
                <ul className="list-disc space-y-1 pl-4 text-xs">{c.strengths.map((s) => <li key={s}>{s}</li>)}</ul>
              </div>
              <div>
                <p className="mb-1 text-xs font-semibold text-down">Weaknesses</p>
                <ul className="list-disc space-y-1 pl-4 text-xs">{c.weaknesses.map((s) => <li key={s}>{s}</li>)}</ul>
              </div>
            </div>
            {c.behavioural_patterns.length ? (
              <div className="space-y-2">
                <p className="text-xs font-semibold">Behavioural patterns</p>
                {c.behavioural_patterns.map((p) => (
                  <div key={p.pattern} className="rounded-md border border-border p-2 text-xs">
                    <p className="font-medium">{p.pattern}</p>
                    <p className="text-muted">Evidence: {p.evidence}</p>
                    <p className="mt-0.5">Try: {p.suggestion}</p>
                  </div>
                ))}
              </div>
            ) : null}
            {c.strategy_review.length ? (
              <div>
                <p className="mb-1 text-xs font-semibold">Strategy review</p>
                <ul className="space-y-1 text-xs">{c.strategy_review.map((s) => <li key={s.strategy}><span className="font-medium">{s.strategy}:</span> {s.assessment}</li>)}</ul>
              </div>
            ) : null}
            {c.rules_to_follow.length ? (
              <div>
                <p className="mb-1 text-xs font-semibold">Rules to follow</p>
                <ol className="list-decimal space-y-1 pl-4 text-xs">{c.rules_to_follow.map((r) => <li key={r}>{r}</li>)}</ol>
              </div>
            ) : null}
            <p className="text-[11px] text-muted">{c.sample_size_note} · {c.disclaimer} · {report?.model}, {fmtDateTime(report?.created_at)}</p>
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}

export default function JournalPage() {
  const { canWrite } = useAuth();
  const [status, setStatus] = React.useState<"" | "OPEN" | "CLOSED">("");
  const [strategy, setStrategy] = React.useState("");
  const qs = new URLSearchParams({ ...(status ? { status } : {}), ...(strategy ? { strategy } : {}) }).toString();
  const { data: trades, error, mutate } = useSWR<JournalTrade[]>(`/journal/trades${qs ? `?${qs}` : ""}`, fetcher, { refreshInterval: 30_000 });
  const statsKey = `/journal/stats${strategy ? `?strategy=${encodeURIComponent(strategy)}` : ""}`;
  const { data: stats, mutate: mutateStats } = useSWR<{ statistics: Stats; findings: Finding[]; methodology: string }>(statsKey, fetcher);
  const { data: meta, mutate: mutateMeta } = useSWR<{ strategies: string[]; tags: string[]; emotions: string[] }>("/journal/meta", fetcher);
  const [dialog, setDialog] = React.useState<{ key: number; open: boolean; trade: JournalTrade | null }>({ key: 0, open: false, trade: null });
  const [closing, setClosing] = React.useState<JournalTrade | null>(null);

  const refresh = () => {
    void mutate();
    void mutateStats();
    void mutateMeta();
  };
  async function remove(t: JournalTrade) {
    if (!window.confirm(`Delete the ${t.symbol} trade from ${fmtDateTime(t.entry_at)}?`)) return;
    try {
      await api.del(`/journal/trades/${t.id}`);
      refresh();
    } catch (err) {
      toast.error(errorMessage(err));
    }
  }
  const s = stats?.statistics;

  return (
    <>
      <PageHeader
        title="Trading Journal"
        description="Log trades with plan, execution and emotions. Statistics, discipline checks and an AI coach turn the log into feedback."
        actions={
          <div className="flex gap-2">
            <Button asChild size="sm" variant="secondary">
              <a href="/api/v1/journal/export" download><Download /> Export</a>
            </Button>
            {canWrite ? <Button size="sm" onClick={() => setDialog((d) => ({ key: d.key + 1, open: true, trade: null }))}><Plus /> Log trade</Button> : null}
          </div>
        }
      />
      <div className="space-y-4">
        {s ? (
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-4 xl:grid-cols-8">
            <Stat label="Net P&L" value={fmtINR(s.net_pnl)} valueClassName={changeColor(s.net_pnl)} sub={`${s.trades} closed · ${s.open_trades} open`} />
            <Stat label="Win rate" value={s.win_rate != null ? `${fmtNum(s.win_rate * 100, 1)}%` : "—"} />
            <Stat label="Profit factor" value={s.profit_factor != null ? fmtNum(s.profit_factor, 2) : "—"} />
            <Stat label="Expectancy / trade" value={s.expectancy != null ? fmtINR(s.expectancy) : "—"} valueClassName={changeColor(s.expectancy)} />
            <Stat label="Avg win / loss" value={s.payoff_ratio != null ? fmtNum(s.payoff_ratio, 2) : "—"} sub={`${fmtINR(s.avg_win)} / ${fmtINR(s.avg_loss)}`} />
            <Stat label="Avg R-multiple" value={s.avg_r != null ? `${fmtNum(s.avg_r, 2)}R` : "—"} />
            <Stat label="Max drawdown" value={s.max_drawdown != null ? fmtINR(s.max_drawdown) : "—"} valueClassName="text-down" />
            <Stat label="Streaks (W / L)" value={`${s.max_consecutive_wins} / ${s.max_consecutive_losses}`} sub={s.avg_holding_days != null ? `avg hold ${fmtNum(s.avg_holding_days, 1)}d` : undefined} />
          </div>
        ) : <Skeleton className="h-16" />}

        <Tabs defaultValue="trades">
          <TabsList>
            <TabsTrigger value="trades">Trades</TabsTrigger>
            <TabsTrigger value="analysis">Analysis</TabsTrigger>
            <TabsTrigger value="coach">Discipline &amp; coach</TabsTrigger>
          </TabsList>
          <TabsContent value="trades">
            <Card>
              <CardHeader>
                <CardTitle>Trades</CardTitle>
                <div className="flex gap-2">
                  <Select aria-label="Status filter" className="h-8 w-32" value={status} onChange={(e) => setStatus(e.target.value as typeof status)}>
                    <option value="">All</option>
                    <option value="OPEN">Open</option>
                    <option value="CLOSED">Closed</option>
                  </Select>
                  <Select aria-label="Strategy filter" className="h-8 w-40" value={strategy} onChange={(e) => setStrategy(e.target.value)}>
                    <option value="">All strategies</option>
                    {meta?.strategies.map((x) => <option key={x} value={x}>{x}</option>)}
                  </Select>
                </div>
              </CardHeader>
              <CardContent>
                <ErrorNotice error={error} />
                {!trades && !error ? <Skeleton className="h-40" /> : null}
                {trades && !trades.length ? <EmptyState title="No trades logged" description="Log your first trade to start building statistics." /> : null}
                {trades?.length ? (
                  <div className="overflow-x-auto">
                    <Table>
                      <THead>
                        <TR>
                          <TH>Entry</TH>
                          <TH>Symbol</TH>
                          <TH>Side</TH>
                          <TH>Strategy</TH>
                          <TH className="text-right">Qty</TH>
                          <TH className="text-right">Entry</TH>
                          <TH className="text-right">Exit</TH>
                          <TH className="text-right">Net P&amp;L</TH>
                          <TH className="text-right">R</TH>
                          <TH>Emotion</TH>
                          {canWrite ? <TH /> : null}
                        </TR>
                      </THead>
                      <TBody>
                        {trades.map((t) => (
                          <TR key={t.id}>
                            <TD className="whitespace-nowrap text-xs">{fmtDateTime(t.entry_at)}</TD>
                            <TD className="font-medium">{t.symbol}</TD>
                            <TD><Badge variant={t.direction === "LONG" ? "up" : "down"}>{t.direction}</Badge></TD>
                            <TD className="text-muted">{t.strategy || "—"}</TD>
                            <TD className="num text-right">{fmtNum(t.quantity, 0)}</TD>
                            <TD className="num text-right">{fmtPrice(t.entry_price)}</TD>
                            <TD className="num text-right">{t.exit_price != null ? fmtPrice(t.exit_price) : <Badge variant="info">open</Badge>}</TD>
                            <TD className={`num text-right ${changeColor(t.net_pnl ?? t.unrealized_pnl)}`}>
                              {t.net_pnl != null ? fmtINR(t.net_pnl) : t.unrealized_pnl != null ? <span title={`Unrealised at ${fmtPrice(t.current_price)} (${t.price_status})`}>{fmtINR(t.unrealized_pnl)}*</span> : "—"}
                              {t.return_pct != null ? <span className="block text-[11px]">{fmtPct(t.return_pct)}</span> : null}
                            </TD>
                            <TD className="num text-right">{t.r_multiple != null ? `${fmtNum(t.r_multiple, 2)}R` : "—"}</TD>
                            <TD className="text-xs text-muted">{t.emotion || "—"}</TD>
                            {canWrite ? (
                              <TD className="whitespace-nowrap text-right">
                                {t.status === "OPEN" ? (
                                  <button type="button" className="rounded p-1 text-muted hover:text-foreground" aria-label={`Close trade ${t.id}`} title="Close trade" onClick={() => setClosing(t)}>
                                    <XCircle className="size-3.5" />
                                  </button>
                                ) : null}
                                <button type="button" className="rounded p-1 text-muted hover:text-foreground" aria-label={`Edit trade ${t.id}`} onClick={() => setDialog((d) => ({ key: d.key + 1, open: true, trade: t }))}>
                                  <Pencil className="size-3.5" />
                                </button>
                                <button type="button" className="rounded p-1 text-muted hover:text-down" aria-label={`Delete trade ${t.id}`} onClick={() => remove(t)}>
                                  <Trash2 className="size-3.5" />
                                </button>
                              </TD>
                            ) : null}
                          </TR>
                        ))}
                      </TBody>
                    </Table>
                    <p className="mt-2 text-[11px] text-muted">* Open trades show unrealised P&amp;L at the latest labelled quote.</p>
                  </div>
                ) : null}
              </CardContent>
            </Card>
          </TabsContent>
          <TabsContent value="analysis" className="space-y-4">
            {s ? (
              <>
                <Card>
                  <CardHeader><CardTitle>Cumulative P&amp;L (closed trades)</CardTitle></CardHeader>
                  <CardContent className="h-64" role="img" aria-label="Cumulative net profit and loss by trade exit date">
                    {s.equity_curve.length ? (
                      <ResponsiveContainer width="100%" height="100%">
                        <LineChart data={s.equity_curve} margin={{ top: 4, right: 8, bottom: 0, left: 8 }}>
                          <CartesianGrid stroke={GRID} vertical={false} />
                          <XAxis dataKey="date" tick={AXIS_TICK} minTickGap={40} />
                          <YAxis tick={AXIS_TICK} width={64} tickFormatter={(v) => fmtNum(Number(v), 0)} />
                          <ReferenceLine y={0} stroke="#383835" />
                          <Tooltip contentStyle={TOOLTIP_STYLE} labelStyle={TOOLTIP_LABEL} formatter={(v, _n, item) => [fmtINR(Number(v)), `Cumulative (last: ${(item?.payload as { symbol?: string })?.symbol ?? ""} ${fmtINR((item?.payload as { pnl?: number })?.pnl)})`]} />
                          <Line dataKey="cumulative" stroke={SERIES[0]} strokeWidth={2} dot={s.equity_curve.length < 60 ? { r: 3 } : false} />
                        </LineChart>
                      </ResponsiveContainer>
                    ) : <EmptyState title="No closed trades yet" />}
                  </CardContent>
                </Card>
                <div className="grid gap-4 lg:grid-cols-2">
                  <Card><CardHeader><CardTitle>By strategy</CardTitle></CardHeader><CardContent><BlockTable rows={s.by_strategy} keyOf={(r) => r.strategy} label="Strategy" /></CardContent></Card>
                  <Card><CardHeader><CardTitle>By month</CardTitle></CardHeader><CardContent><BlockTable rows={s.by_month} keyOf={(r) => r.month} label="Month" /></CardContent></Card>
                  <Card><CardHeader><CardTitle>By emotion</CardTitle></CardHeader><CardContent><BlockTable rows={s.by_emotion} keyOf={(r) => r.emotion} label="Emotion" /></CardContent></Card>
                  <Card><CardHeader><CardTitle>By direction</CardTitle></CardHeader><CardContent><BlockTable rows={s.by_direction} keyOf={(r) => r.direction} label="Direction" /></CardContent></Card>
                </div>
                <p className="text-xs text-muted">{stats?.methodology}</p>
              </>
            ) : <Skeleton className="h-64" />}
          </TabsContent>
          <TabsContent value="coach" className="space-y-4">
            <Card>
              <CardHeader>
                <div>
                  <CardTitle>Discipline checks</CardTitle>
                  <CardDescription className="mt-0.5">Deterministic rules over your trades: missing stops, stops not respected, holding losers, revenge trades, overtrading, sizing and emotions.</CardDescription>
                </div>
              </CardHeader>
              <CardContent className="space-y-2">
                {stats && !stats.findings.length ? <EmptyState title="No issues detected" description="Keep logging trades: checks need enough history to be meaningful." /> : null}
                {stats?.findings.map((f) => (
                  <div key={f.code} className="rounded-md border border-border p-2 text-xs">
                    <div className="flex items-center gap-2">
                      <Badge variant={f.severity === "critical" ? "down" : f.severity === "warning" ? "warn" : "info"}>{f.severity}</Badge>
                      <span className="font-medium">{f.title}</span>
                    </div>
                    <p className="mt-1 text-muted">{f.detail}</p>
                  </div>
                ))}
              </CardContent>
            </Card>
            <CoachCard canWrite={canWrite} closedTrades={s?.trades ?? 0} />
          </TabsContent>
        </Tabs>
      </div>
      <TradeDialog key={dialog.key} trade={dialog.trade} open={dialog.open} onOpenChange={(o) => setDialog((d) => ({ ...d, open: o }))} onSaved={refresh} strategies={meta?.strategies ?? []} emotions={meta?.emotions ?? []} />
      {closing ? <CloseDialog key={closing.id} trade={closing} open onOpenChange={(o) => !o && setClosing(null)} onSaved={refresh} /> : null}
    </>
  );
}
