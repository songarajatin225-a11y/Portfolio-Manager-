from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError

from app.api.deps import DB, CurrentUser, WriterUser
from app.core.bus import CH_COMMANDS, get_bus
from app.core.errors import AppError, ConflictError, NotFoundError
from app.db.models import Instrument, Watchlist, WatchlistItem
from app.schemas.market import instrument_dict
from app.services import instruments as instrument_service
from app.services.market_data import cached_quotes

router = APIRouter(prefix="/watchlists", tags=["watchlists"])
MAX_ITEMS = 200


class WatchlistIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class ItemIn(BaseModel):
    symbol: str | None = Field(default=None, max_length=80)
    instrument_id: int | None = None
    exchange: str | None = Field(default=None, pattern="^(NSE|BSE)$")
    notes: str = Field(default="", max_length=500)


async def _own(db: DB, user_id, watchlist_id: int) -> Watchlist:  # noqa: ANN001
    wl = await db.get(Watchlist, watchlist_id)
    if wl is None or wl.user_id != user_id:
        raise NotFoundError("Watchlist not found")
    return wl


async def _resubscribe() -> None:
    await get_bus().publish(CH_COMMANDS, {"cmd": "resubscribe"})


@router.get("")
async def list_watchlists(user: CurrentUser, db: DB) -> list[dict[str, Any]]:
    rows = (
        await db.execute(
            select(Watchlist, func.count(WatchlistItem.id))
            .outerjoin(WatchlistItem, WatchlistItem.watchlist_id == Watchlist.id)
            .where(Watchlist.user_id == user.id)
            .group_by(Watchlist.id)
            .order_by(Watchlist.created_at)
        )
    ).all()
    return [{"id": w.id, "name": w.name, "item_count": c, "created_at": w.created_at} for w, c in rows]


@router.post("", status_code=201)
async def create_watchlist(body: WatchlistIn, user: WriterUser, db: DB) -> dict[str, Any]:
    wl = Watchlist(user_id=user.id, name=body.name.strip())
    db.add(wl)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise ConflictError("You already have a watchlist with this name") from exc
    return {"id": wl.id, "name": wl.name, "item_count": 0}


@router.patch("/{watchlist_id}")
async def rename_watchlist(watchlist_id: int, body: WatchlistIn, user: WriterUser, db: DB) -> dict[str, Any]:
    wl = await _own(db, user.id, watchlist_id)
    wl.name = body.name.strip()
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise ConflictError("You already have a watchlist with this name") from exc
    return {"id": wl.id, "name": wl.name}


@router.delete("/{watchlist_id}", status_code=204)
async def delete_watchlist(watchlist_id: int, user: WriterUser, db: DB) -> None:
    wl = await _own(db, user.id, watchlist_id)
    await db.delete(wl)
    await db.commit()
    await _resubscribe()


@router.get("/{watchlist_id}")
async def get_watchlist(watchlist_id: int, user: CurrentUser, db: DB) -> dict[str, Any]:
    wl = await _own(db, user.id, watchlist_id)
    rows = (
        await db.execute(
            select(WatchlistItem, Instrument)
            .join(Instrument, Instrument.id == WatchlistItem.instrument_id)
            .where(WatchlistItem.watchlist_id == wl.id)
            .order_by(WatchlistItem.position, WatchlistItem.id)
        )
    ).all()
    quotes = await cached_quotes(db, [inst.id for _, inst in rows])
    return {
        "id": wl.id,
        "name": wl.name,
        "items": [
            {
                "instrument": instrument_dict(inst),
                "quote": quotes.get(inst.id),
                "notes": item.notes,
                "added_at": item.added_at,
            }
            for item, inst in rows
        ],
    }


@router.post("/{watchlist_id}/items", status_code=201)
async def add_item(watchlist_id: int, body: ItemIn, user: WriterUser, db: DB) -> dict[str, Any]:
    wl = await _own(db, user.id, watchlist_id)
    if body.instrument_id is not None:
        inst = await instrument_service.resolve_symbol(db, str(body.instrument_id))
    elif body.symbol:
        inst = await instrument_service.resolve_symbol(db, body.symbol, body.exchange)
    else:
        raise AppError("Provide a symbol or instrument_id", status_code=422)
    count = await db.scalar(select(func.count()).select_from(WatchlistItem).where(WatchlistItem.watchlist_id == wl.id))
    if (count or 0) >= MAX_ITEMS:
        raise AppError(f"A watchlist can hold at most {MAX_ITEMS} instruments")
    payload = instrument_dict(inst)
    db.add(WatchlistItem(watchlist_id=wl.id, instrument_id=inst.id, position=(count or 0), notes=body.notes))
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise ConflictError(f"{payload['symbol']} is already in this watchlist") from exc
    await _resubscribe()
    return {"instrument": payload}


@router.delete("/{watchlist_id}/items/{instrument_id}", status_code=204)
async def remove_item(watchlist_id: int, instrument_id: int, user: WriterUser, db: DB) -> None:
    wl = await _own(db, user.id, watchlist_id)
    await db.execute(
        delete(WatchlistItem).where(WatchlistItem.watchlist_id == wl.id, WatchlistItem.instrument_id == instrument_id)
    )
    await db.commit()
    await _resubscribe()
