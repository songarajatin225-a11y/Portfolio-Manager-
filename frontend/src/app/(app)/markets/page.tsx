"use client";

import * as React from "react";
import Link from "next/link";
import useSWR from "swr";
import { ArrowDown, ArrowUp } from "lucide-react";
import { PageHeader } from "@/components/layout/page-header";
import { ChangeCell, LiveBadge, PriceCell, useLiveQuote } from "@/components/market/quote-row-cells";
import { Card, CardContent } from "@/components/ui/card";
import { EmptyState, ErrorNotice, Skeleton } from "@/components/ui/feedback";
import { Input, Select } from "@/components/ui/input";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { fetcher } from "@/lib/api";
import { useWatch } from "@/lib/market-stream";
import type { InstrumentWithQuote } from "@/lib/types";
import { fmtCompact, fmtPrice } from "@/lib/utils";

interface LiveResponse {
  scope: string;
  data_mode: string;
  items: InstrumentWithQuote[];
}
interface WatchlistLite {
  id: number;
  name: string;
}

type SortKey = "symbol" | "change_pct" | "volume" | "ltp";

function Row({ item }: { item: InstrumentWithQuote }) {
  const q = useLiveQuote(item.instrument.id, item.quote);
  const i = item.instrument;
  return (
    <TR>
      <TD>
        <Link href={`/stocks/${encodeURIComponent(i.symbol)}`} className="font-medium hover:text-accent">
          {i.symbol}
        </Link>
        <p className="max-w-56 truncate text-[11px] text-muted">{i.name}</p>
      </TD>
      <TD className="text-right">
        <PriceCell value={q?.ltp} className="font-semibold" />
      </TD>
      <TD className="text-right">
        <ChangeCell change={q?.change} pct={q?.change_pct} />
      </TD>
      <TD className="num hidden text-right text-xs md:table-cell">{fmtPrice(q?.open)}</TD>
      <TD className="num hidden text-right text-xs md:table-cell">{fmtPrice(q?.high)}</TD>
      <TD className="num hidden text-right text-xs md:table-cell">{fmtPrice(q?.low)}</TD>
      <TD className="num hidden text-right text-xs lg:table-cell">{fmtPrice(q?.prev_close)}</TD>
      <TD className="num hidden text-right text-xs sm:table-cell">{i.is_index ? "—" : fmtCompact(q?.volume)}</TD>
      <TD className="num hidden text-right text-xs xl:table-cell">{fmtPrice(q?.vwap)}</TD>
      <TD className="num hidden whitespace-nowrap text-right text-xs 2xl:table-cell">{q?.bid != null ? `${fmtPrice(q.bid)} / ${fmtPrice(q.ask)}` : "—"}</TD>
      <TD className="text-right">
        <LiveBadge quote={q} />
      </TD>
    </TR>
  );
}

export default function LiveMarketsPage() {
  const [scope, setScope] = React.useState("universe");
  const [filter, setFilter] = React.useState("");
  const [sort, setSort] = React.useState<{ key: SortKey; dir: 1 | -1 }>({ key: "symbol", dir: 1 });
  const { data, error, isLoading } = useSWR<LiveResponse>(`/market/live?scope=${scope}`, fetcher, { refreshInterval: 60_000 });
  const { data: watchlists } = useSWR<WatchlistLite[]>("/watchlists", fetcher);
  const ids = React.useMemo(() => (data?.items ?? []).map((i) => i.instrument.id), [data]);
  useWatch(ids);

  const rows = React.useMemo(() => {
    const f = filter.trim().toLowerCase();
    const list = (data?.items ?? []).filter((i) => !f || i.instrument.symbol.toLowerCase().includes(f) || i.instrument.name.toLowerCase().includes(f));
    const val = (i: InstrumentWithQuote): string | number => {
      if (sort.key === "symbol") return i.instrument.symbol;
      return (i.quote?.[sort.key] as number | null | undefined) ?? -Infinity;
    };
    return [...list].sort((a, b) => (val(a) > val(b) ? sort.dir : val(a) < val(b) ? -sort.dir : 0));
  }, [data, filter, sort]);

  const header = (key: SortKey, label: string, className = "") => (
    <TH className={`cursor-pointer select-none ${className}`} onClick={() => setSort((s) => ({ key, dir: s.key === key ? (s.dir === 1 ? -1 : 1) : key === "symbol" ? 1 : -1 }))}>
      <span className="inline-flex items-center gap-1">
        {label}
        {sort.key === key ? sort.dir === 1 ? <ArrowUp className="size-3" /> : <ArrowDown className="size-3" /> : null}
      </span>
    </TH>
  );

  return (
    <>
      <PageHeader
        title="Live Markets"
        description="Streaming quotes for the tracked universe. Sorting uses the latest snapshot; prices update live."
        actions={
          <>
            <Input className="h-8 w-48" placeholder="Filter symbol or name" value={filter} onChange={(e) => setFilter(e.target.value)} />
            <Select className="h-8 w-52" value={scope} onChange={(e) => setScope(e.target.value)} aria-label="Scope">
              <option value="universe">Tracked universe</option>
              <option value="indices">Indices</option>
              {watchlists?.map((w) => (
                <option key={w.id} value={`watchlist:${w.id}`}>
                  Watchlist: {w.name}
                </option>
              ))}
            </Select>
          </>
        }
      />
      <Card>
        <CardContent className="p-0">
          <ErrorNotice error={error} className="m-4" />
          {isLoading ? <Skeleton className="m-4 h-64" /> : null}
          {data && data.items.length === 0 ? (
            <EmptyState
              title="Nothing to show yet"
              description="Activate a provider, sync the instrument master and import index constituents (or add stocks to a watchlist) to populate the tracked universe."
              action={
                <Link href="/settings/providers" className="text-xs text-accent hover:underline">
                  Open Data Provider Settings
                </Link>
              }
            />
          ) : null}
          {rows.length ? (
            <Table>
              <THead>
                <tr>
                  {header("symbol", "Instrument")}
                  {header("ltp", "LTP", "text-right")}
                  {header("change_pct", "Change", "text-right")}
                  <TH className="hidden text-right md:table-cell">Open</TH>
                  <TH className="hidden text-right md:table-cell">High</TH>
                  <TH className="hidden text-right md:table-cell">Low</TH>
                  <TH className="hidden text-right lg:table-cell">Prev close</TH>
                  {header("volume", "Volume", "hidden text-right sm:table-cell")}
                  <TH className="hidden text-right xl:table-cell">VWAP</TH>
                  <TH className="hidden text-right 2xl:table-cell">Bid / Ask</TH>
                  <TH className="text-right">Data</TH>
                </tr>
              </THead>
              <TBody>
                {rows.map((item) => (
                  <Row key={item.instrument.id} item={item} />
                ))}
              </TBody>
            </Table>
          ) : null}
        </CardContent>
      </Card>
    </>
  );
}
