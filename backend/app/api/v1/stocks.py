"""Stock research endpoints: technicals, fundamentals, institutional data and performance."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import numpy as np
import pandas as pd
from fastapi import APIRouter, Query
from sqlalchemy import select

from app.analytics import indicators as ind
from app.analytics.fundamentals import compute_ratios
from app.api.deps import DB, CurrentUser
from app.core.errors import AppError
from app.db.models import BulkBlockDeal, Fundamental, InstitutionalFlow, InstrumentMetrics, Shareholding
from app.marketdata.types import INTERVALS
from app.schemas.market import instrument_dict
from app.services import instruments as instrument_service
from app.services.analysis import benchmark_frame, fundamental_periods, technical_snapshot
from app.services.candles import get_candles
from app.services.market_data import cached_quotes

router = APIRouter(tags=["stocks"])


def _series(s, decimals: int = 4) -> list[float | None]:  # noqa: ANN001
    return [None if (v is None or (isinstance(v, float) and np.isnan(v))) else round(float(v), decimals) for v in s]


@router.get("/stocks/{symbol}/technicals")
async def technicals(
    symbol: str, _: CurrentUser, db: DB, interval: str = Query("1d"), exchange: str | None = None
) -> dict[str, Any]:
    if interval not in INTERVALS:
        raise AppError(f"interval must be one of {', '.join(INTERVALS)}", status_code=422)
    inst = await instrument_service.resolve_symbol(db, symbol, exchange)
    res = await get_candles(db, inst, interval)
    df = ind.to_frame(res.bars)
    if df.empty:
        return {
            "instrument": instrument_dict(inst),
            "interval": interval,
            "data_status": res.data_status,
            "source": res.source,
            "warnings": res.warnings or ["No candle data available"],
            "series": {},
            "latest": {},
            "levels": {},
        }
    close = df["close"]
    bb = ind.bollinger(close)
    m = ind.macd(close)
    st = ind.supertrend(df)
    adx = ind.adx(df)
    series = {
        "ts": [t.isoformat() for t in df.index],
        "sma_20": _series(ind.sma(close, 20)),
        "sma_50": _series(ind.sma(close, 50)),
        "sma_200": _series(ind.sma(close, 200)),
        "ema_20": _series(ind.ema(close, 20)),
        "ema_50": _series(ind.ema(close, 50)),
        "ema_200": _series(ind.ema(close, 200)),
        "bb_upper": _series(bb["upper"]),
        "bb_middle": _series(bb["middle"]),
        "bb_lower": _series(bb["lower"]),
        "rsi_14": _series(ind.rsi(close)),
        "macd": _series(m["macd"]),
        "macd_signal": _series(m["signal"]),
        "macd_hist": _series(m["hist"]),
        "atr_14": _series(ind.atr(df)),
        "adx_14": _series(adx["adx"]),
        "plus_di": _series(adx["plus_di"]),
        "minus_di": _series(adx["minus_di"]),
        "supertrend": _series(st["supertrend"]),
        "supertrend_dir": _series(st["direction"], 0),
    }
    if interval in ("1m", "5m", "15m", "30m", "1h"):
        from app.marketdata.calendar import IST

        session = df.index.tz_convert(IST).date
        series["vwap"] = _series(ind.vwap(df, session_key=pd.Series(session, index=df.index)))
    bench = await benchmark_frame(db) if interval == "1d" and not inst.is_index else None
    snap = technical_snapshot(df, bench) if interval == "1d" else {}
    fib = ind.fibonacci(df, lookback=120)
    sr = ind.support_resistance(df)
    signals = _signals(snap) if snap else []
    return {
        "instrument": instrument_dict(inst),
        "interval": interval,
        "data_status": res.data_status,
        "source": res.source,
        "as_of": df.index[-1].isoformat(),
        "warnings": res.warnings,
        "series": series,
        "latest": {k: v for k, v in snap.items() if k != "flags"},
        "flags": snap.get("flags", {}),
        "signals": signals,
        "levels": {"fibonacci": fib.__dict__ if fib else None, **sr},
        "volume_profile": ind.volume_profile(df.tail(120)),
    }


def _signals(s: dict[str, Any]) -> list[dict[str, str]]:
    """Plain-language reading of the indicators (descriptive, not a recommendation)."""
    out: list[dict[str, str]] = []
    rsi = s.get("rsi_14")
    if rsi is not None:
        tone = "bearish" if rsi >= 70 else "bullish" if rsi <= 30 else "neutral"
        state = "overbought" if rsi >= 70 else "oversold" if rsi <= 30 else "neutral range"
        out.append({"indicator": "RSI (14)", "reading": f"{rsi:.1f} — {state}", "tone": tone})
    if s.get("macd") is not None and s.get("macd_signal") is not None:
        above = s["macd"] > s["macd_signal"]
        out.append(
            {
                "indicator": "MACD",
                "reading": "MACD above signal" if above else "MACD below signal",
                "tone": "bullish" if above else "bearish",
            }
        )
    if s.get("ema_200") and s.get("last_close"):
        above = s["last_close"] > s["ema_200"]
        out.append(
            {
                "indicator": "200-day EMA",
                "reading": "Price above long-term trend" if above else "Price below long-term trend",
                "tone": "bullish" if above else "bearish",
            }
        )
    if s.get("adx_14") is not None:
        strong = s["adx_14"] >= 25
        out.append(
            {
                "indicator": "ADX (14)",
                "reading": f"{s['adx_14']:.1f} — {'trending' if strong else 'weak trend'}",
                "tone": "neutral",
            }
        )
    if s.get("supertrend_dir") is not None:
        bull = s["supertrend_dir"] == 1
        out.append(
            {
                "indicator": "Supertrend (10, 3)",
                "reading": "Uptrend" if bull else "Downtrend",
                "tone": "bullish" if bull else "bearish",
            }
        )
    if s.get("volume_ratio"):
        out.append(
            {"indicator": "Volume", "reading": f"{s['volume_ratio']:.2f}× the 20-day average", "tone": "neutral"}
        )
    return out


@router.get("/stocks/{symbol}/fundamentals")
async def fundamentals(symbol: str, _: CurrentUser, db: DB, exchange: str | None = None) -> dict[str, Any]:
    inst = await instrument_service.resolve_symbol(db, symbol, exchange)
    q = (await cached_quotes(db, [inst.id])).get(inst.id)
    price, price_source, price_as_of = (q["ltp"], q["source"], q["received_at"]) if q else (None, None, None)
    if price is None:
        met = await db.scalar(select(InstrumentMetrics).where(InstrumentMetrics.instrument_id == inst.id))
        if met and met.last_close:
            price, price_source, price_as_of = met.last_close, "last daily close", met.as_of.isoformat()
    periods = await fundamental_periods(db, inst.id)
    rows = (
        await db.scalars(
            select(Fundamental).where(Fundamental.instrument_id == inst.id).order_by(Fundamental.period_end.desc())
        )
    ).all()
    return {
        "instrument": instrument_dict(inst),
        "ratios": compute_ratios(periods, price, price_source, price_as_of),
        "periods": [
            {
                "period_type": r.period_type,
                "period_end": r.period_end,
                "statement_type": r.statement_type,
                "units": r.units,
                "source": r.source,
                "source_ref": r.source_ref,
                "reported_at": r.reported_at,
                "validation_errors": r.validation_errors,
                **{
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
            }
            for r in rows
        ],
    }


@router.get("/stocks/{symbol}/institutional")
async def institutional(symbol: str, _: CurrentUser, db: DB, exchange: str | None = None) -> dict[str, Any]:
    inst = await instrument_service.resolve_symbol(db, symbol, exchange)
    holdings = (
        await db.scalars(
            select(Shareholding).where(Shareholding.instrument_id == inst.id).order_by(Shareholding.period_end)
        )
    ).all()
    deals = (
        await db.scalars(
            select(BulkBlockDeal)
            .where(BulkBlockDeal.instrument_id == inst.id)
            .order_by(BulkBlockDeal.trade_date.desc())
            .limit(100)
        )
    ).all()
    return {
        "instrument": instrument_dict(inst),
        "shareholding": [
            {
                "period_end": h.period_end,
                "promoter_pct": h.promoter_pct,
                "fii_pct": h.fii_pct,
                "dii_pct": h.dii_pct,
                "public_pct": h.public_pct,
                "others_pct": h.others_pct,
                "source": h.source,
                "reported_at": h.reported_at,
            }
            for h in holdings
        ],
        "deals": [
            {
                "date": d.trade_date,
                "deal_type": d.deal_type,
                "client_name": d.client_name,
                "side": d.side,
                "quantity": d.quantity,
                "price": d.price,
                "source": d.source,
            }
            for d in deals
        ],
    }


@router.get("/market/institutional-flows")
async def institutional_flows(_: CurrentUser, db: DB, days: int = Query(60, ge=1, le=730)) -> dict[str, Any]:
    since = datetime.now(UTC).date() - timedelta(days=days)
    rows = (
        await db.scalars(
            select(InstitutionalFlow)
            .where(InstitutionalFlow.trade_date >= since)
            .order_by(InstitutionalFlow.trade_date)
        )
    ).all()
    return {
        "items": [
            {
                "date": r.trade_date,
                "category": r.category,
                "segment": r.segment,
                "buy_value": r.buy_value,
                "sell_value": r.sell_value,
                "net_value": r.net_value,
                "source": r.source,
            }
            for r in rows
        ]
    }


@router.get("/stocks/{symbol}/performance")
async def performance(symbol: str, _: CurrentUser, db: DB, exchange: str | None = None) -> dict[str, Any]:
    inst = await instrument_service.resolve_symbol(db, symbol, exchange)
    end = datetime.now(UTC)
    res = await get_candles(db, inst, "1d", end - timedelta(days=365 * 5 + 10), end)
    df = ind.to_frame(res.bars)
    if df.empty:
        return {
            "instrument": instrument_dict(inst),
            "returns": {},
            "data_status": res.data_status,
            "source": res.source,
        }
    close = df["close"]
    periods = {"1W": 5, "1M": 21, "3M": 63, "6M": 126, "1Y": 252, "3Y": 756, "5Y": 1260}
    rets = {k: ind.returns(close, n) for k, n in periods.items()}
    ytd_base = close[close.index.year < end.year]
    rets["YTD"] = float(close.iloc[-1] / ytd_base.iloc[-1] - 1) * 100 if len(ytd_base) else None
    years = len(close) / 252
    cagr = (float(close.iloc[-1] / close.iloc[0]) ** (1 / years) - 1) * 100 if years >= 1 else None
    dd = close / close.cummax() - 1
    yearly = close.groupby(close.index.year).last()
    yearly_ret = (yearly / yearly.shift(1) - 1).dropna() * 100
    return {
        "instrument": instrument_dict(inst),
        "data_status": res.data_status,
        "source": res.source,
        "as_of": df.index[-1].isoformat(),
        "returns": rets,
        "cagr": cagr,
        "max_drawdown_pct": float(dd.min() * 100),
        "history_start": df.index[0].isoformat(),
        "calendar_returns": {str(k): round(float(v), 2) for k, v in yearly_ret.items()},
    }
