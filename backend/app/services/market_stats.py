"""Market-wide statistics over the tracked universe: breadth, movers, sectors and heatmap.

Figures cover the instruments this platform tracks (the configured universe index plus holdings and
watchlists), not the entire exchange; responses state the universe size so this is always clear.
Live quotes are used when present; otherwise the last computed daily metrics.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Instrument, InstrumentMetrics
from app.schemas.market import instrument_dict
from app.services import instruments as instrument_service
from app.services.market_data import cached_quotes, is_simulated_mode


async def universe_rows(db: AsyncSession) -> list[dict[str, Any]]:
    simulated = await is_simulated_mode(db)
    ids = await instrument_service.tracked_universe(db, limit=5000)
    if not ids:
        return []
    rows = (
        await db.execute(
            select(Instrument, InstrumentMetrics)
            .outerjoin(
                InstrumentMetrics,
                (InstrumentMetrics.instrument_id == Instrument.id) & (InstrumentMetrics.is_simulated.is_(simulated)),
            )
            .where(Instrument.id.in_(ids), Instrument.is_index.is_(False))
        )
    ).all()
    quotes = await cached_quotes(db, [i.id for i, _ in rows])
    out = []
    for inst, met in rows:
        q = quotes.get(inst.id)
        change = q.get("change_pct") if q else (met.change_pct if met else None)
        price = q.get("ltp") if q else (met.last_close if met else None)
        volume = q.get("volume") if q and q.get("volume") is not None else (met.volume if met else None)
        out.append(
            {
                "instrument": inst,
                "metrics": met,
                "quote": q,
                "change_pct": change,
                "price": price,
                "volume": volume,
                "turnover": (price or 0) * (volume or 0),
                "data_status": q["data_status"] if q else ("SIMULATED" if simulated else "HISTORICAL") if met else None,
                "as_of": (q.get("received_at") if q else (met.as_of.isoformat() if met else None)),
            }
        )
    return out


def _brief(r: dict[str, Any]) -> dict[str, Any]:
    i: Instrument = r["instrument"]
    return {
        "instrument_id": i.id,
        "symbol": i.symbol,
        "name": i.name,
        "sector": i.sector,
        "price": r["price"],
        "change_pct": r["change_pct"],
        "volume": r["volume"],
        "data_status": r["data_status"],
        "as_of": r["as_of"],
    }


async def overview(db: AsyncSession, top: int = 8) -> dict[str, Any]:
    rows = await universe_rows(db)
    priced = [r for r in rows if r["change_pct"] is not None]
    adv = sum(1 for r in priced if r["change_pct"] > 0)
    dec = sum(1 for r in priced if r["change_pct"] < 0)
    unch = len(priced) - adv - dec
    by_change = sorted(priced, key=lambda r: r["change_pct"], reverse=True)
    gainers = [_brief(r) for r in by_change[:top] if r["change_pct"] > 0]
    losers = [_brief(r) for r in reversed(by_change[-top:]) if r["change_pct"] < 0]
    active = [_brief(r) for r in sorted(rows, key=lambda r: r["volume"] or 0, reverse=True)[:top] if r["volume"]]
    statuses = {r["data_status"] for r in priced if r["data_status"]}
    above_200 = sum(1 for r in rows if r["metrics"] and (r["metrics"].flags or {}).get("above_ema200"))
    with_metrics = sum(1 for r in rows if r["metrics"])
    return {
        "universe_size": len(rows),
        "priced": len(priced),
        "data_statuses": sorted(statuses),
        "breadth": {
            "advances": adv,
            "declines": dec,
            "unchanged": unch,
            "advance_decline_ratio": round(adv / dec, 2) if dec else None,
            "pct_above_ema200": round(above_200 / with_metrics * 100, 1) if with_metrics else None,
        },
        "total_volume": sum(r["volume"] or 0 for r in rows),
        "total_turnover": sum(r["turnover"] for r in rows),
        "gainers": gainers,
        "losers": losers,
        "most_active": active,
        "sectors": sector_performance(rows),
    }


def sector_performance(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        if r["change_pct"] is not None:
            groups[r["instrument"].sector or "Unclassified"].append(r)
    out = []
    for sector, members in groups.items():
        changes = [m["change_pct"] for m in members]
        rs = [m["metrics"].rel_strength for m in members if m["metrics"] and m["metrics"].rel_strength is not None]
        r1m = [m["metrics"].ret_1m for m in members if m["metrics"] and m["metrics"].ret_1m is not None]
        out.append(
            {
                "sector": sector,
                "count": len(members),
                "avg_change_pct": sum(changes) / len(changes),
                "advancers": sum(1 for c in changes if c > 0),
                "avg_ret_1m": sum(r1m) / len(r1m) if r1m else None,
                "avg_rel_strength": sum(rs) / len(rs) if rs else None,
            }
        )
    return sorted(out, key=lambda s: s["avg_change_pct"], reverse=True)


async def heatmap(db: AsyncSession) -> dict[str, Any]:
    rows = [r for r in await universe_rows(db) if r["change_pct"] is not None]
    # One sizing basis for every tile; mixing market cap (INR crore) with turnover (INR) would distort areas.
    if rows and all(r["metrics"] and r["metrics"].market_cap for r in rows):
        basis = "market_cap"
    elif rows and all(r["turnover"] for r in rows):
        basis = "turnover"
    else:
        basis = "equal"
    tiles = []
    for r in rows:
        weight = r["metrics"].market_cap if basis == "market_cap" else r["turnover"] if basis == "turnover" else 1.0
        tiles.append(
            {**_brief(r), "weight": weight, "weight_basis": basis, "instrument": instrument_dict(r["instrument"])}
        )
    return {"tiles": tiles, "weight_basis": basis, "sectors": sector_performance(rows)}
