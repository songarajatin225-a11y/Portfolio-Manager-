"use client";

/**
 * Market data stream: one WebSocket per browser tab, authenticated with a short-lived ticket.
 * Quotes live in an external store so only components showing a changed instrument re-render.
 */

import * as React from "react";
import { api } from "@/lib/api";
import type { DataStatus } from "@/lib/types";

export interface Tick {
  instrument_id: number;
  symbol: string | null;
  exchange?: string | null;
  ltp: number;
  change: number | null;
  change_pct: number | null;
  open: number | null;
  high: number | null;
  low: number | null;
  prev_close: number | null;
  volume: number | null;
  vwap: number | null;
  bid: number | null;
  ask: number | null;
  oi?: number | null;
  received_at: string;
  exchange_ts: string | null;
  source: string;
  data_status: DataStatus;
  is_simulated: boolean;
  is_stale?: boolean;
}

export interface FeedStatus {
  state?: string;
  provider_name?: string | null;
  provider_type?: string | null;
  is_simulated?: boolean;
  streaming?: boolean;
  subscribed?: number;
  last_message_at?: string | null;
  last_error?: string | null;
  latency_ms_p50?: number | null;
  latency_ms_p95?: number | null;
  messages_last_minute?: number;
  reconnects?: number;
  stale_after_seconds?: number;
  token_expires_at?: string | null;
  market_status?: Record<string, { status: string; source: string; as_of: string; detail?: string }>;
  invalid_quotes?: number;
  updated_at?: string;
}

export interface NotificationEvent {
  id: number;
  user_id: string;
  title: string;
  body: string;
  severity: "info" | "warning" | "critical";
  created_at: string;
}

class QuoteStore {
  private quotes = new Map<number, Tick>();
  private listeners = new Map<number, Set<() => void>>();
  private anyListeners = new Set<() => void>();

  get(id: number): Tick | undefined {
    return this.quotes.get(id);
  }

  update(ticks: Tick[]) {
    for (const t of ticks) {
      const prev = this.quotes.get(t.instrument_id);
      if (prev && prev.received_at > t.received_at) continue;
      this.quotes.set(t.instrument_id, t);
      this.listeners.get(t.instrument_id)?.forEach((fn) => fn());
    }
    this.anyListeners.forEach((fn) => fn());
  }

  subscribe(id: number, fn: () => void): () => void {
    let set = this.listeners.get(id);
    if (!set) {
      set = new Set();
      this.listeners.set(id, set);
    }
    set.add(fn);
    return () => set!.delete(fn);
  }

  subscribeAny(fn: () => void): () => void {
    this.anyListeners.add(fn);
    return () => this.anyListeners.delete(fn);
  }
}

type ConnState = "idle" | "connecting" | "open" | "closed";

interface StreamContextValue {
  store: QuoteStore;
  connection: ConnState;
  feed: FeedStatus | null;
  watch: (ids: number[]) => () => void;
  onNotification: (fn: (n: NotificationEvent) => void) => () => void;
}

const StreamContext = React.createContext<StreamContextValue | null>(null);

export function MarketStreamProvider({ children }: { children: React.ReactNode }) {
  const [store] = React.useState(() => new QuoteStore());
  const [connection, setConnection] = React.useState<ConnState>("idle");
  const [feed, setFeed] = React.useState<FeedStatus | null>(null);
  const wsRef = React.useRef<WebSocket | null>(null);
  const watchers = React.useRef(new Map<number, number>());
  const notifyListeners = React.useRef(new Set<(n: NotificationEvent) => void>());

  const sendSubscription = React.useCallback(() => {
    const ws = wsRef.current;
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ action: "subscribe", instrument_ids: [...watchers.current.keys()] }));
    }
  }, []);

  const watch = React.useCallback(
    (ids: number[]) => {
      for (const id of ids) watchers.current.set(id, (watchers.current.get(id) ?? 0) + 1);
      sendSubscription();
      return () => {
        for (const id of ids) {
          const n = (watchers.current.get(id) ?? 1) - 1;
          if (n <= 0) watchers.current.delete(id);
          else watchers.current.set(id, n);
        }
        sendSubscription();
      };
    },
    [sendSubscription],
  );

  React.useEffect(() => {
    let stopped = false;
    let attempt = 0;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let pingTimer: ReturnType<typeof setInterval> | undefined;

    async function connect() {
      if (stopped) return;
      setConnection("connecting");
      try {
        const [{ wsUrl }, { ticket }] = await Promise.all([
          fetch("/runtime-config", { cache: "no-store" }).then((r) => r.json() as Promise<{ wsUrl: string }>),
          api.post<{ ticket: string }>("/auth/ws-ticket"),
        ]);
        if (stopped) return;
        const ws = new WebSocket(`${wsUrl}?ticket=${encodeURIComponent(ticket)}`);
        wsRef.current = ws;
        ws.onopen = () => {
          attempt = 0;
          setConnection("open");
          sendSubscription();
          pingTimer = setInterval(() => ws.readyState === WebSocket.OPEN && ws.send(JSON.stringify({ action: "ping" })), 25_000);
        };
        ws.onmessage = (ev) => {
          let msg: { type: string; data: unknown };
          try {
            msg = JSON.parse(ev.data as string);
          } catch {
            return;
          }
          if (msg.type === "ticks") store.update(msg.data as Tick[]);
          else if (msg.type === "feed_status") setFeed(msg.data as FeedStatus);
          else if (msg.type === "notification") notifyListeners.current.forEach((fn) => fn(msg.data as NotificationEvent));
        };
        ws.onclose = () => {
          if (pingTimer) clearInterval(pingTimer);
          wsRef.current = null;
          setConnection("closed");
          scheduleReconnect();
        };
        ws.onerror = () => ws.close();
      } catch {
        setConnection("closed");
        scheduleReconnect();
      }
    }

    function scheduleReconnect() {
      if (stopped) return;
      const delay = Math.min(30_000, 1000 * 2 ** attempt) * (0.5 + Math.random() / 2);
      attempt = Math.min(attempt + 1, 6);
      timer = setTimeout(connect, delay);
    }

    void connect();
    return () => {
      stopped = true;
      if (timer) clearTimeout(timer);
      if (pingTimer) clearInterval(pingTimer);
      wsRef.current?.close();
    };
  }, [store, sendSubscription]);

  const onNotification = React.useCallback((fn: (n: NotificationEvent) => void) => {
    notifyListeners.current.add(fn);
    return () => notifyListeners.current.delete(fn);
  }, []);

  const value = React.useMemo(() => ({ store, connection, feed, watch, onNotification }), [store, connection, feed, watch, onNotification]);
  return <StreamContext.Provider value={value}>{children}</StreamContext.Provider>;
}

export function useMarketStream(): StreamContextValue {
  const ctx = React.useContext(StreamContext);
  if (!ctx) throw new Error("useMarketStream must be used inside MarketStreamProvider");
  return ctx;
}

/** Subscribe to live ticks for these instruments while the component is mounted. */
export function useWatch(ids: number[]) {
  const { watch } = useMarketStream();
  const key = ids.join(",");
  React.useEffect(() => {
    if (!key) return;
    return watch(key.split(",").map(Number));
  }, [key, watch]);
}

/** Latest streamed tick for one instrument (re-renders only when it changes). */
export function useTick(id: number | null | undefined): Tick | undefined {
  const { store } = useMarketStream();
  const subscribe = React.useCallback((fn: () => void) => (id ? store.subscribe(id, fn) : () => {}), [store, id]);
  return React.useSyncExternalStore(subscribe, () => (id ? store.get(id) : undefined), () => undefined);
}

/** Current time that advances every `ms` milliseconds (for "x seconds ago" and staleness). */
export function useNow(ms = 5000): number {
  const subscribe = React.useCallback(
    (fn: () => void) => {
      const t = setInterval(fn, ms);
      return () => clearInterval(t);
    },
    [ms],
  );
  const getSnapshot = React.useCallback(() => Math.floor(Date.now() / ms) * ms, [ms]);
  return React.useSyncExternalStore(subscribe, getSnapshot, () => 0);
}
