"use client";

import Link from "next/link";
import useSWR from "swr";
import { toast } from "sonner";
import { Trash2 } from "lucide-react";
import { useAuth } from "@/components/auth/auth-provider";
import { InstrumentSearch } from "@/components/market/instrument-search";
import { ChangeCell, LiveBadge, PriceCell, useLiveQuote } from "@/components/market/quote-row-cells";
import { Button } from "@/components/ui/button";
import { ErrorNotice, Skeleton } from "@/components/ui/feedback";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { api, errorMessage, fetcher } from "@/lib/api";
import { useWatch } from "@/lib/market-stream";
import type { InstrumentWithQuote } from "@/lib/types";
import { fmtCompact, fmtPrice } from "@/lib/utils";

export interface WatchlistDetail {
  id: number;
  name: string;
  items: (InstrumentWithQuote & { notes: string; added_at: string })[];
}

function ItemRow({ item, onRemove, canWrite }: { item: WatchlistDetail["items"][number]; onRemove: () => void; canWrite: boolean }) {
  const q = useLiveQuote(item.instrument.id, item.quote);
  return (
    <TR>
      <TD>
        <Link href={`/stocks/${encodeURIComponent(item.instrument.symbol)}`} className="font-medium hover:text-accent">
          {item.instrument.symbol}
        </Link>
        <p className="max-w-56 truncate text-[11px] text-muted">{item.instrument.name}</p>
      </TD>
      <TD className="text-right">
        <PriceCell value={q?.ltp} />
      </TD>
      <TD className="text-right">
        <ChangeCell change={q?.change} pct={q?.change_pct} />
      </TD>
      <TD className="num hidden text-right text-xs md:table-cell">{fmtPrice(q?.high)} / {fmtPrice(q?.low)}</TD>
      <TD className="num hidden text-right text-xs sm:table-cell">{item.instrument.is_index ? "—" : fmtCompact(q?.volume)}</TD>
      <TD className="text-right">
        <LiveBadge quote={q} />
      </TD>
      <TD className="text-right">
        {canWrite ? (
          <Button size="icon" variant="ghost" onClick={onRemove} aria-label={`Remove ${item.instrument.symbol}`}>
            <Trash2 />
          </Button>
        ) : null}
      </TD>
    </TR>
  );
}

export function WatchlistTable({ id, compact = false }: { id: number; compact?: boolean }) {
  const { canWrite } = useAuth();
  const { data, error, mutate } = useSWR<WatchlistDetail>(`/watchlists/${id}`, fetcher);
  useWatch((data?.items ?? []).map((i) => i.instrument.id));
  if (error) return <ErrorNotice error={error} />;
  if (!data) return <Skeleton className="h-24" />;
  async function remove(instrumentId: number) {
    try {
      await api.del(`/watchlists/${id}/items/${instrumentId}`);
      void mutate();
    } catch (err) {
      toast.error(errorMessage(err));
    }
  }
  async function add(instrumentId: number) {
    try {
      await api.post(`/watchlists/${id}/items`, { instrument_id: instrumentId });
      void mutate();
    } catch (err) {
      toast.error(errorMessage(err));
    }
  }
  return (
    <div className="space-y-2">
      {!compact && canWrite ? <InstrumentSearch className="max-w-md" placeholder="Add a symbol to this watchlist…" onSelect={(i) => add(i.id)} /> : null}
      {data.items.length === 0 ? (
        <p className="py-6 text-center text-xs text-muted">Empty watchlist. Search above to add instruments.</p>
      ) : (
        <Table>
          <THead>
            <tr>
              <TH>Instrument</TH>
              <TH className="text-right">LTP</TH>
              <TH className="text-right">Change</TH>
              <TH className="hidden text-right md:table-cell">High / Low</TH>
              <TH className="hidden text-right sm:table-cell">Volume</TH>
              <TH className="text-right">Data</TH>
              <TH />
            </tr>
          </THead>
          <TBody>
            {data.items.map((item) => (
              <ItemRow key={item.instrument.id} item={item} canWrite={canWrite && !compact} onRemove={() => remove(item.instrument.id)} />
            ))}
          </TBody>
        </Table>
      )}
    </div>
  );
}
