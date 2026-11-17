"""Simulated market data provider - DEVELOPMENT AND AUTOMATED TESTS ONLY.

* Enabled only when ``ALLOW_SIMULATED_FEED=true``.
* Uses clearly synthetic instruments (``SIM*`` symbols, "Simulated ... Ltd" names) so simulated
  prices can never be confused with a real listed company.
* Every quote, candle, instrument, metric and prediction derived from it is flagged
  ``is_simulated`` and labelled SIMULATED in the API and UI. Reads always filter on the active
  data mode, so simulated and real data are never mixed.

Prices follow a one-factor model (market factor + idiosyncratic noise, geometric Brownian motion)
so correlations, betas and volatility behave plausibly for exercising analytics, ML and risk code.
"""

from __future__ import annotations

import asyncio
import hashlib
import math
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

import numpy as np

from app.marketdata.base import MarketDataProvider, NotSupported
from app.marketdata.calendar import IST, session_bounds
from app.marketdata.types import CandleBar, DepthLevel, InstrumentRecord, MarketStatusInfo, Quote

ANCHOR = date(2021, 1, 4)


@dataclass(frozen=True)
class SimSpec:
    symbol: str
    name: str
    sector: str
    start_price: float
    beta: float
    idio_vol: float
    is_index: bool = False


SECTORS = ["Banking", "Information Technology", "Energy", "FMCG", "Automobile", "Pharma", "Metals", "Telecom"]

_STOCKS = [
    ("SIMBANKA", "Simulated Bank Alpha Ltd", "Banking", 1650, 1.10, 0.012),
    ("SIMBANKB", "Simulated Bank Beta Ltd", "Banking", 980, 1.20, 0.014),
    ("SIMBANKC", "Simulated Finance Gamma Ltd", "Banking", 7200, 1.30, 0.016),
    ("SIMBANKD", "Simulated Bank Delta Ltd", "Banking", 610, 1.05, 0.013),
    ("SIMITA", "Simulated Infotech Alpha Ltd", "Information Technology", 3900, 0.85, 0.013),
    ("SIMITB", "Simulated Software Beta Ltd", "Information Technology", 1500, 0.90, 0.015),
    ("SIMITC", "Simulated Systems Gamma Ltd", "Information Technology", 1250, 0.95, 0.016),
    ("SIMITD", "Simulated Digital Delta Ltd", "Information Technology", 5400, 0.80, 0.017),
    ("SIMENGA", "Simulated Energy Alpha Ltd", "Energy", 2450, 1.00, 0.013),
    ("SIMENGB", "Simulated Power Beta Ltd", "Energy", 310, 1.15, 0.018),
    ("SIMENGC", "Simulated Oil Gamma Ltd", "Energy", 165, 1.10, 0.017),
    ("SIMENGD", "Simulated Gas Delta Ltd", "Energy", 520, 0.95, 0.016),
    ("SIMFMCGA", "Simulated Consumer Alpha Ltd", "FMCG", 2600, 0.55, 0.010),
    ("SIMFMCGB", "Simulated Foods Beta Ltd", "FMCG", 4800, 0.60, 0.011),
    ("SIMFMCGC", "Simulated Brands Gamma Ltd", "FMCG", 430, 0.65, 0.012),
    ("SIMFMCGD", "Simulated Household Delta Ltd", "FMCG", 1180, 0.50, 0.011),
    ("SIMAUTOA", "Simulated Motors Alpha Ltd", "Automobile", 950, 1.25, 0.017),
    ("SIMAUTOB", "Simulated Auto Beta Ltd", "Automobile", 11200, 1.05, 0.014),
    ("SIMAUTOC", "Simulated Two-Wheelers Gamma Ltd", "Automobile", 4300, 1.00, 0.015),
    ("SIMAUTOD", "Simulated Components Delta Ltd", "Automobile", 780, 1.15, 0.018),
    ("SIMPHRMA", "Simulated Pharma Alpha Ltd", "Pharma", 1420, 0.70, 0.013),
    ("SIMPHRMB", "Simulated Labs Beta Ltd", "Pharma", 5800, 0.65, 0.014),
    ("SIMPHRMC", "Simulated Healthcare Gamma Ltd", "Pharma", 1080, 0.75, 0.015),
    ("SIMPHRMD", "Simulated Biotech Delta Ltd", "Pharma", 690, 0.80, 0.019),
    ("SIMMETA", "Simulated Steel Alpha Ltd", "Metals", 145, 1.40, 0.020),
    ("SIMMETB", "Simulated Metals Beta Ltd", "Metals", 870, 1.35, 0.019),
    ("SIMMETC", "Simulated Aluminium Gamma Ltd", "Metals", 520, 1.45, 0.021),
    ("SIMMETD", "Simulated Mining Delta Ltd", "Metals", 330, 1.30, 0.020),
    ("SIMTELA", "Simulated Telecom Alpha Ltd", "Telecom", 1250, 0.80, 0.013),
    ("SIMTELB", "Simulated Networks Beta Ltd", "Telecom", 16, 1.20, 0.025),
    ("SIMTELC", "Simulated Towers Gamma Ltd", "Telecom", 340, 0.90, 0.016),
    ("SIMTELD", "Simulated Fibre Delta Ltd", "Telecom", 210, 1.00, 0.018),
]

_INDICES = [
    ("SIMNIFTY50", "SIM NIFTY 50", 18000, 1.00, 0.0015),
    ("SIMSENSEX", "SIM SENSEX", 60000, 0.98, 0.0015),
    ("SIMBANKNIFTY", "SIM BANK NIFTY", 42000, 1.15, 0.004),
    ("SIMNIFTYNEXT50", "SIM NIFTY NEXT 50", 43000, 1.05, 0.004),
    ("SIMNIFTYMIDCAP", "SIM NIFTY MIDCAP 100", 32000, 1.20, 0.005),
    ("SIMNIFTYSMALLCAP", "SIM NIFTY SMALLCAP 100", 10000, 1.35, 0.007),
    ("SIMINDIAVIX", "SIM INDIA VIX", 14, 0.0, 0.0),
]

SPECS: dict[str, SimSpec] = {
    **{s[0]: SimSpec(s[0], s[1], s[2], s[3], s[4], s[5]) for s in _STOCKS},
    **{i[0]: SimSpec(i[0], i[1], "Index", i[2], i[3], i[4], is_index=True) for i in _INDICES},
}

# Simulated index constituents (all simulated stocks belong to SIMNIFTY50 for the tracked universe).
SIM_INDEX_MEMBERS = {
    "SIMNIFTY50": [s[0] for s in _STOCKS],
    "SIMBANKNIFTY": [s[0] for s in _STOCKS if s[2] == "Banking"],
}


def sim_key(symbol: str) -> str:
    return f"SIM:{symbol}"


def _seed(*parts: object) -> int:
    return int(hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()[:16], 16)


def _trading_days(start: date, end: date) -> list[date]:
    days, d = [], start
    while d <= end:
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    return days


class _History:
    """Deterministic daily history shared by all instances in the process."""

    _cache: dict[tuple[str, date], list[CandleBar]] = {}
    _market: dict[date, np.ndarray] = {}

    @classmethod
    def market_returns(cls, end: date) -> tuple[list[date], np.ndarray]:
        days = _trading_days(ANCHOR, end)
        if end not in cls._market:
            rng = np.random.default_rng(_seed("market"))
            # regime-switching drift/vol so regime detection has something to find
            n = len(days)
            regimes = np.repeat(rng.choice([0, 1, 2], size=n // 60 + 1, p=[0.5, 0.3, 0.2]), 60)[:n]
            mu = np.choose(regimes, [0.0006, -0.0004, 0.0002])
            sigma = np.choose(regimes, [0.008, 0.014, 0.010])
            cls._market = {end: rng.normal(mu, sigma)}
        return days, cls._market[end]

    @classmethod
    def daily(cls, symbol: str, end: date) -> list[CandleBar]:
        key = (symbol, end)
        if key in cls._cache:
            return cls._cache[key]
        spec = SPECS[symbol]
        days, mret = cls.market_returns(end)
        rng = np.random.default_rng(_seed("daily", symbol))
        n = len(days)
        if symbol == "SIMINDIAVIX":
            vix = np.empty(n)
            vix[0] = spec.start_price
            for i in range(1, n):
                vix[i] = max(9.0, vix[i - 1] + 0.08 * (14 - vix[i - 1]) - 180 * mret[i] + rng.normal(0, 0.5))
            closes = vix
        else:
            r = spec.beta * mret + rng.normal(0, spec.idio_vol, n)
            closes = spec.start_price * np.exp(np.cumsum(r - 0.5 * (spec.idio_vol**2)))
        vol_scale = max(spec.idio_vol, 0.006)
        bars: list[CandleBar] = []
        prev = closes[0]
        base_volume = 0 if spec.is_index else 2_000_000 * (1000 / max(spec.start_price, 50)) ** 0.5
        for i, d in enumerate(days):
            close = float(closes[i])
            open_ = float(prev * (1 + rng.normal(0, vol_scale * 0.3)))
            high = max(open_, close) * (1 + abs(rng.normal(0, vol_scale * 0.5)))
            low = min(open_, close) * (1 - abs(rng.normal(0, vol_scale * 0.5)))
            spike = 3.5 if rng.random() < 0.02 else 1.0  # occasional volume anomalies
            volume = float(base_volume * rng.lognormal(0, 0.35) * spike) if base_volume else 0.0
            ts = datetime.combine(d, datetime.min.time(), IST).astimezone(UTC)
            bars.append(
                CandleBar(
                    ts=ts,
                    open=round(open_, 2),
                    high=round(high, 2),
                    low=round(low, 2),
                    close=round(close, 2),
                    volume=round(volume),
                )
            )
            prev = close
        cls._cache[key] = bars
        return bars


class SimulatedProvider(MarketDataProvider):
    provider_type = "simulated"
    display_name = "Simulated feed (development only)"
    supports_streaming = True
    is_simulated = True
    credential_fields = []
    notes = "Synthetic SIM* instruments for development and tests. Every data point is labelled SIMULATED."

    def __init__(self, *args, **kwargs) -> None:  # type: ignore[no-untyped-def]
        super().__init__(*args, **kwargs)
        self._state: dict[str, dict[str, float]] = {}
        self._rng = np.random.default_rng()
        self.tick_interval = float(self.settings.options.get("tick_interval_seconds", 1.0))

    @property
    def source_name(self) -> str:
        return "simulated"

    async def authenticate(self) -> None:
        return None

    def _today(self) -> date:
        return datetime.now(IST).date()

    def _last_close(self, symbol: str) -> float:
        hist = _History.daily(symbol, self._today() - timedelta(days=1))
        return hist[-1].close if hist else SPECS[symbol].start_price

    def _ensure_state(self, symbol: str) -> dict[str, float]:
        st = self._state.get(symbol)
        if st is None:
            prev = self._last_close(symbol)
            gap = 1 + self._rng.normal(0, 0.004)
            o = prev * gap
            st = {"prev_close": prev, "open": o, "high": o, "low": o, "ltp": o, "volume": 0.0, "notional": 0.0}
            self._state[symbol] = st
        return st

    def _step(self, symbol: str, market_shock: float) -> Quote:
        spec = SPECS[symbol]
        st = self._ensure_state(symbol)
        if symbol == "SIMINDIAVIX":
            st["ltp"] = max(9.0, st["ltp"] + 0.02 * (14 - st["ltp"]) - 600 * market_shock + self._rng.normal(0, 0.02))
        else:
            idio = self._rng.normal(0, spec.idio_vol / math.sqrt(22500))
            st["ltp"] *= math.exp(spec.beta * market_shock + idio)
        ltp = round(st["ltp"], 2)
        st["high"], st["low"] = max(st["high"], ltp), min(st["low"], ltp)
        qty = 0.0 if spec.is_index else float(self._rng.integers(10, 2000))
        st["volume"] += qty
        st["notional"] += qty * ltp
        tick = 0.05
        now = datetime.now(UTC)
        depth_buy = (
            []
            if spec.is_index
            else [
                DepthLevel(
                    round(ltp - tick * (i + 1), 2), float(self._rng.integers(50, 5000)), int(self._rng.integers(1, 40))
                )
                for i in range(5)
            ]
        )
        depth_sell = (
            []
            if spec.is_index
            else [
                DepthLevel(
                    round(ltp + tick * (i + 1), 2), float(self._rng.integers(50, 5000)), int(self._rng.integers(1, 40))
                )
                for i in range(5)
            ]
        )
        return Quote(
            provider_key=sim_key(symbol),
            ltp=ltp,
            open=round(st["open"], 2),
            high=round(st["high"], 2),
            low=round(st["low"], 2),
            prev_close=round(st["prev_close"], 2),
            volume=None if spec.is_index else st["volume"],
            vwap=(st["notional"] / st["volume"]) if st["volume"] else None,
            depth_buy=depth_buy,
            depth_sell=depth_sell,
            last_trade_time=now,
            exchange_ts=now,
            received_at=now,
            source="simulated",
            is_simulated=True,
        ).finalize()

    def _symbols(self, keys: list[str]) -> list[str]:
        return [k.split(":", 1)[1] for k in keys if k.startswith("SIM:") and k.split(":", 1)[1] in SPECS]

    async def get_quotes(self, provider_keys: list[str]) -> list[Quote]:
        shock = self._rng.normal(0, 0.008 / math.sqrt(22500))
        return [self._step(s, shock) for s in self._symbols(provider_keys)]

    async def get_candles(self, provider_key: str, interval: str, start: datetime, end: datetime) -> list[CandleBar]:
        symbol = provider_key.split(":", 1)[1] if ":" in provider_key else provider_key
        if symbol not in SPECS:
            return []
        if interval in ("1d", "1w", "1mo"):
            bars = [b for b in _History.daily(symbol, self._today() - timedelta(days=1)) if start <= b.ts <= end]
            if interval != "1d":
                from app.analytics.resample import resample_bars

                return resample_bars(bars, interval)
            return bars
        if interval not in ("1m", "5m", "15m", "30m", "1h"):
            raise NotSupported(interval)
        # Intraday: synthesise minute bars for each trading day in range from that day's daily bar.
        out: list[CandleBar] = []
        daily = {b.ts.astimezone(IST).date(): b for b in _History.daily(symbol, self._today() - timedelta(days=1))}
        for d in _trading_days(
            start.astimezone(IST).date(), min(end.astimezone(IST).date(), self._today() - timedelta(days=1))
        ):
            day = daily.get(d)
            if not day:
                continue
            rng = np.random.default_rng(_seed("intraday", symbol, d.isoformat()))
            s0, s1 = session_bounds(d)
            n = int((s1 - s0).total_seconds() // 60)
            steps = rng.normal(0, 1, n)
            path = np.cumsum(steps)
            path = (path - path[0]) / (np.ptp(path) or 1)
            prices = day.open + (day.close - day.open) * np.linspace(0, 1, n) + path * (day.high - day.low) * 0.3
            prices = np.clip(prices, day.low, day.high)
            vols = rng.dirichlet(np.ones(n)) * day.volume
            for i in range(n):
                p0 = float(prices[i - 1] if i else day.open)
                p1 = float(prices[i])
                ts = s0 + timedelta(minutes=i)
                if start <= ts <= end:
                    out.append(
                        CandleBar(
                            ts=ts,
                            open=round(p0, 2),
                            high=round(max(p0, p1), 2),
                            low=round(min(p0, p1), 2),
                            close=round(p1, 2),
                            volume=round(float(vols[i])),
                        )
                    )
        if interval != "1m":
            from app.analytics.resample import resample_bars

            return resample_bars(out, interval)
        return out

    async def get_instruments(self) -> list[InstrumentRecord]:
        return [
            InstrumentRecord(
                provider_key=sim_key(s.symbol),
                provider_token=s.symbol,
                symbol=s.symbol,
                exchange="NSE",
                segment="INDEX" if s.is_index else "EQ",
                name=s.name,
                instrument_type="INDEX" if s.is_index else "EQ",
                is_index=s.is_index,
            )
            for s in SPECS.values()
        ]

    async def get_market_status(self, exchange: str) -> MarketStatusInfo | None:
        return MarketStatusInfo(
            exchange=exchange,
            status="open",
            source="simulated",
            as_of=datetime.now(UTC),
            detail="Simulated session (always open)",
        )

    async def stream(self, provider_keys: list[str]) -> AsyncIterator[list[Quote]]:  # type: ignore[override]
        symbols = self._symbols(provider_keys)
        if not symbols:
            return
        while True:
            shock = self._rng.normal(0, 0.008 / math.sqrt(22500 / max(self.tick_interval, 0.1)))
            batch = [self._step(s, shock) for s in symbols if s in ("SIMINDIAVIX",) or self._rng.random() < 0.7]
            if batch:
                yield batch
            await asyncio.sleep(self.tick_interval)

    @staticmethod
    def sectors() -> dict[str, str]:
        return {s.symbol: s.sector for s in SPECS.values() if not s.is_index}
