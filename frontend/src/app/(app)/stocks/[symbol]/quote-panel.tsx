"use client";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { LiveBadge, type LiveQuote } from "@/components/market/quote-row-cells";
import type { Quote } from "@/lib/types";
import { fmtCompact, fmtDateTime, fmtPrice } from "@/lib/utils";

function Row({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between py-1 text-xs">
      <span className="text-muted">{label}</span>
      <span className="num">{value}</span>
    </div>
  );
}

export function QuotePanel({ quote, live, isIndex }: { quote: Quote | null | undefined; live: LiveQuote | null; isIndex: boolean }) {
  const q = live ?? quote;
  const depthBuy = quote?.depth_buy ?? [];
  const depthSell = quote?.depth_sell ?? [];
  const rangePos = q?.low != null && q?.high != null && q.high > q.low ? ((q.ltp - q.low) / (q.high - q.low)) * 100 : null;
  return (
    <Card>
      <CardHeader>
        <CardTitle>Quote</CardTitle>
        <LiveBadge quote={live ?? (quote as unknown as LiveQuote) ?? null} />
      </CardHeader>
      <CardContent className="space-y-3">
        {!q ? <p className="text-xs text-muted">No quote yet. The instrument may not be in the streamed universe — add it to a watchlist to stream it.</p> : null}
        {q ? (
          <>
            <div>
              <Row label="Open" value={fmtPrice(q.open)} />
              <Row label="High" value={fmtPrice(q.high)} />
              <Row label="Low" value={fmtPrice(q.low)} />
              <Row label="Previous close" value={fmtPrice(q.prev_close)} />
              {!isIndex ? <Row label="VWAP" value={fmtPrice(q.vwap)} /> : null}
              {!isIndex ? <Row label="Volume" value={fmtCompact(q.volume)} /> : null}
              {quote?.oi ? <Row label="Open interest" value={fmtCompact(quote.oi)} /> : null}
              {quote?.high_52w ? <Row label="52-week range" value={`${fmtPrice(quote.low_52w)} – ${fmtPrice(quote.high_52w)}`} /> : null}
              {quote?.upper_circuit ? <Row label="Circuit limits" value={`${fmtPrice(quote.lower_circuit)} – ${fmtPrice(quote.upper_circuit)}`} /> : null}
            </div>
            {rangePos != null ? (
              <div>
                <div className="mb-1 flex justify-between text-[10px] text-subtle">
                  <span>Day low</span>
                  <span>Day high</span>
                </div>
                <div className="relative h-1.5 rounded bg-surface-3">
                  <div className="absolute top-1/2 size-2.5 -translate-x-1/2 -translate-y-1/2 rounded-full bg-accent" style={{ left: `${rangePos}%` }} />
                </div>
              </div>
            ) : null}
            {depthBuy.length || depthSell.length ? (
              <div>
                <p className="mb-1 text-[10px] font-semibold uppercase tracking-wider text-subtle">Market depth</p>
                <table className="w-full text-[11px]">
                  <thead className="text-subtle">
                    <tr>
                      <th className="text-left font-normal">Bid qty</th>
                      <th className="text-right font-normal">Bid</th>
                      <th className="text-right font-normal">Ask</th>
                      <th className="text-right font-normal">Ask qty</th>
                    </tr>
                  </thead>
                  <tbody className="num">
                    {Array.from({ length: Math.max(depthBuy.length, depthSell.length) }).map((_, i) => (
                      <tr key={i}>
                        <td className="text-left text-muted">{depthBuy[i] ? fmtCompact(depthBuy[i].quantity) : ""}</td>
                        <td className="text-right text-up">{depthBuy[i] ? fmtPrice(depthBuy[i].price) : ""}</td>
                        <td className="text-right text-down">{depthSell[i] ? fmtPrice(depthSell[i].price) : ""}</td>
                        <td className="text-right text-muted">{depthSell[i] ? fmtCompact(depthSell[i].quantity) : ""}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : null}
            <p className="text-[10px] text-subtle">
              Source: {q.source} · {fmtDateTime(q.exchange_ts ?? q.received_at)}
            </p>
          </>
        ) : null}
      </CardContent>
    </Card>
  );
}
