"use client";

import Link from "next/link";
import { ExternalLink } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { fmtDateTime, timeAgo } from "@/lib/utils";

export interface NewsItem {
  id: number;
  title: string;
  summary: string;
  url: string;
  source: string;
  category: string;
  published_at: string;
  sentiment: "positive" | "negative" | "neutral";
  sentiment_score: number;
  sentiment_confidence: number;
  sentiment_method: string;
  impact: "high" | "normal";
  symbols: string[];
}
const SENT_VARIANT = { positive: "up", negative: "down", neutral: "default" } as const;

export function NewsRow({ n }: { n: NewsItem }) {
  return (
    <li className="space-y-1 py-3">
      <div className="flex flex-wrap items-center gap-1.5 text-[11px] text-muted">
        <span className="font-medium text-foreground/80">{n.source}</span>
        <span title={fmtDateTime(n.published_at)}>· {timeAgo(n.published_at)}</span>
        <Badge variant={SENT_VARIANT[n.sentiment]} title={`Score ${n.sentiment_score} · confidence ${n.sentiment_confidence} · method ${n.sentiment_method}`}>
          {n.sentiment}
        </Badge>
        {n.impact === "high" ? <Badge variant="warn">high impact</Badge> : null}
        {n.category !== "market" ? <Badge>{n.category}</Badge> : null}
        {n.symbols.map((s) => (
          <Link key={s} href={`/stocks/${encodeURIComponent(s)}`} className="rounded bg-accent/15 px-1.5 py-0.5 text-[10px] font-semibold text-accent hover:bg-accent/25">
            {s}
          </Link>
        ))}
      </div>
      <a href={n.url} target="_blank" rel="noopener noreferrer nofollow" className="group flex items-start gap-1 text-sm font-medium hover:text-accent">
        {n.title} <ExternalLink className="mt-1 size-3 shrink-0 opacity-0 group-hover:opacity-100" />
      </a>
      {n.summary ? <p className="line-clamp-2 text-xs text-muted">{n.summary}</p> : null}
    </li>
  );
}
