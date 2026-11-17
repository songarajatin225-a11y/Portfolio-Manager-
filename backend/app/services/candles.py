"""Candle storage and retrieval with on-demand backfill from the active provider."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics.resample import resample_bars
from app.core.bus import get_bus
from app.db.models import Candle, Instrument
from app.marketdata.base import AuthenticationRequired, NotSupported, ProviderError
from app.marketdata.types import INTERVALS, CandleBar, DataStatus, validate_candle
from app.services import instruments as instrument_service
from app.services.market_data import active_provider_config, rest_provider

logger = logging.getLogger(__name__)

DEFAULT_LOOKBACK = {"1m": 5, "5m": 20, "15m": 45, "30m": 90, "1h": 180, "1d": 730, "1w": 3650, "1mo": 7300}
FETCH_CACHE_TTL = 600


@dataclass
class CandleResult:
    bars: list[CandleBar]
    source: str
    data_status: str
    fetched_from_provider: bool = False
    warnings: list[str] = field(default_factory=list)


async def upsert_candles(
    db: AsyncSession, instrument_id: int, interval: str, bars: list[CandleBar], source: str, simulated: bool
) -> int:
    rows = []
    for b in bars:
        if validate_candle(b):
            continue
        rows.append(
            {
                "instrument_id": instrument_id,
                "interval": interval,
                "ts": b.ts,
                "is_simulated": simulated,
                "open": b.open,
                "high": b.high,
                "low": b.low,
                "close": b.close,
                "volume": b.volume,
                "oi": b.oi,
                "source": source,
            }
        )
    for i in range(0, len(rows), 2000):
        stmt = insert(Candle).values(rows[i : i + 2000])
        stmt = stmt.on_conflict_do_update(
            index_elements=["instrument_id", "interval", "ts", "is_simulated"],
            set_={
                "open": stmt.excluded.open,
                "high": stmt.excluded.high,
                "low": stmt.excluded.low,
                "close": stmt.excluded.close,
                "volume": stmt.excluded.volume,
                "oi": stmt.excluded.oi,
                "source": stmt.excluded.source,
            },
        )
        await db.execute(stmt)
    return len(rows)


async def stored_candles(
    db: AsyncSession, instrument: Instrument, interval: str, start: datetime, end: datetime
) -> tuple[list[CandleBar], str | None]:
    rows = (
        (
            await db.execute(
                select(Candle)
                .where(
                    Candle.instrument_id == instrument.id,
                    Candle.interval == interval,
                    Candle.is_simulated.is_(instrument.is_simulated),
                    Candle.ts >= start,
                    Candle.ts <= end,
                )
                .order_by(Candle.ts)
            )
        )
        .scalars()
        .all()
    )
    bars = [
        CandleBar(ts=r.ts, open=r.open, high=r.high, low=r.low, close=r.close, volume=r.volume, oi=r.oi) for r in rows
    ]
    return bars, (rows[-1].source if rows else None)


def _needs_fetch(bars: list[CandleBar], interval: str, start: datetime, end: datetime) -> bool:
    if not bars:
        return True
    slack = timedelta(days=5) if interval in ("1d", "1w", "1mo") else timedelta(hours=20)
    if interval == "1w":
        slack = timedelta(days=10)
    if interval == "1mo":
        slack = timedelta(days=35)
    stale_end = min(end, datetime.now(UTC)) - bars[-1].ts > slack
    late_start = bars[0].ts - start > slack + timedelta(days=4)
    return stale_end or late_start


async def get_candles(
    db: AsyncSession,
    instrument: Instrument,
    interval: str,
    start: datetime | None = None,
    end: datetime | None = None,
    *,
    allow_fetch: bool = True,
) -> CandleResult:
    if interval not in INTERVALS:
        raise ValueError(f"interval must be one of {INTERVALS}")
    end = end or datetime.now(UTC)
    start = start or end - timedelta(days=DEFAULT_LOOKBACK[interval])
    status = DataStatus.SIMULATED if instrument.is_simulated else DataStatus.HISTORICAL
    bars, source = await stored_candles(db, instrument, interval, start, end)
    warnings: list[str] = []
    fetched = False

    cfg = await active_provider_config(db) if allow_fetch else None
    if (
        cfg is not None
        and (cfg.provider_type == "simulated") == instrument.is_simulated
        and _needs_fetch(bars, interval, start, end)
    ):
        cache_key = f"mi:candles_fetch:{instrument.id}:{interval}:{start.date()}:{end.date()}"
        if not await get_bus().get_json(cache_key):
            await get_bus().set_json(cache_key, True, ttl_seconds=FETCH_CACHE_TTL)
            keys = await instrument_service.provider_keys(db, cfg.provider_type, [instrument.id])
            if instrument.id not in keys:
                warnings.append(f"No {cfg.provider_type} mapping for this instrument - sync the instrument master")
            else:
                try:
                    async with rest_provider(db) as (_, provider):
                        fresh = await provider.get_candles(keys[instrument.id], interval, start, end)
                        await upsert_candles(
                            db, instrument.id, interval, fresh, provider.source_name, instrument.is_simulated
                        )
                        await db.commit()
                        fetched = True
                except NotSupported as exc:
                    warnings.append(str(exc))
                except AuthenticationRequired as exc:
                    warnings.append(f"Provider authentication required: {exc}")
                except ProviderError as exc:
                    logger.warning("Candle backfill failed for %s: %s", instrument.symbol, exc)
                    warnings.append("Provider request failed; showing stored data only")
                if fetched:
                    bars, source = await stored_candles(db, instrument, interval, start, end)

    # Fall back to resampling finer stored data when the requested interval has none.
    if not bars and interval in ("5m", "15m", "30m", "1h"):
        base, base_source = await stored_candles(db, instrument, "1m", start, end)
        if base:
            bars, source = resample_bars(base, interval), base_source
            warnings.append("Aggregated from 1-minute bars")
    if not bars and interval in ("1w", "1mo"):
        base, base_source = await stored_candles(db, instrument, "1d", start, end)
        if base:
            bars, source = resample_bars(base, interval), base_source
            warnings.append("Aggregated from daily bars")
    return CandleResult(
        bars=bars,
        source=source or (cfg.provider_type if cfg else "none"),
        data_status=status.value,
        fetched_from_provider=fetched,
        warnings=warnings,
    )
