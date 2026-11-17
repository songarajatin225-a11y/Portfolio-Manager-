import * as React from "react";
import { cn } from "@/lib/utils";

/** Compact KPI tile: label, value and an optional sub-line. */
export function Stat({ label, value, sub, className, valueClassName, title }: { label: string; value: React.ReactNode; sub?: React.ReactNode; className?: string; valueClassName?: string; title?: string }) {
  return (
    <div className={cn("rounded-md border border-border bg-surface-2/60 px-3 py-2", className)} title={title}>
      <p className="text-[10px] font-medium uppercase tracking-wider text-subtle">{label}</p>
      <p className={cn("num mt-0.5 text-base font-semibold", valueClassName)}>{value}</p>
      {sub ? <p className="mt-0.5 text-[11px] text-muted">{sub}</p> : null}
    </div>
  );
}

/** Horizontal share bars with direct labels (accessible alternative to pie charts). */
export function ShareBars({ rows, color = "#3987e5", format }: { rows: { name: string; weight: number; value?: number }[]; color?: string; format?: (v: number) => string }) {
  return (
    <ul className="space-y-1.5">
      {rows.map((r) => (
        <li key={r.name} className="text-xs">
          <div className="mb-0.5 flex justify-between gap-2">
            <span className="truncate">{r.name}</span>
            <span className="num shrink-0 text-muted">
              {(r.weight * 100).toFixed(1)}%{r.value != null && format ? ` · ${format(r.value)}` : ""}
            </span>
          </div>
          <div className="h-1.5 rounded bg-surface-3">
            <div className="h-1.5 rounded" style={{ width: `${Math.max(0.5, r.weight * 100)}%`, background: color }} />
          </div>
        </li>
      ))}
    </ul>
  );
}
