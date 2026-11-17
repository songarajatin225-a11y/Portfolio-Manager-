"""Instrument master: sync from providers, symbol mapping, search, universe selection, CSV imports."""

from __future__ import annotations

import csv
import io
import logging
from collections.abc import Iterable
from datetime import date
from typing import Any

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, NotFoundError
from app.db.models import (
    Alert,
    IndexConstituent,
    Instrument,
    InstrumentProviderKey,
    JournalTrade,
    Transaction,
    WatchlistItem,
)
from app.marketdata.base import MarketDataProvider
from app.marketdata.simulated import SIM_INDEX_MEMBERS, SimulatedProvider
from app.marketdata.types import InstrumentRecord
from app.services import app_settings
from app.services.market_data import is_simulated_mode

logger = logging.getLogger(__name__)
BATCH = 1000


def _identity(r: InstrumentRecord | Instrument) -> tuple:
    return (r.exchange, r.segment, r.symbol, r.expiry, r.strike, r.option_type)


async def upsert_instruments(
    db: AsyncSession, provider_type: str, records: list[InstrumentRecord], simulated: bool
) -> dict[str, int]:
    """Upsert instruments (stable ids) and replace this provider's key mapping."""
    seen: dict[tuple, InstrumentRecord] = {}
    for r in records:
        seen.setdefault(_identity(r), r)  # first occurrence wins (e.g. EQ over BE duplicates)
    unique = list(seen.values())
    id_by_identity: dict[tuple, int] = {}
    for i in range(0, len(unique), BATCH):
        chunk = unique[i : i + BATCH]
        rows = [
            {
                "symbol": r.symbol,
                "exchange": r.exchange,
                "segment": r.segment,
                "name": r.name or r.symbol,
                "isin": r.isin,
                "instrument_type": r.instrument_type,
                "lot_size": r.lot_size,
                "tick_size": r.tick_size,
                "expiry": r.expiry,
                "strike": r.strike,
                "option_type": r.option_type,
                "underlying": r.underlying,
                "is_index": r.is_index,
                "is_active": True,
                "is_simulated": simulated,
            }
            for r in chunk
        ]
        stmt = insert(Instrument).values(rows)
        stmt = stmt.on_conflict_do_update(
            constraint="uq_instruments_identity",
            set_={
                "name": stmt.excluded.name,
                "isin": func.coalesce(stmt.excluded.isin, Instrument.isin),
                "instrument_type": stmt.excluded.instrument_type,
                "lot_size": stmt.excluded.lot_size,
                "tick_size": stmt.excluded.tick_size,
                "underlying": stmt.excluded.underlying,
                "is_index": stmt.excluded.is_index,
                "is_active": True,
                "updated_at": func.now(),
            },
        ).returning(
            Instrument.id,
            Instrument.exchange,
            Instrument.segment,
            Instrument.symbol,
            Instrument.expiry,
            Instrument.strike,
            Instrument.option_type,
        )
        for row in (await db.execute(stmt)).all():
            id_by_identity[(row.exchange, row.segment, row.symbol, row.expiry, row.strike, row.option_type)] = row.id

    await db.execute(delete(InstrumentProviderKey).where(InstrumentProviderKey.provider_type == provider_type))
    key_rows = [
        {
            "instrument_id": id_by_identity[_identity(r)],
            "provider_type": provider_type,
            "provider_key": r.provider_key,
            "provider_token": r.provider_token,
        }
        for r in unique
    ]
    for i in range(0, len(key_rows), BATCH):
        await db.execute(insert(InstrumentProviderKey).values(key_rows[i : i + BATCH]).on_conflict_do_nothing())
    return {"received": len(records), "instruments": len(unique)}


async def sync_from_provider(db: AsyncSession, provider: MarketDataProvider) -> dict[str, Any]:
    records = await provider.get_instruments()
    if not records:
        raise AppError("The provider returned an empty instrument list")
    result = await upsert_instruments(db, provider.provider_type, records, simulated=provider.is_simulated)
    if isinstance(provider, SimulatedProvider):
        await _seed_simulated_metadata(db)
    await db.commit()
    logger.info("Instrument sync complete for %s: %s", provider.provider_type, result)
    return result


async def _seed_simulated_metadata(db: AsyncSession) -> None:
    sectors = SimulatedProvider.sectors()
    for symbol, sector in sectors.items():
        await db.execute(
            update(Instrument)
            .where(Instrument.symbol == symbol, Instrument.is_simulated.is_(True))
            .values(sector=sector, industry=sector)
        )
    start = date(2021, 1, 4)
    for index_code, members in SIM_INDEX_MEMBERS.items():
        code = index_code.removeprefix("SIM")
        ids = (
            await db.scalars(
                select(Instrument.id).where(Instrument.symbol.in_(members), Instrument.is_simulated.is_(True))
            )
        ).all()
        await db.execute(delete(IndexConstituent).where(IndexConstituent.index_code == f"SIM:{code}"))
        for iid in ids:
            db.add(IndexConstituent(index_code=f"SIM:{code}", instrument_id=iid, start_date=start, source="simulated"))


def mode_filter(simulated: bool):  # noqa: ANN201
    return Instrument.is_simulated.is_(simulated)


async def search(db: AsyncSession, q: str, limit: int = 20, segment: str | None = None) -> list[Instrument]:
    simulated = await is_simulated_mode(db)
    term = q.strip()
    if not term:
        return []
    like = f"%{term}%"
    stmt = select(Instrument).where(
        mode_filter(simulated),
        Instrument.is_active.is_(True),
        or_(Instrument.symbol.ilike(like), Instrument.name.ilike(like), Instrument.isin == term.upper()),
    )
    if segment:
        stmt = stmt.where(Instrument.segment == segment)
    stmt = stmt.order_by(
        (func.upper(Instrument.symbol) == term.upper()).desc(),
        Instrument.symbol.ilike(f"{term}%").desc(),
        Instrument.is_index.desc(),
        (Instrument.exchange == "NSE").desc(),
        func.length(Instrument.symbol),
        Instrument.symbol,
    )
    return list((await db.scalars(stmt.limit(limit))).all())


async def resolve_symbol(db: AsyncSession, symbol: str, exchange: str | None = None) -> Instrument:
    """Find an instrument by symbol (or numeric id) in the current data mode, preferring NSE equities."""
    simulated = await is_simulated_mode(db)
    if symbol.isdigit():
        inst = await db.get(Instrument, int(symbol))
        if inst and inst.is_simulated == simulated:
            return inst
    stmt = select(Instrument).where(
        mode_filter(simulated), func.upper(Instrument.symbol) == symbol.upper(), Instrument.expiry.is_(None)
    )
    if exchange:
        stmt = stmt.where(Instrument.exchange == exchange.upper())
    stmt = stmt.order_by((Instrument.exchange == "NSE").desc(), (Instrument.segment == "EQ").desc())
    inst = await db.scalar(stmt.limit(1))
    if inst is None:
        raise NotFoundError(f"Instrument '{symbol}' not found. Sync the instrument master in Data Provider Settings.")
    return inst


async def provider_keys(db: AsyncSession, provider_type: str, instrument_ids: Iterable[int]) -> dict[int, str]:
    ids = list(set(instrument_ids))
    if not ids:
        return {}
    rows = await db.execute(
        select(InstrumentProviderKey.instrument_id, InstrumentProviderKey.provider_key).where(
            InstrumentProviderKey.provider_type == provider_type, InstrumentProviderKey.instrument_id.in_(ids)
        )
    )
    return {r.instrument_id: r.provider_key for r in rows.all()}


async def dashboard_indices(db: AsyncSession) -> list[dict[str, Any]]:
    """Resolve configured dashboard indices to instruments in the current data mode."""
    simulated = await is_simulated_mode(db)
    cfg = (await app_settings.get_section(db, "market"))["dashboard_indices"]
    out = []
    for entry in cfg:
        if simulated:
            inst = await db.scalar(
                select(Instrument).where(Instrument.is_simulated.is_(True), Instrument.symbol == f"SIM{entry['code']}")
            )
        else:
            names = [n.lower() for n in entry.get("names", [])]
            inst = await db.scalar(
                select(Instrument)
                .where(
                    Instrument.is_simulated.is_(False),
                    Instrument.is_index.is_(True),
                    Instrument.exchange == entry.get("exchange", "NSE"),
                    or_(func.lower(Instrument.name).in_(names), func.lower(Instrument.symbol).in_(names)),
                )
                .limit(1)
            )
        out.append({**entry, "instrument": inst})
    return out


async def index_members(db: AsyncSession, index_code: str, on: date | None = None) -> list[int]:
    simulated = await is_simulated_mode(db)
    code = f"SIM:{index_code}" if simulated else index_code
    stmt = select(IndexConstituent.instrument_id).where(IndexConstituent.index_code == code)
    if on:
        stmt = stmt.where(
            IndexConstituent.start_date <= on, or_(IndexConstituent.end_date.is_(None), IndexConstituent.end_date >= on)
        )
    else:
        stmt = stmt.where(IndexConstituent.end_date.is_(None))
    return list((await db.scalars(stmt)).all())


async def tracked_universe(db: AsyncSession, limit: int | None = None) -> list[int]:
    """Instruments the worker streams and refreshes: indices, holdings, watchlists, alerts, universe index."""
    simulated = await is_simulated_mode(db)
    market = await app_settings.get_section(db, "market")
    limit = limit or int(market.get("max_stream_instruments", 500))
    ordered: list[int] = []

    def add(ids: Iterable[int]) -> None:
        for i in ids:
            if i not in ordered:
                ordered.append(i)

    add(e["instrument"].id for e in await dashboard_indices(db) if e["instrument"] is not None)
    add((await db.scalars(select(Transaction.instrument_id).distinct())).all())
    add((await db.scalars(select(JournalTrade.instrument_id).where(JournalTrade.status == "OPEN").distinct())).all())
    add((await db.scalars(select(WatchlistItem.instrument_id).distinct())).all())
    add(
        (
            await db.scalars(
                select(Alert.instrument_id)
                .where(Alert.is_active.is_(True), Alert.instrument_id.is_not(None))
                .distinct()
            )
        ).all()
    )
    add(await index_members(db, market.get("tracked_universe_index", "NIFTY50")))
    if not ordered:
        return []
    valid = set(
        (await db.scalars(select(Instrument.id).where(Instrument.id.in_(ordered), mode_filter(simulated)))).all()
    )
    return [i for i in ordered if i in valid][:limit]


async def import_constituents_csv(db: AsyncSession, index_code: str, content: bytes, start: date) -> dict[str, Any]:
    """Import an NSE index constituent CSV (columns: Company Name, Industry, Symbol, Series, ISIN Code).

    Also records each company's industry as its sector. Members missing from the new file get an end date,
    which keeps membership point-in-time for survivorship-bias-aware backtests.
    """
    text = content.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames or "Symbol" not in reader.fieldnames:
        raise AppError("CSV must have a 'Symbol' column (NSE index constituent format)")
    matched, missing = [], []
    for row in reader:
        sym = (row.get("Symbol") or "").strip().upper()
        if not sym:
            continue
        inst = await db.scalar(
            select(Instrument).where(
                Instrument.is_simulated.is_(False),
                Instrument.exchange == "NSE",
                Instrument.segment == "EQ",
                func.upper(Instrument.symbol) == sym,
            )
        )
        if inst is None:
            missing.append(sym)
            continue
        industry = (row.get("Industry") or "").strip()
        if industry:
            inst.sector = industry
            inst.industry = industry
        if row.get("Company Name"):
            inst.name = row["Company Name"].strip()
        matched.append(inst.id)
    current = set(await index_members(db, index_code))
    for iid in current - set(matched):
        await db.execute(
            update(IndexConstituent)
            .where(
                IndexConstituent.index_code == index_code,
                IndexConstituent.instrument_id == iid,
                IndexConstituent.end_date.is_(None),
            )
            .values(end_date=start)
        )
    for iid in set(matched) - current:
        db.add(IndexConstituent(index_code=index_code, instrument_id=iid, start_date=start, source="csv_import"))
    await db.commit()
    return {
        "index_code": index_code,
        "matched": len(matched),
        "added": len(set(matched) - current),
        "removed": len(current - set(matched)),
        "unmatched_symbols": missing[:50],
    }
