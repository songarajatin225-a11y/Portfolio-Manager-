"use client";

import * as React from "react";
import useSWR from "swr";
import { toast } from "sonner";
import { Star } from "lucide-react";
import * as DropdownMenu from "@radix-ui/react-dropdown-menu";
import { useAuth } from "@/components/auth/auth-provider";
import { Button } from "@/components/ui/button";
import { api, errorMessage, fetcher } from "@/lib/api";

interface WatchlistLite {
  id: number;
  name: string;
  item_count: number;
}

export function AddToWatchlist({ instrumentId, symbol }: { instrumentId: number; symbol: string }) {
  const { canWrite } = useAuth();
  const { data, mutate } = useSWR<WatchlistLite[]>("/watchlists", fetcher);
  if (!canWrite) return null;

  async function add(wl: WatchlistLite) {
    try {
      await api.post(`/watchlists/${wl.id}/items`, { instrument_id: instrumentId });
      toast.success(`${symbol} added to ${wl.name}`);
      void mutate();
    } catch (err) {
      toast.error(errorMessage(err));
    }
  }

  async function createAndAdd() {
    const name = window.prompt("New watchlist name");
    if (!name) return;
    try {
      const wl = await api.post<WatchlistLite>("/watchlists", { name });
      await add(wl);
    } catch (err) {
      toast.error(errorMessage(err));
    }
  }

  return (
    <DropdownMenu.Root>
      <DropdownMenu.Trigger asChild>
        <Button size="sm" variant="secondary">
          <Star /> Watchlist
        </Button>
      </DropdownMenu.Trigger>
      <DropdownMenu.Portal>
        <DropdownMenu.Content align="end" sideOffset={4} className="z-50 min-w-48 rounded-md border border-border-strong bg-surface-2 p-1 text-sm shadow-xl">
          {data?.map((wl) => (
            <DropdownMenu.Item key={wl.id} onSelect={() => add(wl)} className="cursor-pointer rounded px-2 py-1.5 outline-none data-[highlighted]:bg-surface-3">
              {wl.name} <span className="text-xs text-subtle">({wl.item_count})</span>
            </DropdownMenu.Item>
          ))}
          {data?.length ? <DropdownMenu.Separator className="my-1 h-px bg-border" /> : null}
          <DropdownMenu.Item onSelect={createAndAdd} className="cursor-pointer rounded px-2 py-1.5 text-accent outline-none data-[highlighted]:bg-surface-3">
            + New watchlist…
          </DropdownMenu.Item>
        </DropdownMenu.Content>
      </DropdownMenu.Portal>
    </DropdownMenu.Root>
  );
}
