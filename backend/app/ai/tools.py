"""Tools Claude can call. Every tool reads the platform's own data and returns it with sources,
data labels and timestamps, so answers can be grounded and cited. Tools never fabricate values:
missing data comes back as ``null`` with an explanation.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics.fundamentals import compute_ratios
from app.core.errors import AppError, NotFoundError
from app.db.models import Instrument, InstrumentMetrics, MLModel, MLPrediction, NewsArticle, NewsArticleInstrument
from app.services import instruments as instrument_service
from app.services import market_stats
from app.services.analysis import benchmark_frame, fundamental_periods, load_daily, technical_snapshot
from app.services.market_data import cached_quotes, data_mode, market_session
from app.services.screener import FIELDS, Filter, ScreenRequest, run_screen

ToolFn = Callable[[AsyncSession, uuid.UUID, dict[str, Any]], Awaitable[dict[str, Any]]]
_REGISTRY: dict[str, tuple[dict[str, Any], ToolFn]] = {}


def tool(name: str, description: str, properties: dict[str, Any]):  # noqa: ANN201
    def deco(fn: ToolFn) -> ToolFn:
        schema = {
            "type": "object",
            "properties": properties,
            "required": list(properties),
            "additionalProperties": False,
        }
        _REGISTRY[name] = ({"name": name, "description": description, "input_schema": schema, "strict": True}, fn)
        return fn

    return deco


def definitions() -> list[dict[str, Any]]:
    """Tool definitions in a stable order (prompt-cache friendly)."""
    return [_REGISTRY[k][0] for k in sorted(_REGISTRY)]


async def execute(db: AsyncSession, user_id: uuid.UUID, name: str, args: dict[str, Any]) -> tuple[str, bool, list]:
    """Run a tool; returns (json_result, is_error, sources)."""
    entry = _REGISTRY.get(name)
    if entry is None:
        return json.dumps({"error": f"Unknown tool {name}"}), True, []
    try:
        result = await entry[1](db, user_id, args)
    except (NotFoundError, AppError, ValueError) as exc:
        return json.dumps({"error": str(exc)}), True, []
    return json.dumps(result, default=str), False, result.get("sources", [])


def _src(kind: str, name: str, source: str | None, as_of: Any, status: str | None = None) -> dict[str, Any]:
    return {
        "type": kind,
        "name": name,
        "source": source,
        "as_of": as_of.isoformat() if hasattr(as_of, "isoformat") else as_of,
        "data_status": status,
    }


async def _quote_brief(db: AsyncSession, inst: Instrument) -> dict[str, Any] | None:
    q = (await cached_quotes(db, [inst.id])).get(inst.id)
    if not q:
        return None
    keys = (
        "ltp",
        "open",
        "high",
        "low",
        "prev_close",
        "change",
        "change_pct",
        "volume",
        "vwap",
        "high_52w",
        "low_52w",
        "data_status",
        "is_stale",
        "source",
        "received_at",
        "exchange_ts",
    )
    return {k: q.get(k) for k in keys}


@tool(
    "get_market_overview",
    "Current market overview for the tracked Indian equity universe: index levels, breadth "
    "(advances/declines), top gainers/losers, most active stocks, sector performance and market session status. "
    "Use for questions about what the market is doing today.",
    {},
)
async def get_market_overview(db: AsyncSession, _: uuid.UUID, args: dict[str, Any]) -> dict[str, Any]:
    entries = await instrument_service.dashboard_indices(db)
    qs = await cached_quotes(db, [e["instrument"].id for e in entries if e["instrument"]])
    indices = []
    sources = []
    for e in entries:
        inst = e["instrument"]
        q = qs.get(inst.id) if inst else None
        indices.append(
            {
                "index": e["label"],
                "ltp": q.get("ltp") if q else None,
                "change_pct": q.get("change_pct") if q else None,
                "data_status": q.get("data_status") if q else None,
                "as_of": q.get("received_at") if q else None,
            }
        )
        if q:
            sources.append(_src("quote", e["label"], q.get("source"), q.get("received_at"), q.get("data_status")))
    ov = await market_stats.overview(db, top=6)
    sources.append(
        _src(
            "market_stats",
            "Tracked universe statistics",
            "platform metrics + live quotes",
            datetime.now(UTC),
            ",".join(ov["data_statuses"]),
        )
    )
    return {
        "data": {
            "data_mode": await data_mode(db),
            "session": await market_session(db),
            "indices": indices,
            "universe_size": ov["universe_size"],
            "breadth": ov["breadth"],
            "gainers": ov["gainers"],
            "losers": ov["losers"],
            "most_active": ov["most_active"],
            "sectors": ov["sectors"],
        },
        "sources": sources,
    }


@tool(
    "get_quote",
    "Latest quote for one instrument (NSE symbol, e.g. RELIANCE) with its data label and timestamp.",
    {"symbol": {"type": "string", "description": "Trading symbol"}},
)
async def get_quote(db: AsyncSession, _: uuid.UUID, args: dict[str, Any]) -> dict[str, Any]:
    inst = await instrument_service.resolve_symbol(db, args["symbol"])
    q = await _quote_brief(db, inst)
    return {
        "data": {
            "symbol": inst.symbol,
            "name": inst.name,
            "sector": inst.sector,
            "quote": q,
            "note": None if q else "No live or recent quote is available for this instrument.",
        },
        "sources": [_src("quote", inst.symbol, q["source"], q["received_at"], q["data_status"])] if q else [],
    }


@tool(
    "get_technical_analysis",
    "Technical indicators for an instrument from daily candles: moving averages, RSI, "
    "MACD, ATR, ADX, Supertrend, returns over several periods, relative strength vs the benchmark, 52-week range, "
    "volume vs average and boolean signals (crossovers, breakouts).",
    {"symbol": {"type": "string"}},
)
async def get_technical_analysis(db: AsyncSession, _: uuid.UUID, args: dict[str, Any]) -> dict[str, Any]:
    inst = await instrument_service.resolve_symbol(db, args["symbol"])
    df = await load_daily(db, inst)
    if len(df) < 30:
        return {
            "data": {"symbol": inst.symbol, "note": "Insufficient daily history for technical analysis."},
            "sources": [],
        }
    snap = technical_snapshot(df, await benchmark_frame(db) if not inst.is_index else None)
    status = "SIMULATED" if inst.is_simulated else "HISTORICAL"
    return {
        "data": {
            "symbol": inst.symbol,
            "as_of": df.index[-1].isoformat(),
            "bars": len(df),
            **{k: (round(v, 4) if isinstance(v, float) else v) for k, v in snap.items()},
        },
        "sources": [_src("candles", f"{inst.symbol} daily candles", "stored daily candles", df.index[-1], status)],
    }


@tool(
    "get_fundamentals",
    "Financial ratios and statement figures for a company, computed only from validated "
    "filings stored in the platform. Each ratio states its formula, period, source and reporting date.",
    {"symbol": {"type": "string"}},
)
async def get_fundamentals(db: AsyncSession, _: uuid.UUID, args: dict[str, Any]) -> dict[str, Any]:
    inst = await instrument_service.resolve_symbol(db, args["symbol"])
    q = await _quote_brief(db, inst)
    periods = await fundamental_periods(db, inst.id)
    ratios = compute_ratios(
        periods, q["ltp"] if q else None, q["source"] if q else None, q["received_at"] if q else None
    )
    if not periods:
        return {
            "data": {
                "symbol": inst.symbol,
                "note": "No financial statements are stored for this company; "
                "fundamental analysis is not possible without them.",
            },
            "sources": [],
        }
    sources = [
        _src("filing", f"{inst.symbol} {p.period_type} {p.period_end}", p.source, p.reported_at) for p in periods[:6]
    ]
    return {
        "data": {
            "symbol": inst.symbol,
            **ratios,
            "statements": [
                {
                    "period_type": p.period_type,
                    "period_end": p.period_end.isoformat(),
                    "source": p.source,
                    "excluded": bool(p.validation_errors),
                    **p.values,
                }
                for p in periods[:8]
            ],
        },
        "sources": sources,
    }


@tool(
    "get_news",
    "Recent news headlines from configured feeds with sentiment labels, impact, source, URL and "
    "publication time. Pass an empty symbol for market-wide news.",
    {
        "symbol": {"type": "string", "description": "Symbol, or empty string for all news"},
        "days": {"type": "integer", "description": "Look-back in days (1-30)"},
        "limit": {"type": "integer", "description": "Max headlines (1-30)"},
    },
)
async def get_news(db: AsyncSession, _: uuid.UUID, args: dict[str, Any]) -> dict[str, Any]:
    days = min(max(int(args.get("days") or 3), 1), 30)
    limit = min(max(int(args.get("limit") or 15), 1), 30)
    stmt = select(NewsArticle).where(NewsArticle.published_at >= datetime.now(UTC) - timedelta(days=days))
    sym = (args.get("symbol") or "").strip()
    if sym:
        inst = await instrument_service.resolve_symbol(db, sym)
        stmt = stmt.join(NewsArticleInstrument, NewsArticleInstrument.article_id == NewsArticle.id).where(
            NewsArticleInstrument.instrument_id == inst.id
        )
    rows = (await db.scalars(stmt.order_by(NewsArticle.published_at.desc()).limit(limit))).all()
    items = [
        {
            "title": a.title,
            "summary": a.summary[:300],
            "source": a.source_name,
            "url": a.url,
            "published_at": a.published_at.isoformat(),
            "sentiment": a.sentiment_label,
            "sentiment_score": a.sentiment_score,
            "sentiment_method": a.sentiment_method,
            "impact": a.impact,
        }
        for a in rows
    ]
    return {
        "data": {
            "symbol": sym or None,
            "days": days,
            "articles": items,
            "note": None if items else "No news articles found for this period.",
        },
        "sources": [_src("news", a["title"][:80], a["source"], a["published_at"]) | {"url": a["url"]} for a in items],
    }


_FIELD_LIST = ", ".join(sorted(FIELDS))


@tool(
    "screen_stocks",
    f"Screen the tracked universe using computed metrics. Available fields: {_FIELD_LIST}. "
    "Operators: gt, gte, lt, lte, between (uses number and number2), eq/neq (uses text, e.g. sector), is_true, "
    "is_false (boolean flags). Returns matching stocks sorted by the chosen field.",
    {
        "filters": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "field": {"type": "string"},
                    "op": {
                        "type": "string",
                        "enum": ["gt", "gte", "lt", "lte", "between", "eq", "neq", "is_true", "is_false"],
                    },
                    "number": {"type": "number"},
                    "number2": {"type": "number"},
                    "text": {"type": "string"},
                },
                "required": ["field", "op", "number", "number2", "text"],
                "additionalProperties": False,
            },
        },
        "sort": {"type": "string"},
        "direction": {"type": "string", "enum": ["asc", "desc"]},
        "limit": {"type": "integer"},
    },
)
async def screen_stocks(db: AsyncSession, _: uuid.UUID, args: dict[str, Any]) -> dict[str, Any]:
    filters = []
    for f in args.get("filters", []):
        value: Any = f.get("text") if f["op"] in ("eq", "neq") else f.get("number")
        filters.append(Filter(field=f["field"], op=f["op"], value=value, value2=f.get("number2")))
    req = ScreenRequest(
        filters=filters,
        sort=args.get("sort") or "change_pct",
        direction=args.get("direction") or "desc",
        limit=min(max(int(args.get("limit") or 15), 1), 30),
    )
    res = await run_screen(db, req)
    rows = [
        {
            "symbol": r["symbol"],
            "name": r["name"],
            "sector": r["sector"],
            "metrics_as_of": r["metrics_as_of"],
            "values": {
                k: v
                for k, v in r["values"].items()
                if v is not None
                and (
                    k in {f.field for f in filters}
                    or k in ("last_close", "change_pct", "ret_1m", "ret_3m", "rsi_14", "volume_ratio", req.sort)
                )
            },
        }
        for r in res["results"]
    ]
    return {
        "data": {"universe": res["universe"], "matched": res["matched"], "results": rows},
        "sources": [
            _src(
                "metrics",
                "Screener metrics snapshot",
                "platform metrics",
                rows[0]["metrics_as_of"] if rows else datetime.now(UTC),
                "SIMULATED" if res["data_mode"] == "simulated" else "HISTORICAL",
            )
        ],
    }


@tool(
    "get_ml_prediction",
    "Latest machine-learning outputs for an instrument: probability of a bullish / neutral / "
    "bearish move over the model horizon, predicted volatility, unusual-activity score, market regime, model "
    "version and its out-of-sample test metrics. These are statistical estimates, not forecasts of certainty.",
    {"symbol": {"type": "string"}},
)
async def get_ml_prediction(db: AsyncSession, _: uuid.UUID, args: dict[str, Any]) -> dict[str, Any]:
    inst = await instrument_service.resolve_symbol(db, args["symbol"])
    row = (
        await db.execute(
            select(MLPrediction, MLModel)
            .join(MLModel, MLModel.id == MLPrediction.model_id)
            .where(MLPrediction.instrument_id == inst.id)
            .order_by(MLPrediction.as_of.desc(), MLPrediction.created_at.desc())
            .limit(1)
        )
    ).first()
    if not row:
        return {
            "data": {
                "symbol": inst.symbol,
                "note": "No ML prediction exists for this instrument (no validated "
                "model is active, or the instrument lacks history).",
            },
            "sources": [],
        }
    p, m = row
    test = m.metrics.get("test", {})
    return {
        "data": {
            "symbol": inst.symbol,
            "as_of": p.as_of.isoformat(),
            "horizon_trading_days": p.horizon_days,
            "prob_bullish": p.prob_up,
            "prob_neutral": p.prob_neutral,
            "prob_bearish": p.prob_down,
            "trend_class": p.trend_class,
            "confidence": p.confidence,
            "predicted_volatility_annualised": p.predicted_vol,
            "unusual_activity_percentile": p.anomaly_score,
            "market_regime": p.regime,
            "model": f"{m.name} v{m.version} ({m.algorithm})",
            "model_test_metrics": {k: test.get(k) for k in ("f1_macro", "accuracy", "directional_accuracy", "brier")},
            "baseline_f1_macro": m.metrics.get("baseline", {}).get("f1_macro"),
        },
        "sources": [
            _src(
                "ml_model",
                f"{m.name} v{m.version}",
                "platform ML pipeline",
                p.as_of,
                "SIMULATED" if p.is_simulated else "HISTORICAL",
            )
        ],
    }


@tool(
    "get_unusual_activity",
    "Stocks with unusual trading activity: highest volume relative to the 20-day average "
    "and highest ML unusual-activity scores.",
    {"limit": {"type": "integer"}},
)
async def get_unusual_activity(db: AsyncSession, _: uuid.UUID, args: dict[str, Any]) -> dict[str, Any]:
    limit = min(max(int(args.get("limit") or 10), 1), 25)
    from app.services.market_data import is_simulated_mode

    sim = await is_simulated_mode(db)
    rows = (
        await db.execute(
            select(Instrument, InstrumentMetrics)
            .join(InstrumentMetrics, InstrumentMetrics.instrument_id == Instrument.id)
            .where(InstrumentMetrics.is_simulated.is_(sim), Instrument.is_index.is_(False))
        )
    ).all()
    vol = sorted([r for r in rows if r[1].volume_ratio], key=lambda r: -r[1].volume_ratio)[:limit]
    anom = sorted(
        [r for r in rows if (r[1].scores or {}).get("anomaly_score") is not None],
        key=lambda r: -r[1].scores["anomaly_score"],
    )[:limit]
    as_of = max((r[1].as_of for r in rows), default=datetime.now(UTC))
    return {
        "data": {
            "volume_vs_average": [
                {
                    "symbol": i.symbol,
                    "volume_ratio": round(m.volume_ratio, 2),
                    "change_pct": m.change_pct,
                    "as_of": m.as_of.isoformat(),
                }
                for i, m in vol
            ],
            "ml_unusual_activity": [
                {"symbol": i.symbol, "percentile": m.scores["anomaly_score"], "as_of": m.scores.get("ml_as_of")}
                for i, m in anom
            ],
        },
        "sources": [
            _src("metrics", "Daily metrics snapshot", "platform metrics", as_of, "SIMULATED" if sim else "HISTORICAL")
        ],
    }


@tool(
    "get_sector_strength",
    "Sector-level relative strength: average day change, 1-month return, relative strength "
    "vs the benchmark and the share of advancing stocks per sector.",
    {},
)
async def get_sector_strength(db: AsyncSession, _: uuid.UUID, args: dict[str, Any]) -> dict[str, Any]:
    ov = await market_stats.overview(db, top=1)
    return {
        "data": {"sectors": ov["sectors"], "universe_size": ov["universe_size"]},
        "sources": [
            _src(
                "market_stats",
                "Sector aggregates of tracked universe",
                "platform metrics",
                datetime.now(UTC),
                ",".join(ov["data_statuses"]),
            )
        ],
    }


@tool(
    "get_portfolio_risk",
    "The user's own portfolio: holdings, P&L, XIRR, allocation, concentration and risk (VaR, volatility, beta, "
    "correlation, scenarios). Pass a portfolio name or id, or an empty string to list the user's portfolios.",
    {"portfolio": {"type": "string", "description": "Portfolio name or numeric id; empty to list portfolios"}},
)
async def get_portfolio_risk(db: AsyncSession, user_id: uuid.UUID, args: dict[str, Any]) -> dict[str, Any]:
    from app.db.models import Portfolio
    from app.services import portfolio as portfolio_service

    ref = str(args.get("portfolio", "")).strip()
    ports = list((await db.scalars(select(Portfolio).where(Portfolio.user_id == user_id))).all())
    if not ref:
        return {
            "data": {"portfolios": [{"id": p.id, "name": p.name} for p in ports]},
            "sources": [_src("portfolio", "Your portfolios", "platform portfolio records", datetime.now(UTC))],
        }
    match = next((p for p in ports if str(p.id) == ref or p.name.lower() == ref.lower()), None)
    if match is None:
        raise NotFoundError(f"No portfolio named '{ref}'. Available: {', '.join(p.name for p in ports) or 'none'}")
    risk = await portfolio_service.risk(db, match)
    summary = risk.pop("summary", None) or await portfolio_service.summary(db, match)
    keep = (
        "current_value",
        "total_invested",
        "unrealized_pnl",
        "realized_pnl",
        "dividends",
        "total_pnl",
        "absolute_return_pct",
        "xirr_pct",
        "allocation_by_sector",
        "concentration",
        "note",
        "price_statuses",
    )
    holdings = [
        {
            k: h[k]
            for k in (
                "symbol",
                "quantity",
                "avg_cost",
                "price",
                "market_value",
                "unrealized_pnl",
                "weight",
                "sector",
                "price_status",
                "price_as_of",
            )
        }
        for h in summary["holdings"]
    ]
    risk_view = {k: v for k, v in risk.items() if k not in ("position_exposure", "sector_exposure", "concentration")}
    statuses = ",".join(summary.get("price_statuses") or []) or None
    return {
        "data": {
            "portfolio": match.name,
            "summary": {k: summary.get(k) for k in keep},
            "holdings": holdings,
            "risk": risk_view,
        },
        "sources": [
            _src(
                "portfolio",
                f"Portfolio '{match.name}' holdings and prices",
                "platform portfolio records + quotes",
                datetime.now(UTC),
                statuses,
            ),
            _src(
                "risk",
                "Historical risk estimates",
                "platform daily candles",
                risk.get("as_of"),
                risk.get("data_status"),
            ),
        ],
    }
