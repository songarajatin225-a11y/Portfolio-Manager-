"use client";

import * as React from "react";
import { useRouter } from "next/navigation";
import useSWR from "swr";
import { ResponsiveContainer, Tooltip, Treemap } from "recharts";
import { PageHeader } from "@/components/layout/page-header";
import { DataBadge } from "@/components/market/data-badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { EmptyState, ErrorNotice, Skeleton } from "@/components/ui/feedback";
import { Select } from "@/components/ui/input";
import { fetcher } from "@/lib/api";
import type { DataStatus } from "@/lib/types";
import { changeColor, cn, fmtPct, fmtPrice } from "@/lib/utils";

interface Tile {
  instrument_id: number;
  symbol: string;
  name: string;
  sector: string | null;
  price: number | null;
  change_pct: number;
  weight: number;
  weight_basis: string;
  data_status: DataStatus | null;
  as_of: string | null;
}
interface HeatmapResponse {
  tiles: Tile[];
  weight_basis: "market_cap" | "turnover" | "equal";
  sectors: { sector: string; count: number; avg_change_pct: number; advancers: number }[];
}

/** Diverging scale: red (falling) → neutral grey → green (rising), saturating at ±4%. */
function colorFor(change: number): string {
  const t = Math.max(-1, Math.min(1, change / 4));
  if (t >= 0) return `rgb(${Math.round(30 - 8 * t)}, ${Math.round(41 + 120 * t)}, ${Math.round(55 + 20 * t)})`;
  return `rgb(${Math.round(30 + 170 * -t)}, ${Math.round(41 - 10 * -t)}, ${Math.round(55 - 10 * -t)})`;
}

interface CellProps {
  x?: number;
  y?: number;
  width?: number;
  height?: number;
  depth?: number;
  name?: string;
  change?: number;
}

function Cell({ x = 0, y = 0, width = 0, height = 0, depth, name, change = 0 }: CellProps) {
  if (depth === 1) {
    return (
      <g>
        <rect x={x} y={y} width={width} height={height} fill="none" stroke="#07090d" strokeWidth={3} />
        {width > 70 && height > 18 ? (
          <text x={x + 4} y={y + 13} fill="#e6edf3" fontSize={11} fontWeight={600}>
            {name}
          </text>
        ) : null}
      </g>
    );
  }
  return (
    <g>
      <rect x={x} y={y} width={width} height={height} fill={colorFor(change)} stroke="#07090d" strokeWidth={1} />
      {width > 46 && height > 26 ? (
        <>
          <text x={x + width / 2} y={y + height / 2 - 2} textAnchor="middle" fill="#fff" fontSize={Math.min(13, width / 6)} fontWeight={600}>
            {name}
          </text>
          <text x={x + width / 2} y={y + height / 2 + 12} textAnchor="middle" fill="rgba(255,255,255,0.85)" fontSize={10}>
            {fmtPct(change)}
          </text>
        </>
      ) : null}
    </g>
  );
}

export default function HeatmapPage() {
  const router = useRouter();
  const [sizeBy, setSizeBy] = React.useState<"weight" | "equal">("weight");
  const { data, error, isLoading } = useSWR<HeatmapResponse>("/market/heatmap", fetcher, { refreshInterval: 15_000 });
  const tree = React.useMemo(() => {
    const groups: Record<string, Tile[]> = {};
    for (const t of data?.tiles ?? []) (groups[t.sector ?? "Unclassified"] ??= []).push(t);
    return Object.entries(groups).map(([sector, tiles]) => ({
      name: sector,
      children: tiles.map((t) => ({ name: t.symbol, size: sizeBy === "equal" ? 1 : t.weight, change: t.change_pct, tile: t })),
    }));
  }, [data, sizeBy]);
  const basis = data?.weight_basis;
  const status = data?.tiles.find((t) => t.data_status)?.data_status ?? null;

  return (
    <>
      <PageHeader
        title="Market Heatmap"
        description="Tracked universe grouped by sector, coloured by day change."
        actions={
          <Select className="h-8 w-48" value={sizeBy} onChange={(e) => setSizeBy(e.target.value as "weight" | "equal")} aria-label="Tile size">
            <option value="weight">Size by {basis === "market_cap" ? "market cap" : basis === "turnover" ? "turnover" : "equal weight"}</option>
            <option value="equal">Equal size</option>
          </Select>
        }
      />
      <div className="grid gap-4 xl:grid-cols-[1fr_280px]">
        <Card>
          <CardHeader>
            <CardTitle>Heatmap</CardTitle>
            <DataBadge status={status} source={status === "SIMULATED" ? "simulated" : "feed / daily metrics"} asOf={data?.tiles[0]?.as_of} />
          </CardHeader>
          <CardContent className="h-[560px] p-2">
            <ErrorNotice error={error} />
            {isLoading ? <Skeleton className="h-full" /> : null}
            {data && data.tiles.length === 0 ? <EmptyState title="No data" description="Heatmap tiles appear once quotes or daily metrics exist for the tracked universe." /> : null}
            {data && data.tiles.length > 0 ? (
              <ResponsiveContainer width="100%" height="100%">
                <Treemap
                  data={tree}
                  dataKey="size"
                  isAnimationActive={false}
                  content={<Cell />}
                  onClick={(node) => {
                    const sym = (node as unknown as { name?: string; depth?: number }).name;
                    if ((node as unknown as { depth?: number }).depth === 2 && sym) router.push(`/stocks/${encodeURIComponent(sym)}`);
                  }}
                >
                  <Tooltip
                    contentStyle={{ background: "#131a23", border: "1px solid #2f3c4f", fontSize: 12 }}
                    formatter={(_v, _n, item) => {
                      const t = (item?.payload as { tile?: Tile })?.tile;
                      return t ? [`${fmtPrice(t.price)} (${fmtPct(t.change_pct)})`, t.name] : ["", ""];
                    }}
                  />
                </Treemap>
              </ResponsiveContainer>
            ) : null}
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle>Sector performance</CardTitle>
          </CardHeader>
          <CardContent className="space-y-1.5">
            {data?.sectors.map((s) => (
              <div key={s.sector} className="flex items-center justify-between text-xs">
                <span className="truncate">{s.sector}</span>
                <span className="flex items-center gap-2">
                  <span className="text-subtle">
                    {s.advancers}/{s.count} up
                  </span>
                  <span className={cn("num w-16 text-right", changeColor(s.avg_change_pct))}>{fmtPct(s.avg_change_pct)}</span>
                </span>
              </div>
            ))}
          </CardContent>
        </Card>
      </div>
    </>
  );
}
