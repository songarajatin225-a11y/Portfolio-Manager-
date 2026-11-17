"use client";

import * as React from "react";
import {
  CandlestickSeries,
  ColorType,
  createChart,
  HistogramSeries,
  LineSeries,
  LineStyle,
  type IChartApi,
  type ISeriesApi,
  type Time,
  type UTCTimestamp,
} from "lightweight-charts";
import type { Candle } from "@/lib/types";

const IST_OFFSET_SECONDS = 5.5 * 3600;
const INTRADAY = new Set(["1m", "5m", "15m", "30m", "1h"]);

export type OverlayKey = "sma_20" | "sma_50" | "sma_200" | "ema_20" | "ema_50" | "ema_200" | "bb" | "supertrend" | "vwap";

export const OVERLAY_COLORS: Record<string, string> = {
  sma_20: "#f59e0b",
  sma_50: "#a855f7",
  sma_200: "#ec4899",
  ema_20: "#22d3ee",
  ema_50: "#3b82f6",
  ema_200: "#f97316",
  bb: "#64748b",
  supertrend: "#10b981",
  vwap: "#eab308",
};

function cssVar(name: string, fallback: string): string {
  if (typeof window === "undefined") return fallback;
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback;
}

/** Charts show exchange time (IST): intraday bars are shifted by +5:30, daily bars use the IST calendar date. */
export function toChartTime(iso: string, interval: string): Time {
  const d = new Date(iso);
  if (INTRADAY.has(interval)) return (Math.floor(d.getTime() / 1000) + IST_OFFSET_SECONDS) as UTCTimestamp;
  const ist = new Date(d.getTime() + IST_OFFSET_SECONDS * 1000);
  return ist.toISOString().slice(0, 10);
}

export interface PriceChartProps {
  candles: Candle[];
  interval: string;
  series?: Record<string, (number | null)[] | string[]>;
  overlays?: OverlayKey[];
  showRsi?: boolean;
  showMacd?: boolean;
  levels?: { price: number; label: string; color?: string }[];
  live?: { ltp: number; received_at: string } | null;
  height?: number;
}

export function PriceChart({ candles, interval, series, overlays = [], showRsi, showMacd, levels = [], live, height = 460 }: PriceChartProps) {
  const ref = React.useRef<HTMLDivElement>(null);
  const chartRef = React.useRef<IChartApi | null>(null);
  const candleRef = React.useRef<ISeriesApi<"Candlestick"> | null>(null);
  const lastBarRef = React.useRef<{ time: Time; open: number; high: number; low: number; close: number } | null>(null);

  React.useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const up = cssVar("--up", "#22c55e");
    const down = cssVar("--down", "#ef4444");
    const chart = createChart(el, {
      height,
      layout: {
        background: { type: ColorType.Solid, color: cssVar("--surface", "#0d1117") },
        textColor: cssVar("--muted", "#8b98a9"),
        fontSize: 11,
        panes: { separatorColor: cssVar("--border", "#222c3a") },
      },
      grid: { vertLines: { color: "rgba(34,44,58,0.5)" }, horzLines: { color: "rgba(34,44,58,0.5)" } },
      rightPriceScale: { borderColor: cssVar("--border", "#222c3a") },
      timeScale: { borderColor: cssVar("--border", "#222c3a"), timeVisible: INTRADAY.has(interval), secondsVisible: false },
      crosshair: { mode: 0 },
      autoSize: true,
    });
    chartRef.current = chart;

    const cs = chart.addSeries(CandlestickSeries, { upColor: up, downColor: down, borderVisible: false, wickUpColor: up, wickDownColor: down });
    const data = candles.map((c) => ({ time: toChartTime(c.ts, interval), open: c.open, high: c.high, low: c.low, close: c.close }));
    cs.setData(data);
    candleRef.current = cs;
    lastBarRef.current = data.length ? { ...data[data.length - 1] } : null;

    const vol = chart.addSeries(HistogramSeries, { priceFormat: { type: "volume" }, priceScaleId: "vol" }, 1);
    vol.setData(candles.map((c) => ({ time: toChartTime(c.ts, interval), value: c.volume, color: c.close >= c.open ? "rgba(34,197,94,0.45)" : "rgba(239,68,68,0.45)" })));

    const ts = (series?.ts as string[] | undefined) ?? [];
    const line = (key: string, color: string, pane = 0, opts: Record<string, unknown> = {}) => {
      const values = series?.[key] as (number | null)[] | undefined;
      if (!values || !ts.length) return;
      const s = chart.addSeries(LineSeries, { color, lineWidth: 1, priceLineVisible: false, lastValueVisible: false, ...opts }, pane);
      s.setData(ts.map((t, i) => (values[i] == null ? { time: toChartTime(t, interval) } : { time: toChartTime(t, interval), value: values[i] as number })));
      return s;
    };
    for (const key of overlays) {
      if (key === "bb") {
        line("bb_upper", OVERLAY_COLORS.bb, 0, { lineStyle: LineStyle.Dashed });
        line("bb_middle", OVERLAY_COLORS.bb);
        line("bb_lower", OVERLAY_COLORS.bb, 0, { lineStyle: LineStyle.Dashed });
      } else {
        line(key, OVERLAY_COLORS[key], 0, key === "supertrend" ? { lineWidth: 2 } : {});
      }
    }
    let pane = 2;
    if (showRsi) {
      const r = line("rsi_14", "#a78bfa", pane, { lastValueVisible: true });
      r?.createPriceLine({ price: 70, color: down, lineStyle: LineStyle.Dotted, lineWidth: 1, axisLabelVisible: false, title: "70" });
      r?.createPriceLine({ price: 30, color: up, lineStyle: LineStyle.Dotted, lineWidth: 1, axisLabelVisible: false, title: "30" });
      pane += 1;
    }
    if (showMacd && series?.macd_hist) {
      const h = chart.addSeries(HistogramSeries, { priceLineVisible: false, lastValueVisible: false }, pane);
      const hist = series.macd_hist as (number | null)[];
      h.setData(ts.map((t, i) => (hist[i] == null ? { time: toChartTime(t, interval) } : { time: toChartTime(t, interval), value: hist[i] as number, color: (hist[i] as number) >= 0 ? "rgba(34,197,94,0.6)" : "rgba(239,68,68,0.6)" })));
      line("macd", "#38bdf8", pane);
      line("macd_signal", "#f59e0b", pane);
      pane += 1;
    }
    for (const lv of levels) {
      cs.createPriceLine({ price: lv.price, color: lv.color ?? "#64748b", lineStyle: LineStyle.Dashed, lineWidth: 1, axisLabelVisible: true, title: lv.label });
    }
    const panes = chart.panes();
    panes[0]?.setStretchFactor(4);
    panes[1]?.setStretchFactor(1);
    for (let i = 2; i < panes.length; i++) panes[i].setStretchFactor(1.3);
    chart.timeScale().fitContent();
    return () => {
      chart.remove();
      chartRef.current = null;
      candleRef.current = null;
    };
  }, [candles, interval, series, overlays, showRsi, showMacd, levels, height]);

  // Apply live ticks to the current bar (or start a new intraday bar) without redrawing the chart.
  React.useEffect(() => {
    const cs = candleRef.current;
    const last = lastBarRef.current;
    if (!cs || !last || !live) return;
    const t = toChartTime(live.received_at, interval);
    if (INTRADAY.has(interval)) {
      const step = { "1m": 60, "5m": 300, "15m": 900, "30m": 1800, "1h": 3600 }[interval] ?? 60;
      const bucket = (Math.floor((t as number) / step) * step) as UTCTimestamp;
      if ((bucket as number) > (last.time as number)) {
        lastBarRef.current = { time: bucket, open: live.ltp, high: live.ltp, low: live.ltp, close: live.ltp };
      } else {
        lastBarRef.current = { ...last, high: Math.max(last.high, live.ltp), low: Math.min(last.low, live.ltp), close: live.ltp };
      }
    } else if (t === last.time) {
      lastBarRef.current = { ...last, high: Math.max(last.high, live.ltp), low: Math.min(last.low, live.ltp), close: live.ltp };
    } else if (String(t) > String(last.time)) {
      lastBarRef.current = { time: t, open: live.ltp, high: live.ltp, low: live.ltp, close: live.ltp };
    } else {
      return;
    }
    cs.update(lastBarRef.current!);
  }, [live, interval]);

  return <div ref={ref} style={{ height }} className="w-full" />;
}
