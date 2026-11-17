import { Badge } from "@/components/ui/badge";
import type { DataStatus } from "@/lib/types";
import { fmtDateTime } from "@/lib/utils";

const VARIANT: Record<DataStatus, "live" | "delayed" | "historical" | "simulated"> = {
  LIVE: "live",
  DELAYED: "delayed",
  HISTORICAL: "historical",
  SIMULATED: "simulated",
};

/**
 * Every data point in the UI carries one of these labels plus its source and timestamp.
 * `stale` turns a LIVE label into DELAYED (STALE) when no update arrived within the threshold.
 */
export function DataBadge({ status, source, asOf, stale, className }: { status: DataStatus | null | undefined; source?: string | null; asOf?: string | null; stale?: boolean; className?: string }) {
  if (!status) return <Badge className={className}>NO DATA</Badge>;
  const effective: DataStatus = stale && status === "LIVE" ? "DELAYED" : status;
  const label = stale ? `${effective} · STALE` : effective;
  const title = [`Status: ${label}`, source ? `Source: ${source}` : null, asOf ? `As of: ${fmtDateTime(asOf)}` : null].filter(Boolean).join("\n");
  return (
    <Badge variant={VARIANT[effective]} className={className} title={title}>
      {effective === "LIVE" ? <span className="size-1.5 animate-pulse rounded-full bg-live" /> : null}
      {label}
    </Badge>
  );
}

/** Compute staleness for a streamed LIVE tick on the client. */
export function isStale(status: DataStatus | undefined, receivedAt: string | null | undefined, staleAfterSeconds: number | undefined, now: number): boolean {
  if (status !== "LIVE" || !receivedAt || !now) return false;
  return (now - new Date(receivedAt).getTime()) / 1000 > (staleAfterSeconds ?? 30);
}
