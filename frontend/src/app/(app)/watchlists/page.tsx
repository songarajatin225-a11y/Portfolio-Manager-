"use client";

import * as React from "react";
import useSWR from "swr";
import { toast } from "sonner";
import { Pencil, Plus, Trash2 } from "lucide-react";
import { useAuth } from "@/components/auth/auth-provider";
import { PageHeader } from "@/components/layout/page-header";
import { WatchlistTable } from "@/components/market/watchlist-table";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { EmptyState, ErrorNotice, Skeleton } from "@/components/ui/feedback";
import { api, errorMessage, fetcher } from "@/lib/api";
import { cn } from "@/lib/utils";

interface WatchlistLite {
  id: number;
  name: string;
  item_count: number;
}

export default function WatchlistsPage() {
  const { canWrite } = useAuth();
  const { data, error, isLoading, mutate } = useSWR<WatchlistLite[]>("/watchlists", fetcher);
  const [selected, setSelected] = React.useState<number | null>(null);
  const current = selected ?? data?.[0]?.id ?? null;

  async function create() {
    const name = window.prompt("Watchlist name");
    if (!name) return;
    try {
      const wl = await api.post<WatchlistLite>("/watchlists", { name });
      await mutate();
      setSelected(wl.id);
    } catch (err) {
      toast.error(errorMessage(err));
    }
  }
  async function rename(wl: WatchlistLite) {
    const name = window.prompt("Rename watchlist", wl.name);
    if (!name || name === wl.name) return;
    try {
      await api.patch(`/watchlists/${wl.id}`, { name });
      void mutate();
    } catch (err) {
      toast.error(errorMessage(err));
    }
  }
  async function remove(wl: WatchlistLite) {
    if (!window.confirm(`Delete watchlist "${wl.name}"?`)) return;
    try {
      await api.del(`/watchlists/${wl.id}`);
      setSelected(null);
      void mutate();
    } catch (err) {
      toast.error(errorMessage(err));
    }
  }

  return (
    <>
      <PageHeader
        title="Watchlists"
        description="Custom lists with streaming prices. Instruments in any watchlist are streamed by the feed."
        actions={
          canWrite ? (
            <Button size="sm" onClick={create}>
              <Plus /> New watchlist
            </Button>
          ) : null
        }
      />
      <ErrorNotice error={error} />
      {isLoading ? <Skeleton className="h-40" /> : null}
      {data && data.length === 0 ? (
        <Card>
          <EmptyState title="No watchlists yet" description="Create a watchlist to follow instruments with live prices and alerts." action={canWrite ? <Button size="sm" onClick={create}><Plus /> New watchlist</Button> : undefined} />
        </Card>
      ) : null}
      {data && data.length > 0 && current != null ? (
        <div className="grid gap-4 lg:grid-cols-[220px_1fr]">
          <Card>
            <CardContent className="space-y-0.5 p-2">
              {data.map((wl) => (
                <div key={wl.id} className={cn("group flex items-center justify-between rounded px-2 py-1.5 text-sm", wl.id === current ? "bg-surface-3" : "hover:bg-surface-2")}>
                  <button type="button" className="flex-1 truncate text-left" onClick={() => setSelected(wl.id)}>
                    {wl.name} <span className="text-xs text-subtle">({wl.item_count})</span>
                  </button>
                  {canWrite ? (
                    <span className="hidden gap-0.5 group-hover:flex">
                      <button type="button" className="rounded p-1 text-muted hover:text-foreground" aria-label={`Rename ${wl.name}`} onClick={() => rename(wl)}>
                        <Pencil className="size-3.5" />
                      </button>
                      <button type="button" className="rounded p-1 text-muted hover:text-down" aria-label={`Delete ${wl.name}`} onClick={() => remove(wl)}>
                        <Trash2 className="size-3.5" />
                      </button>
                    </span>
                  ) : null}
                </div>
              ))}
            </CardContent>
          </Card>
          <Card>
            <CardHeader>
              <CardTitle>{data.find((w) => w.id === current)?.name}</CardTitle>
            </CardHeader>
            <CardContent>
              <WatchlistTable id={current} />
            </CardContent>
          </Card>
        </div>
      ) : null}
    </>
  );
}
