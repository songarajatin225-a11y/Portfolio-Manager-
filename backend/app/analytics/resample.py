"""Resample OHLCV bars to coarser intervals (aggregation only - never interpolates)."""

from __future__ import annotations

from datetime import datetime, timedelta

from app.marketdata.calendar import IST
from app.marketdata.types import CandleBar

MINUTES = {"1m": 1, "5m": 5, "15m": 15, "30m": 30, "1h": 60}


def _bucket(ts: datetime, interval: str) -> datetime:
    local = ts.astimezone(IST)
    if interval == "1w":
        start = (local - timedelta(days=local.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    elif interval == "1mo":
        start = local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    elif interval == "1d":
        start = local.replace(hour=0, minute=0, second=0, microsecond=0)
    elif interval in MINUTES:
        # NSE sessions start at 09:15 IST, so align intraday buckets to the session open.
        step = MINUTES[interval]
        session_open = local.replace(hour=9, minute=15, second=0, microsecond=0)
        offset = int((local - session_open).total_seconds() // 60)
        start = session_open + timedelta(minutes=(offset // step) * step)
    else:
        raise ValueError(f"Unsupported interval {interval}")
    return start.astimezone(ts.tzinfo)


def resample_bars(bars: list[CandleBar], interval: str) -> list[CandleBar]:
    out: dict[datetime, CandleBar] = {}
    for b in sorted(bars, key=lambda x: x.ts):
        key = _bucket(b.ts, interval)
        cur = out.get(key)
        if cur is None:
            out[key] = CandleBar(ts=key, open=b.open, high=b.high, low=b.low, close=b.close, volume=b.volume, oi=b.oi)
        else:
            cur.high = max(cur.high, b.high)
            cur.low = min(cur.low, b.low)
            cur.close = b.close
            cur.volume += b.volume
            cur.oi = b.oi if b.oi is not None else cur.oi
    return list(out.values())
