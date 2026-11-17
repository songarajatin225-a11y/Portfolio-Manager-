"use client";

import * as React from "react";
import Link from "next/link";
import useSWR from "swr";
import { toast } from "sonner";
import { Play, Plus, Save, Trash2, X } from "lucide-react";
import { useAuth } from "@/components/auth/auth-provider";
import { PageHeader } from "@/components/layout/page-header";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { EmptyState, ErrorNotice, Spinner } from "@/components/ui/feedback";
import { Input, Select } from "@/components/ui/input";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { api, errorMessage, fetcher } from "@/lib/api";
import { changeColor, cn, fmtDateTime, fmtNum, fmtPrice } from "@/lib/utils";
import type { ScreenField, ScreenFilter, ScreenRequest, ScreenResult } from "./types";

interface FieldsResponse {
  fields: ScreenField[];
  presets: { name: string; filters: ScreenFilter[]; sort: string; direction?: "asc" | "desc" }[];
}
interface Saved {
  id: number;
  name: string;
  definition: ScreenRequest;
}

const CATEGORY_LABEL = { technical: "Technical", fundamental: "Fundamental", ai_ml: "AI / ML", meta: "Classification" };
const NUM_OPS: [ScreenFilter["op"], string][] = [
  ["gt", ">"],
  ["gte", "≥"],
  ["lt", "<"],
  ["lte", "≤"],
  ["between", "between"],
];

function FilterRow({ f, fields, onChange, onRemove }: { f: ScreenFilter; fields: ScreenField[]; onChange: (f: ScreenFilter) => void; onRemove: () => void }) {
  const def = fields.find((x) => x.key === f.field);
  return (
    <div className="flex flex-wrap items-center gap-2">
      <Select
        className="h-8 w-64"
        value={f.field}
        onChange={(e) => {
          const nd = fields.find((x) => x.key === e.target.value);
          onChange({ field: e.target.value, op: nd?.type === "boolean" ? "is_true" : nd?.type === "string" ? "eq" : "gt", value: nd?.type === "number" ? 0 : "" });
        }}
        aria-label="Field"
      >
        {(["technical", "fundamental", "ai_ml", "meta"] as const).map((cat) => (
          <optgroup key={cat} label={CATEGORY_LABEL[cat]}>
            {fields
              .filter((x) => x.category === cat)
              .map((x) => (
                <option key={x.key} value={x.key}>
                  {x.label}
                </option>
              ))}
          </optgroup>
        ))}
      </Select>
      {def?.type === "boolean" ? (
        <Select className="h-8 w-28" value={f.op} onChange={(e) => onChange({ ...f, op: e.target.value as ScreenFilter["op"] })} aria-label="Condition">
          <option value="is_true">is true</option>
          <option value="is_false">is false</option>
        </Select>
      ) : def?.type === "string" ? (
        <>
          <Select className="h-8 w-24" value={f.op} onChange={(e) => onChange({ ...f, op: e.target.value as ScreenFilter["op"] })} aria-label="Condition">
            <option value="eq">is</option>
            <option value="neq">is not</option>
          </Select>
          <Input className="h-8 w-48" value={String(f.value ?? "")} onChange={(e) => onChange({ ...f, value: e.target.value })} aria-label="Value" />
        </>
      ) : (
        <>
          <Select className="h-8 w-24" value={f.op} onChange={(e) => onChange({ ...f, op: e.target.value as ScreenFilter["op"] })} aria-label="Condition">
            {NUM_OPS.map(([v, l]) => (
              <option key={v} value={v}>
                {l}
              </option>
            ))}
          </Select>
          <Input className="h-8 w-28" type="number" step="any" value={String(f.value ?? "")} onChange={(e) => onChange({ ...f, value: e.target.value === "" ? null : Number(e.target.value) })} aria-label="Value" />
          {f.op === "between" ? (
            <>
              <span className="text-xs text-muted">and</span>
              <Input className="h-8 w-28" type="number" step="any" value={String(f.value2 ?? "")} onChange={(e) => onChange({ ...f, value2: e.target.value === "" ? null : Number(e.target.value) })} aria-label="Upper value" />
            </>
          ) : null}
          {def?.unit ? <span className="text-xs text-subtle">{def.unit}</span> : null}
        </>
      )}
      <Button size="icon" variant="ghost" onClick={onRemove} aria-label="Remove filter">
        <X />
      </Button>
    </div>
  );
}

function fmtVal(v: number | boolean | string | null | undefined, f?: ScreenField): string {
  if (v == null) return "—";
  if (typeof v === "boolean") return v ? "yes" : "no";
  if (typeof v === "string") return v;
  if (f?.unit === "%") return `${fmtNum(v)}%`;
  if (f?.unit === "x") return `${fmtNum(v)}×`;
  return fmtNum(v);
}

export default function ScreenerPage() {
  const { canWrite } = useAuth();
  const { data: meta } = useSWR<FieldsResponse>("/screener/fields", fetcher);
  const { data: saved, mutate: mutateSaved } = useSWR<Saved[]>("/screener/saved", fetcher);
  const initial: ScreenRequest = { filters: [{ field: "rsi_14", op: "between", value: 40, value2: 70 }], sort: "ret_3m", direction: "desc", limit: 100 };
  const [req, setReq] = React.useState<ScreenRequest>(initial);
  const [submitted, setSubmitted] = React.useState<ScreenRequest>(initial);
  const { data: result, error: runError, isValidating: busy, mutate: rerun } = useSWR<ScreenResult>(["screen", JSON.stringify(submitted)], () =>
    api.post<ScreenResult>("/screener/run", submitted),
  );
  const error = runError ? errorMessage(runError) : null;
  const fields = meta?.fields ?? [];
  const fieldMap = Object.fromEntries(fields.map((f) => [f.key, f]));

  function run(r: ScreenRequest) {
    if (JSON.stringify(r) === JSON.stringify(submitted)) void rerun();
    else setSubmitted(structuredClone(r));
  }

  function load(r: ScreenRequest) {
    setReq(r);
    run(r);
  }

  async function saveScreen() {
    const name = window.prompt("Name this screen");
    if (!name) return;
    try {
      await api.post("/screener/saved", { name, definition: req });
      toast.success("Screen saved");
      void mutateSaved();
    } catch (err) {
      toast.error(errorMessage(err));
    }
  }

  const columns = [...new Set(["change_pct", "ret_3m", "rsi_14", ...req.filters.map((f) => f.field).filter((f) => fieldMap[f]?.type !== "boolean"), req.sort])].filter((c) => c !== "sector").slice(0, 7);

  return (
    <>
      <PageHeader title="Stock Screener" description="Filter the tracked universe on technical, fundamental and AI/ML fields. Fundamental fields only exist for stocks with validated statements." />
      <div className="grid gap-4 lg:grid-cols-[1fr_260px]">
        <Card>
          <CardHeader>
            <CardTitle>Filters</CardTitle>
            <div className="flex gap-2">
              <Button size="sm" variant="secondary" onClick={() => setReq({ ...req, filters: [...req.filters, { field: "ret_1m", op: "gt", value: 0 }] })}>
                <Plus /> Add filter
              </Button>
              {canWrite ? (
                <Button size="sm" variant="secondary" onClick={saveScreen}>
                  <Save /> Save
                </Button>
              ) : null}
              <Button size="sm" onClick={() => run(req)} disabled={busy}>
                {busy ? <Spinner className="text-white" /> : <Play />} Run
              </Button>
            </div>
          </CardHeader>
          <CardContent className="space-y-2">
            {req.filters.length === 0 ? <p className="text-xs text-muted">No filters — the whole universe matches.</p> : null}
            {req.filters.map((f, i) => (
              <FilterRow key={i} f={f} fields={fields} onChange={(nf) => setReq({ ...req, filters: req.filters.map((x, j) => (j === i ? nf : x)) })} onRemove={() => setReq({ ...req, filters: req.filters.filter((_, j) => j !== i) })} />
            ))}
            <div className="flex flex-wrap items-center gap-2 border-t border-border pt-3 text-xs">
              <span className="text-muted">Sort by</span>
              <Select className="h-8 w-56" value={req.sort} onChange={(e) => setReq({ ...req, sort: e.target.value })} aria-label="Sort by">
                {fields
                  .filter((f) => f.type !== "boolean")
                  .map((f) => (
                    <option key={f.key} value={f.key}>
                      {f.label}
                    </option>
                  ))}
              </Select>
              <Select className="h-8 w-32" value={req.direction} onChange={(e) => setReq({ ...req, direction: e.target.value as "asc" | "desc" })} aria-label="Direction">
                <option value="desc">Descending</option>
                <option value="asc">Ascending</option>
              </Select>
            </div>
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle>Presets & saved</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3 text-xs">
            <div className="space-y-1">
              {meta?.presets.map((p) => (
                <button key={p.name} type="button" className="block w-full rounded px-2 py-1 text-left hover:bg-surface-2" onClick={() => load({ filters: p.filters, sort: p.sort, direction: p.direction ?? "desc", limit: 100 })}>
                  {p.name}
                </button>
              ))}
            </div>
            <div className="border-t border-border pt-2">
              <p className="mb-1 font-medium text-muted">Saved screens</p>
              {saved?.length ? null : <p className="text-subtle">None yet.</p>}
              {saved?.map((s) => (
                <div key={s.id} className="flex items-center justify-between gap-1">
                  <button type="button" className="flex-1 truncate rounded px-2 py-1 text-left hover:bg-surface-2" onClick={() => load(s.definition)}>
                    {s.name}
                  </button>
                  {canWrite ? (
                    <Button
                      size="icon"
                      variant="ghost"
                      aria-label={`Delete ${s.name}`}
                      onClick={async () => {
                        await api.del(`/screener/saved/${s.id}`);
                        void mutateSaved();
                      }}
                    >
                      <Trash2 />
                    </Button>
                  ) : null}
                </div>
              ))}
            </div>
          </CardContent>
        </Card>
      </div>
      <Card className="mt-4">
        <CardHeader>
          <div>
            <CardTitle>Results</CardTitle>
            <p className="mt-0.5 text-xs text-muted">{result ? `${result.matched} of ${result.universe} match` : "Run a screen"}</p>
          </div>
          {result?.data_mode === "simulated" ? <Badge variant="simulated">simulated data</Badge> : null}
        </CardHeader>
        <CardContent className="p-0">
          <ErrorNotice error={error} className="m-4" />
          {result && result.results.length === 0 ? <EmptyState title="No matches" description="Loosen the filters or check that metrics have been computed." /> : null}
          {result?.results.length ? (
            <Table>
              <THead>
                <tr>
                  <TH>Instrument</TH>
                  <TH className="text-right">Price</TH>
                  {columns.map((c) => (
                    <TH key={c} className="text-right">
                      {fieldMap[c]?.label ?? c}
                    </TH>
                  ))}
                  <TH className="hidden text-right lg:table-cell">Metrics as of</TH>
                </tr>
              </THead>
              <TBody>
                {result.results.map((r) => (
                  <TR key={r.instrument_id}>
                    <TD>
                      <Link href={`/stocks/${encodeURIComponent(r.symbol)}`} className="font-medium hover:text-accent">
                        {r.symbol}
                      </Link>
                      <p className="max-w-52 truncate text-[11px] text-muted">{r.sector ?? r.name}</p>
                    </TD>
                    <TD className="num text-right text-xs">{fmtPrice(r.live?.ltp ?? (r.values.last_close as number))}</TD>
                    {columns.map((c) => (
                      <TD key={c} className={cn("num text-right text-xs", fieldMap[c]?.unit === "%" ? changeColor(r.values[c] as number) : "")}>
                        {fmtVal(c === "change_pct" && r.live?.change_pct != null ? r.live.change_pct : r.values[c], fieldMap[c])}
                      </TD>
                    ))}
                    <TD className="hidden text-right text-[11px] text-subtle lg:table-cell">{fmtDateTime(r.metrics_as_of)}</TD>
                  </TR>
                ))}
              </TBody>
            </Table>
          ) : null}
        </CardContent>
      </Card>
    </>
  );
}
