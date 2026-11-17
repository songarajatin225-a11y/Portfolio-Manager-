"use client";

import * as React from "react";
import { toast } from "sonner";
import { InstrumentSearch } from "@/components/market/instrument-search";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent } from "@/components/ui/dialog";
import { Spinner } from "@/components/ui/feedback";
import { Field, Input, Select, Textarea } from "@/components/ui/input";
import { api, errorMessage } from "@/lib/api";

export interface JournalTrade {
  id: number;
  symbol: string;
  exchange: string;
  direction: "LONG" | "SHORT";
  status: "OPEN" | "CLOSED";
  entry_at: string;
  entry_price: number;
  quantity: number;
  exit_at: string | null;
  exit_price: number | null;
  stop_loss: number | null;
  target: number | null;
  strategy: string;
  brokerage: number;
  taxes: number;
  entry_reason: string;
  exit_reason: string;
  tags: string[];
  emotion: string;
  net_pnl: number | null;
  return_pct: number | null;
  r_multiple: number | null;
  planned_rr: number | null;
  holding_days: number | null;
  unrealized_pnl?: number;
  current_price?: number;
  price_status?: string;
}

type Form = Record<"symbol" | "direction" | "entry_at" | "entry_price" | "quantity" | "exit_at" | "exit_price" | "stop_loss" | "target" | "strategy" | "brokerage" | "taxes" | "entry_reason" | "exit_reason" | "tags" | "emotion", string>;

/** ISO timestamp -> value for <input type="datetime-local"> in IST. */
function toLocalInput(iso: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  const ist = new Date(d.getTime() + 330 * 60_000);
  return ist.toISOString().slice(0, 16);
}
/** datetime-local value (interpreted as IST) -> ISO string with offset. */
function fromLocalInput(v: string): string | null {
  return v ? `${v}:00+05:30` : null;
}

function toForm(t: JournalTrade | null): Form {
  return {
    symbol: t?.symbol ?? "",
    direction: t?.direction ?? "LONG",
    entry_at: toLocalInput(t?.entry_at ?? new Date().toISOString()),
    entry_price: t ? String(t.entry_price) : "",
    quantity: t ? String(t.quantity) : "",
    exit_at: toLocalInput(t?.exit_at ?? null),
    exit_price: t?.exit_price != null ? String(t.exit_price) : "",
    stop_loss: t?.stop_loss != null ? String(t.stop_loss) : "",
    target: t?.target != null ? String(t.target) : "",
    strategy: t?.strategy ?? "",
    brokerage: t ? String(t.brokerage) : "0",
    taxes: t ? String(t.taxes) : "0",
    entry_reason: t?.entry_reason ?? "",
    exit_reason: t?.exit_reason ?? "",
    tags: t?.tags.join(", ") ?? "",
    emotion: t?.emotion ?? "",
  };
}

export function TradeDialog({ trade, open, onOpenChange, onSaved, strategies, emotions }: { trade: JournalTrade | null; open: boolean; onOpenChange: (o: boolean) => void; onSaved: () => void; strategies: string[]; emotions: string[] }) {
  const [f, setF] = React.useState<Form>(() => toForm(trade));
  const [busy, setBusy] = React.useState(false);
  const set = (k: keyof Form) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>) => setF((x) => ({ ...x, [k]: e.target.value }));
  const num = (v: string) => (v === "" ? null : Number(v));

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    const body = {
      symbol: f.symbol,
      direction: f.direction,
      entry_at: fromLocalInput(f.entry_at),
      entry_price: Number(f.entry_price),
      quantity: Number(f.quantity),
      exit_at: fromLocalInput(f.exit_at),
      exit_price: num(f.exit_price),
      stop_loss: num(f.stop_loss),
      target: num(f.target),
      strategy: f.strategy,
      brokerage: Number(f.brokerage || 0),
      taxes: Number(f.taxes || 0),
      entry_reason: f.entry_reason,
      exit_reason: f.exit_reason,
      tags: f.tags.split(",").map((t) => t.trim()).filter(Boolean),
      emotion: f.emotion,
    };
    try {
      if (trade) await api.put(`/journal/trades/${trade.id}`, body);
      else await api.post("/journal/trades", body);
      toast.success(trade ? "Trade updated" : "Trade logged");
      onSaved();
      onOpenChange(false);
    } catch (err) {
      toast.error(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl" title={trade ? "Edit trade" : "Log a trade"} description="Times are IST. Leave exit fields empty for an open trade. A stop-loss lets the journal measure R-multiples.">
        <form onSubmit={submit} className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <div className="col-span-2">
            <span className="mb-1 block text-xs font-medium text-muted">Instrument</span>
            {f.symbol ? (
              <div className="flex items-center justify-between rounded-md border border-border-strong bg-surface-2 px-3 py-1.5 text-sm">
                <span className="font-medium">{f.symbol}</span>
                <button type="button" className="text-xs text-accent hover:underline" onClick={() => setF((x) => ({ ...x, symbol: "" }))}>
                  Change
                </button>
              </div>
            ) : (
              <InstrumentSearch onSelect={(i) => setF((x) => ({ ...x, symbol: i.symbol }))} />
            )}
          </div>
          <Field label="Direction">
            <Select value={f.direction} onChange={set("direction")}>
              <option value="LONG">Long</option>
              <option value="SHORT">Short</option>
            </Select>
          </Field>
          <Field label="Strategy">
            <Input list="journal-strategies" maxLength={100} value={f.strategy} onChange={set("strategy")} />
            <datalist id="journal-strategies">
              {strategies.map((s) => (
                <option key={s} value={s} />
              ))}
            </datalist>
          </Field>
          <Field label="Entry time"><Input type="datetime-local" required value={f.entry_at} onChange={set("entry_at")} /></Field>
          <Field label="Entry price"><Input type="number" min="0" step="any" required value={f.entry_price} onChange={set("entry_price")} /></Field>
          <Field label="Quantity"><Input type="number" min="0" step="any" required value={f.quantity} onChange={set("quantity")} /></Field>
          <Field label="Emotion">
            <Select value={f.emotion} onChange={set("emotion")}>
              <option value="">—</option>
              {emotions.map((e) => (
                <option key={e} value={e}>{e}</option>
              ))}
            </Select>
          </Field>
          <Field label="Stop-loss"><Input type="number" min="0" step="any" value={f.stop_loss} onChange={set("stop_loss")} /></Field>
          <Field label="Target"><Input type="number" min="0" step="any" value={f.target} onChange={set("target")} /></Field>
          <Field label="Exit time"><Input type="datetime-local" value={f.exit_at} onChange={set("exit_at")} /></Field>
          <Field label="Exit price"><Input type="number" min="0" step="any" value={f.exit_price} onChange={set("exit_price")} /></Field>
          <Field label="Brokerage (₹)"><Input type="number" min="0" step="any" value={f.brokerage} onChange={set("brokerage")} /></Field>
          <Field label="Taxes & charges (₹)"><Input type="number" min="0" step="any" value={f.taxes} onChange={set("taxes")} /></Field>
          <Field label="Tags (comma separated)" className="col-span-2"><Input value={f.tags} onChange={set("tags")} /></Field>
          <Field label="Entry reason" className="col-span-2"><Textarea maxLength={4000} value={f.entry_reason} onChange={set("entry_reason")} /></Field>
          <Field label="Exit reason / review" className="col-span-2"><Textarea maxLength={4000} value={f.exit_reason} onChange={set("exit_reason")} /></Field>
          <div className="col-span-full flex justify-end gap-2">
            <Button type="button" variant="ghost" onClick={() => onOpenChange(false)}>Cancel</Button>
            <Button type="submit" disabled={busy || !f.symbol}>{busy ? <Spinner className="text-white" /> : null} Save</Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}

export function CloseDialog({ trade, open, onOpenChange, onSaved }: { trade: JournalTrade; open: boolean; onOpenChange: (o: boolean) => void; onSaved: () => void }) {
  const [exitAt, setExitAt] = React.useState(() => toLocalInput(new Date().toISOString()));
  const [price, setPrice] = React.useState(trade.current_price != null ? String(trade.current_price) : "");
  const [reason, setReason] = React.useState("");
  async function submit(e: React.FormEvent) {
    e.preventDefault();
    try {
      await api.post(`/journal/trades/${trade.id}/close`, { exit_at: fromLocalInput(exitAt), exit_price: Number(price), exit_reason: reason });
      toast.success("Trade closed");
      onSaved();
      onOpenChange(false);
    } catch (err) {
      toast.error(errorMessage(err));
    }
  }
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent title={`Close ${trade.direction.toLowerCase()} ${trade.symbol}`}>
        <form onSubmit={submit} className="space-y-3">
          <Field label="Exit time (IST)"><Input type="datetime-local" required value={exitAt} onChange={(e) => setExitAt(e.target.value)} /></Field>
          <Field label="Exit price" hint={trade.current_price != null ? `Prefilled from the latest quote (${trade.price_status}). Use your actual fill price.` : undefined}>
            <Input type="number" min="0" step="any" required value={price} onChange={(e) => setPrice(e.target.value)} />
          </Field>
          <Field label="Exit reason / review"><Textarea value={reason} onChange={(e) => setReason(e.target.value)} /></Field>
          <div className="flex justify-end gap-2">
            <Button type="button" variant="ghost" onClick={() => onOpenChange(false)}>Cancel</Button>
            <Button type="submit">Close trade</Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}
