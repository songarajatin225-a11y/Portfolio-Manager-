"use client";

/** Stock-page tabs contributed by the intelligence modules: news, ML predictions and AI research. */

import * as React from "react";
import useSWR from "swr";
import { toast } from "sonner";
import { FileText } from "lucide-react";
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { useAuth } from "@/components/auth/auth-provider";
import { ReportView, type ResearchReport } from "@/components/ai/ai-parts";
import { DataBadge } from "@/components/market/data-badge";
import { NewsRow, type NewsItem } from "@/components/news/news-row";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { EmptyState, ErrorNotice, Notice, Skeleton, Spinner } from "@/components/ui/feedback";
import { TabsContent, TabsTrigger } from "@/components/ui/tabs";
import { api, errorMessage, fetcher } from "@/lib/api";
import type { DataStatus, Instrument } from "@/lib/types";
import { fmtDate, fmtNum } from "@/lib/utils";

export function StockExtraTriggers({ isIndex }: { isIndex: boolean }) {
  return (
    <>
      {!isIndex ? <TabsTrigger value="news">News</TabsTrigger> : null}
      {!isIndex ? <TabsTrigger value="ml">ML prediction</TabsTrigger> : null}
      {!isIndex ? <TabsTrigger value="ai">AI research</TabsTrigger> : null}
    </>
  );
}

function NewsTab({ symbol }: { symbol: string }) {
  const { data, error } = useSWR<{ items: NewsItem[] }>(`/news?symbol=${encodeURIComponent(symbol)}&days=30&limit=50`, fetcher);
  if (error) return <ErrorNotice error={error} />;
  if (!data) return <Skeleton className="h-32" />;
  return (
    <Card>
      <CardContent className="py-0">
        {data.items.length === 0 ? <EmptyState title="No tagged news in the last 30 days" description="Articles are tagged to companies by name and symbol matching in headlines." /> : null}
        <ul className="divide-y divide-border/60">
          {data.items.map((n) => (
            <NewsRow key={n.id} n={n} />
          ))}
        </ul>
      </CardContent>
    </Card>
  );
}

interface StockPrediction {
  disclaimer: string;
  data_status?: DataStatus;
  latest: { as_of: string; horizon_days: number; prob_up: number | null; prob_neutral: number | null; prob_down: number | null; trend_class: string | null; confidence: number | null; predicted_vol: number | null; anomaly_score: number | null; regime: string | null } | null;
  model?: { name: string; version: number; algorithm: string; test: Record<string, number>; baseline: Record<string, number> };
  history: { as_of: string; prob_up: number | null; trend_class: string | null; realized_label: string | null }[];
  instrument_hit_rate?: number | null;
}

function MLTab({ symbol }: { symbol: string }) {
  const { data, error } = useSWR<StockPrediction>(`/stocks/${encodeURIComponent(symbol)}/prediction`, fetcher);
  if (error) return <ErrorNotice error={error} />;
  if (!data) return <Skeleton className="h-32" />;
  const l = data.latest;
  if (!l) return <Card><EmptyState title="No prediction for this instrument" description="Predictions appear after a validated model is trained and the instrument has enough history." /></Card>;
  return (
    <div className="grid gap-4 lg:grid-cols-[1fr_1.4fr]">
      <Card>
        <CardHeader>
          <div>
            <CardTitle>Next {l.horizon_days} trading days</CardTitle>
            <CardDescription className="mt-0.5">Features as of {fmtDate(l.as_of)} · {data.model ? `${data.model.name} v${data.model.version} (${data.model.algorithm})` : ""}</CardDescription>
          </div>
          <DataBadge status={data.data_status} source="platform ML pipeline" asOf={l.as_of} />
        </CardHeader>
        <CardContent className="space-y-3 text-sm">
          {l.prob_up == null ? <Notice>No trend model has passed validation, so movement probabilities are not shown.</Notice> : null}
          {(l.prob_up == null ? [] : [
            ["Bullish", l.prob_up, "bg-up"],
            ["Neutral", l.prob_neutral ?? 0, "bg-subtle"],
            ["Bearish", l.prob_down ?? 0, "bg-down"],
          ]).map(([label, p, color]) => (
            <div key={label as string}>
              <div className="mb-0.5 flex justify-between text-xs">
                <span>{label as string} probability</span>
                <span className="num">{fmtNum((p as number) * 100, 1)}%</span>
              </div>
              <div className="h-2 rounded bg-surface-3">
                <div className={`h-2 rounded ${color}`} style={{ width: `${(p as number) * 100}%` }} />
              </div>
            </div>
          ))}
          <div className="grid grid-cols-2 gap-2 text-xs">
            <p>
              Class: {l.trend_class ? <Badge variant={l.trend_class === "bullish" ? "up" : l.trend_class === "bearish" ? "down" : "default"}>{l.trend_class}</Badge> : "—"}
            </p>
            <p>Confidence: <span className="num">{l.confidence != null ? `${fmtNum(l.confidence * 100, 1)}%` : "—"}</span></p>
            <p>Predicted volatility: <span className="num">{l.predicted_vol != null ? `${fmtNum(l.predicted_vol * 100, 1)}%` : "—"}</span></p>
            <p>Unusual activity: <span className="num">{l.anomaly_score != null ? `${fmtNum(l.anomaly_score, 0)}th pct` : "—"}</span></p>
            <p className="col-span-2">Market regime: {l.regime ?? "—"}</p>
          </div>
          {data.model?.test?.f1_macro != null ? (
            <p className="text-xs text-muted">
              Model test macro-F1 {fmtNum(data.model.test.f1_macro, 3)} vs baseline {fmtNum(data.model.baseline.f1_macro, 3)}; directional accuracy {fmtNum(data.model.test.directional_accuracy, 3)}.
              {data.instrument_hit_rate != null ? ` Realised hit rate on this stock: ${fmtNum(data.instrument_hit_rate * 100, 1)}%.` : ""}
            </p>
          ) : null}
          <Notice tone="warn">{data.disclaimer}</Notice>
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle>Bullish probability history</CardTitle>
        </CardHeader>
        <CardContent className="h-64">
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={data.history.filter((h) => h.prob_up != null).map((h) => ({ ...h, p: +((h.prob_up ?? 0) * 100).toFixed(1) }))}>
              <CartesianGrid stroke="#222c3a" />
              <XAxis dataKey="as_of" tick={{ fill: "#8b98a9", fontSize: 10 }} />
              <YAxis domain={[0, 100]} unit="%" tick={{ fill: "#8b98a9", fontSize: 10 }} />
              <Tooltip contentStyle={{ background: "#131a23", border: "1px solid #2f3c4f", fontSize: 12 }} formatter={(v, _n, item) => [`${v}%`, `Bullish (realised: ${(item?.payload as { realized_label?: string })?.realized_label ?? "pending"})`]} />
              <Line dataKey="p" stroke="#22c55e" dot={false} />
            </LineChart>
          </ResponsiveContainer>
        </CardContent>
      </Card>
    </div>
  );
}

function AITab({ symbol }: { symbol: string }) {
  const { canWrite } = useAuth();
  const { data: status } = useSWR<{ configured: boolean; enabled: boolean }>("/ai/status", fetcher);
  const { data: list, mutate } = useSWR<{ id: number }[]>(`/ai/reports?symbol=${encodeURIComponent(symbol)}`, fetcher);
  const latestId = list?.[0]?.id;
  const { data: report } = useSWR<ResearchReport>(latestId ? `/ai/reports/${latestId}` : null, fetcher);
  const [busy, setBusy] = React.useState(false);
  async function generate() {
    setBusy(true);
    try {
      await api.post("/ai/reports", { symbol });
      await mutate();
      toast.success("Research report generated");
    } catch (err) {
      toast.error(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }
  const can = status?.configured && status.enabled && canWrite;
  return (
    <Card>
      <CardHeader>
        <div>
          <CardTitle>AI research report</CardTitle>
          <CardDescription className="mt-0.5">Written by Claude from this platform&apos;s data only, with a data-quality assessment and sources.</CardDescription>
        </div>
        {can ? (
          <Button size="sm" onClick={generate} disabled={busy}>
            {busy ? <Spinner className="text-white" /> : <FileText />} {report ? "Regenerate" : "Generate report"}
          </Button>
        ) : null}
      </CardHeader>
      <CardContent>
        {status && !status.configured ? <Notice tone="warn">AI is not configured on this server (ANTHROPIC_API_KEY).</Notice> : null}
        {busy ? <p className="mb-3 flex items-center gap-2 text-xs text-muted"><Spinner /> Gathering data and writing the report…</p> : null}
        {report ? <ReportView report={report} /> : !busy ? <EmptyState title="No report yet" description={can ? "Generate one to get a 12-section analysis." : "Ask an analyst to generate one."} /> : null}
      </CardContent>
    </Card>
  );
}

export function StockExtraTabs({ symbol, instrument }: { symbol: string; instrument: Instrument }) {
  if (instrument.is_index) return null;
  return (
    <>
      <TabsContent value="news">
        <NewsTab symbol={symbol} />
      </TabsContent>
      <TabsContent value="ml">
        <MLTab symbol={symbol} />
      </TabsContent>
      <TabsContent value="ai">
        <AITab symbol={symbol} />
      </TabsContent>
    </>
  );
}
