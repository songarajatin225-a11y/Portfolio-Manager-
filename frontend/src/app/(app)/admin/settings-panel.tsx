"use client";

import * as React from "react";
import useSWR from "swr";
import { toast } from "sonner";
import { Save } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { ErrorNotice, Notice, Skeleton } from "@/components/ui/feedback";
import { Field, Input, Select } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { api, errorMessage, fetcher } from "@/lib/api";

type Section = Record<string, unknown>;
interface SettingsResponse {
  sections: Record<string, Section>;
  secrets: Record<string, boolean>;
  infrastructure: {
    environment: string;
    database: { host: string; port: number; name: string; configured_via: string };
    redis_configured: boolean;
    worker_mode: string;
    simulated_feed_allowed: boolean;
    anthropic_key_configured: boolean;
    public_registration: boolean;
  };
}

type FieldDef =
  | { key: string; label: string; type: "text" | "number"; hint?: string; step?: string }
  | { key: string; label: string; type: "select"; options: [string, string][]; hint?: string }
  | { key: string; label: string; type: "bool"; hint?: string };

const SECTIONS: { key: string; title: string; description: string; fields: FieldDef[] }[] = [
  {
    key: "ai",
    title: "AI provider",
    description: "Model used by the research assistant, reports and summaries. The API key is set on the server (ANTHROPIC_API_KEY) and never sent to the browser.",
    fields: [
      { key: "enabled", label: "AI features enabled", type: "bool" },
      { key: "provider", label: "Provider", type: "select", options: [["anthropic", "Anthropic Claude"]] },
      {
        key: "model",
        label: "Model",
        type: "select",
        options: [
          ["claude-opus-5-5", "Claude Opus 5.5 (default)"],
          ["claude-sonnet-5-5", "Claude Sonnet 5.5 (lower cost)"],
          ["claude-fable-5-2", "Claude Fable 5.2 (most capable, highest cost)"],
          ["claude-haiku-5-5", "Claude Haiku 5.5 (lowest cost)"],
        ],
      },
      { key: "chat_effort", label: "Assistant effort", type: "select", options: [["low", "Low"], ["medium", "Medium"], ["high", "High"], ["xhigh", "Extra high"]] },
      { key: "report_effort", label: "Research report effort", type: "select", options: [["medium", "Medium"], ["high", "High"], ["xhigh", "Extra high"], ["max", "Max"]] },
      { key: "use_ai_news_sentiment", label: "Use Claude for news sentiment (costs tokens)", type: "bool", hint: "Off = deterministic finance lexicon." },
    ],
  },
  {
    key: "refresh",
    title: "Refresh intervals",
    description: "How often background jobs run and when live data counts as stale.",
    fields: [
      { key: "stale_after_seconds", label: "Mark quotes stale after (seconds, market hours)", type: "number" },
      { key: "quote_poll_seconds", label: "REST quote polling interval (seconds, when no stream)", type: "number" },
      { key: "metrics_refresh_minutes", label: "Screener metrics refresh (minutes)", type: "number" },
      { key: "news_refresh_minutes", label: "News ingestion (minutes)", type: "number" },
      { key: "alert_eval_seconds", label: "Alert evaluation (seconds)", type: "number" },
      { key: "candle_backfill_days", label: "Daily candle backfill depth (days)", type: "number" },
    ],
  },
  {
    key: "market",
    title: "Market",
    description: "Timezone and the universe tracked by the worker.",
    fields: [
      { key: "timezone", label: "Market timezone", type: "select", options: [["Asia/Kolkata", "Asia/Kolkata (IST)"]] },
      { key: "primary_exchange", label: "Primary exchange", type: "select", options: [["NSE", "NSE"], ["BSE", "BSE"]] },
      { key: "benchmark_index", label: "Benchmark index code", type: "text", hint: "Used for relative strength and portfolio comparison (e.g. NIFTY50)." },
      { key: "tracked_universe_index", label: "Tracked universe (index code)", type: "text", hint: "Constituents of this index are backfilled and streamed in addition to watchlists and holdings." },
      { key: "max_stream_instruments", label: "Max streamed instruments", type: "number" },
    ],
  },
  {
    key: "ml",
    title: "Machine learning",
    description: "Prediction horizon, labelling and model promotion rules.",
    fields: [
      { key: "horizon_days", label: "Prediction horizon (trading days)", type: "number" },
      { key: "neutral_band_atr", label: "Neutral band (× ATR%)", type: "number", step: "0.1" },
      { key: "min_training_rows", label: "Minimum training rows", type: "number" },
      { key: "promotion_min_f1_gain", label: "Min F1 gain to replace active model", type: "number", step: "0.01" },
      { key: "auto_retrain_weekly", label: "Retrain weekly (promotes only after validation)", type: "bool" },
    ],
  },
];

function SectionForm({ def, initial, onSaved }: { def: (typeof SECTIONS)[number]; initial: Section; onSaved: () => void }) {
  const [values, setValues] = React.useState<Section>(initial);
  const [busy, setBusy] = React.useState(false);

  async function save() {
    setBusy(true);
    try {
      const payload: Section = {};
      for (const f of def.fields) {
        const v = values[f.key];
        payload[f.key] = f.type === "number" ? Number(v) : v;
      }
      await api.put(`/admin/settings/${def.key}`, { value: payload });
      toast.success(`${def.title} saved`);
      onSaved();
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
          <CardTitle>{def.title}</CardTitle>
          <CardDescription className="mt-0.5">{def.description}</CardDescription>
        </div>
        <Button size="sm" onClick={save} disabled={busy}>
          <Save /> Save
        </Button>
      </CardHeader>
      <CardContent className="grid gap-3 sm:grid-cols-2">
        {def.fields.map((f) => {
          const v = values[f.key];
          if (f.type === "bool") {
            return (
              <div key={f.key} className="flex items-center justify-between gap-3 rounded-md border border-border px-3 py-2">
                <div>
                  <p className="text-xs font-medium">{f.label}</p>
                  {f.hint ? <p className="text-[11px] text-subtle">{f.hint}</p> : null}
                </div>
                <Switch checked={Boolean(v)} onCheckedChange={(c) => setValues({ ...values, [f.key]: c })} aria-label={f.label} />
              </div>
            );
          }
          return (
            <Field key={f.key} label={f.label} hint={f.hint}>
              {f.type === "select" ? (
                <Select value={String(v ?? "")} onChange={(e) => setValues({ ...values, [f.key]: e.target.value })}>
                  {f.options.map(([val, label]) => (
                    <option key={val} value={val}>
                      {label}
                    </option>
                  ))}
                </Select>
              ) : (
                <Input
                  type={f.type}
                  step={f.type === "number" ? (f.step ?? "1") : undefined}
                  value={String(v ?? "")}
                  onChange={(e) => setValues({ ...values, [f.key]: e.target.value })}
                />
              )}
            </Field>
          );
        })}
      </CardContent>
    </Card>
  );
}

function NotificationsForm({ initial, secrets, onSaved }: { initial: Section; secrets: Record<string, boolean>; onSaved: () => void }) {
  const email = (initial.email ?? {}) as Record<string, unknown>;
  const telegram = (initial.telegram ?? {}) as Record<string, unknown>;
  const [e, setE] = React.useState(email);
  const [t, setT] = React.useState(telegram);
  const [smtpPassword, setSmtpPassword] = React.useState("");
  const [botToken, setBotToken] = React.useState("");
  const [busy, setBusy] = React.useState(false);

  async function save() {
    setBusy(true);
    try {
      await api.put("/admin/settings/notifications", { value: { email: { ...e, smtp_port: Number(e.smtp_port) }, telegram: t } });
      const secretPayload: Record<string, string> = {};
      if (smtpPassword) secretPayload.smtp_password = smtpPassword;
      if (botToken) secretPayload.telegram_bot_token = botToken;
      if (Object.keys(secretPayload).length) await api.put("/admin/settings-secrets", secretPayload);
      setSmtpPassword("");
      setBotToken("");
      toast.success("Notification settings saved");
      onSaved();
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
          <CardTitle>Notifications</CardTitle>
          <CardDescription className="mt-0.5">In-app notifications are always on. Email and Telegram are optional; secrets are encrypted at rest and never shown again.</CardDescription>
        </div>
        <Button size="sm" onClick={save} disabled={busy}>
          <Save /> Save
        </Button>
      </CardHeader>
      <CardContent className="grid gap-4 lg:grid-cols-2">
        <div className="space-y-3 rounded-md border border-border p-3">
          <div className="flex items-center justify-between">
            <p className="text-sm font-medium">Email (SMTP)</p>
            <Switch checked={Boolean(e.enabled)} onCheckedChange={(c) => setE({ ...e, enabled: c })} aria-label="Enable email" />
          </div>
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="SMTP host">
              <Input value={String(e.smtp_host ?? "")} onChange={(ev) => setE({ ...e, smtp_host: ev.target.value })} />
            </Field>
            <Field label="Port">
              <Input type="number" value={String(e.smtp_port ?? 587)} onChange={(ev) => setE({ ...e, smtp_port: ev.target.value })} />
            </Field>
            <Field label="Username">
              <Input value={String(e.smtp_username ?? "")} onChange={(ev) => setE({ ...e, smtp_username: ev.target.value })} />
            </Field>
            <Field label="From address">
              <Input value={String(e.from_address ?? "")} onChange={(ev) => setE({ ...e, from_address: ev.target.value })} />
            </Field>
          </div>
          <Field label="SMTP password" hint={secrets.smtp_password ? "A password is stored. Leave blank to keep it." : "Not set."}>
            <Input type="password" value={smtpPassword} onChange={(ev) => setSmtpPassword(ev.target.value)} autoComplete="new-password" />
          </Field>
        </div>
        <div className="space-y-3 rounded-md border border-border p-3">
          <div className="flex items-center justify-between">
            <p className="text-sm font-medium">Telegram</p>
            <Switch checked={Boolean(t.enabled)} onCheckedChange={(c) => setT({ ...t, enabled: c })} aria-label="Enable Telegram" />
          </div>
          <Field label="Chat ID">
            <Input value={String(t.chat_id ?? "")} onChange={(ev) => setT({ ...t, chat_id: ev.target.value })} />
          </Field>
          <Field label="Bot token" hint={secrets.telegram_bot_token ? "A token is stored. Leave blank to keep it." : "Not set."}>
            <Input type="password" value={botToken} onChange={(ev) => setBotToken(ev.target.value)} autoComplete="new-password" />
          </Field>
        </div>
      </CardContent>
    </Card>
  );
}

export function SettingsPanel() {
  const { data, error, isLoading, mutate } = useSWR<SettingsResponse>("/admin/settings", fetcher);
  if (isLoading) return <Skeleton className="h-64" />;
  if (error || !data) return <ErrorNotice error={error ?? "Failed to load settings"} />;
  const infra = data.infrastructure;
  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <div>
            <CardTitle>Infrastructure</CardTitle>
            <CardDescription className="mt-0.5">Read-only. These are set through environment variables because the application needs them before it can read its own settings.</CardDescription>
          </div>
        </CardHeader>
        <CardContent className="grid gap-2 text-xs sm:grid-cols-2 lg:grid-cols-3">
          <p>
            <span className="text-muted">Environment:</span> {infra.environment}
          </p>
          <p>
            <span className="text-muted">Database:</span> {infra.database.name}@{infra.database.host}:{infra.database.port}
          </p>
          <p>
            <span className="text-muted">Database configured via:</span> {infra.database.configured_via}
          </p>
          <p>
            <span className="text-muted">Redis:</span> {infra.redis_configured ? "configured" : "not configured (memory bus)"}
          </p>
          <p>
            <span className="text-muted">Worker:</span> {infra.worker_mode}
          </p>
          <p className="flex items-center gap-1">
            <span className="text-muted">Claude API key:</span>
            <Badge variant={infra.anthropic_key_configured ? "up" : "warn"}>{infra.anthropic_key_configured ? "configured" : "missing"}</Badge>
          </p>
        </CardContent>
      </Card>
      {!infra.anthropic_key_configured ? (
        <Notice tone="warn">AI features need ANTHROPIC_API_KEY set on the backend server. Everything else works without it.</Notice>
      ) : null}
      {SECTIONS.map((def) => (
        <SectionForm
          key={`${def.key}:${JSON.stringify(data.sections[def.key] ?? {})}`}
          def={def}
          initial={data.sections[def.key] ?? {}}
          onSaved={() => mutate()}
        />
      ))}
      <NotificationsForm initial={data.sections.notifications ?? {}} secrets={data.secrets} onSaved={() => mutate()} />
    </div>
  );
}
