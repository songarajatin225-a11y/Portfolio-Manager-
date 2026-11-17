"""Stock screener over the computed metrics snapshot (technical, fundamental and AI/ML fields)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.db.models import Instrument, InstrumentMetrics
from app.services.market_data import cached_quotes, is_simulated_mode

FieldType = Literal["number", "boolean", "string"]

FIELDS: dict[str, dict[str, str]] = {
    # technical
    "last_close": {"label": "Price", "category": "technical", "type": "number", "unit": "INR"},
    "change_pct": {"label": "Day change", "category": "technical", "type": "number", "unit": "%"},
    "rsi_14": {"label": "RSI (14)", "category": "technical", "type": "number", "unit": ""},
    "macd_hist": {"label": "MACD histogram", "category": "technical", "type": "number", "unit": ""},
    "adx_14": {"label": "ADX (14)", "category": "technical", "type": "number", "unit": ""},
    "volume_ratio": {"label": "Volume / 20-day average", "category": "technical", "type": "number", "unit": "x"},
    "rel_strength": {
        "label": "Relative strength vs benchmark (3M)",
        "category": "technical",
        "type": "number",
        "unit": "pp",
    },
    "ret_1w": {"label": "Return 1W", "category": "technical", "type": "number", "unit": "%"},
    "ret_1m": {"label": "Return 1M", "category": "technical", "type": "number", "unit": "%"},
    "ret_3m": {"label": "Return 3M (momentum)", "category": "technical", "type": "number", "unit": "%"},
    "ret_6m": {"label": "Return 6M", "category": "technical", "type": "number", "unit": "%"},
    "ret_1y": {"label": "Return 1Y", "category": "technical", "type": "number", "unit": "%"},
    "volatility_20": {"label": "Volatility (20D, annualised)", "category": "technical", "type": "number", "unit": "%"},
    "atr_pct": {"label": "ATR as % of price", "category": "technical", "type": "number", "unit": "%"},
    "dist_52w_high": {"label": "Distance below 52W high", "category": "technical", "type": "number", "unit": "%"},
    "macd_cross_up": {"label": "MACD bullish crossover (3 bars)", "category": "technical", "type": "boolean"},
    "macd_cross_down": {"label": "MACD bearish crossover (3 bars)", "category": "technical", "type": "boolean"},
    "golden_cross": {"label": "50/200 SMA golden cross (5 bars)", "category": "technical", "type": "boolean"},
    "death_cross": {"label": "50/200 SMA death cross (5 bars)", "category": "technical", "type": "boolean"},
    "ema20_cross_up": {"label": "Price crossed above EMA 20 (3 bars)", "category": "technical", "type": "boolean"},
    "above_ema20": {"label": "Price above EMA 20", "category": "technical", "type": "boolean"},
    "above_ema50": {"label": "Price above EMA 50", "category": "technical", "type": "boolean"},
    "above_ema200": {"label": "Price above EMA 200", "category": "technical", "type": "boolean"},
    "volume_breakout": {"label": "Volume breakout (≥2× avg, up day)", "category": "technical", "type": "boolean"},
    "breakout_52w": {"label": "New 52-week high", "category": "technical", "type": "boolean"},
    "breakdown_52w": {"label": "New 52-week low", "category": "technical", "type": "boolean"},
    "supertrend_bull": {"label": "Supertrend bullish", "category": "technical", "type": "boolean"},
    "strong_trend": {"label": "Strong trend (ADX ≥ 25)", "category": "technical", "type": "boolean"},
    # fundamental
    "pe": {"label": "P/E", "category": "fundamental", "type": "number", "unit": "x"},
    "pb": {"label": "P/B", "category": "fundamental", "type": "number", "unit": "x"},
    "roe": {"label": "ROE", "category": "fundamental", "type": "number", "unit": "%"},
    "roce": {"label": "ROCE", "category": "fundamental", "type": "number", "unit": "%"},
    "revenue_growth": {"label": "Revenue growth YoY", "category": "fundamental", "type": "number", "unit": "%"},
    "profit_growth": {"label": "Profit growth YoY", "category": "fundamental", "type": "number", "unit": "%"},
    "debt_to_equity": {"label": "Debt / Equity", "category": "fundamental", "type": "number", "unit": "x"},
    "market_cap": {"label": "Market cap", "category": "fundamental", "type": "number", "unit": "INR cr"},
    "dividend_yield": {"label": "Dividend yield", "category": "fundamental", "type": "number", "unit": "%"},
    "net_margin": {"label": "Net margin", "category": "fundamental", "type": "number", "unit": "%"},
    "earnings_quality": {
        "label": "Earnings quality (OCF / profit)",
        "category": "fundamental",
        "type": "number",
        "unit": "x",
    },
    # AI / ML (filled by the ML pipeline and news sentiment)
    "ml_bullish_prob": {"label": "ML bullish probability", "category": "ai_ml", "type": "number", "unit": "%"},
    "ml_momentum_score": {"label": "ML momentum score", "category": "ai_ml", "type": "number", "unit": ""},
    "ml_trend": {"label": "ML trend classification", "category": "ai_ml", "type": "string"},
    "predicted_volatility": {"label": "Predicted volatility", "category": "ai_ml", "type": "number", "unit": "%"},
    "risk_score": {"label": "Risk score (0-100)", "category": "ai_ml", "type": "number", "unit": ""},
    "sentiment_score": {"label": "News sentiment score (-1 to 1)", "category": "ai_ml", "type": "number", "unit": ""},
    "anomaly_score": {"label": "Unusual activity score", "category": "ai_ml", "type": "number", "unit": ""},
    # meta
    "sector": {"label": "Sector", "category": "meta", "type": "string"},
}

PRESETS = [
    {
        "name": "Momentum leaders",
        "filters": [
            {"field": "ret_3m", "op": "gt", "value": 10},
            {"field": "above_ema50", "op": "is_true"},
            {"field": "rsi_14", "op": "between", "value": 50, "value2": 75},
        ],
        "sort": "ret_3m",
    },
    {"name": "Volume breakouts", "filters": [{"field": "volume_breakout", "op": "is_true"}], "sort": "volume_ratio"},
    {"name": "New 52-week highs", "filters": [{"field": "breakout_52w", "op": "is_true"}], "sort": "ret_1m"},
    {"name": "Golden cross", "filters": [{"field": "golden_cross", "op": "is_true"}], "sort": "ret_1m"},
    {
        "name": "Oversold in uptrend",
        "filters": [{"field": "rsi_14", "op": "lt", "value": 35}, {"field": "above_ema200", "op": "is_true"}],
        "sort": "rsi_14",
        "direction": "asc",
    },
    {
        "name": "Bearish setups",
        "filters": [{"field": "macd_cross_down", "op": "is_true"}, {"field": "above_ema50", "op": "is_false"}],
        "sort": "ret_1m",
        "direction": "asc",
    },
    {
        "name": "Quality at reasonable price",
        "filters": [
            {"field": "roe", "op": "gt", "value": 15},
            {"field": "pe", "op": "between", "value": 0, "value2": 30},
            {"field": "debt_to_equity", "op": "lt", "value": 1},
        ],
        "sort": "roe",
    },
]


class Filter(BaseModel):
    field: str
    op: Literal["gt", "gte", "lt", "lte", "between", "eq", "neq", "in", "is_true", "is_false"]
    value: float | str | list[str] | None = None
    value2: float | None = None


class ScreenRequest(BaseModel):
    filters: list[Filter] = Field(default_factory=list, max_length=30)
    sort: str = "change_pct"
    direction: Literal["asc", "desc"] = "desc"
    limit: int = Field(default=100, ge=1, le=500)


def _record(inst: Instrument, m: InstrumentMetrics) -> dict[str, Any]:
    rec: dict[str, Any] = {
        c: getattr(m, c)
        for c in (
            "last_close",
            "change_pct",
            "rsi_14",
            "macd_hist",
            "adx_14",
            "volume_ratio",
            "rel_strength",
            "ret_1w",
            "ret_1m",
            "ret_3m",
            "ret_6m",
            "ret_1y",
            "volatility_20",
        )
    }
    rec["atr_pct"] = (m.atr_14 / m.last_close * 100) if m.atr_14 and m.last_close else None
    rec["dist_52w_high"] = ((1 - m.last_close / m.high_52w) * 100) if m.high_52w and m.last_close else None
    rec.update({k: bool(v) for k, v in (m.flags or {}).items()})
    rec.update({k: v for k, v in (m.fundamentals or {}).items() if k in FIELDS})
    rec.update({k: v for k, v in (m.scores or {}).items() if k in FIELDS})
    rec["sector"] = inst.sector
    return rec


def _match(rec: dict[str, Any], f: Filter) -> bool:
    v = rec.get(f.field)
    if f.op == "is_true":
        return v is True
    if f.op == "is_false":
        return v is False
    if v is None:
        return False
    if f.op in ("eq", "neq", "in"):
        target = f.value if isinstance(f.value, list) else [f.value]
        hit = str(v).lower() in {str(t).lower() for t in target}
        return hit if f.op != "neq" else not hit
    try:
        x, a = float(v), float(f.value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return False
    if f.op == "gt":
        return x > a
    if f.op == "gte":
        return x >= a
    if f.op == "lt":
        return x < a
    if f.op == "lte":
        return x <= a
    if f.op == "between":
        return f.value2 is not None and a <= x <= float(f.value2)
    return False


async def run_screen(db: AsyncSession, req: ScreenRequest) -> dict[str, Any]:
    for f in req.filters:
        if f.field not in FIELDS:
            raise AppError(f"Unknown screener field '{f.field}'", status_code=422)
    if req.sort not in FIELDS:
        raise AppError(f"Unknown sort field '{req.sort}'", status_code=422)
    simulated = await is_simulated_mode(db)
    rows = (
        await db.execute(
            select(Instrument, InstrumentMetrics)
            .join(InstrumentMetrics, InstrumentMetrics.instrument_id == Instrument.id)
            .where(InstrumentMetrics.is_simulated.is_(simulated), Instrument.is_index.is_(False))
        )
    ).all()
    matched = []
    for inst, m in rows:
        rec = _record(inst, m)
        if all(_match(rec, f) for f in req.filters):
            matched.append((inst, m, rec))
    with_value = [t for t in matched if t[2].get(req.sort) is not None]
    without = [t for t in matched if t[2].get(req.sort) is None]
    with_value.sort(key=lambda t: t[2][req.sort], reverse=req.direction == "desc")
    page = (with_value + without)[: req.limit]
    quotes = await cached_quotes(db, [inst.id for inst, _, _ in page])
    return {
        "universe": len(rows),
        "matched": len(matched),
        "data_mode": "simulated" if simulated else "real",
        "results": [
            {
                "instrument_id": inst.id,
                "symbol": inst.symbol,
                "name": inst.name,
                "sector": inst.sector,
                "metrics_as_of": m.as_of,
                "values": rec,
                "live": (
                    {
                        "ltp": quotes[inst.id]["ltp"],
                        "change_pct": quotes[inst.id]["change_pct"],
                        "data_status": quotes[inst.id]["data_status"],
                    }
                    if inst.id in quotes
                    else None
                ),
            }
            for inst, m, rec in page
        ],
    }
