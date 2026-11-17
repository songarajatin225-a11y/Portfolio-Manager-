"use client";

import * as React from "react";
import useSWR from "swr";
import { toast } from "sonner";
import { Pencil, Plus, Trash2 } from "lucide-react";
import { useAuth } from "@/components/auth/auth-provider";
import { PageHeader } from "@/components/layout/page-header";
import type { PortfolioLite } from "@/components/portfolio/types";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Dialog, DialogContent } from "@/components/ui/dialog";
import { EmptyState, ErrorNotice, Skeleton } from "@/components/ui/feedback";
import { Field, Input } from "@/components/ui/input";
import { api, errorMessage, fetcher } from "@/lib/api";
import { changeColor, cn, fmtINR, fmtPct } from "@/lib/utils";
import { PortfolioDetail } from "./portfolio-detail";

interface FormState {
  id?: number;
  name: string;
  description: string;
  benchmark_symbol: string;
}

function PortfolioDialog({ initial, open, onOpenChange, onSaved }: { initial: FormState; open: boolean; onOpenChange: (o: boolean) => void; onSaved: (id: number) => void }) {
  const [form, setForm] = React.useState(initial);
  async function submit(e: React.FormEvent) {
    e.preventDefault();
    try {
      const body = { name: form.name, description: form.description, benchmark_symbol: form.benchmark_symbol };
      const p = form.id ? await api.patch<{ id: number }>(`/portfolios/${form.id}`, body) : await api.post<{ id: number }>("/portfolios", body);
      onSaved(p.id);
      onOpenChange(false);
    } catch (err) {
      toast.error(errorMessage(err));
    }
  }
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent title={form.id ? "Edit portfolio" : "New portfolio"}>
        <form onSubmit={submit} className="space-y-3">
          <Field label="Name">
            <Input required maxLength={100} value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
          </Field>
          <Field label="Description">
            <Input maxLength={500} value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} />
          </Field>
          <Field label="Benchmark symbol" hint="An index symbol from the instrument master; falls back to the platform benchmark if not found.">
            <Input required maxLength={80} value={form.benchmark_symbol} onChange={(e) => setForm({ ...form, benchmark_symbol: e.target.value })} />
          </Field>
          <div className="flex justify-end gap-2">
            <Button type="button" variant="ghost" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button type="submit">Save</Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}

export default function PortfoliosPage() {
  const { canWrite } = useAuth();
  const { data, error, isLoading, mutate } = useSWR<PortfolioLite[]>("/portfolios", fetcher);
  const [selected, setSelected] = React.useState<number | null>(null);
  const [dialog, setDialog] = React.useState<{ key: number; open: boolean; initial: FormState }>({ key: 0, open: false, initial: { name: "", description: "", benchmark_symbol: "NIFTY 50" } });
  const current = data?.find((p) => p.id === selected)?.id ?? data?.[0]?.id ?? null;

  function openDialog(p?: PortfolioLite) {
    setDialog((d) => ({ key: d.key + 1, open: true, initial: p ? { id: p.id, name: p.name, description: p.description, benchmark_symbol: p.benchmark_symbol } : { name: "", description: "", benchmark_symbol: "NIFTY 50" } }));
  }
  async function remove(p: PortfolioLite) {
    if (!window.confirm(`Delete portfolio "${p.name}" and all its transactions?`)) return;
    try {
      await api.del(`/portfolios/${p.id}`);
      setSelected(null);
      void mutate();
    } catch (err) {
      toast.error(errorMessage(err));
    }
  }

  return (
    <>
      <PageHeader
        title="Portfolio Manager"
        description="Multiple portfolios with FIFO cost basis, realised and unrealised P&L, XIRR, time-weighted returns, allocation and rebalancing."
        actions={canWrite ? <Button size="sm" onClick={() => openDialog()}><Plus /> New portfolio</Button> : null}
      />
      <ErrorNotice error={error} />
      {isLoading ? <Skeleton className="h-40" /> : null}
      {data && data.length === 0 ? (
        <Card>
          <EmptyState title="No portfolios yet" description="Create a portfolio, then add transactions or import them from CSV." action={canWrite ? <Button size="sm" onClick={() => openDialog()}><Plus /> New portfolio</Button> : undefined} />
        </Card>
      ) : null}
      {data && data.length > 0 && current != null ? (
        <div className="grid gap-4 xl:grid-cols-[240px_minmax(0,1fr)]">
          <Card className="h-fit">
            <CardContent className="space-y-0.5 p-2">
              {data.map((p) => (
                <div key={p.id} className={cn("group rounded px-2 py-1.5 text-sm", p.id === current ? "bg-surface-3" : "hover:bg-surface-2")}>
                  <div className="flex items-center justify-between">
                    <button type="button" className="flex-1 truncate text-left font-medium" onClick={() => setSelected(p.id)}>
                      {p.name}
                    </button>
                    {canWrite ? (
                      <span className="hidden gap-0.5 group-hover:flex">
                        <button type="button" className="rounded p-1 text-muted hover:text-foreground" aria-label={`Edit ${p.name}`} onClick={() => openDialog(p)}>
                          <Pencil className="size-3.5" />
                        </button>
                        <button type="button" className="rounded p-1 text-muted hover:text-down" aria-label={`Delete ${p.name}`} onClick={() => remove(p)}>
                          <Trash2 className="size-3.5" />
                        </button>
                      </span>
                    ) : null}
                  </div>
                  <button type="button" onClick={() => setSelected(p.id)} className="flex w-full justify-between text-left text-[11px] text-muted">
                    <span className="num">{fmtINR(p.current_value)}</span>
                    <span className={`num ${changeColor(p.xirr_pct)}`}>{p.xirr_pct != null ? `XIRR ${fmtPct(p.xirr_pct, 1)}` : `${p.holdings} holdings`}</span>
                  </button>
                </div>
              ))}
            </CardContent>
          </Card>
          <PortfolioDetail key={current} id={current} canWrite={canWrite} />
        </div>
      ) : null}
      <PortfolioDialog
        key={dialog.key}
        initial={dialog.initial}
        open={dialog.open}
        onOpenChange={(o) => setDialog((d) => ({ ...d, open: o }))}
        onSaved={(id) => {
          setSelected(id);
          void mutate();
        }}
      />
    </>
  );
}
