"use client";

import * as React from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import useSWR from "swr";
import { PageHeader } from "@/components/layout/page-header";
import { InstrumentSearch } from "@/components/market/instrument-search";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { EmptyState, ErrorNotice, Skeleton } from "@/components/ui/feedback";
import { Select } from "@/components/ui/input";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { api } from "@/lib/api";
import { changeColor, cn, fmtNum, fmtPct, fmtPrice } from "@/lib/utils";
import type { ScreenResult } from "../screener/types";

export default function StockExplorerPage() {
  const router = useRouter();
  const [sector, setSector] = React.useState("");
  const body = { filters: sector ? [{ field: "sector", op: "eq", value: sector }] : [], sort: "ret_3m", direction: "desc", limit: 500 };
  const { data, error, isLoading } = useSWR<ScreenResult>(["/screener/run", JSON.stringify(body)], () => api.post<ScreenResult>("/screener/run", body), { refreshInterval: 60_000 });
  const { data: all } = useSWR<ScreenResult>(["/screener/run", "sectors"], () => api.post<ScreenResult>("/screener/run", { filters: [], limit: 500 }));
  const sectors = React.useMemo(() => [...new Set((all?.results ?? []).map((r) => r.sector).filter(Boolean))].sort() as string[], [all]);

  return (
    <>
      <PageHeader title="Stock Explorer" description="Search any instrument, or browse the tracked universe with its latest technical snapshot." />
      <div className="mb-4 max-w-xl">
        <InstrumentSearch autoFocus onSelect={(i) => router.push(`/stocks/${encodeURIComponent(i.symbol)}`)} />
      </div>
      <Card>
        <CardHeader>
          <div>
            <CardTitle>Tracked universe</CardTitle>
            <p className="mt-0.5 text-xs text-muted">{data ? `${data.matched} of ${data.universe} instruments with computed metrics` : "Loading…"}</p>
          </div>
          <Select className="h-8 w-56" value={sector} onChange={(e) => setSector(e.target.value)} aria-label="Sector">
            <option value="">All sectors</option>
            {sectors.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </Select>
        </CardHeader>
        <CardContent className="p-0">
          <ErrorNotice error={error} className="m-4" />
          {isLoading ? <Skeleton className="m-4 h-48" /> : null}
          {data && data.results.length === 0 ? (
            <EmptyState title="No metrics yet" description="Metrics are computed by the worker from daily candles after a provider is active and the universe is defined." />
          ) : null}
          {data?.results.length ? (
            <Table>
              <THead>
                <tr>
                  <TH>Instrument</TH>
                  <TH className="text-right">Price</TH>
                  <TH className="text-right">Day</TH>
                  <TH className="hidden text-right sm:table-cell">1M</TH>
                  <TH className="text-right">3M</TH>
                  <TH className="hidden text-right md:table-cell">1Y</TH>
                  <TH className="hidden text-right md:table-cell">RSI</TH>
                  <TH className="hidden text-right lg:table-cell">Rel. strength</TH>
                  <TH className="hidden text-right lg:table-cell">Volatility</TH>
                </tr>
              </THead>
              <TBody>
                {data.results.map((r) => {
                  const v = r.values;
                  const day = r.live?.change_pct ?? (v.change_pct as number | null);
                  return (
                    <TR key={r.instrument_id}>
                      <TD>
                        <Link href={`/stocks/${encodeURIComponent(r.symbol)}`} className="font-medium hover:text-accent">
                          {r.symbol}
                        </Link>
                        <p className="max-w-60 truncate text-[11px] text-muted">
                          {r.name} {r.sector ? `· ${r.sector}` : ""}
                        </p>
                      </TD>
                      <TD className="num text-right">{fmtPrice(r.live?.ltp ?? (v.last_close as number))}</TD>
                      <TD className={cn("num text-right text-xs", changeColor(day))}>{fmtPct(day)}</TD>
                      <TD className={cn("num hidden text-right text-xs sm:table-cell", changeColor(v.ret_1m as number))}>{fmtPct(v.ret_1m as number)}</TD>
                      <TD className={cn("num text-right text-xs", changeColor(v.ret_3m as number))}>{fmtPct(v.ret_3m as number)}</TD>
                      <TD className={cn("num hidden text-right text-xs md:table-cell", changeColor(v.ret_1y as number))}>{fmtPct(v.ret_1y as number)}</TD>
                      <TD className="num hidden text-right text-xs md:table-cell">{fmtNum(v.rsi_14 as number, 1)}</TD>
                      <TD className={cn("num hidden text-right text-xs lg:table-cell", changeColor(v.rel_strength as number))}>{fmtNum(v.rel_strength as number, 1)}</TD>
                      <TD className="num hidden text-right text-xs lg:table-cell">{v.volatility_20 != null ? `${fmtNum(v.volatility_20 as number, 1)}%` : "—"}</TD>
                    </TR>
                  );
                })}
              </TBody>
            </Table>
          ) : null}
        </CardContent>
      </Card>
    </>
  );
}
