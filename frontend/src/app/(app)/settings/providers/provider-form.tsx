"use client";

import * as React from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { DialogContent } from "@/components/ui/dialog";
import { ErrorNotice, Notice } from "@/components/ui/feedback";
import { Field, Input, Select, Textarea } from "@/components/ui/input";
import { api, errorMessage } from "@/lib/api";

export interface CredentialField {
  key: string;
  label: string;
  secret: boolean;
  required: boolean;
  help: string;
}

export interface ProviderType {
  type: string;
  name: string;
  supports_streaming: boolean;
  is_simulated: boolean;
  default_rest_url: string | null;
  default_ws_url: string | null;
  docs_url: string | null;
  notes: string;
  credential_fields: CredentialField[];
}

export interface ProviderConfig {
  id: number;
  name: string;
  provider_type: string;
  provider_name: string;
  rest_url: string | null;
  ws_url: string | null;
  exchange: string;
  data_interval: string;
  feed_type: string;
  options: Record<string, unknown>;
  credentials: Record<string, string | null>;
  credentials_set: Record<string, boolean>;
  is_active: boolean;
  token_expires_at: string | null;
  session_active: boolean;
  last_test_at: string | null;
  last_test_ok: boolean | null;
  last_test_message: string | null;
  last_test_latency_ms: number | null;
}

const FEED_TYPES: Record<string, [string, string][]> = {
  upstox: [["full", "Full (LTP, OHLC, 5-level depth)"], ["ltpc", "LTP only (more instruments)"], ["full_d30", "Full with 30-level depth"]],
  angelone: [["snap", "Snap quote (depth, OI)"], ["quote", "Quote (OHLC, volume)"], ["ltp", "LTP only"]],
};

export function ProviderForm({ types, existing, onSaved }: { types: ProviderType[]; existing?: ProviderConfig; onSaved: () => void }) {
  const [type, setType] = React.useState(existing?.provider_type ?? types[0]?.type ?? "upstox");
  const meta = types.find((t) => t.type === type);
  const [name, setName] = React.useState(existing?.name ?? "");
  const [restUrl, setRestUrl] = React.useState(existing?.rest_url ?? "");
  const [wsUrl, setWsUrl] = React.useState(existing?.ws_url ?? "");
  const [exchange, setExchange] = React.useState(existing?.exchange ?? "NSE");
  const [interval, setInterval] = React.useState(existing?.data_interval ?? "1minute");
  const [feedType, setFeedType] = React.useState(existing?.feed_type ?? "full");
  const [options, setOptions] = React.useState(JSON.stringify(existing?.options ?? {}, null, 2));
  const [creds, setCreds] = React.useState<Record<string, string>>({});
  const [error, setError] = React.useState<string | null>(null);
  const [busy, setBusy] = React.useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    let parsedOptions: Record<string, unknown>;
    try {
      parsedOptions = options.trim() ? JSON.parse(options) : {};
    } catch {
      setError("Options must be valid JSON");
      return;
    }
    const credentials = Object.fromEntries(Object.entries(creds).filter(([, v]) => v !== ""));
    const payload = {
      name: name || meta?.name || type,
      rest_url: restUrl || null,
      ws_url: wsUrl || null,
      exchange,
      data_interval: interval,
      feed_type: feedType,
      options: parsedOptions,
      credentials,
    };
    setBusy(true);
    try {
      if (existing) await api.patch(`/admin/providers/${existing.id}`, payload);
      else await api.post("/admin/providers", { ...payload, provider_type: type });
      toast.success(existing ? "Provider updated" : "Provider added");
      onSaved();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <DialogContent
      title={existing ? `Edit ${existing.name}` : "Add market data provider"}
      description="Credentials are encrypted on the server and never shown again after saving."
      className="max-w-2xl"
    >
      <form onSubmit={submit} className="space-y-3">
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Provider">
            <Select value={type} disabled={!!existing} onChange={(e) => setType(e.target.value)}>
              {types.map((t) => (
                <option key={t.type} value={t.type}>
                  {t.name}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="Display name">
            <Input value={name} onChange={(e) => setName(e.target.value)} placeholder={meta?.name} required={!!existing} />
          </Field>
        </div>
        {meta?.notes ? <Notice>{meta.notes}</Notice> : null}
        {meta?.is_simulated ? (
          <Notice tone="warn">Development only. Generates synthetic SIM* instruments; everything is labelled SIMULATED and never mixed with real data.</Notice>
        ) : null}
        {!meta?.is_simulated ? (
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="REST API base URL" hint={meta?.default_rest_url ? `Default: ${meta.default_rest_url}` : undefined}>
              <Input value={restUrl} onChange={(e) => setRestUrl(e.target.value)} placeholder={meta?.default_rest_url ?? "https://"} />
            </Field>
            <Field label="WebSocket URL" hint={meta?.default_ws_url ? `Default: ${meta.default_ws_url}` : undefined}>
              <Input value={wsUrl} onChange={(e) => setWsUrl(e.target.value)} placeholder={meta?.default_ws_url ?? "wss://"} />
            </Field>
          </div>
        ) : null}
        <div className="grid gap-3 sm:grid-cols-3">
          <Field label="Exchange">
            <Select value={exchange} onChange={(e) => setExchange(e.target.value)}>
              <option value="NSE">NSE</option>
              <option value="BSE">BSE</option>
            </Select>
          </Field>
          <Field label="Data interval">
            <Select value={interval} onChange={(e) => setInterval(e.target.value)}>
              <option value="tick">Tick</option>
              <option value="1minute">1 minute</option>
              <option value="5minute">5 minutes</option>
            </Select>
          </Field>
          <Field label="Feed type">
            {FEED_TYPES[type] ? (
              <Select value={feedType} onChange={(e) => setFeedType(e.target.value)}>
                {FEED_TYPES[type].map(([v, l]) => (
                  <option key={v} value={v}>
                    {l}
                  </option>
                ))}
              </Select>
            ) : (
              <Input value={feedType} onChange={(e) => setFeedType(e.target.value)} />
            )}
          </Field>
        </div>
        {meta?.credential_fields.length ? (
          <div className="space-y-3 rounded-md border border-border p-3">
            <p className="text-xs font-medium">Credentials</p>
            <div className="grid gap-3 sm:grid-cols-2">
              {meta.credential_fields.map((f) => {
                const isSet = existing?.credentials_set?.[f.key];
                const masked = existing?.credentials?.[f.key];
                return (
                  <Field key={f.key} label={`${f.label}${f.required ? "" : " (optional)"}`} hint={f.help || (isSet ? `Stored: ${masked}. Leave blank to keep.` : undefined)}>
                    <Input
                      type={f.secret ? "password" : "text"}
                      autoComplete="off"
                      value={creds[f.key] ?? ""}
                      placeholder={isSet ? (masked ?? "stored") : ""}
                      onChange={(e) => setCreds({ ...creds, [f.key]: e.target.value })}
                    />
                  </Field>
                );
              })}
            </div>
          </div>
        ) : null}
        <Field
          label="Options (JSON)"
          hint={
            type.startsWith("custom")
              ? 'Paths and field mappings, e.g. {"quote_path": "/v1/quotes?symbols={keys}", "quote_items_path": "data", "quote_map": {"key": "symbol", "ltp": "last"}}'
              : 'Optional, e.g. {"include_fo": true, "test_instrument_keys": ["NSE_INDEX|Nifty 50"]}'
          }
        >
          <Textarea className="font-mono text-xs" rows={5} value={options} onChange={(e) => setOptions(e.target.value)} />
        </Field>
        <ErrorNotice error={error} />
        <div className="flex justify-end gap-2">
          <Button type="submit" disabled={busy}>
            {existing ? "Save changes" : "Add provider"}
          </Button>
        </div>
      </form>
    </DialogContent>
  );
}
