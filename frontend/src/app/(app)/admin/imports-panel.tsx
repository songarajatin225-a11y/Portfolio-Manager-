"use client";

import * as React from "react";
import useSWR from "swr";
import { toast } from "sonner";
import { Download, RefreshCw, Upload } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { ErrorNotice, Spinner } from "@/components/ui/feedback";
import { Field, Input, Select } from "@/components/ui/input";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { api, apiRequest, errorMessage, fetcher } from "@/lib/api";
import { fmtDateTime } from "@/lib/utils";

const DATASETS: { key: string; label: string; help: string }[] = [
  { key: "fundamentals", label: "Financial statements", help: "Quarterly/annual figures in INR crore (shares in crore). Each row needs a source; rows failing validation are stored but excluded from ratios." },
  { key: "shareholding", label: "Shareholding pattern", help: "Promoter / FII / DII / public percentages per quarter end." },
  { key: "institutional_flows", label: "FII / DII flows", help: "Daily buy, sell and net values (INR crore) by category." },
  { key: "bulk_block_deals", label: "Bulk & block deals", help: "Exchange-reported large deals." },
  { key: "candles", label: "Historical candles", help: "OHLCV bars you have obtained legitimately (e.g. exchange bhavcopy files). Labelled HISTORICAL with the file name as source." },
];

interface ImportResult {
  dataset: string;
  rows: number;
  imported: number;
  rejected: number;
  errors: { line: number; error: string }[];
  warnings: { line: number; warning: string }[];
  candles_stored?: number;
}

export function ImportsPanel() {
  const [dataset, setDataset] = React.useState("fundamentals");
  const [file, setFile] = React.useState<File | null>(null);
  const [result, setResult] = React.useState<ImportResult | null>(null);
  const [error, setError] = React.useState<string | null>(null);
  const [busy, setBusy] = React.useState(false);
  const fileRef = React.useRef<HTMLInputElement>(null);
  const { data: metrics, mutate } = useSWR<{ state: string; instruments?: number; finished_at?: string; error?: string }>("/admin/metrics/status", fetcher, {
    refreshInterval: (d) => (d?.state === "running" ? 2000 : 30_000),
  });
  const meta = DATASETS.find((d) => d.key === dataset)!;

  async function upload(e: React.FormEvent) {
    e.preventDefault();
    if (!file) return;
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      const form = new FormData();
      form.append("file", file);
      const r = await api.upload<ImportResult>(`/admin/data-import/${dataset}`, form);
      setResult(r);
      toast.success(`Imported ${r.imported} of ${r.rows} rows`);
      if (fileRef.current) fileRef.current.value = "";
      setFile(null);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  async function downloadTemplate() {
    try {
      const res = await apiRequest<Response>(`/admin/data-import/templates/${dataset}`, { raw: true });
      const blob = new Blob([await res.text()], { type: "text/csv" });
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = `${dataset}_template.csv`;
      a.click();
      URL.revokeObjectURL(a.href);
    } catch (err) {
      toast.error(errorMessage(err));
    }
  }

  async function refreshMetrics() {
    try {
      await api.post("/admin/metrics/refresh");
      toast.success("Metrics refresh started");
      void mutate();
    } catch (err) {
      toast.error(errorMessage(err));
    }
  }

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <div>
            <CardTitle>Data imports</CardTitle>
            <CardDescription className="mt-0.5">For data the broker APIs don&apos;t provide. Only import data you are licensed to use; every record keeps its source.</CardDescription>
          </div>
        </CardHeader>
        <CardContent>
          <form onSubmit={upload} className="grid gap-3 md:grid-cols-[220px_1fr_auto] md:items-end">
            <Field label="Dataset">
              <Select value={dataset} onChange={(e) => setDataset(e.target.value)}>
                {DATASETS.map((d) => (
                  <option key={d.key} value={d.key}>
                    {d.label}
                  </option>
                ))}
              </Select>
            </Field>
            <Field label="CSV file" hint={meta.help}>
              <Input ref={fileRef} type="file" accept=".csv,text/csv" className="pt-1.5" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
            </Field>
            <div className="flex gap-2 md:mb-5">
              <Button type="button" variant="secondary" size="sm" onClick={downloadTemplate}>
                <Download /> Template
              </Button>
              <Button type="submit" size="sm" disabled={!file || busy}>
                {busy ? <Spinner className="text-white" /> : <Upload />} Import
              </Button>
            </div>
          </form>
          <ErrorNotice error={error} className="mt-3" />
          {result ? (
            <div className="mt-4 space-y-2">
              <div className="flex flex-wrap gap-2 text-xs">
                <Badge variant="up">{result.imported} imported</Badge>
                <Badge variant={result.rejected ? "down" : "default"}>{result.rejected} rejected</Badge>
                {result.warnings.length ? <Badge variant="warn">{result.warnings.length} warnings</Badge> : null}
                {result.candles_stored != null ? <Badge variant="info">{result.candles_stored} candles stored</Badge> : null}
              </div>
              {result.errors.length || result.warnings.length ? (
                <Table>
                  <THead>
                    <tr>
                      <TH>Line</TH>
                      <TH>Issue</TH>
                    </tr>
                  </THead>
                  <TBody>
                    {result.errors.map((e) => (
                      <TR key={`e${e.line}`}>
                        <TD className="text-xs">{e.line}</TD>
                        <TD className="text-xs text-down">{e.error}</TD>
                      </TR>
                    ))}
                    {result.warnings.map((w) => (
                      <TR key={`w${w.line}`}>
                        <TD className="text-xs">{w.line}</TD>
                        <TD className="text-xs text-warn">{w.warning}</TD>
                      </TR>
                    ))}
                  </TBody>
                </Table>
              ) : null}
            </div>
          ) : null}
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <div>
            <CardTitle>Screener metrics</CardTitle>
            <CardDescription className="mt-0.5">Technical snapshot and fundamental ratios for the tracked universe. The worker refreshes them on the configured interval.</CardDescription>
          </div>
          <Button size="sm" variant="secondary" onClick={refreshMetrics} disabled={metrics?.state === "running"}>
            <RefreshCw className={metrics?.state === "running" ? "animate-spin" : ""} /> Refresh now
          </Button>
        </CardHeader>
        <CardContent className="text-xs text-muted">
          Status: {metrics?.state ?? "idle"}
          {metrics?.instruments != null ? ` · ${metrics.instruments} instruments` : ""}
          {metrics?.finished_at ? ` · finished ${fmtDateTime(metrics.finished_at)}` : ""}
          {metrics?.error ? <span className="text-down"> · {metrics.error}</span> : null}
        </CardContent>
      </Card>
    </div>
  );
}
