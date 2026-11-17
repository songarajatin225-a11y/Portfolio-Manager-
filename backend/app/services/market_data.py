"""Market data helpers shared by the API and worker: data mode, quote labelling, provider access."""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.bus import K_FEED_HEALTH, K_QUOTE, get_bus
from app.core.crypto import encrypt_json
from app.core.errors import ServiceUnavailable
from app.db.models import MarketHoliday, ProviderConfig
from app.marketdata.base import MarketDataProvider
from app.marketdata.calendar import session_state
from app.marketdata.registry import build_provider
from app.marketdata.types import DataStatus
from app.services import app_settings


async def active_provider_config(db: AsyncSession) -> ProviderConfig | None:
    return await db.scalar(select(ProviderConfig).where(ProviderConfig.is_active.is_(True)).limit(1))


async def data_mode(db: AsyncSession) -> str:
    """'simulated' when the simulated provider is active, otherwise 'real' (also when none is configured)."""
    cfg = await active_provider_config(db)
    return "simulated" if cfg is not None and cfg.provider_type == "simulated" else "real"


async def is_simulated_mode(db: AsyncSession) -> bool:
    return (await data_mode(db)) == "simulated"


async def holidays(db: AsyncSession, exchange: str = "NSE") -> set[date]:
    rows = await db.scalars(select(MarketHoliday.holiday_date).where(MarketHoliday.exchange == exchange))
    return set(rows.all())


async def market_session(db: AsyncSession, exchange: str = "NSE") -> dict[str, Any]:
    """Market state: provider/feed-reported when available, otherwise the exchange calendar."""
    now = datetime.now(UTC)
    calendar_state = session_state(now, await holidays(db, exchange))
    health = await get_bus().get_json(K_FEED_HEALTH) or {}
    feed_status = (health.get("market_status") or {}).get(exchange)
    if feed_status and health.get("provider_type") == "simulated":
        return {"state": "open", "source": "simulated", "calendar_state": calendar_state, "as_of": now.isoformat()}
    if feed_status:
        return {
            "state": feed_status["status"],
            "source": feed_status["source"],
            "calendar_state": calendar_state,
            "as_of": feed_status.get("as_of"),
        }
    return {"state": calendar_state, "source": "calendar", "calendar_state": calendar_state, "as_of": now.isoformat()}


def label_quote(q: dict[str, Any], *, market_state: str, stale_after: float, now: datetime | None = None) -> dict:
    """Attach data_status / is_stale / age_seconds to a cached quote dict."""
    now = now or datetime.now(UTC)
    received = datetime.fromisoformat(q["received_at"]) if q.get("received_at") else None
    age = (now - received).total_seconds() if received else None
    is_stale = False
    if q.get("is_simulated"):
        status = DataStatus.SIMULATED
    elif q.get("provider_delayed"):
        status = DataStatus.DELAYED
    elif market_state != "open":
        status = DataStatus.HISTORICAL
    elif age is not None and age > stale_after:
        status, is_stale = DataStatus.DELAYED, True
    else:
        status = DataStatus.LIVE
    return {**q, "data_status": status.value, "is_stale": is_stale, "age_seconds": round(age, 1) if age else age}


async def cached_quotes(db: AsyncSession, instrument_ids: list[int]) -> dict[int, dict[str, Any]]:
    if not instrument_ids:
        return {}
    raw = await get_bus().mget_json([f"{K_QUOTE}{i}" for i in instrument_ids])
    session = await market_session(db)
    stale_after = float((await app_settings.get_section(db, "refresh"))["stale_after_seconds"])
    simulated = await is_simulated_mode(db)
    out: dict[int, dict[str, Any]] = {}
    now = datetime.now(UTC)
    for iid, q in zip(instrument_ids, raw, strict=True):
        if q is None or bool(q.get("is_simulated")) != simulated:
            continue  # never serve simulated quotes in real mode or vice versa
        out[iid] = label_quote(q, market_state=session["state"], stale_after=stale_after, now=now)
    return out


async def persist_credentials(db: AsyncSession, cfg: ProviderConfig, provider: MarketDataProvider) -> None:
    if provider.updated_credentials is not None:
        cfg.encrypted_credentials = encrypt_json(provider.updated_credentials)
        provider.updated_credentials = None
    if provider.token_expires_at is not None:
        cfg.token_expires_at = provider.token_expires_at
    await db.flush()


@contextlib.asynccontextmanager
async def rest_provider(db: AsyncSession) -> AsyncIterator[tuple[ProviderConfig, MarketDataProvider]]:
    """Active provider for on-demand REST calls from the API process (candles, fundamentals)."""
    cfg = await active_provider_config(db)
    if cfg is None:
        raise ServiceUnavailable(
            "No market data provider is active. Configure one in Data Provider Settings.", code="no_provider"
        )
    provider = build_provider(cfg)
    try:
        await provider.authenticate()
        yield cfg, provider
        await persist_credentials(db, cfg, provider)
        await db.commit()
    finally:
        await provider.close()
