"use client";

import * as React from "react";
import Link from "next/link";
import useSWR from "swr";
import { toast } from "sonner";
import * as DropdownMenu from "@radix-ui/react-dropdown-menu";
import { Bell, CheckCheck } from "lucide-react";
import { api, fetcher } from "@/lib/api";
import { useMarketStream } from "@/lib/market-stream";
import { cn, timeAgo } from "@/lib/utils";

export interface AppNotification {
  id: number;
  alert_id: number | null;
  title: string;
  body: string;
  severity: "info" | "warning" | "critical";
  data: { data_status?: string | null; as_of?: string | null; source?: string | null; url?: string; delivery?: Record<string, string> };
  read_at: string | null;
  created_at: string;
}

const DOT = { info: "bg-info", warning: "bg-warn", critical: "bg-down" };

export function NotificationBell() {
  const { data, mutate } = useSWR<{ items: AppNotification[]; unread: number }>("/notifications?limit=15", fetcher, { refreshInterval: 60_000 });
  const { onNotification } = useMarketStream();

  React.useEffect(
    () =>
      onNotification((n) => {
        const show = n.severity === "critical" ? toast.error : n.severity === "warning" ? toast.warning : toast.info;
        show(n.title, { description: n.body });
        void mutate();
      }),
    [onNotification, mutate],
  );

  async function markAll() {
    await api.post("/notifications/read-all");
    void mutate();
  }
  async function open(n: AppNotification) {
    if (!n.read_at) {
      await api.post(`/notifications/${n.id}/read`);
      void mutate();
    }
  }

  const unread = data?.unread ?? 0;
  return (
    <DropdownMenu.Root>
      <DropdownMenu.Trigger asChild>
        <button type="button" className="relative rounded-md p-1.5 text-muted hover:bg-surface-2 hover:text-foreground" aria-label={`Notifications${unread ? ` (${unread} unread)` : ""}`}>
          <Bell className="size-4" />
          {unread ? <span className="absolute -right-0.5 -top-0.5 min-w-4 rounded-full bg-down px-1 text-center text-[9px] font-bold leading-4 text-white">{unread > 99 ? "99+" : unread}</span> : null}
        </button>
      </DropdownMenu.Trigger>
      <DropdownMenu.Portal>
        <DropdownMenu.Content align="end" sideOffset={6} className="z-50 w-[22rem] max-w-[95vw] rounded-lg border border-border bg-surface p-1 shadow-2xl">
          <div className="flex items-center justify-between px-2 py-1.5">
            <span className="text-xs font-semibold">Notifications</span>
            {unread ? (
              <button type="button" onClick={markAll} className="flex items-center gap-1 text-[11px] text-accent hover:underline">
                <CheckCheck className="size-3.5" /> Mark all read
              </button>
            ) : null}
          </div>
          <div className="max-h-96 overflow-y-auto">
            {!data?.items.length ? <p className="px-2 py-6 text-center text-xs text-muted">No notifications yet. Create alerts to be notified.</p> : null}
            {data?.items.map((n) => (
              <DropdownMenu.Item key={n.id} onSelect={() => void open(n)} className={cn("flex cursor-default gap-2 rounded px-2 py-2 text-xs outline-none data-[highlighted]:bg-surface-2", !n.read_at && "bg-surface-2/60")}>
                <span className={cn("mt-1 size-2 shrink-0 rounded-full", n.read_at ? "bg-transparent" : DOT[n.severity])} />
                <span className="min-w-0 flex-1">
                  <span className="block font-medium text-foreground">{n.title}</span>
                  <span className="block text-muted">{n.body}</span>
                  <span className="mt-0.5 block text-[10px] text-subtle">
                    {timeAgo(n.created_at)}
                    {n.data?.data_status ? ` · ${n.data.data_status}` : ""}
                    {n.data?.source ? ` · ${n.data.source}` : ""}
                  </span>
                </span>
              </DropdownMenu.Item>
            ))}
          </div>
          <DropdownMenu.Separator className="my-1 h-px bg-border" />
          <DropdownMenu.Item asChild>
            <Link href="/alerts" className="block rounded px-2 py-1.5 text-center text-xs text-accent outline-none data-[highlighted]:bg-surface-2">
              Manage alerts
            </Link>
          </DropdownMenu.Item>
        </DropdownMenu.Content>
      </DropdownMenu.Portal>
    </DropdownMenu.Root>
  );
}
