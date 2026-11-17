"use client";

import * as React from "react";
import useSWR from "swr";
import { toast } from "sonner";
import { Plus, RefreshCw, Trash2 } from "lucide-react";
import { useAuth } from "@/components/auth/auth-provider";
import type { AppNotification } from "@/components/layout/notification-bell";
import { PageHeader } from "@/components/layout/page-header";
import { InstrumentSearch } from "@/components/market/instrument-search";
import type { PortfolioLite } from "@/components/portfolio/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { EmptyState, ErrorNotice, Skeleton, Spinner } from "@/components/ui/feedback";
import { Field, Input, Select } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { api, errorMessage, fetcher } from "@/lib/api";
import { fmtDateTime, timeAgo } from "@/lib/utils";

type ParamSchema = { type: "number" } | { type: "string"; enum: string[] } | { type: "boolean" } | { type: "array"; items: { type: "string" } };
interface AlertType {
  label: string;
  params: Record<string, ParamSchema>;
  needs: "instrument" | "portfolio";
  basis: string;
}
interface TypesResponse {
  types: Record<string, AlertType>;
  channels: { in_app: boolean; email: boolean; telegram: boolean };
}
interface AlertRow {
  id: number;
  name: string;
  alert_type: string;
  type_label: string;
  basis: string;
  symbol: string | null;
  portfolio_id: number | null;
  params: Record<string, unknown>;
  channels: string[];
  is_active: boolean;
  trigger_once: boolean;
  cooldown_minutes: number;
  last_triggered_at: string | null;
  description: string;
}

const PARAM_LABEL: Record<string, string> = {
  price: "Price (₹)",
  threshold_pct: "Threshold (%)",
  direction: "Direction",
  multiple: "Multiple of 20-day average volume",
  level: "RSI level",
  keywords: "Keywords in headline (comma separated, optional)",
  high_impact_only: "High-impact news only",
  min_score: "Minimum anomaly percentile",
};

function CreateAlert({ types, onCreated }: { types: TypesResponse; onCreated: () => void }) {
  const { data: portfolios } = useSWR<PortfolioLite[]>("/portfolios", fetcher);
  const [type, setType] = React.useState("price_above");
  const [symbol, setSymbol] = React.useState("");
  const [portfolioId, setPortfolioId] = React.useState<number | "">("");
  const [params, setParams] = React.useState<Record<string, string | boolean>>({});
  const [channels, setChannels] = React.useState<string[]>([]);
  const [once, setOnce] = React.useState(false);
  const [cooldown, setCooldown] = React.useState("60");
  const [name, setName] = React.useState("");
  const [busy, setBusy] = React.useState(false);
  const def = types.types[type];

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    const body: Record<string, unknown> = {};
    for (const [k, schema] of Object.entries(def.params)) {
      const v = params[k];
      if (schema.type === "number") body[k] = Number(v);
      else if (schema.type === "boolean") body[k] = Boolean(v);
      else if (schema.type === "array") body[k] = String(v ?? "").split(",").map((x) => x.trim()).filter(Boolean);
      else body[k] = v || schema.enum[0];
    }
    setBusy(true);
    try {
      await api.post("/alerts", {
        name,
        alert_type: type,
        symbol: def.needs === "instrument" ? symbol : null,
        portfolio_id: def.needs === "portfolio" ? portfolioId || null : null,
        params: body,
        channels: ["in_app", ...channels],
        trigger_once: once,
        cooldown_minutes: Number(cooldown) || 60,
      });
      toast.success("Alert created");
      setParams({});
      setName("");
      onCreated();
    } catch (err) {
      toast.error(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card>
      <CardHeader>
        <div>
          <CardTitle>New alert</CardTitle>
          <CardDescription className="mt-0.5">Evaluated on: {def.basis}. Alerts fire when the condition becomes true and re-arm once it is false again.</CardDescription>
        </div>
      </CardHeader>
      <CardContent>
        <form onSubmit={submit} className="grid grid-cols-2 gap-3 md:grid-cols-4">
          <Field label="Alert type" className="col-span-2">
            <Select value={type} onChange={(e) => { setType(e.target.value); setParams({}); }}>
              {Object.entries(types.types).map(([k, t]) => <option key={k} value={k}>{t.label}</option>)}
            </Select>
          </Field>
          {def.needs === "instrument" ? (
            <div className="col-span-2">
              <span className="mb-1 block text-xs font-medium text-muted">Instrument</span>
              {symbol ? (
                <div className="flex items-center justify-between rounded-md border border-border-strong bg-surface-2 px-3 py-1.5 text-sm">
                  <span className="font-medium">{symbol}</span>
                  <button type="button" className="text-xs text-accent hover:underline" onClick={() => setSymbol("")}>Change</button>
                </div>
              ) : (
                <InstrumentSearch onSelect={(i) => setSymbol(i.symbol)} />
              )}
            </div>
          ) : (
            <Field label="Portfolio" className="col-span-2">
              <Select required value={portfolioId} onChange={(e) => setPortfolioId(e.target.value ? Number(e.target.value) : "")}>
                <option value="">Choose…</option>
                {portfolios?.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
              </Select>
            </Field>
          )}
          {Object.entries(def.params).map(([k, schema]) =>
            schema.type === "boolean" ? (
              <label key={k} className="col-span-2 flex items-center gap-2 text-xs">
                <Switch checked={Boolean(params[k])} onCheckedChange={(c) => setParams((p) => ({ ...p, [k]: c }))} aria-label={PARAM_LABEL[k] ?? k} />
                {PARAM_LABEL[k] ?? k}
              </label>
            ) : schema.type === "string" ? (
              <Field key={k} label={PARAM_LABEL[k] ?? k}>
                <Select value={String(params[k] ?? schema.enum[0])} onChange={(e) => setParams((p) => ({ ...p, [k]: e.target.value }))}>
                  {schema.enum.map((o) => <option key={o} value={o}>{o}</option>)}
                </Select>
              </Field>
            ) : schema.type === "array" ? (
              <Field key={k} label={PARAM_LABEL[k] ?? k} className="col-span-2">
                <Input value={String(params[k] ?? "")} onChange={(e) => setParams((p) => ({ ...p, [k]: e.target.value }))} placeholder="results, dividend, order" />
              </Field>
            ) : (
              <Field key={k} label={PARAM_LABEL[k] ?? k}>
                <Input type="number" min="0" step="any" required value={String(params[k] ?? "")} onChange={(e) => setParams((p) => ({ ...p, [k]: e.target.value }))} />
              </Field>
            ),
          )}
          <Field label="Name (optional)"><Input maxLength={150} value={name} onChange={(e) => setName(e.target.value)} /></Field>
          <Field label="Cooldown (minutes)"><Input type="number" min="1" max="10080" value={cooldown} onChange={(e) => setCooldown(e.target.value)} /></Field>
          <div className="col-span-2 flex flex-wrap items-center gap-4 text-xs">
            <span className="text-muted">Channels:</span>
            <label className="flex items-center gap-1.5"><input type="checkbox" checked disabled /> In-app</label>
            {(["email", "telegram"] as const).map((c) => (
              <label key={c} className="flex items-center gap-1.5" title={types.channels[c] ? undefined : "Not configured by an administrator"}>
                <input type="checkbox" disabled={!types.channels[c]} checked={channels.includes(c)} onChange={(e) => setChannels((x) => (e.target.checked ? [...x, c] : x.filter((y) => y !== c)))} />
                {c === "email" ? "Email" : "Telegram"}
                {!types.channels[c] ? <span className="text-subtle">(off)</span> : null}
              </label>
            ))}
          </div>
          <label className="col-span-2 flex items-center gap-2 text-xs">
            <Switch checked={once} onCheckedChange={setOnce} aria-label="Trigger once" /> Trigger once, then deactivate
          </label>
          <div className="col-span-full flex justify-end">
            <Button type="submit" disabled={busy || (def.needs === "instrument" && !symbol)}>
              {busy ? <Spinner className="text-white" /> : <Plus />} Create alert
            </Button>
          </div>
        </form>
      </CardContent>
    </Card>
  );
}

export default function AlertsPage() {
  const { canWrite } = useAuth();
  const { data: types, error: typesError } = useSWR<TypesResponse>("/alerts/types", fetcher);
  const { data: alerts, error, mutate } = useSWR<AlertRow[]>("/alerts", fetcher, { refreshInterval: 30_000 });
  const { data: notes, mutate: mutateNotes } = useSWR<{ items: AppNotification[]; unread: number }>("/notifications?limit=100", fetcher, { refreshInterval: 30_000 });
  const [checking, setChecking] = React.useState(false);

  async function toggle(a: AlertRow) {
    try {
      await api.patch(`/alerts/${a.id}`, { is_active: !a.is_active });
      void mutate();
    } catch (err) {
      toast.error(errorMessage(err));
    }
  }
  async function remove(a: AlertRow) {
    if (!window.confirm(`Delete alert "${a.name}"?`)) return;
    try {
      await api.del(`/alerts/${a.id}`);
      void mutate();
    } catch (err) {
      toast.error(errorMessage(err));
    }
  }
  async function checkNow() {
    setChecking(true);
    try {
      const r = await api.post<{ triggered: number }>("/alerts/evaluate");
      toast.info(r.triggered ? `${r.triggered} alert(s) triggered` : "No alerts triggered");
      void mutate();
      void mutateNotes();
    } catch (err) {
      toast.error(errorMessage(err));
    } finally {
      setChecking(false);
    }
  }

  return (
    <>
      <PageHeader
        title="Alerts"
        description="Price, move, volume, RSI, MACD, breakout, stop-loss, portfolio drawdown, news and AI unusual-activity alerts. The background worker checks them continuously."
        actions={canWrite ? <Button size="sm" variant="secondary" onClick={checkNow} disabled={checking}>{checking ? <Spinner /> : <RefreshCw />} Check now</Button> : null}
      />
      <div className="space-y-4">
        <ErrorNotice error={typesError ?? error} />
        {types && canWrite ? <CreateAlert types={types} onCreated={() => void mutate()} /> : null}
        <Card>
          <CardHeader><CardTitle>Your alerts</CardTitle></CardHeader>
          <CardContent>
            {!alerts && !error ? <Skeleton className="h-24" /> : null}
            {alerts && !alerts.length ? <EmptyState title="No alerts" description="Create one above to be notified in-app (and by email or Telegram if configured)." /> : null}
            {alerts?.length ? (
              <div className="overflow-x-auto">
                <Table>
                  <THead>
                    <TR>
                      <TH>Active</TH>
                      <TH>Alert</TH>
                      <TH>Type</TH>
                      <TH>Channels</TH>
                      <TH>Last triggered</TH>
                      <TH>Basis</TH>
                      {canWrite ? <TH /> : null}
                    </TR>
                  </THead>
                  <TBody>
                    {alerts.map((a) => (
                      <TR key={a.id}>
                        <TD><Switch checked={a.is_active} disabled={!canWrite} onCheckedChange={() => toggle(a)} aria-label={`Toggle ${a.name}`} /></TD>
                        <TD>
                          <span className="font-medium">{a.name}</span>
                          {a.name !== a.description ? <span className="block text-[11px] text-muted">{a.description}</span> : null}
                        </TD>
                        <TD className="text-xs">{a.type_label}{a.trigger_once ? <Badge className="ml-1">once</Badge> : null}</TD>
                        <TD className="text-xs text-muted">{a.channels.join(", ")}</TD>
                        <TD className="text-xs" title={fmtDateTime(a.last_triggered_at)}>{a.last_triggered_at ? timeAgo(a.last_triggered_at) : "never"}</TD>
                        <TD className="text-xs text-muted">{a.basis}</TD>
                        {canWrite ? (
                          <TD className="text-right">
                            <button type="button" className="rounded p-1 text-muted hover:text-down" aria-label={`Delete ${a.name}`} onClick={() => remove(a)}>
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
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle>Notification history</CardTitle>
            {notes?.unread ? <Button size="xs" variant="ghost" onClick={async () => { await api.post("/notifications/read-all"); void mutateNotes(); }}>Mark all read</Button> : null}
          </CardHeader>
          <CardContent>
            {notes && !notes.items.length ? <EmptyState title="No notifications yet" /> : null}
            <ul className="divide-y divide-border/60">
              {notes?.items.map((n) => (
                <li key={n.id} className="flex gap-3 py-2 text-xs">
                  <Badge variant={n.severity === "critical" ? "down" : n.severity === "warning" ? "warn" : "info"} className="h-fit">{n.severity}</Badge>
                  <div className="min-w-0 flex-1">
                    <p className={n.read_at ? "text-muted" : "font-medium"}>{n.title}</p>
                    <p className="text-muted">{n.body}</p>
                    <p className="mt-0.5 text-[10px] text-subtle">
                      {fmtDateTime(n.created_at)}
                      {n.data?.data_status ? ` · ${n.data.data_status}` : ""}
                      {n.data?.source ? ` · source: ${n.data.source}` : ""}
                      {n.data?.as_of ? ` · value as of ${fmtDateTime(n.data.as_of)}` : ""}
                      {n.data?.delivery ? ` · ${Object.entries(n.data.delivery).map(([c, s]) => `${c}: ${s}`).join(", ")}` : ""}
                    </p>
                    {n.data?.url ? <a href={n.data.url} target="_blank" rel="noopener noreferrer" className="text-accent hover:underline">Open article</a> : null}
                  </div>
                </li>
              ))}
            </ul>
          </CardContent>
        </Card>
      </div>
    </>
  );
}
