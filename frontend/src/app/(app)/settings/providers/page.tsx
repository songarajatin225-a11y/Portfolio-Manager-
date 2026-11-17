"use client";

import * as React from "react";
import useSWR from "swr";
import { useRouter, useSearchParams } from "next/navigation";
import { toast } from "sonner";
import { Database, KeyRound, Pencil, Play, Plus, Power, Trash2, Zap } from "lucide-react";
import { useAuth } from "@/components/auth/auth-provider";
import { PageHeader } from "@/components/layout/page-header";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Dialog, DialogTrigger } from "@/components/ui/dialog";
import { EmptyState, ErrorNotice, Notice, Skeleton, Spinner } from "@/components/ui/feedback";
import { api, errorMessage, fetcher } from "@/lib/api";
import { fmtDateTime } from "@/lib/utils";
import { FeedHealthCard } from "./feed-health";
import { HolidaysCard, InstrumentsCard } from "./instruments-card";
import { ProviderForm, type ProviderConfig, type ProviderType } from "./provider-form";

interface SyncStatus {
  state: "idle" | "running" | "completed" | "failed";
  provider_id?: number;
  started_at?: string;
  finished_at?: string;
  result?: { received: number; instruments: number };
  error?: string;
}

function OAuthToasts() {
  const params = useSearchParams();
  const router = useRouter();
  React.useEffect(() => {
    const connected = params.get("connected");
    const err = params.get("oauth_error");
    if (connected) toast.success("Upstox connected. The session is valid until about 03:30 IST.");
    if (err) toast.error(`Upstox login failed: ${err.replaceAll("_", " ")}`);
    if (connected || err) router.replace("/settings/providers");
  }, [params, router]);
  return null;
}

function ProviderCard({ p, types, onChange, sync }: { p: ProviderConfig; types: ProviderType[]; onChange: () => void; sync?: SyncStatus }) {
  const [busy, setBusy] = React.useState<string | null>(null);
  const [editOpen, setEditOpen] = React.useState(false);
  const syncing = sync?.state === "running" && sync.provider_id === p.id;

  async function run(label: string, fn: () => Promise<unknown>, success?: string) {
    setBusy(label);
    try {
      await fn();
      if (success) toast.success(success);
      onChange();
    } catch (err) {
      toast.error(errorMessage(err));
    } finally {
      setBusy(null);
    }
  }

  async function testConnection() {
    setBusy("test");
    try {
      const r = await api.post<{ ok: boolean; message: string; latency_ms: number | null }>(`/admin/providers/${p.id}/test`);
      if (r.ok) toast.success(`${r.message}${r.latency_ms ? ` (${r.latency_ms} ms)` : ""}`);
      else toast.error(r.message);
      onChange();
    } catch (err) {
      toast.error(errorMessage(err));
    } finally {
      setBusy(null);
    }
  }

  async function connectUpstox() {
    setBusy("oauth");
    try {
      const { url } = await api.get<{ url: string }>(`/admin/providers/${p.id}/oauth-url`);
      window.location.assign(url);
    } catch (err) {
      toast.error(errorMessage(err));
      setBusy(null);
    }
  }

  return (
    <Card className={p.is_active ? "border-accent/60" : undefined}>
      <CardHeader>
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <CardTitle>{p.name}</CardTitle>
            <Badge variant="info">{p.provider_name}</Badge>
            {p.is_active ? <Badge variant="accent">active</Badge> : null}
            {p.provider_type === "simulated" ? <Badge variant="simulated">simulated</Badge> : null}
          </div>
          <p className="mt-1 truncate text-xs text-muted">
            {p.rest_url ?? "—"} {p.ws_url ? ` · ${p.ws_url}` : ""} · {p.exchange} · feed: {p.feed_type}
          </p>
        </div>
        <Dialog open={editOpen} onOpenChange={setEditOpen}>
          <DialogTrigger asChild>
            <Button size="icon" variant="ghost" aria-label={`Edit ${p.name}`}>
              <Pencil />
            </Button>
          </DialogTrigger>
          {editOpen ? (
            <ProviderForm
              types={types}
              existing={p}
              onSaved={() => {
                setEditOpen(false);
                onChange();
              }}
            />
          ) : null}
        </Dialog>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="grid gap-2 text-xs sm:grid-cols-3">
          <p>
            <span className="text-muted">Connection test: </span>
            {p.last_test_at ? (
              <span className={p.last_test_ok ? "text-up" : "text-down"} title={p.last_test_message ?? ""}>
                {p.last_test_ok ? "passed" : "failed"} {fmtDateTime(p.last_test_at)}
                {p.last_test_latency_ms ? ` · ${p.last_test_latency_ms} ms` : ""}
              </span>
            ) : (
              "not run"
            )}
          </p>
          <p>
            <span className="text-muted">Session: </span>
            {p.provider_type === "simulated" ? "n/a" : p.session_active ? `active${p.token_expires_at ? ` until ${fmtDateTime(p.token_expires_at)}` : ""}` : "not logged in"}
          </p>
          <p className="truncate">
            <span className="text-muted">Credentials: </span>
            {Object.entries(p.credentials_set).length
              ? Object.entries(p.credentials_set)
                  .map(([k, v]) => `${k} ${v ? "✓" : "—"}`)
                  .join(" · ")
              : "none required"}
          </p>
        </div>
        {p.last_test_ok === false && p.last_test_message ? <ErrorNotice error={p.last_test_message} /> : null}
        <div className="flex flex-wrap gap-2">
          <Button size="sm" variant="secondary" onClick={testConnection} disabled={!!busy}>
            {busy === "test" ? <Spinner /> : <Zap />} Test connection
          </Button>
          {p.provider_type === "upstox" ? (
            <Button size="sm" variant="secondary" onClick={connectUpstox} disabled={!!busy}>
              <KeyRound /> Connect Upstox
            </Button>
          ) : null}
          {p.is_active ? (
            <Button size="sm" variant="outline" disabled={!!busy} onClick={() => run("deactivate", () => api.post(`/admin/providers/${p.id}/deactivate`), "Provider deactivated")}>
              <Power /> Deactivate
            </Button>
          ) : (
            <Button size="sm" disabled={!!busy} onClick={() => run("activate", () => api.post(`/admin/providers/${p.id}/activate`), "Provider activated; the worker is reconnecting")}>
              <Play /> Activate
            </Button>
          )}
          <Button size="sm" variant="outline" disabled={!!busy || syncing} onClick={() => run("sync", () => api.post(`/admin/providers/${p.id}/sync-instruments`), "Instrument sync started")}>
            {syncing ? <Spinner /> : <Database />} {syncing ? "Syncing instruments…" : "Sync instruments"}
          </Button>
          <Button
            size="sm"
            variant="ghost"
            className="text-down hover:text-down"
            disabled={!!busy}
            onClick={() => {
              if (window.confirm(`Delete provider "${p.name}"? Its stored credentials are removed.`)) {
                void run("delete", () => api.del(`/admin/providers/${p.id}`), "Provider deleted");
              }
            }}
          >
            <Trash2 /> Delete
          </Button>
        </div>
        {sync && sync.provider_id === p.id && sync.state !== "running" && sync.state !== "idle" ? (
          <p className={`text-xs ${sync.state === "failed" ? "text-down" : "text-muted"}`}>
            Last instrument sync {sync.state} {fmtDateTime(sync.finished_at)}
            {sync.result ? ` · ${sync.result.instruments.toLocaleString()} instruments` : ""}
            {sync.error ? ` · ${sync.error}` : ""}
          </p>
        ) : null}
      </CardContent>
    </Card>
  );
}

export default function ProvidersPage() {
  const { isAdmin } = useAuth();
  const { data: types } = useSWR<ProviderType[]>(isAdmin ? "/admin/providers/types" : null, fetcher);
  const { data: providers, error, isLoading, mutate } = useSWR<ProviderConfig[]>(isAdmin ? "/admin/providers" : null, fetcher);
  const { data: sync, mutate: mutateSync } = useSWR<SyncStatus>(isAdmin ? "/admin/providers/sync-status" : null, fetcher, {
    refreshInterval: (d) => (d?.state === "running" ? 1500 : 15_000),
  });
  const [addOpen, setAddOpen] = React.useState(false);
  const origin = React.useSyncExternalStore(
    () => () => {},
    () => window.location.origin,
    () => "",
  );

  if (!isAdmin) {
    return <EmptyState title="Administrators only" description="Ask an administrator to configure market data providers." />;
  }
  const refresh = () => {
    void mutate();
    void mutateSync();
  };

  return (
    <>
      <React.Suspense fallback={null}>
        <OAuthToasts />
      </React.Suspense>
      <PageHeader
        title="Data Provider Settings"
        description="Configure licensed market data sources. Switching providers needs no code changes."
        actions={
          <Dialog open={addOpen} onOpenChange={setAddOpen}>
            <DialogTrigger asChild>
              <Button size="sm" disabled={!types}>
                <Plus /> Add provider
              </Button>
            </DialogTrigger>
            {addOpen && types ? (
              <ProviderForm
                types={types}
                onSaved={() => {
                  setAddOpen(false);
                  refresh();
                }}
              />
            ) : null}
          </Dialog>
        }
      />
      <div className="space-y-4">
        <FeedHealthCard />
        <Notice>
          Broker APIs provide market data for the account holder&apos;s own use. Showing live NSE/BSE prices to other people requires an exchange data licence — keep this
          deployment private or use a licensed vendor feed.
        </Notice>
        <ErrorNotice error={error} />
        {isLoading ? <Skeleton className="h-32" /> : null}
        {providers && providers.length === 0 ? (
          <Card>
            <EmptyState
              title="No providers configured"
              description="Add Upstox or Angel One with your API credentials, or a custom REST/WebSocket vendor. Then test the connection, sync instruments and activate it."
            />
          </Card>
        ) : null}
        {providers?.map((p) => <ProviderCard key={p.id} p={p} types={types ?? []} onChange={refresh} sync={sync} />)}
        <InstrumentsCard />
        <HolidaysCard />
        <Card>
          <CardHeader>
            <CardTitle>Upstox redirect URI</CardTitle>
          </CardHeader>
          <CardContent className="text-xs text-muted">
            Register <code className="rounded bg-surface-3 px-1 py-0.5 text-foreground">{origin}/api/v1/admin/providers/upstox/callback</code> as the redirect URL in the Upstox
            developer console and enter the same value in the provider&apos;s Redirect URI field.
          </CardContent>
        </Card>
      </div>
    </>
  );
}
