"use client";

import * as React from "react";
import Link from "next/link";
import useSWR from "swr";
import { toast } from "sonner";
import { Sparkles } from "lucide-react";
import { useAuth } from "@/components/auth/auth-provider";
import { NewsRow, type NewsItem } from "@/components/news/news-row";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Spinner } from "@/components/ui/feedback";
import { api, errorMessage, fetcher } from "@/lib/api";
import { timeAgo } from "@/lib/utils";

interface Summary {
  id: number;
  model: string;
  created_at: string;
  content: { headline: string; summary: string; key_points: string[]; sector_view: string; risks: string[]; data_caveats: string[]; disclaimer: string };
  data_quality: { simulated?: boolean };
}

function MarketIntelligenceWidget() {
  const { canWrite } = useAuth();
  const { data: status } = useSWR<{ configured: boolean; enabled: boolean }>("/ai/status", fetcher);
  const { data: summary, mutate } = useSWR<Summary | null>("/ai/market-summary/latest", fetcher);
  const { data: news } = useSWR<{ items: NewsItem[] }>("/news?days=2&limit=6", fetcher, { refreshInterval: 120_000 });
  const [busy, setBusy] = React.useState(false);
  async function generate() {
    setBusy(true);
    try {
      await api.post("/ai/market-summary");
      await mutate();
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
        <CardTitle>Market intelligence</CardTitle>
        {can ? (
          <Button size="xs" variant="secondary" onClick={generate} disabled={busy}>
            {busy ? <Spinner /> : <Sparkles />} {summary ? "Refresh AI summary" : "Generate AI summary"}
          </Button>
        ) : null}
      </CardHeader>
      <CardContent className="grid gap-4 lg:grid-cols-2">
        <div className="space-y-2">
          {summary ? (
            <>
              <div className="flex flex-wrap items-center gap-1.5 text-[11px] text-muted">
                <Badge variant="accent">AI summary</Badge>
                <span>{summary.model}</span>
                <span>· {timeAgo(summary.created_at)}</span>
                {summary.data_quality?.simulated ? <Badge variant="simulated">simulated data</Badge> : null}
              </div>
              <p className="text-sm font-semibold">{summary.content.headline}</p>
              <p className="text-xs text-muted">{summary.content.summary}</p>
              <ul className="list-disc space-y-0.5 pl-4 text-xs">
                {summary.content.key_points.map((k, i) => (
                  <li key={i}>{k}</li>
                ))}
              </ul>
              {summary.content.risks.length ? <p className="text-xs text-warn">Risks: {summary.content.risks.join("; ")}</p> : null}
              <p className="text-[10px] text-subtle">{summary.content.disclaimer}</p>
            </>
          ) : (
            <p className="text-xs text-muted">{status?.configured ? "No AI summary yet." : "AI summaries need ANTHROPIC_API_KEY on the server."}</p>
          )}
        </div>
        <div>
          <div className="mb-1 flex items-center justify-between">
            <p className="text-[11px] font-semibold uppercase tracking-wider text-subtle">Latest news</p>
            <Link href="/news" className="text-[11px] text-accent hover:underline">
              All news →
            </Link>
          </div>
          {news?.items.length ? (
            <ul className="divide-y divide-border/60">
              {news.items.map((n) => (
                <NewsRow key={n.id} n={n} />
              ))}
            </ul>
          ) : (
            <p className="text-xs text-muted">No recent articles. Configure feeds in News Intelligence.</p>
          )}
        </div>
      </CardContent>
    </Card>
  );
}

/** Dashboard widgets contributed by later modules. */
export const EXTRA_WIDGETS: { key: string; label: string; wide?: boolean; render: () => React.ReactNode }[] = [
  { key: "intelligence", label: "Market intelligence (news & AI summary)", wide: true, render: () => <MarketIntelligenceWidget /> },
];
