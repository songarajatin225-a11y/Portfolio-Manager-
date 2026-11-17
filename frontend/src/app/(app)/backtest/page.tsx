"use client";

import * as React from "react";
import useSWR from "swr";
import { toast } from "sonner";
import { Play, Plus, Trash2, X } from "lucide-react";
import { useAuth } from "@/components/auth/auth-provider";
import { PageHeader } from "@/components/layout/page-header";
import { InstrumentSearch } from "@/components/market/instrument-search";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { EmptyState, ErrorNotice, Skeleton, Spinner } from "@/components/ui/feedback";
import { Field, Input, Select } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { api, errorMessage, fetcher } from "@/lib/api";
import { changeColor, cn, fmtDateTime, fmtPct } from "@/lib/utils";
import { BacktestResults, type BacktestResult } from "./results";

type ParamValue = number | string | boolean | Rule[];
interface Rule {
  left: string;
  op: string;
  right: string;
}
interface StrategyDef {
  label: string;
  description: string;
  params: Record<string, ParamValue>;
}
interface Meta {
  strategies: Record<string, StrategyDef>;
  operators: string[];
  series_help: string;
  default_costs: { brokerage_pct: number; brokerage_cap: number | null; statutory_pct: number; slippage_bps: number };
  max_instruments: number;
  max_years: number;
}
interface RunBrief {
  id: number;
  name: string;
  strategy: string;
  created_at: string;
  total_return_pct: number | null;
  max_drawdown_pct: number | null;
  trades: number | null;
  data_status: string | null;
}
interface RunFull extends RunBrief {
  result: BacktestResult;
}

const PARAM_LABEL: Record<string, string> = {
  fast: "Fast period",
  slow: "Slow period",
  ma_type: "Average type",
  period: "RSI period",
  oversold: "Oversold level",
  overbought: "Overbought level",
  signal: "Signal period",
  require_above_zero: "Only when MACD > 0",
  entry_lookback: "Entry lookback (bars)",
  exit_lookback: "Exit lookback (bars)",
  volume_confirm: "Volume ≥ × 20-day avg (0 = off)",
  lookback: "Momentum lookback (bars)",
  skip: "Skip recent bars",
  top_n: "Hold top N",
  require_positive: "Only positive momentum",
};

const iso = (d: Date) => d.toISOString().slice(0, 10);

function RulesEditor({ title, rules, logic, onChange, onLogic, operators }: { title: string; rules: Rule[]; logic: string; onChange: (r: Rule[]) => void; onLogic: (l: string) => void; operators: string[] }) {
  return (
    <div className="col-span-full space-y-2 rounded-md border border-border p-2">
      <div className="flex items-center justify-between">
        <span className="text-xs font-medium">{title}</span>
        <Select aria-label={`${title} logic`} className="h-7 w-44 text-xs" value={logic} onChange={(e) => onLogic(e.target.value)}>
          <option value="all">All conditions (AND)</option>
          <option value="any">Any condition (OR)</option>
        </Select>
      </div>
      {rules.map((r, i) => (
        <div key={i} className="flex items-center gap-1.5">
          <Input aria-label="Left series" className="h-8" placeholder="e.g. rsi_14" value={r.left} onChange={(e) => onChange(rules.map((x, j) => (j === i ? { ...x, left: e.target.value } : x)))} />
          <Select aria-label="Operator" className="h-8 w-36" value={r.op} onChange={(e) => onChange(rules.map((x, j) => (j === i ? { ...x, op: e.target.value } : x)))}>
            {operators.map((o) => <option key={o} value={o}>{o.replace("_", " ")}</option>)}
          </Select>
          <Input aria-label="Right value or series" className="h-8" placeholder="30 or sma_200" value={r.right} onChange={(e) => onChange(rules.map((x, j) => (j === i ? { ...x, right: e.target.value } : x)))} />
          <button type="button" className="rounded p-1 text-muted hover:text-down" aria-label="Remove condition" onClick={() => onChange(rules.filter((_, j) => j !== i))}>
            <X className="size-4" />
          </button>
        </div>
      ))}
      <Button type="button" size="xs" variant="ghost" onClick={() => onChange([...rules, { left: "close", op: ">", right: "sma_50" }])}>
        <Plus /> Add condition
      </Button>
    </div>
  );
}

function ConfigForm({ meta, onDone, canWrite }: { meta: Meta; onDone: (r: RunFull) => void; canWrite: boolean }) {
  const [strategy, setStrategy] = React.useState("ma_crossover");
  const [params, setParams] = React.useState<Record<string, ParamValue>>(meta.strategies.ma_crossover.params);
  const [entryRules, setEntryRules] = React.useState<Rule[]>([{ left: "rsi_14", op: "crosses_above", right: "30" }]);
  const [exitRules, setExitRules] = React.useState<Rule[]>([{ left: "rsi_14", op: ">", right: "70" }]);
  const [universeType, setUniverseType] = React.useState<"symbols" | "index">("symbols");
  const [symbols, setSymbols] = React.useState<string[]>([]);
  const [indexCode, setIndexCode] = React.useState("NIFTY50");
  const [f, setF] = React.useState(() => {
    const end = new Date(Date.now() - 86_400_000);
    return {
      today: iso(new Date()),
      name: "",
      start: iso(new Date(end.getTime() - 3 * 365 * 86_400_000)),
      end: iso(end),
      initial_capital: "1000000",
      sizing_method: "percent_equity",
      sizing_value: "20",
      max_positions: "5",
      stop_loss_pct: "",
      take_profit_pct: "",
      trailing_stop_pct: "",
      brokerage_pct: String(meta.default_costs.brokerage_pct),
      brokerage_cap: meta.default_costs.brokerage_cap != null ? String(meta.default_costs.brokerage_cap) : "",
      statutory_pct: String(meta.default_costs.statutory_pct),
      slippage_bps: String(meta.default_costs.slippage_bps),
      risk_free_rate_pct: "6.5",
    };
  });
  const [busy, setBusy] = React.useState(false);
  const set = (k: keyof typeof f) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => setF((x) => ({ ...x, [k]: e.target.value }));
  const opt = (v: string) => (v === "" ? null : Number(v));

  function pickStrategy(s: string) {
    setStrategy(s);
    setParams(meta.strategies[s].params);
    if (s === "momentum") setUniverseType("index");
  }

  async function run(e: React.FormEvent) {
    e.preventDefault();
    const finalParams: Record<string, unknown> = { ...params };
    if (strategy === "custom") {
      const conv = (r: Rule) => ({ left: r.left.trim(), op: r.op, right: r.right.trim() });
      finalParams.entry = entryRules.map(conv);
      finalParams.exit = exitRules.map(conv);
    }
    setBusy(true);
    try {
      const res = await api.post<RunFull>("/backtests", {
        name: f.name,
        strategy,
        params: finalParams,
        universe: universeType === "symbols" ? { type: "symbols", symbols } : { type: "index", index_code: indexCode },
        start: f.start,
        end: f.end,
        initial_capital: Number(f.initial_capital),
        sizing_method: f.sizing_method,
        sizing_value: Number(f.sizing_value),
        max_positions: Number(f.max_positions),
        stop_loss_pct: opt(f.stop_loss_pct),
        take_profit_pct: opt(f.take_profit_pct),
        trailing_stop_pct: opt(f.trailing_stop_pct),
        costs: { brokerage_pct: Number(f.brokerage_pct), brokerage_cap: opt(f.brokerage_cap), statutory_pct: Number(f.statutory_pct), slippage_bps: Number(f.slippage_bps) },
        risk_free_rate_pct: Number(f.risk_free_rate_pct),
      });
      onDone(res);
      toast.success("Backtest complete");
    } catch (err) {
      toast.error(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  const def = meta.strategies[strategy];
  return (
    <Card>
      <CardHeader>
        <div>
          <CardTitle>Strategy</CardTitle>
          <CardDescription className="mt-0.5">{def.description}</CardDescription>
        </div>
      </CardHeader>
      <CardContent>
        <form onSubmit={run} className="grid grid-cols-2 gap-3 md:grid-cols-4">
          <Field label="Strategy" className="col-span-2">
            <Select value={strategy} onChange={(e) => pickStrategy(e.target.value)}>
              {Object.entries(meta.strategies).map(([k, v]) => <option key={k} value={k}>{v.label}</option>)}
            </Select>
          </Field>
          <Field label="Name (optional)" className="col-span-2"><Input maxLength={150} value={f.name} onChange={set("name")} /></Field>
          {Object.entries(params).filter(([k]) => !["entry", "exit", "entry_logic", "exit_logic"].includes(k)).map(([k, v]) =>
            typeof v === "boolean" ? (
              <label key={k} className="flex items-center gap-2 self-end pb-2 text-xs">
                <Switch checked={v} onCheckedChange={(c) => setParams((p) => ({ ...p, [k]: c }))} aria-label={PARAM_LABEL[k] ?? k} />
                {PARAM_LABEL[k] ?? k}
              </label>
            ) : k === "ma_type" ? (
              <Field key={k} label={PARAM_LABEL[k]}>
                <Select value={String(v)} onChange={(e) => setParams((p) => ({ ...p, [k]: e.target.value }))}>
                  <option value="ema">EMA</option>
                  <option value="sma">SMA</option>
                </Select>
              </Field>
            ) : (
              <Field key={k} label={PARAM_LABEL[k] ?? k}>
                <Input type="number" step="any" value={String(v)} onChange={(e) => setParams((p) => ({ ...p, [k]: Number(e.target.value) }))} />
              </Field>
            ),
          )}
          {strategy === "custom" ? (
            <>
              <RulesEditor title="Entry conditions" rules={entryRules} logic={String(params.entry_logic ?? "all")} onChange={setEntryRules} onLogic={(l) => setParams((p) => ({ ...p, entry_logic: l }))} operators={meta.operators} />
              <RulesEditor title="Exit conditions" rules={exitRules} logic={String(params.exit_logic ?? "any")} onChange={setExitRules} onLogic={(l) => setParams((p) => ({ ...p, exit_logic: l }))} operators={meta.operators} />
              <p className="col-span-full text-[11px] text-subtle">Series: {meta.series_help}</p>
            </>
          ) : null}

          <div className="col-span-full border-t border-border pt-3 text-xs font-medium">Universe &amp; period</div>
          <Field label="Universe">
            <Select value={universeType} onChange={(e) => setUniverseType(e.target.value as "symbols" | "index")}>
              <option value="symbols">Selected symbols</option>
              <option value="index">Index members (point-in-time)</option>
            </Select>
          </Field>
          {universeType === "index" ? (
            <Field label="Index">
              <Select value={indexCode} onChange={(e) => setIndexCode(e.target.value)}>
                <option value="NIFTY50">NIFTY 50</option>
                <option value="BANKNIFTY">BANK NIFTY</option>
                <option value="NIFTYNEXT50">NIFTY NEXT 50</option>
              </Select>
            </Field>
          ) : (
            <div className="col-span-1 md:col-span-3">
              <span className="mb-1 block text-xs font-medium text-muted">Symbols ({symbols.length}/{meta.max_instruments})</span>
              <InstrumentSearch segment="EQ" placeholder="Add a symbol…" onSelect={(i) => setSymbols((s) => (s.includes(i.symbol) || s.length >= meta.max_instruments ? s : [...s, i.symbol]))} />
              <div className="mt-1.5 flex flex-wrap gap-1">
                {symbols.map((s) => (
                  <Badge key={s} variant="accent" className="normal-case">
                    {s}
                    <button type="button" aria-label={`Remove ${s}`} onClick={() => setSymbols((x) => x.filter((y) => y !== s))}><X className="size-3" /></button>
                  </Badge>
                ))}
              </div>
            </div>
          )}
          <Field label="Start"><Input type="date" required value={f.start} onChange={set("start")} /></Field>
          <Field label="End"><Input type="date" required value={f.end} max={f.today} onChange={set("end")} /></Field>

          <div className="col-span-full border-t border-border pt-3 text-xs font-medium">Capital, sizing &amp; exits</div>
          <Field label="Initial capital (₹)"><Input type="number" min="1" step="any" value={f.initial_capital} onChange={set("initial_capital")} /></Field>
          <Field label="Sizing">
            <Select value={f.sizing_method} onChange={set("sizing_method")}>
              <option value="percent_equity">% of equity per trade</option>
              <option value="fixed_amount">Fixed ₹ per trade</option>
              <option value="risk_percent">% of equity at risk (needs stop)</option>
            </Select>
          </Field>
          <Field label={f.sizing_method === "fixed_amount" ? "Amount (₹)" : "Percent"}><Input type="number" min="0" step="any" value={f.sizing_value} onChange={set("sizing_value")} /></Field>
          <Field label="Max open positions"><Input type="number" min="1" max="50" value={f.max_positions} onChange={set("max_positions")} disabled={strategy === "momentum"} /></Field>
          <Field label="Stop-loss %"><Input type="number" min="0" step="any" placeholder="off" value={f.stop_loss_pct} onChange={set("stop_loss_pct")} /></Field>
          <Field label="Take-profit %"><Input type="number" min="0" step="any" placeholder="off" value={f.take_profit_pct} onChange={set("take_profit_pct")} /></Field>
          <Field label="Trailing stop %"><Input type="number" min="0" step="any" placeholder="off" value={f.trailing_stop_pct} onChange={set("trailing_stop_pct")} /></Field>
          <Field label="Risk-free rate %"><Input type="number" min="0" step="any" value={f.risk_free_rate_pct} onChange={set("risk_free_rate_pct")} /></Field>

          <div className="col-span-full border-t border-border pt-3 text-xs font-medium">Costs</div>
          <Field label="Brokerage % per order"><Input type="number" min="0" step="any" value={f.brokerage_pct} onChange={set("brokerage_pct")} /></Field>
          <Field label="Brokerage cap ₹ (blank = none)"><Input type="number" min="0" step="any" value={f.brokerage_cap} onChange={set("brokerage_cap")} /></Field>
          <Field label="Statutory charges % per order" hint="STT, exchange, GST, stamp duty"><Input type="number" min="0" step="any" value={f.statutory_pct} onChange={set("statutory_pct")} /></Field>
          <Field label="Slippage (bps)"><Input type="number" min="0" step="any" value={f.slippage_bps} onChange={set("slippage_bps")} /></Field>

          <div className="col-span-full flex justify-end">
            {canWrite ? (
              <Button type="submit" disabled={busy || (universeType === "symbols" && !symbols.length)}>
                {busy ? <Spinner className="text-white" /> : <Play />} Run backtest
              </Button>
            ) : <span className="text-xs text-muted">Viewers cannot run backtests.</span>}
          </div>
        </form>
      </CardContent>
    </Card>
  );
}

export default function BacktestPage() {
  const { canWrite } = useAuth();
  const { data: meta, error } = useSWR<Meta>("/backtests/strategies", fetcher);
  const { data: runs, mutate } = useSWR<RunBrief[]>("/backtests", fetcher);
  const [selected, setSelected] = React.useState<number | null>(null);
  const { data: run } = useSWR<RunFull>(selected ? `/backtests/${selected}` : null, fetcher);

  async function remove(id: number) {
    if (!window.confirm("Delete this backtest?")) return;
    try {
      await api.del(`/backtests/${id}`);
      if (selected === id) setSelected(null);
      void mutate();
    } catch (err) {
      toast.error(errorMessage(err));
    }
  }

  return (
    <>
      <PageHeader title="Strategy Backtesting" description="Test rule-based strategies on daily history with next-open execution, stops, position sizing, brokerage, statutory charges and slippage." />
      <ErrorNotice error={error} />
      {!meta && !error ? <Skeleton className="h-96" /> : null}
      {meta ? (
        <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_260px]">
          <div className="space-y-4">
            <ConfigForm meta={meta} canWrite={canWrite} onDone={(r) => { void mutate(); setSelected(r.id); }} />
            {run ? <BacktestResults r={run.result} name={run.name} /> : selected ? <Skeleton className="h-96" /> : null}
          </div>
          <Card className="h-fit">
            <CardHeader><CardTitle>Saved runs</CardTitle></CardHeader>
            <CardContent className="space-y-0.5 p-2">
              {runs && !runs.length ? <EmptyState title="No runs yet" /> : null}
              {runs?.map((r) => (
                <div key={r.id} className={cn("group rounded px-2 py-1.5 text-xs", r.id === selected ? "bg-surface-3" : "hover:bg-surface-2")}>
                  <div className="flex items-start justify-between gap-1">
                    <button type="button" className="flex-1 text-left font-medium" onClick={() => setSelected(r.id)}>{r.name}</button>
                    {canWrite ? (
                      <button type="button" className="hidden rounded p-0.5 text-muted hover:text-down group-hover:block" aria-label={`Delete ${r.name}`} onClick={() => remove(r.id)}>
                        <Trash2 className="size-3.5" />
                      </button>
                    ) : null}
                  </div>
                  <button type="button" className="flex w-full justify-between text-left text-[11px] text-muted" onClick={() => setSelected(r.id)}>
                    <span>{fmtDateTime(r.created_at)}</span>
                    <span className={changeColor(r.total_return_pct)}>{fmtPct(r.total_return_pct, 1)}</span>
                  </button>
                  {r.data_status === "SIMULATED" ? <Badge variant="simulated" className="mt-0.5">simulated</Badge> : null}
                </div>
              ))}
            </CardContent>
          </Card>
        </div>
      ) : null}
    </>
  );
}
