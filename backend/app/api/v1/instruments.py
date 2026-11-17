from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from fastapi import APIRouter, File, Form, Query, Request, UploadFile
from sqlalchemy import func, select

from app.api.deps import DB, AdminUser, CurrentUser
from app.core.errors import AppError
from app.db.models import IndexConstituent, Instrument, InstrumentProviderKey
from app.marketdata.types import INTERVALS
from app.schemas.market import instrument_dict
from app.services import candles as candle_service
from app.services import instruments as instrument_service
from app.services.audit import audit
from app.services.market_data import cached_quotes

router = APIRouter(tags=["instruments"])
MAX_UPLOAD = 5 * 1024 * 1024


@router.get("/instruments/search")
async def search(
    _: CurrentUser,
    db: DB,
    q: str = Query(..., min_length=1, max_length=50),
    limit: int = Query(20, ge=1, le=50),
    segment: str | None = Query(None, pattern="^(EQ|INDEX|FO)$"),
) -> list[dict[str, Any]]:
    return [instrument_dict(i) for i in await instrument_service.search(db, q, limit, segment)]


@router.get("/instruments/summary")
async def summary(_: CurrentUser, db: DB) -> dict[str, Any]:
    counts = (
        await db.execute(
            select(Instrument.is_simulated, Instrument.segment, func.count()).group_by(
                Instrument.is_simulated, Instrument.segment
            )
        )
    ).all()
    providers = (
        await db.execute(
            select(InstrumentProviderKey.provider_type, func.count()).group_by(InstrumentProviderKey.provider_type)
        )
    ).all()
    indices = (
        await db.execute(
            select(IndexConstituent.index_code, func.count())
            .where(IndexConstituent.end_date.is_(None))
            .group_by(IndexConstituent.index_code)
        )
    ).all()
    return {
        "instruments": [{"simulated": s, "segment": seg, "count": c} for s, seg, c in counts],
        "provider_mappings": {p: c for p, c in providers},
        "index_constituents": {code: c for code, c in indices},
    }


@router.get("/instruments/{symbol}")
async def get_instrument(symbol: str, _: CurrentUser, db: DB, exchange: str | None = None) -> dict[str, Any]:
    inst = await instrument_service.resolve_symbol(db, symbol, exchange)
    q = await cached_quotes(db, [inst.id])
    return {"instrument": instrument_dict(inst), "quote": q.get(inst.id)}


@router.post("/instruments/import-constituents")
async def import_constituents(
    request: Request,
    admin: AdminUser,
    db: DB,
    file: UploadFile = File(...),
    index_code: str = Form(..., pattern="^[A-Z0-9_]{2,40}$"),
    effective_date: date | None = Form(None),
) -> dict[str, Any]:
    content = await file.read(MAX_UPLOAD + 1)
    if len(content) > MAX_UPLOAD:
        raise AppError("File too large (max 5 MB)", status_code=413)
    result = await instrument_service.import_constituents_csv(
        db, index_code, content, effective_date or datetime.now(UTC).date()
    )
    await audit(
        db,
        action="instruments.import_constituents",
        user_id=admin.id,
        request=request,
        details={"index_code": index_code, "matched": result["matched"]},
        commit=True,
    )
    return result


@router.get("/stocks/{symbol}/candles")
async def candles(
    symbol: str,
    _: CurrentUser,
    db: DB,
    interval: str = Query("1d"),
    start: datetime | None = None,
    end: datetime | None = None,
    exchange: str | None = None,
) -> dict[str, Any]:
    if interval not in INTERVALS:
        raise AppError(f"interval must be one of {', '.join(INTERVALS)}", status_code=422)
    inst = await instrument_service.resolve_symbol(db, symbol, exchange)
    res = await candle_service.get_candles(db, inst, interval, start, end)
    return {
        "instrument": instrument_dict(inst),
        "interval": interval,
        "data_status": res.data_status,
        "source": res.source,
        "fetched_from_provider": res.fetched_from_provider,
        "warnings": res.warnings,
        "as_of": res.bars[-1].ts.isoformat() if res.bars else None,
        "candles": [
            {
                "ts": b.ts.isoformat(),
                "open": b.open,
                "high": b.high,
                "low": b.low,
                "close": b.close,
                "volume": b.volume,
                "oi": b.oi,
            }
            for b in res.bars
        ],
    }
