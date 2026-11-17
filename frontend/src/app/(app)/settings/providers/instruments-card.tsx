"use client";

import * as React from "react";
import useSWR from "swr";
import { toast } from "sonner";
import { Trash2, Upload } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Field, Input } from "@/components/ui/input";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { api, errorMessage, fetcher } from "@/lib/api";
import { fmtDate } from "@/lib/utils";

interface Summary {
  instruments: { simulated: boolean; segment: string; count: number }[];
  provider_mappings: Record<string, number>;
  index_constituents: Record<string, number>;
}

export function InstrumentsCard() {
  const { data, mutate } = useSWR<Summary>("/instruments/summary", fetcher, { refreshInterval: 15_000 });
  const [indexCode, setIndexCode] = React.useState("NIFTY50");
  const [file, setFile] = React.useState<File | null>(null);
  const [busy, setBusy] = React.useState(false);
  const fileRef = React.useRef<HTMLInputElement>(null);

  async function upload(e: React.FormEvent) {
    e.preventDefault();
    if (!file) return;
    setBusy(true);
    try {
      const form = new FormData();
      form.append("file", file);
      form.append("index_code", indexCode.toUpperCase());
      const res = await api.upload<{ matched: number; added: number; removed: number; unmatched_symbols: string[] }>("/instruments/import-constituents", form);
      toast.success(`Imported ${res.matched} constituents (+${res.added} / -${res.removed})${res.unmatched_symbols.length ? `; unmatched: ${res.unmatched_symbols.slice(0, 5).join(", ")}` : ""}`);
      setFile(null);
      if (fileRef.current) fileRef.current.value = "";
      void mutate();
    } catch (err) {
      toast.error(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  const real = data?.instruments.filter((i) => !i.simulated) ?? [];
  const sim = data?.instruments.filter((i) => i.simulated) ?? [];
  return (
    <Card>
      <CardHeader>
        <div>
          <CardTitle>Instrument master & index constituents</CardTitle>
          <CardDescription className="mt-0.5">
            Sync instruments from the provider card above. Import NSE index constituent CSVs (columns: Company Name, Industry, Symbol, Series, ISIN Code) to set sectors and the tracked universe.
          </CardDescription>
        </div>
      </CardHeader>
      <CardContent className="grid gap-4 lg:grid-cols-2">
        <div className="space-y-2 text-xs">
          <p className="font-medium">Instruments</p>
          <p className="text-muted">Real: {real.length ? real.map((i) => `${i.segment} ${i.count.toLocaleString()}`).join(" · ") : "none"}</p>
          <p className="text-muted">Simulated: {sim.length ? sim.map((i) => `${i.segment} ${i.count}`).join(" · ") : "none"}</p>
          <p className="text-muted">
            Provider mappings:{" "}
            {Object.keys(data?.provider_mappings ?? {}).length ? Object.entries(data!.provider_mappings).map(([p, c]) => `${p} ${c.toLocaleString()}`).join(" · ") : "none"}
          </p>
          <p className="text-muted">
            Index constituents:{" "}
            {Object.keys(data?.index_constituents ?? {}).length ? Object.entries(data!.index_constituents).map(([p, c]) => `${p} (${c})`).join(" · ") : "none imported"}
          </p>
        </div>
        <form onSubmit={upload} className="space-y-2 rounded-md border border-border p-3">
          <div className="grid gap-2 sm:grid-cols-2">
            <Field label="Index code">
              <Input value={indexCode} onChange={(e) => setIndexCode(e.target.value)} placeholder="NIFTY50" />
            </Field>
            <Field label="Constituents CSV">
              <Input ref={fileRef} type="file" accept=".csv,text/csv" onChange={(e) => setFile(e.target.files?.[0] ?? null)} className="pt-1.5" />
            </Field>
          </div>
          <Button type="submit" size="sm" disabled={!file || busy}>
            <Upload /> Import constituents
          </Button>
        </form>
      </CardContent>
    </Card>
  );
}

interface Holiday {
  id: number;
  exchange: string;
  date: string;
  description: string;
  source: string;
}

export function HolidaysCard() {
  const year = new Date().getFullYear();
  const { data, mutate } = useSWR<Holiday[]>(`/market/holidays?year=${year}`, fetcher);
  const [date, setDate] = React.useState("");
  const [desc, setDesc] = React.useState("");

  async function add(e: React.FormEvent) {
    e.preventDefault();
    try {
      await api.post("/market/holidays", { date, description: desc, exchange: "NSE" });
      setDate("");
      setDesc("");
      void mutate();
    } catch (err) {
      toast.error(errorMessage(err));
    }
  }

  async function remove(id: number) {
    try {
      await api.del(`/market/holidays/${id}`);
      void mutate();
    } catch (err) {
      toast.error(errorMessage(err));
    }
  }

  return (
    <Card>
      <CardHeader>
        <div>
          <CardTitle>Market holidays {year}</CardTitle>
          <CardDescription className="mt-0.5">Synced from Upstox when it is the active provider; add exchange holidays manually otherwise. Used for market status and stale-data detection.</CardDescription>
        </div>
      </CardHeader>
      <CardContent className="space-y-3">
        <form onSubmit={add} className="flex flex-wrap items-end gap-2">
          <Field label="Date">
            <Input type="date" required value={date} onChange={(e) => setDate(e.target.value)} className="w-40" />
          </Field>
          <Field label="Description" className="min-w-48 flex-1">
            <Input value={desc} onChange={(e) => setDesc(e.target.value)} />
          </Field>
          <Button type="submit" size="sm">
            Add holiday
          </Button>
        </form>
        <Table>
          <THead>
            <tr>
              <TH>Date</TH>
              <TH>Exchange</TH>
              <TH>Description</TH>
              <TH>Source</TH>
              <TH />
            </tr>
          </THead>
          <TBody>
            {data?.length ? (
              data.map((h) => (
                <TR key={h.id}>
                  <TD className="text-xs">{fmtDate(h.date)}</TD>
                  <TD className="text-xs">{h.exchange}</TD>
                  <TD className="text-xs">{h.description}</TD>
                  <TD className="text-xs text-muted">{h.source}</TD>
                  <TD className="text-right">
                    <Button size="icon" variant="ghost" onClick={() => remove(h.id)} aria-label="Delete holiday">
                      <Trash2 />
                    </Button>
                  </TD>
                </TR>
              ))
            ) : (
              <TR>
                <TD colSpan={5} className="text-center text-xs text-muted">
                  No holidays recorded for {year}.
                </TD>
              </TR>
            )}
          </TBody>
        </Table>
      </CardContent>
    </Card>
  );
}
