"""Aggregates ticks into 1-minute OHLCV bars.

Bar time comes from the exchange timestamp when present (falls back to receive time). Volume is
derived from the provider's cumulative day volume, so a bar's volume is the increase in day volume
during that minute. Ticks older than the bar currently being built are ignored.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from app.marketdata.types import CandleBar, Quote


@dataclass
class _Building:
    minute: datetime
    open: float
    high: float
    low: float
    close: float
    vol_start: float | None
    vol_last: float | None
    simulated: bool
    source: str


@dataclass
class CompletedBar:
    instrument_id: int
    bar: CandleBar
    simulated: bool
    source: str


class CandleBuilder:
    def __init__(self) -> None:
        self._cur: dict[int, _Building] = {}
        self._last_cum: dict[int, float] = {}

    @staticmethod
    def _minute(ts: datetime) -> datetime:
        return ts.astimezone(UTC).replace(second=0, microsecond=0)

    def _finish(self, iid: int, b: _Building) -> CompletedBar:
        vol = 0.0
        if b.vol_last is not None and b.vol_start is not None:
            vol = max(0.0, b.vol_last - b.vol_start)
        return CompletedBar(
            iid,
            CandleBar(ts=b.minute, open=b.open, high=b.high, low=b.low, close=b.close, volume=vol),
            b.simulated,
            b.source,
        )

    def on_quote(self, q: Quote) -> list[CompletedBar]:
        if q.instrument_id is None:
            return []
        iid = q.instrument_id
        minute = self._minute(q.exchange_ts or q.last_trade_time or q.received_at)
        done: list[CompletedBar] = []
        cur = self._cur.get(iid)
        if cur is not None and minute < cur.minute:
            return done  # late tick
        if cur is not None and minute > cur.minute:
            done.append(self._finish(iid, cur))
            if cur.vol_last is not None:
                self._last_cum[iid] = cur.vol_last
            cur = None
        if cur is None:
            start_vol = self._last_cum.get(iid, q.volume)
            if q.volume is not None and start_vol is not None and q.volume < start_vol:
                start_vol = q.volume  # new trading day: cumulative volume reset
            self._cur[iid] = _Building(
                minute, q.ltp, q.ltp, q.ltp, q.ltp, start_vol, q.volume, q.is_simulated, q.source
            )
        else:
            cur.high = max(cur.high, q.ltp)
            cur.low = min(cur.low, q.ltp)
            cur.close = q.ltp
            if q.volume is not None:
                cur.vol_last = q.volume
        return done

    def flush_idle(self, now: datetime, grace: timedelta = timedelta(seconds=75)) -> list[CompletedBar]:
        """Close bars whose minute ended more than ``grace`` ago without a newer tick."""
        done = []
        for iid, cur in list(self._cur.items()):
            if now - cur.minute >= grace:
                done.append(self._finish(iid, cur))
                if cur.vol_last is not None:
                    self._last_cum[iid] = cur.vol_last
                del self._cur[iid]
        return done
