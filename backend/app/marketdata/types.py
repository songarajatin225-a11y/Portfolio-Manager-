"""Provider-neutral market data types shared by every provider, the feed manager and the API."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Any


class DataStatus(StrEnum):
    LIVE = "LIVE"
    DELAYED = "DELAYED"
    HISTORICAL = "HISTORICAL"
    SIMULATED = "SIMULATED"


INTERVALS = ("1m", "5m", "15m", "30m", "1h", "1d", "1w", "1mo")


@dataclass(slots=True)
class DepthLevel:
    price: float
    quantity: float
    orders: int | None = None


@dataclass(slots=True)
class Quote:
    provider_key: str
    ltp: float
    received_at: datetime
    source: str
    instrument_id: int | None = None
    symbol: str | None = None
    exchange: str | None = None
    open: float | None = None
    high: float | None = None
    low: float | None = None
    prev_close: float | None = None
    change: float | None = None
    change_pct: float | None = None
    volume: float | None = None
    vwap: float | None = None
    bid: float | None = None
    ask: float | None = None
    bid_qty: float | None = None
    ask_qty: float | None = None
    total_buy_qty: float | None = None
    total_sell_qty: float | None = None
    oi: float | None = None
    upper_circuit: float | None = None
    lower_circuit: float | None = None
    high_52w: float | None = None
    low_52w: float | None = None
    last_trade_time: datetime | None = None
    exchange_ts: datetime | None = None
    depth_buy: list[DepthLevel] = field(default_factory=list)
    depth_sell: list[DepthLevel] = field(default_factory=list)
    is_simulated: bool = False
    provider_delayed: bool = False

    def finalize(self) -> Quote:
        """Derive change fields from prev_close when the provider didn't supply them."""
        if self.prev_close and self.prev_close > 0:
            if self.change is None:
                self.change = self.ltp - self.prev_close
            if self.change_pct is None:
                self.change_pct = (self.ltp - self.prev_close) / self.prev_close * 100.0
        if self.bid is None and self.depth_buy:
            self.bid, self.bid_qty = self.depth_buy[0].price, self.depth_buy[0].quantity
        if self.ask is None and self.depth_sell:
            self.ask, self.ask_qty = self.depth_sell[0].price, self.depth_sell[0].quantity
        return self

    def to_cache(self) -> dict[str, Any]:
        d = asdict(self)
        for k in ("received_at", "last_trade_time", "exchange_ts"):
            if d[k] is not None:
                d[k] = d[k].isoformat()
        return d

    @staticmethod
    def from_cache(d: dict[str, Any]) -> Quote:
        d = dict(d)
        for k in ("received_at", "last_trade_time", "exchange_ts"):
            if d.get(k):
                d[k] = datetime.fromisoformat(d[k])
        d["depth_buy"] = [DepthLevel(**x) for x in d.get("depth_buy") or []]
        d["depth_sell"] = [DepthLevel(**x) for x in d.get("depth_sell") or []]
        return Quote(**d)


@dataclass(slots=True)
class CandleBar:
    ts: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0
    oi: float | None = None


@dataclass(slots=True)
class InstrumentRecord:
    provider_key: str
    symbol: str
    exchange: str
    segment: str  # EQ | INDEX | FO
    name: str = ""
    provider_token: str | None = None
    isin: str | None = None
    instrument_type: str = "EQ"
    lot_size: int = 1
    tick_size: float = 0.05
    expiry: date | None = None
    strike: float | None = None
    option_type: str | None = None
    underlying: str | None = None
    is_index: bool = False


@dataclass(slots=True)
class MarketStatusInfo:
    exchange: str
    status: str  # open | closed | pre_open | post_close | holiday | weekend
    source: str
    as_of: datetime
    detail: str = ""


@dataclass(slots=True)
class ConnectionTestResult:
    ok: bool
    message: str
    latency_ms: float | None = None
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class HolidayRecord:
    exchange: str
    day: date
    description: str


# --- Validation ------------------------------------------------------------------------

MAX_FUTURE_SKEW_SECONDS = 120


def _finite_nonneg(v: float | None) -> bool:
    return v is None or (isinstance(v, (int, float)) and math.isfinite(v) and v >= 0)


def validate_quote(q: Quote, now: datetime | None = None) -> list[str]:
    """Return a list of problems; empty means the quote is acceptable."""
    problems: list[str] = []
    if not isinstance(q.ltp, (int, float)) or not math.isfinite(q.ltp) or q.ltp <= 0:
        problems.append("ltp must be a positive finite number")
    for name in ("open", "high", "low", "prev_close", "volume", "vwap", "bid", "ask", "oi"):
        if not _finite_nonneg(getattr(q, name)):
            problems.append(f"{name} must be a non-negative finite number")
    if q.high and q.low and q.high > 0 and q.low > 0 and q.high < q.low:
        problems.append("high is below low")
    if q.high and q.low and q.ltp and q.high > 0 and q.low > 0:
        # allow 1% tolerance for ticks arriving before the day range updates
        if q.ltp > q.high * 1.01 or q.ltp < q.low * 0.99:
            problems.append("ltp outside the day's high/low range")
    now = now or datetime.now(UTC)
    ts = q.exchange_ts or q.last_trade_time
    if ts is not None and (ts - now).total_seconds() > MAX_FUTURE_SKEW_SECONDS:
        problems.append("timestamp is in the future")
    return problems


def validate_candle(c: CandleBar) -> list[str]:
    problems: list[str] = []
    vals = (c.open, c.high, c.low, c.close)
    if any(not math.isfinite(v) or v <= 0 for v in vals):
        problems.append("prices must be positive finite numbers")
    elif c.high < max(c.open, c.close, c.low) - 1e-9 or c.low > min(c.open, c.close, c.high) + 1e-9:
        problems.append("inconsistent OHLC")
    if not _finite_nonneg(c.volume):
        problems.append("volume must be non-negative")
    return problems
