from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import delete, select

from app.api.deps import DB, AdminUser, CurrentUser
from app.core.bus import K_FEED_HEALTH, get_bus
from app.core.errors import AppError, NotFoundError
from app.db.models import Instrument, MarketHoliday, Watchlist, WatchlistItem
from app.schemas.market import instrument_dict
from app.services import instruments as instrument_service
from app.services.audit import audit
from app.services.market_data import cached_quotes, data_mode, market_session

router = APIRouter(prefix="/market", tags=["market"])


def _parse_ids(ids: str) -> list[int]:
    out = []
    for part in ids.split(","):
        part = part.strip()
        if part.isdigit():
            out.append(int(part))
    if len(out) > 1000:
        raise AppError("At most 1000 instruments per request")
    return out


@router.get("/status")
async def status(_: CurrentUser, db: DB) -> dict[str, Any]:
    health = await get_bus().get_json(K_FEED_HEALTH) or {}
    return {
        "data_mode": await data_mode(db),
        "session": await market_session(db),
        "feed": {
            k: health.get(k)
            for k in (
                "state",
                "provider_name",
                "provider_type",
                "is_simulated",
                "streaming",
                "subscribed",
                "last_message_at",
                "last_error",
                "latency_ms_p50",
                "messages_last_minute",
                "stale_after_seconds",
                "token_expires_at",
                "updated_at",
            )
        },
    }


@router.get("/quotes")
async def quotes(_: CurrentUser, db: DB, ids: str = Query(..., max_length=10000)) -> dict[str, Any]:
    instrument_ids = _parse_ids(ids)
    return {"as_of": datetime.now(UTC).isoformat(), "quotes": await cached_quotes(db, instrument_ids)}


async def _with_quotes(db: DB, instruments: list[Instrument]) -> list[dict[str, Any]]:
    qs = await cached_quotes(db, [i.id for i in instruments])
    return [{"instrument": instrument_dict(i), "quote": qs.get(i.id)} for i in instruments]


@router.get("/indices")
async def indices(_: CurrentUser, db: DB) -> dict[str, Any]:
    entries = await instrument_service.dashboard_indices(db)
    resolved = [e for e in entries if e["instrument"] is not None]
    qs = await cached_quotes(db, [e["instrument"].id for e in resolved])
    return {
        "data_mode": await data_mode(db),
        "items": [
            {
                "code": e["code"],
                "label": e["label"],
                "instrument": instrument_dict(e["instrument"]) if e["instrument"] else None,
                "quote": qs.get(e["instrument"].id) if e["instrument"] else None,
            }
            for e in entries
        ],
    }


@router.get("/live")
async def live(user: CurrentUser, db: DB, scope: str = Query("universe", max_length=50)) -> dict[str, Any]:
    """Instruments + latest quotes for the Live Markets page. scope: universe | indices | watchlist:<id>."""
    if scope == "indices":
        ids = [e["instrument"].id for e in await instrument_service.dashboard_indices(db) if e["instrument"]]
    elif scope.startswith("watchlist:"):
        wid = scope.split(":", 1)[1]
        if not wid.isdigit():
            raise AppError("Invalid watchlist id")
        wl = await db.get(Watchlist, int(wid))
        if wl is None or wl.user_id != user.id:
            raise NotFoundError("Watchlist not found")
        ids = list(
            (
                await db.scalars(
                    select(WatchlistItem.instrument_id)
                    .where(WatchlistItem.watchlist_id == wl.id)
                    .order_by(WatchlistItem.position)
                )
            ).all()
        )
    else:
        ids = await instrument_service.tracked_universe(db)
    rows = (await db.scalars(select(Instrument).where(Instrument.id.in_(ids)))).all()
    by_id = {r.id: r for r in rows}
    ordered = [by_id[i] for i in ids if i in by_id]
    return {"scope": scope, "data_mode": await data_mode(db), "items": await _with_quotes(db, ordered)}


# --- Holidays ------------------------------------------------------------------------------


@router.get("/holidays")
async def list_holidays(_: CurrentUser, db: DB, year: int | None = Query(None, ge=2000, le=2100)) -> list[dict]:
    stmt = select(MarketHoliday).order_by(MarketHoliday.holiday_date)
    if year:
        stmt = stmt.where(
            MarketHoliday.holiday_date >= date(year, 1, 1), MarketHoliday.holiday_date <= date(year, 12, 31)
        )
    return [
        {"id": h.id, "exchange": h.exchange, "date": h.holiday_date, "description": h.description, "source": h.source}
        for h in (await db.scalars(stmt)).all()
    ]


class HolidayIn(BaseModel):
    exchange: str = Field(default="NSE", pattern="^(NSE|BSE)$")
    date: date
    description: str = Field(default="", max_length=200)


@router.post("/holidays", status_code=201)
async def add_holiday(body: HolidayIn, request: Request, admin: AdminUser, db: DB) -> dict:
    existing = await db.scalar(
        select(MarketHoliday).where(MarketHoliday.exchange == body.exchange, MarketHoliday.holiday_date == body.date)
    )
    if existing:
        existing.description = body.description
        h = existing
    else:
        h = MarketHoliday(exchange=body.exchange, holiday_date=body.date, description=body.description, source="manual")
        db.add(h)
    await audit(
        db,
        action="calendar.holiday_add",
        user_id=admin.id,
        request=request,
        details={"exchange": body.exchange, "date": body.date.isoformat()},
    )
    await db.commit()
    return {
        "id": h.id,
        "exchange": h.exchange,
        "date": h.holiday_date,
        "description": h.description,
        "source": h.source,
    }


@router.delete("/holidays/{holiday_id}", status_code=204)
async def delete_holiday(holiday_id: int, request: Request, admin: AdminUser, db: DB) -> None:
    await db.execute(delete(MarketHoliday).where(MarketHoliday.id == holiday_id))
    await audit(db, action="calendar.holiday_delete", user_id=admin.id, request=request, entity_id=holiday_id)
    await db.commit()
