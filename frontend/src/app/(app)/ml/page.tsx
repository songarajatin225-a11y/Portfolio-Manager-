"use client";

import * as React from "react";
import Link from "next/link";
import useSWR from "swr";
import { toast } from "sonner";
import { CartesianGrid, Line, LineChart, ReferenceLine, ResponsiveContainer, Scatter, ScatterChart, Tooltip, XAxis, YAxis, ZAxis } from "recharts";
import { BrainCircuit, Play } from "lucide-react";
import { useAuth } from "@/components/auth/auth-provider";
import { PageHeader } from "@/components/layout/page-header";
import { DataBadge } from "@/components/market/data-badge";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { EmptyState, ErrorNotice, Notice, Skeleton, Spinner } from "@/components/ui/feedback";
import { Select } from "@/components/ui/input";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { api, errorMessage, fetcher } from "@/lib/api";
import type { DataStatus } from "@/lib/types";
import { cn, fmtDate, fmtDateTime, fmtNum } from "@/lib/utils";

interface Prediction {
  symbol: string;
  name: string;
  sector: string | null;
  as_of: string;
  horizon_days: number;
  prob_up: number | null;
  prob_neutral: number | null;
  prob_down: number | null;
  trend_class: string | null;
  confidence: number | null;
  predicted_vol: number | null;
  anomaly_score: number | null;
  regime: string | null;
  momentum_score: number | null;
  risk_score: number | null;
  model: string;
}
interface Model {
  id: number;
  name: string;
  task: string;
  algorithm: string;
  version: number;
  status: string;
  horizon_days: number;
  train_start: string | null;
  train_end: string | null;
  test_start: string | null;
  test_end: string | null;
  metrics: Record<string, unknown> & { test?: Record<string, number | null>; baseline?: Record<string, number>; walk_forward?: Record<string, { mean_f1_macro: number }> };
  calibration: { curve?: { mean_predicted: number; observed: number; count: number }[]; ece?: number };
  notes: string;
  created_at: string;
}
interface Accuracy {
  evaluated: number;
  hit_rate: number | null;
  directional_hit_rate: number | null;
  weekly: { week: string; n: number; hit_rate: number }[];
}
interface Drift {
  models: { name: string; version: number; overall: string; features: { feature: string; psi: number | null; status: string }[]; prediction_drift?: { psi: number; status: string } }[];
}

const TREND_VARIANT = { bullish: "up", bearish: "down", neutral: "default" } as const;
const STATUS_VARIANT: Record<string, "up" | "down" | "warn" | "default"> = { active: "up", rejected: "down", candidate: "warn", retired: "default" };
const DRIFT_VARIANT: Record<string, "up" | "warn" | "down" | "default"> = { stable: "up", moderate: "warn", significant: "down", unknown: "default" };

function ProbBar({ up, neutral, down }: { up: number; neutral: number; down: number }) {
  return (
    <div className="flex h-2 w-32 overflow-hidden rounded bg-surface-3" title={`Bullish ${(up * 100).toFixed(1)}% · Neutral ${(neutral * 100).toFixed(1)}% · Bearish ${(down * 100).toFixed(1)}%`}>
      <div className="bg-up" style={{ width: `${up * 100}%` }} />
      <div className="bg-subtle" style={{ width: `${neutral * 100}%` }} />
      <div className="bg-down" style={{ width: `${down * 100}%` }} />
    </div>
  );
}

function TrainingControls() {
  const { isAdmin } = useAuth();
  const { data: status, mutate } = useSWR<{ state: string; stage?: string; error?: string; finished_at?: string; results?: { task: string; status: string; reason: string }[] }>("/ml/status", fetcher, {
    refreshInterval: (d) => (d?.state === "running" ? 2000 : 30_000),
  });
  async function train() {
    try {
      await api.post("/ml/train");
      toast.success("Training started — this can take several minutes");
      void mutate();
    } catch (err) {
      toast.error(errorMessage(err));
    }
  }
  return (
    <Card>
      <CardHeader>
        <div>
          <CardTitle>Training</CardTitle>
          <CardDescription className="mt-0.5">Chronological splits with an embargo, walk-forward model selection, calibration on validation, a single test evaluation. New models replace active ones only if they score better on the same test period.</CardDescription>
        </div>
        {isAdmin ? (
          <Button size="sm" onClick={train} disabled={status?.state === "running"}>
            {status?.state === "running" ? <Spinner className="text-white" /> : <Play />} Retrain
          </Button>
        ) : null}
      </CardHeader>
      <CardContent className="space-y-2 text-xs">
        <p>
          Status: <span className="font-medium">{status?.state ?? "idle"}</span>
          {status?.stage && status.state === "running" ? ` — ${status.stage}` : ""}
          {status?.finished_at ? ` · finished ${fmtDateTime(status.finished_at)}` : ""}
        </p>
        {status?.error ? <ErrorNotice error={status.error} /> : null}
        {status?.results?.length ? (
          <ul className="space-y-0.5">
            {status.results.map((r) => (
              <li key={r.task}>
                <Badge variant={STATUS_VARIANT[r.status] ?? "default"}>{r.task}: {r.status}</Badge> <span className="text-muted">{r.reason}</span>
              </li>
            ))}
          </ul>
        ) : null}
      </CardContent>
    </Card>
  );
}

function ModelsCard({ models }: { models: Model[] }) {
  const trend = models.find((m) => m.name === "trend_classifier" && m.status === "active");
  const hasCalibration = Boolean(trend?.calibration?.curve?.length);
  return (
    <div className={cn("grid gap-4", hasCalibration && "xl:grid-cols-[1fr_380px]")}>
      <Card>
        <CardHeader>
          <CardTitle>Model registry</CardTitle>
        </CardHeader>
        <CardContent className="p-0">
          <Table>
            <THead>
              <tr>
                <TH>Model</TH>
                <TH>Status</TH>
                <TH>Algorithm</TH>
                <TH className="text-right">Test metric</TH>
                <TH className="hidden text-right md:table-cell">Baseline</TH>
                <TH className="hidden lg:table-cell">Test period</TH>
                <TH className="hidden xl:table-cell">Decision</TH>
              </tr>
            </THead>
            <TBody>
              {models.map((m) => {
                const t = m.metrics.test ?? {};
                const isClf = m.task === "trend" || m.task === "momentum";
                const isVol = m.task === "volatility";
                return (
                  <TR key={m.id}>
                    <TD className="text-xs">
                      <span className="font-medium">{m.name}</span> <span className="text-subtle">v{m.version}</span>
                    </TD>
                    <TD>
                      <Badge variant={STATUS_VARIANT[m.status] ?? "default"}>{m.status}</Badge>
                    </TD>
                    <TD className="text-xs">{m.algorithm}</TD>
                    <TD className="num text-right text-xs">{isClf ? `F1 ${fmtNum(t.f1_macro as number, 3)} · dir ${fmtNum(t.directional_accuracy as number, 3)}` : isVol ? `MAE ${fmtNum(t.mae as number, 4)}` : "unsupervised"}</TD>
                    <TD className="num hidden text-right text-xs md:table-cell">{isClf ? `F1 ${fmtNum(m.metrics.baseline?.f1_macro, 3)}` : isVol ? `MAE ${fmtNum(t.naive_mae as number, 4)}` : "—"}</TD>
                    <TD className="hidden text-xs text-muted lg:table-cell">{m.test_start ? `${fmtDate(m.test_start)} – ${fmtDate(m.test_end)}` : "—"}</TD>
                    <TD className="hidden max-w-72 truncate text-[11px] text-muted xl:table-cell" title={m.notes}>
                      {m.notes}
                    </TD>
                  </TR>
                );
              })}
            </TBody>
          </Table>
        </CardContent>
      </Card>
      {trend?.calibration?.curve?.length ? (
        <Card>
          <CardHeader>
            <div>
              <CardTitle>Calibration (bullish probability)</CardTitle>
              <CardDescription className="mt-0.5">Predicted vs observed frequency on the test period. ECE {fmtNum(trend.calibration.ece, 3)}.</CardDescription>
            </div>
          </CardHeader>
          <CardContent className="h-64">
            <ResponsiveContainer width="100%" height="100%">
              <ScatterChart margin={{ left: 0, right: 10 }}>
                <CartesianGrid stroke="#222c3a" />
                <XAxis type="number" dataKey="mean_predicted" domain={[0, 1]} tick={{ fill: "#8b98a9", fontSize: 10 }} name="Predicted" />
                <YAxis type="number" dataKey="observed" domain={[0, 1]} tick={{ fill: "#8b98a9", fontSize: 10 }} name="Observed" />
                <ZAxis type="number" dataKey="count" range={[30, 200]} />
                <ReferenceLine segment={[{ x: 0, y: 0 }, { x: 1, y: 1 }]} stroke="#5c6878" strokeDasharray="4 4" />
                <Tooltip contentStyle={{ background: "#131a23", border: "1px solid #2f3c4f", fontSize: 12 }} />
                <Scatter data={trend.calibration.curve} fill="#3b82f6" />
              </ScatterChart>
            </ResponsiveContainer>
          </CardContent>
        </Card>
      ) : null}
    </div>
  );
}

export default function MLPage() {
  const [trend, setTrend] = React.useState("");
  const { data: preds, error, isLoading } = useSWR<{ disclaimer: string; data_status: DataStatus; items: Prediction[] }>(`/ml/predictions${trend ? `?trend=${trend}` : ""}`, fetcher);
  const { data: models } = useSWR<Model[]>("/ml/models", fetcher);
  const { data: acc } = useSWR<Accuracy>("/ml/accuracy", fetcher);
  const { data: drift } = useSWR<Drift>("/ml/drift", fetcher);
  const { data: sectors } = useSWR<{ sector: string; strength_score: number | null; avg_rel_strength: number | null; avg_ml_bullish_prob: number | null; count: number }[]>("/ml/sector-strength", fetcher);
  const regime = preds?.items.find((p) => p.regime)?.regime;

  return (
    <>
      <PageHeader title="ML Predictions" description="Probabilities from validated models trained on historical data. Statistical estimates — not certain forecasts of future prices." />
      <Notice tone="warn" className="mb-4">
        {preds?.disclaimer ?? "Model outputs are statistical probabilities, not forecasts of certainty."} Historical accuracy does not guarantee future performance.
      </Notice>
      <div className="space-y-4">
        <div className="grid gap-4 lg:grid-cols-3">
          <TrainingControls />
          <Card>
            <CardHeader>
              <CardTitle>Realised accuracy</CardTitle>
            </CardHeader>
            <CardContent className="space-y-2 text-xs">
              {acc && acc.evaluated ? (
                <>
                  <p>
                    {acc.evaluated} predictions evaluated · hit rate <span className="num font-medium">{fmtNum((acc.hit_rate ?? 0) * 100, 1)}%</span>
                    {acc.directional_hit_rate != null ? (
                      <>
                        {" "}
                        · directional <span className="num font-medium">{fmtNum(acc.directional_hit_rate * 100, 1)}%</span>
                      </>
                    ) : null}
                  </p>
                  <div className="h-32">
                    <ResponsiveContainer width="100%" height="100%">
                      <LineChart data={acc.weekly.map((w) => ({ ...w, pct: +(w.hit_rate * 100).toFixed(1) }))}>
                        <XAxis dataKey="week" tick={{ fill: "#8b98a9", fontSize: 9 }} />
                        <YAxis domain={[0, 100]} tick={{ fill: "#8b98a9", fontSize: 9 }} unit="%" />
                        <Tooltip contentStyle={{ background: "#131a23", border: "1px solid #2f3c4f", fontSize: 12 }} />
                        <Line dataKey="pct" stroke="#3b82f6" dot={false} name="Hit rate" />
                      </LineChart>
                    </ResponsiveContainer>
                  </div>
                </>
              ) : (
                <p className="text-muted">Predictions are scored once their horizon has passed. No outcomes yet.</p>
              )}
            </CardContent>
          </Card>
          <Card>
            <CardHeader>
              <CardTitle>Market regime & drift</CardTitle>
            </CardHeader>
            <CardContent className="space-y-2 text-xs">
              <p>
                Current regime (Gaussian mixture on benchmark return/volatility): <span className="font-medium">{regime ?? "—"}</span>
              </p>
              {drift?.models.map((m) => (
                <div key={m.name} className="flex items-center justify-between gap-2">
                  <span className="truncate">
                    {m.name} v{m.version}
                  </span>
                  <span className="flex gap-1">
                    <Badge variant={DRIFT_VARIANT[m.overall]} title={m.features.slice(0, 5).map((f) => `${f.feature}: ${f.psi}`).join("\n")}>
                      features {m.overall}
                    </Badge>
                    {m.prediction_drift ? <Badge variant={DRIFT_VARIANT[m.prediction_drift.status]}>predictions {m.prediction_drift.status}</Badge> : null}
                  </span>
                </div>
              ))}
              <p className="text-[10px] text-subtle">Population Stability Index vs training data: &lt;0.1 stable, 0.1–0.25 moderate, &gt;0.25 significant.</p>
            </CardContent>
          </Card>
        </div>

        <Card>
          <CardHeader>
            <div>
              <CardTitle>Latest predictions</CardTitle>
              <CardDescription className="mt-0.5">{preds?.items[0] ? `Horizon ${preds.items[0].horizon_days} trading days · features as of ${fmtDate(preds.items[0].as_of)}` : ""}</CardDescription>
            </div>
            <div className="flex items-center gap-2">
              {preds ? <DataBadge status={preds.data_status} source="platform ML pipeline" asOf={preds.items[0]?.as_of} /> : null}
              <Select className="h-8 w-36" value={trend} onChange={(e) => setTrend(e.target.value)} aria-label="Trend filter">
                <option value="">All classes</option>
                <option value="bullish">Bullish</option>
                <option value="neutral">Neutral</option>
                <option value="bearish">Bearish</option>
              </Select>
            </div>
          </CardHeader>
          <CardContent className="p-0">
            <ErrorNotice error={error} className="m-4" />
            {isLoading ? <Skeleton className="m-4 h-40" /> : null}
            {preds?.items.length && preds.items.every((p) => p.prob_up == null) ? (
              <Notice className="m-4">No trend model has passed validation (it must beat a majority-class baseline on unseen data), so movement probabilities are not shown. Volatility, unusual-activity and regime outputs come from separately validated models.</Notice>
            ) : null}
            {preds && preds.items.length === 0 ? <EmptyState icon={<BrainCircuit className="size-8" />} title="No predictions" description="Train models (admin) once enough daily history is available. Models must beat a baseline before they produce predictions." /> : null}
            {preds?.items.length ? (
              <Table>
                <THead>
                  <tr>
                    <TH>Instrument</TH>
                    <TH>Class</TH>
                    <TH>Probabilities</TH>
                    <TH className="text-right">Bullish</TH>
                    <TH className="hidden text-right md:table-cell">Confidence</TH>
                    <TH className="hidden text-right md:table-cell">Momentum</TH>
                    <TH className="hidden text-right lg:table-cell">Pred. volatility</TH>
                    <TH className="hidden text-right lg:table-cell">Unusual activity</TH>
                    <TH className="hidden text-right xl:table-cell">Risk</TH>
                  </tr>
                </THead>
                <TBody>
                  {preds.items.map((p) => (
                    <TR key={p.symbol}>
                      <TD>
                        <Link href={`/stocks/${encodeURIComponent(p.symbol)}`} className="font-medium hover:text-accent">
                          {p.symbol}
                        </Link>
                        <p className="text-[11px] text-muted">{p.sector}</p>
                      </TD>
                      <TD>
                        {p.trend_class ? <Badge variant={TREND_VARIANT[p.trend_class as keyof typeof TREND_VARIANT] ?? "default"}>{p.trend_class}</Badge> : <span className="text-xs text-subtle">—</span>}
                      </TD>
                      <TD>
                        {p.prob_up != null ? <ProbBar up={p.prob_up} neutral={p.prob_neutral ?? 0} down={p.prob_down ?? 0} /> : <span className="text-xs text-subtle">no validated trend model</span>}
                      </TD>
                      <TD className="num text-right text-xs">{p.prob_up != null ? `${fmtNum(p.prob_up * 100, 1)}%` : "—"}</TD>
                      <TD className="num hidden text-right text-xs md:table-cell">{p.confidence != null ? `${fmtNum(p.confidence * 100, 1)}%` : "—"}</TD>
                      <TD className="num hidden text-right text-xs md:table-cell">{p.momentum_score != null ? fmtNum(p.momentum_score, 1) : "—"}</TD>
                      <TD className="num hidden text-right text-xs lg:table-cell">{p.predicted_vol != null ? `${fmtNum(p.predicted_vol * 100, 1)}%` : "—"}</TD>
                      <TD className={cn("num hidden text-right text-xs lg:table-cell", (p.anomaly_score ?? 0) >= 99 ? "text-warn" : "")}>{p.anomaly_score != null ? `${fmtNum(p.anomaly_score, 0)}th pct` : "—"}</TD>
                      <TD className="num hidden text-right text-xs xl:table-cell">{p.risk_score != null ? fmtNum(p.risk_score, 0) : "—"}</TD>
                    </TR>
                  ))}
                </TBody>
              </Table>
            ) : null}
          </CardContent>
        </Card>

        {models?.length ? <ModelsCard models={models} /> : null}

        {sectors?.length ? (
          <Card>
            <CardHeader>
              <div>
                <CardTitle>Sector strength analysis</CardTitle>
                <CardDescription className="mt-0.5">Composite of relative strength, 1-month momentum and average ML bullish probability (percentile ranks).</CardDescription>
              </div>
            </CardHeader>
            <CardContent className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
              {sectors.map((s) => (
                <div key={s.sector} className="rounded border border-border bg-surface-2 px-3 py-2 text-xs">
                  <div className="flex justify-between">
                    <span className="font-medium">{s.sector}</span>
                    <span className="num">{s.strength_score ?? "—"}</span>
                  </div>
                  <p className="text-muted">
                    RS {fmtNum(s.avg_rel_strength, 1)} · ML bull {s.avg_ml_bullish_prob != null ? `${fmtNum(s.avg_ml_bullish_prob, 0)}%` : "—"} · {s.count} stocks
                  </p>
                </div>
              ))}
            </CardContent>
          </Card>
        ) : null}
      </div>
    </>
  );
}
