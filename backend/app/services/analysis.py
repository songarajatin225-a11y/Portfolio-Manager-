"""Per-instrument analysis: technical snapshot, fundamentals loading and the metrics refresh job."""

from __future__ import annotations

import logging
import math
from datetime import UTC, datetime, timedelta
from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics import indicators as ind
from app.analytics.fundamentals import Period, compute_ratios, screener_values
from app.db.models import Fundamental, Instrument, InstrumentMetrics
from app.services import app_settings
from app.services import instruments as instrument_service
from app.services.candles import get_candles
from app.services.market_data import cached_quotes, is_simulated_mode

logger = logging.getLogger(__name__)


def _f(v: Any) -> float | None:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


async def load_daily(db: AsyncSession, inst: Instrument, days: int = 420, allow_fetch: bool = True) -> pd.DataFrame:
    end = datetime.now(UTC)
    res = await get_candles(db, inst, "1d", end - timedelta(days=days), end, allow_fetch=allow_fetch)
    return ind.to_frame(res.bars)


def technical_snapshot(df: pd.DataFrame, benchmark: pd.DataFrame | None = None) -> dict[str, Any]:
    """Latest technical values and boolean screening flags from completed daily bars."""
    if len(df) < 2:
        return {"bars_used": len(df)}
    close, vol = df["close"], df["volume"]
    m = ind.macd(close)
    st = ind.supertrend(df)
    adx = ind.adx(df)
    e20, e50, e200 = ind.ema(close, 20), ind.ema(close, 50), ind.ema(close, 200)
    s50, s200 = ind.sma(close, 50), ind.sma(close, 200)
    rsi = ind.rsi(close)
    avg_vol20 = vol.rolling(20, min_periods=20).mean().shift(1)  # excludes today's bar
    year = df.tail(252)
    last = float(close.iloc[-1])
    out: dict[str, Any] = {
        "last_close": last,
        "prev_close": float(close.iloc[-2]),
        "change_pct": (last / float(close.iloc[-2]) - 1) * 100 if close.iloc[-2] else None,
        "volume": float(vol.iloc[-1]),
        "avg_volume_20": _f(avg_vol20.iloc[-1]),
        "rsi_14": _f(rsi.iloc[-1]),
        "macd": _f(m["macd"].iloc[-1]),
        "macd_signal": _f(m["signal"].iloc[-1]),
        "macd_hist": _f(m["hist"].iloc[-1]),
        "ema_20": _f(e20.iloc[-1]),
        "ema_50": _f(e50.iloc[-1]),
        "ema_200": _f(e200.iloc[-1]),
        "sma_50": _f(s50.iloc[-1]),
        "sma_200": _f(s200.iloc[-1]),
        "atr_14": _f(ind.atr(df).iloc[-1]),
        "adx_14": _f(adx["adx"].iloc[-1]),
        "supertrend_dir": int(st["direction"].iloc[-1]) if not np.isnan(st["direction"].iloc[-1]) else None,
        "ret_1w": ind.returns(close, 5),
        "ret_1m": ind.returns(close, 21),
        "ret_3m": ind.returns(close, 63),
        "ret_6m": ind.returns(close, 126),
        "ret_1y": ind.returns(close, 252),
        "high_52w": float(year["high"].max()),
        "low_52w": float(year["low"].min()),
        "volatility_20": _f(ind.annualized_volatility(close).iloc[-1]),
        "bars_used": len(df),
    }
    out["volume_ratio"] = (out["volume"] / out["avg_volume_20"]) if out["avg_volume_20"] else None
    if benchmark is not None and len(benchmark) > 63 and out["ret_3m"] is not None:
        b = ind.returns(benchmark["close"], 63)
        out["rel_strength"] = out["ret_3m"] - b if b is not None else None
    else:
        out["rel_strength"] = None
    vr = out["volume_ratio"] or 0
    prior_high = float(df["high"].iloc[-253:-1].max()) if len(df) > 20 else None
    prior_low = float(df["low"].iloc[-253:-1].min()) if len(df) > 20 else None
    out["flags"] = {
        "macd_cross_up": ind.crossed_above(m["macd"], m["signal"], within=3),
        "macd_cross_down": ind.crossed_below(m["macd"], m["signal"], within=3),
        "golden_cross": ind.crossed_above(s50, s200, within=5),
        "death_cross": ind.crossed_below(s50, s200, within=5),
        "ema20_cross_up": ind.crossed_above(close, e20, within=3),
        "above_ema20": bool(out["ema_20"] and last > out["ema_20"]),
        "above_ema50": bool(out["ema_50"] and last > out["ema_50"]),
        "above_ema200": bool(out["ema_200"] and last > out["ema_200"]),
        "volume_breakout": bool(vr >= 2 and out["change_pct"] and out["change_pct"] > 0),
        "breakout_52w": bool(prior_high and last >= prior_high),
        "breakdown_52w": bool(prior_low and last <= prior_low),
        "rsi_overbought": bool(out["rsi_14"] and out["rsi_14"] >= 70),
        "rsi_oversold": bool(out["rsi_14"] and out["rsi_14"] <= 30),
        "supertrend_bull": out["supertrend_dir"] == 1,
        "strong_trend": bool(out["adx_14"] and out["adx_14"] >= 25),
    }
    return out


async def fundamental_periods(db: AsyncSession, instrument_id: int) -> list[Period]:
    rows = (
        await db.scalars(
            select(Fundamental)
            .where(Fundamental.instrument_id == instrument_id)
            .order_by(Fundamental.period_end.desc())
        )
    ).all()
    preferred = {}
    for r in rows:  # prefer consolidated statements when both exist for a period
        key = (r.period_type, r.period_end)
        if key not in preferred or r.statement_type == "consolidated":
            preferred[key] = r
    return [
        Period(
            period_type=r.period_type,
            period_end=r.period_end,
            source=r.source,
            reported_at=r.reported_at,
            statement_type=r.statement_type,
            values={
                f: getattr(r, f)
                for f in (
                    "revenue",
                    "ebitda",
                    "ebit",
                    "net_profit",
                    "eps",
                    "total_equity",
                    "total_debt",
                    "total_assets",
                    "current_liabilities",
                    "cash",
                    "operating_cash_flow",
                    "capex",
                    "shares_outstanding",
                    "dividend_per_share",
                )
            },
            validation_errors=list(r.validation_errors or []),
        )
        for r in preferred.values()
    ]


async def benchmark_frame(db: AsyncSession) -> pd.DataFrame | None:
    code = (await app_settings.get_section(db, "market")).get("benchmark_index", "NIFTY50")
    for entry in await instrument_service.dashboard_indices(db):
        if entry["code"] == code and entry["instrument"] is not None:
            return await load_daily(db, entry["instrument"])
    return None


async def refresh_metrics(db: AsyncSession, instrument_ids: list[int] | None = None, allow_fetch: bool = True) -> int:
    simulated = await is_simulated_mode(db)
    ids = instrument_ids or await instrument_service.tracked_universe(db, limit=5000)
    if not ids:
        return 0
    insts = (
        await db.scalars(select(Instrument).where(Instrument.id.in_(ids), Instrument.is_simulated.is_(simulated)))
    ).all()
    bench = await benchmark_frame(db)
    quotes = await cached_quotes(db, [i.id for i in insts])
    count = 0
    for inst in insts:
        try:
            df = await load_daily(db, inst, allow_fetch=allow_fetch)
        except Exception:  # noqa: BLE001 - one bad instrument must not stop the refresh
            logger.exception("Daily candles unavailable for %s", inst.symbol)
            continue
        if len(df) < 2:
            continue
        snap = technical_snapshot(df, bench if not inst.is_index else None)
        q = quotes.get(inst.id)
        price = q["ltp"] if q else snap["last_close"]
        ratios = compute_ratios(
            await fundamental_periods(db, inst.id),
            price,
            q["source"] if q else "last daily close",
            q["received_at"] if q else None,
        )
        fvals = screener_values(ratios)
        if q and q.get("change_pct") is not None:  # live day change overrides the last completed bar's change
            snap["change_pct"] = q["change_pct"]
        flags = snap.pop("flags", {})
        row = {
            k: snap.get(k)
            for k in (
                "last_close",
                "prev_close",
                "change_pct",
                "volume",
                "avg_volume_20",
                "volume_ratio",
                "rsi_14",
                "macd",
                "macd_signal",
                "macd_hist",
                "ema_20",
                "ema_50",
                "ema_200",
                "sma_50",
                "sma_200",
                "atr_14",
                "adx_14",
                "supertrend_dir",
                "ret_1w",
                "ret_1m",
                "ret_3m",
                "ret_6m",
                "ret_1y",
                "rel_strength",
                "high_52w",
                "low_52w",
                "volatility_20",
                "bars_used",
            )
        }
        stmt = insert(InstrumentMetrics).values(
            instrument_id=inst.id,
            is_simulated=simulated,
            as_of=df.index[-1].to_pydatetime(),
            flags=flags,
            fundamentals=fvals,
            market_cap=fvals.get("market_cap"),
            source="simulated" if simulated else "stored daily candles",
            **row,
        )
        update_cols = {
            **{k: getattr(stmt.excluded, k) for k in row},
            "as_of": stmt.excluded.as_of,
            "flags": stmt.excluded.flags,
            "fundamentals": stmt.excluded.fundamentals,
            "market_cap": stmt.excluded.market_cap,
            "source": stmt.excluded.source,
        }
        await db.execute(stmt.on_conflict_do_update(index_elements=["instrument_id", "is_simulated"], set_=update_cols))
        count += 1
    # merge recent news sentiment into the screener scores (ML scores are left untouched)
    from sqlalchemy import literal, update
    from sqlalchemy.dialects.postgresql import JSONB

    from app.news.ingest import instrument_sentiment

    for iid, sent in (await instrument_sentiment(db)).items():
        await db.execute(
            update(InstrumentMetrics)
            .where(InstrumentMetrics.instrument_id == iid, InstrumentMetrics.is_simulated.is_(simulated))
            .values(
                scores=InstrumentMetrics.scores.op("||")(
                    literal({"sentiment_score": round(sent["score"], 3), "sentiment_articles": sent["articles"]}, JSONB)
                )
            )
        )
    await db.commit()
    return count
