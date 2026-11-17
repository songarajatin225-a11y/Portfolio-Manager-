"use client";

import * as React from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { ExternalLink } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Notice } from "@/components/ui/feedback";
import type { DataStatus } from "@/lib/types";
import { fmtDateTime } from "@/lib/utils";

export interface AISource {
  type: string;
  name: string;
  source: string | null;
  as_of: string | null;
  data_status: string | null;
  url?: string;
}

/** Renders model output as Markdown. Raw HTML is not rendered, links open in a new tab. */
export function Markdown({ children }: { children: string }) {
  return (
    <div className="prose-sm max-w-none space-y-2 text-sm leading-relaxed [&_code]:rounded [&_code]:bg-surface-3 [&_code]:px-1 [&_h1]:text-base [&_h1]:font-semibold [&_h2]:text-sm [&_h2]:font-semibold [&_h3]:text-sm [&_h3]:font-semibold [&_li]:ml-4 [&_ol]:list-decimal [&_strong]:text-foreground [&_table]:w-full [&_table]:text-xs [&_td]:border [&_td]:border-border [&_td]:px-2 [&_td]:py-1 [&_th]:border [&_th]:border-border [&_th]:bg-surface-2 [&_th]:px-2 [&_th]:py-1 [&_ul]:list-disc">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          a: ({ href, children: c }) => (
            <a href={href} target="_blank" rel="noopener noreferrer nofollow" className="text-accent hover:underline">
              {c}
            </a>
          ),
        }}
      >
        {children}
      </ReactMarkdown>
    </div>
  );
}

const STATUS_VARIANT: Record<string, "live" | "delayed" | "historical" | "simulated"> = {
  LIVE: "live",
  DELAYED: "delayed",
  HISTORICAL: "historical",
  SIMULATED: "simulated",
};

export function SourcesList({ sources }: { sources: AISource[] }) {
  const [open, setOpen] = React.useState(false);
  if (!sources.length) return <p className="text-[11px] text-subtle">No platform data was used for this answer.</p>;
  const shown = open ? sources : sources.slice(0, 4);
  return (
    <div className="space-y-1">
      <p className="text-[10px] font-semibold uppercase tracking-wider text-subtle">Sources ({sources.length})</p>
      <ul className="space-y-0.5">
        {shown.map((s, i) => {
          const status = (s.data_status ?? "").split(",")[0] as DataStatus;
          return (
            <li key={i} className="flex flex-wrap items-center gap-1.5 text-[11px] text-muted">
              {STATUS_VARIANT[status] ? <Badge variant={STATUS_VARIANT[status]}>{status}</Badge> : <Badge>{s.type}</Badge>}
              {s.url ? (
                <a href={s.url} target="_blank" rel="noopener noreferrer nofollow" className="inline-flex items-center gap-0.5 hover:text-foreground">
                  {s.name} <ExternalLink className="size-3" />
                </a>
              ) : (
                <span>{s.name}</span>
              )}
              <span className="text-subtle">
                · {s.source ?? "—"} · {fmtDateTime(s.as_of)}
              </span>
            </li>
          );
        })}
      </ul>
      {sources.length > 4 ? (
        <button type="button" className="text-[11px] text-accent hover:underline" onClick={() => setOpen((o) => !o)}>
          {open ? "Show fewer" : `Show all ${sources.length}`}
        </button>
      ) : null}
    </div>
  );
}

export interface ResearchReport {
  id: number;
  kind: string;
  symbol: string | null;
  model: string;
  created_at: string;
  content: Record<string, unknown>;
  sources: AISource[];
  data_quality: { datasets?: Record<string, string>; completeness?: number; simulated?: boolean };
}

const SECTIONS: [string, string][] = [
  ["company_overview", "1. Company overview"],
  ["financial_performance", "2. Financial performance"],
  ["technical_indicators", "3. Technical indicators"],
  ["trend_analysis", "4. Trend analysis"],
  ["news_sentiment", "5. News sentiment"],
  ["industry_outlook", "6. Industry outlook"],
  ["bullish_factors", "7. Bullish factors"],
  ["bearish_factors", "8. Bearish factors"],
  ["risk_factors", "9. Risk factors"],
  ["valuation_analysis", "10. Valuation analysis"],
  ["investment_thesis", "11. Investment thesis"],
];

export function ReportView({ report }: { report: ResearchReport }) {
  const c = report.content;
  const conf = c.confidence as { level: string; rationale: string } | undefined;
  const dq = c.data_quality as { assessment: string; missing_data: string[] } | undefined;
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2 text-xs text-muted">
        <span className="text-base font-semibold text-foreground">{String(c.symbol ?? report.symbol)}</span>
        <span>{String(c.name ?? "")}</span>
        <Badge>{report.model}</Badge>
        <span>{fmtDateTime(report.created_at)}</span>
        {report.data_quality?.simulated ? <Badge variant="simulated">simulated data</Badge> : null}
      </div>
      <Notice tone="warn">{String(c.disclaimer ?? "AI-generated research. Not investment advice.")}</Notice>
      <div className="grid gap-3 md:grid-cols-2">
        {SECTIONS.map(([key, title]) => {
          const v = c[key];
          return (
            <section key={key} className="rounded-md border border-border bg-surface-2 p-3">
              <h4 className="mb-1 text-xs font-semibold uppercase tracking-wider text-muted">{title}</h4>
              {Array.isArray(v) ? (
                <ul className="list-disc space-y-0.5 pl-4 text-sm">
                  {v.map((x, i) => (
                    <li key={i}>{String(x)}</li>
                  ))}
                </ul>
              ) : (
                <p className="whitespace-pre-line text-sm">{String(v ?? "—")}</p>
              )}
            </section>
          );
        })}
        <section className="rounded-md border border-border bg-surface-2 p-3">
          <h4 className="mb-1 text-xs font-semibold uppercase tracking-wider text-muted">12. Confidence & data quality</h4>
          {conf ? (
            <p className="text-sm">
              <Badge variant={conf.level === "high" ? "up" : conf.level === "medium" ? "warn" : "down"}>{conf.level} confidence</Badge> {conf.rationale}
            </p>
          ) : null}
          {dq ? <p className="mt-1 text-sm">{dq.assessment}</p> : null}
          {dq?.missing_data?.length ? <p className="mt-1 text-xs text-warn">Missing: {dq.missing_data.join(", ")}</p> : null}
          {report.data_quality?.datasets ? (
            <div className="mt-2 flex flex-wrap gap-1">
              {Object.entries(report.data_quality.datasets).map(([k, v]) => (
                <Badge key={k} variant={v === "available" ? "up" : "down"}>
                  {k}: {v}
                </Badge>
              ))}
              {report.data_quality.completeness != null ? <Badge variant="info">completeness {Math.round(report.data_quality.completeness * 100)}%</Badge> : null}
            </div>
          ) : null}
        </section>
      </div>
      <SourcesList sources={report.sources} />
    </div>
  );
}
