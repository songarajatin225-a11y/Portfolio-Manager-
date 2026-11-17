"use client";

/** Transaction ledger: add / edit / delete, CSV import (with dry run) and export. */

import * as React from "react";
import useSWR, { useSWRConfig } from "swr";
import { toast } from "sonner";
import { Download, Pencil, Plus, Trash2, Upload } from "lucide-react";
import type { TransactionRow } from "@/components/portfolio/types";
import { InstrumentSearch } from "@/components/market/instrument-search";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent } from "@/components/ui/dialog";
import { EmptyState, ErrorNotice, Notice, Skeleton, Spinner } from "@/components/ui/feedback";
import { Field, Input, Select } from "@/components/ui/input";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { api, errorMessage, fetcher } from "@/lib/api";
import { fmtDate, fmtNum, fmtPrice } from "@/lib/utils";

interface TxnForm {
  symbol: string;
  side: "BUY" | "SELL" | "DIVIDEND";
  quantity: string;
  price: string;
  charges: string;
  trade_date: string;
  category: string;
  notes: string;
}

const today = () => new Date().toISOString().slice(0, 10);
const EMPTY: TxnForm = { symbol: "", side: "BUY", quantity: "", price: "", charges: "0", trade_date: today(), category: "Long-term", notes: "" };

function TxnDialog({ portfolioId, initial, open, onOpenChange, onSaved }: { portfolioId: number; initial: (TxnForm & { id?: number }) | null; open: boolean; onOpenChange: (o: boolean) => void; onSaved: () => void }) {
  const [form, setForm] = React.useState<TxnForm & { id?: number }>(initial ?? EMPTY);
  const [busy, setBusy] = React.useState(false);
  const set = (k: keyof TxnForm) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => setForm((f) => ({ ...f, [k]: e.target.value }));

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    const body = { ...form, quantity: Number(form.quantity), price: Number(form.price), charges: Number(form.charges || 0) };
    try {
      if (form.id) await api.patch(`/portfolios/${portfolioId}/transactions/${form.id}`, body);
      else await api.post(`/portfolios/${portfolioId}/transactions`, body);
      toast.success(form.id ? "Transaction updated" : "Transaction added");
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
      <DialogContent title={form.id ? "Edit transaction" : "Add transaction"} description="Sells are matched to earlier buys first-in, first-out. For dividends, quantity is the number of shares and price the dividend per share.">
        <form onSubmit={submit} className="grid grid-cols-2 gap-3">
          <div className="col-span-2">
            <span className="mb-1 block text-xs font-medium text-muted">Instrument</span>
            {form.symbol ? (
              <div className="flex items-center justify-between rounded-md border border-border-strong bg-surface-2 px-3 py-1.5 text-sm">
                <span className="font-medium">{form.symbol}</span>
                <button type="button" className="text-xs text-accent hover:underline" onClick={() => setForm((f) => ({ ...f, symbol: "" }))}>
                  Change
                </button>
              </div>
            ) : (
              <InstrumentSearch segment="EQ" onSelect={(i) => setForm((f) => ({ ...f, symbol: i.symbol }))} placeholder="Search equity symbol…" />
            )}
          </div>
          <Field label="Type">
            <Select value={form.side} onChange={set("side")}>
              <option value="BUY">Buy</option>
              <option value="SELL">Sell</option>
              <option value="DIVIDEND">Dividend</option>
            </Select>
          </Field>
          <Field label="Trade date">
            <Input type="date" required max={today()} value={form.trade_date} onChange={set("trade_date")} />
          </Field>
          <Field label={form.side === "DIVIDEND" ? "Shares held" : "Quantity"}>
            <Input type="number" required min="0" step="any" value={form.quantity} onChange={set("quantity")} />
          </Field>
          <Field label={form.side === "DIVIDEND" ? "Dividend per share (₹)" : "Price (₹)"}>
            <Input type="number" required min="0" step="any" value={form.price} onChange={set("price")} />
          </Field>
          <Field label="Charges (₹)" hint="Brokerage, STT, stamp duty, GST">
            <Input type="number" min="0" step="any" value={form.charges} onChange={set("charges")} />
          </Field>
          <Field label="Category">
            <Input list="txn-categories" maxLength={50} value={form.category} onChange={set("category")} />
            <datalist id="txn-categories">
              <option value="Long-term" />
              <option value="Swing" />
              <option value="Dividend" />
              <option value="Speculative" />
            </datalist>
          </Field>
          <Field label="Notes" className="col-span-2">
            <Input maxLength={500} value={form.notes} onChange={set("notes")} />
          </Field>
          <div className="col-span-2 flex justify-end gap-2">
            <Button type="button" variant="ghost" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button type="submit" disabled={busy || !form.symbol}>
              {busy ? <Spinner className="text-white" /> : null} Save
            </Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}

interface ImportResult {
  imported: number;
  valid_rows: number;
  errors: { row: number; error: string }[];
  error_count: number;
  dry_run?: boolean;
}

function ImportDialog({ portfolioId, open, onOpenChange, onDone }: { portfolioId: number; open: boolean; onOpenChange: (o: boolean) => void; onDone: () => void }) {
  const [file, setFile] = React.useState<File | null>(null);
  const [result, setResult] = React.useState<ImportResult | null>(null);
  const [busy, setBusy] = React.useState(false);

  async function run(dry: boolean) {
    if (!file) return;
    setBusy(true);
    try {
      const fd = new FormData();
      fd.append("file", file);
      const r = await api.upload<ImportResult>(`/portfolios/${portfolioId}/import${dry ? "?dry_run=true" : ""}`, fd);
      setResult(r);
      if (!dry && r.imported) {
        toast.success(`Imported ${r.imported} transactions`);
        onDone();
        onOpenChange(false);
      }
    } catch (err) {
      toast.error(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent title="Import transactions (CSV)" description="Columns: date, symbol, exchange, side, quantity, price, charges, category, notes. Nothing is saved if any row is invalid.">
        <div className="space-y-3 text-sm">
          <a className="text-xs text-accent hover:underline" href="/api/v1/portfolios/import-template" download>
            Download template
          </a>
          <Input type="file" accept=".csv,text/csv" aria-label="CSV file" onChange={(e) => { setFile(e.target.files?.[0] ?? null); setResult(null); }} />
          {result ? (
            result.error_count ? (
              <div className="rounded-md border border-down/40 bg-down/10 p-2 text-xs">
                <p className="mb-1 font-medium text-down">{result.error_count} rows have errors — nothing was imported.</p>
                <ul className="max-h-40 space-y-0.5 overflow-y-auto">
                  {result.errors.map((e) => (
                    <li key={`${e.row}-${e.error}`}>Row {e.row}: {e.error}</li>
                  ))}
                </ul>
              </div>
            ) : (
              <Notice>{result.dry_run ? `${result.valid_rows} rows are valid and ready to import.` : `Imported ${result.imported} rows.`}</Notice>
            )
          ) : null}
          <div className="flex justify-end gap-2">
            <Button variant="secondary" disabled={!file || busy} onClick={() => run(true)}>
              Validate
            </Button>
            <Button disabled={!file || busy} onClick={() => run(false)}>
              {busy ? <Spinner className="text-white" /> : <Upload />} Import
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}

export function TransactionsPanel({ portfolioId, canWrite }: { portfolioId: number; canWrite: boolean }) {
  const key = `/portfolios/${portfolioId}/transactions`;
  const { data, error, mutate } = useSWR<TransactionRow[]>(key, fetcher);
  const { mutate: globalMutate } = useSWRConfig();
  const [editing, setEditing] = React.useState<(TxnForm & { id?: number }) | null>(null);
  const [dialogKey, setDialogKey] = React.useState(0);
  const [open, setOpen] = React.useState(false);
  const [importOpen, setImportOpen] = React.useState(false);

  const refreshAll = () => {
    void mutate();
    void globalMutate((k) => typeof k === "string" && k.startsWith(`/portfolios`));
  };
  function edit(t?: TransactionRow) {
    setEditing(t ? { id: t.id, symbol: t.symbol, side: t.side, quantity: String(t.quantity), price: String(t.price), charges: String(t.charges), trade_date: t.trade_date, category: t.category, notes: t.notes } : null);
    setDialogKey((k) => k + 1);
    setOpen(true);
  }
  async function remove(t: TransactionRow) {
    if (!window.confirm(`Delete ${t.side} ${t.quantity} ${t.symbol} on ${t.trade_date}?`)) return;
    try {
      await api.del(`/portfolios/${portfolioId}/transactions/${t.id}`);
      refreshAll();
    } catch (err) {
      toast.error(errorMessage(err));
    }
  }

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center justify-end gap-2">
        <Button asChild size="sm" variant="secondary">
          <a href={`/api/v1/portfolios/${portfolioId}/export`} download>
            <Download /> Export CSV
          </a>
        </Button>
        {canWrite ? (
          <>
            <Button size="sm" variant="secondary" onClick={() => setImportOpen(true)}>
              <Upload /> Import CSV
            </Button>
            <Button size="sm" onClick={() => edit()}>
              <Plus /> Add transaction
            </Button>
          </>
        ) : null}
      </div>
      <ErrorNotice error={error} />
      {!data && !error ? <Skeleton className="h-32" /> : null}
      {data && data.length === 0 ? <EmptyState title="No transactions" description="Add buys, sells and dividends, or import them from a CSV file." /> : null}
      {data && data.length > 0 ? (
        <div className="overflow-x-auto">
          <Table>
            <THead>
              <TR>
                <TH>Date</TH>
                <TH>Symbol</TH>
                <TH>Type</TH>
                <TH className="text-right">Qty</TH>
                <TH className="text-right">Price</TH>
                <TH className="text-right">Charges</TH>
                <TH className="text-right">Value</TH>
                <TH>Category</TH>
                <TH>Notes</TH>
                {canWrite ? <TH /> : null}
              </TR>
            </THead>
            <TBody>
              {data.map((t) => (
                <TR key={t.id}>
                  <TD>{fmtDate(t.trade_date)}</TD>
                  <TD className="font-medium">{t.symbol}</TD>
                  <TD>
                    <Badge variant={t.side === "BUY" ? "up" : t.side === "SELL" ? "down" : "info"}>{t.side}</Badge>
                  </TD>
                  <TD className="num text-right">{fmtNum(t.quantity, 0)}</TD>
                  <TD className="num text-right">{fmtPrice(t.price)}</TD>
                  <TD className="num text-right">{fmtPrice(t.charges)}</TD>
                  <TD className="num text-right">{fmtPrice(t.value)}</TD>
                  <TD className="text-muted">{t.category}</TD>
                  <TD className="max-w-48 truncate text-muted" title={t.notes}>{t.notes}</TD>
                  {canWrite ? (
                    <TD className="whitespace-nowrap text-right">
                      <button type="button" className="rounded p-1 text-muted hover:text-foreground" aria-label={`Edit transaction ${t.id}`} onClick={() => edit(t)}>
                        <Pencil className="size-3.5" />
                      </button>
                      <button type="button" className="rounded p-1 text-muted hover:text-down" aria-label={`Delete transaction ${t.id}`} onClick={() => remove(t)}>
                        <Trash2 className="size-3.5" />
                      </button>
                    </TD>
                  ) : null}
                </TR>
              ))}
            </TBody>
          </Table>
        </div>
      ) : null}
      <TxnDialog key={dialogKey} portfolioId={portfolioId} initial={editing} open={open} onOpenChange={setOpen} onSaved={refreshAll} />
      <ImportDialog key={`imp-${importOpen}`} portfolioId={portfolioId} open={importOpen} onOpenChange={setImportOpen} onDone={refreshAll} />
    </div>
  );
}
