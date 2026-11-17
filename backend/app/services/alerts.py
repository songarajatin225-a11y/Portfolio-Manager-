"""Alert definitions and the evaluation engine.

Condition alerts are edge-triggered: they fire when the condition becomes true and re-arm once it is
false again, so a price sitting above a level does not notify every cycle. Crossover alerts (MACD)
record the initial state silently and fire only on an actual cross. A cooldown and an optional
"trigger once" flag further limit repeats. Every notification records the data label (LIVE, DELAYED,
HISTORICAL or SIMULATED) and the timestamp of the value that triggered it.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Alert, Instrument, InstrumentMetrics, NewsArticle, NewsArticleInstrument, Portfolio
from app.marketdata.calendar import IST
from app.services import notify
from app.services.market_data import cached_quotes, is_simulated_mode

logger = logging.getLogger(__name__)

NUM = {"type": "number"}
ALERT_TYPES: dict[str, dict[str, Any]] = {
    "price_above": {
        "label": "Price rises above",
        "params": {"price": NUM},
        "needs": "instrument",
        "basis": "live quote",
    },
    "price_below": {
        "label": "Price falls below",
        "params": {"price": NUM},
        "needs": "instrument",
        "basis": "live quote",
    },
    "pct_change": {
        "label": "Day change beyond ±%",
        "params": {"threshold_pct": NUM, "direction": {"type": "string", "enum": ["up", "down", "either"]}},
        "needs": "instrument",
        "basis": "live quote vs previous close",
    },
    "volume_spike": {
        "label": "Volume spike",
        "params": {"multiple": NUM},
        "needs": "instrument",
        "basis": "today's cumulative volume vs 20-day average",
    },
    "rsi_above": {
        "label": "RSI(14) rises above",
        "params": {"level": NUM},
        "needs": "instrument",
        "basis": "daily bars (metrics refresh)",
    },
    "rsi_below": {
        "label": "RSI(14) falls below",
        "params": {"level": NUM},
        "needs": "instrument",
        "basis": "daily bars (metrics refresh)",
    },
    "macd_cross": {
        "label": "MACD crossover",
        "params": {"direction": {"type": "string", "enum": ["bullish", "bearish"]}},
        "needs": "instrument",
        "basis": "daily bars (metrics refresh)",
    },
    "breakout": {
        "label": "52-week breakout / breakdown",
        "params": {"direction": {"type": "string", "enum": ["up", "down"]}},
        "needs": "instrument",
        "basis": "live quote vs 52-week range",
    },
    "stop_loss": {"label": "Stop-loss hit", "params": {"price": NUM}, "needs": "instrument", "basis": "live quote"},
    "portfolio_drawdown": {
        "label": "Portfolio drawdown exceeds %",
        "params": {"threshold_pct": NUM},
        "needs": "portfolio",
        "basis": "daily portfolio valuation (time-weighted)",
    },
    "announcement": {
        "label": "New company news / announcement",
        "params": {"keywords": {"type": "array", "items": {"type": "string"}}, "high_impact_only": {"type": "boolean"}},
        "needs": "instrument",
        "basis": "ingested news feeds",
    },
    "unusual_activity": {
        "label": "AI unusual activity",
        "params": {"min_score": NUM},
        "needs": "instrument",
        "basis": "ML anomaly percentile (isolation forest)",
    },
}
EDGE_INIT_SILENT = {"macd_cross"}
DRAWDOWN_EVAL_MINUTES = 15


def validate_params(alert_type: str, params: dict[str, Any]) -> dict[str, Any]:
    spec = ALERT_TYPES.get(alert_type)
    if spec is None:
        raise ValueError(f"alert_type must be one of {', '.join(ALERT_TYPES)}")
    out: dict[str, Any] = {}
    for key, schema in spec["params"].items():
        v = params.get(key)
        if schema["type"] == "number":
            if not isinstance(v, int | float) or isinstance(v, bool) or v <= 0:
                raise ValueError(f"{key} must be a positive number")
            out[key] = float(v)
        elif schema["type"] == "string":
            v = v or schema["enum"][0]
            if v not in schema["enum"]:
                raise ValueError(f"{key} must be one of {', '.join(schema['enum'])}")
            out[key] = v
        elif schema["type"] == "boolean":
            out[key] = bool(v)
        elif schema["type"] == "array":
            items = v or []
            if not isinstance(items, list) or len(items) > 20:
                raise ValueError(f"{key} must be a list of up to 20 strings")
            out[key] = [str(x).strip()[:60] for x in items if str(x).strip()]
    if alert_type in ("rsi_above", "rsi_below") and out["level"] >= 100:
        raise ValueError("RSI level must be below 100")
    if alert_type == "unusual_activity" and out["min_score"] > 100:
        raise ValueError("min_score is a percentile (0-100)")
    if alert_type == "portfolio_drawdown" and out["threshold_pct"] >= 100:
        raise ValueError("threshold must be below 100%")
    return out


def describe(alert_type: str, params: dict[str, Any], symbol: str | None) -> str:
    s = symbol or ""
    p = params
    return {
        "price_above": lambda: f"{s} above ₹{p['price']:,.2f}",
        "price_below": lambda: f"{s} below ₹{p['price']:,.2f}",
        "pct_change": lambda: f"{s} day change {p['direction']} {p['threshold_pct']:g}%",
        "volume_spike": lambda: f"{s} volume ≥ {p['multiple']:g}× 20-day average",
        "rsi_above": lambda: f"{s} RSI(14) above {p['level']:g}",
        "rsi_below": lambda: f"{s} RSI(14) below {p['level']:g}",
        "macd_cross": lambda: f"{s} MACD {p['direction']} crossover",
        "breakout": lambda: f"{s} 52-week {'high breakout' if p['direction'] == 'up' else 'low breakdown'}",
        "stop_loss": lambda: f"{s} stop-loss at ₹{p['price']:,.2f}",
        "portfolio_drawdown": lambda: f"Portfolio drawdown beyond {p['threshold_pct']:g}%",
        "announcement": lambda: f"{s} news" + (f" matching {', '.join(p['keywords'])}" if p.get("keywords") else ""),
        "unusual_activity": lambda: f"{s} unusual activity ≥ {p['min_score']:g}th percentile",
    }[alert_type]()


class _Ctx:
    def __init__(
        self, quotes: dict[int, dict[str, Any]], metrics: dict[int, InstrumentMetrics], symbols: dict[int, str]
    ) -> None:
        self.quotes, self.metrics, self.symbols = quotes, metrics, symbols


def _edge(alert: Alert, cond: bool | None) -> bool:
    """Edge trigger with re-arm. Returns True when the alert should fire."""
    if cond is None:
        return False
    state = dict(alert.state or {})
    first = "armed" not in state
    armed = state.get("armed", True)
    fire = cond and armed and not (first and alert.alert_type in EDGE_INIT_SILENT)
    state["armed"] = not cond
    alert.state = state
    return fire


async def _evaluate_one(db: AsyncSession, alert: Alert, ctx: _Ctx, now: datetime) -> tuple[str, str, dict] | None:
    t, p = alert.alert_type, alert.params
    sym = ctx.symbols.get(alert.instrument_id or 0, "")
    q = ctx.quotes.get(alert.instrument_id or 0)
    m = ctx.metrics.get(alert.instrument_id or 0)
    data: dict[str, Any] = {"symbol": sym, "alert_type": t}
    if q:
        data.update(
            {
                "price": q.get("ltp"),
                "data_status": q.get("data_status"),
                "as_of": q.get("exchange_ts") or q.get("received_at"),
                "source": q.get("source"),
            }
        )
    ltp = float(q["ltp"]) if q and q.get("ltp") else None

    if t in ("price_above", "price_below", "stop_loss"):
        if ltp is None:
            return None
        above = t == "price_above"
        cond = ltp >= p["price"] if above else ltp <= p["price"]
        if _edge(alert, cond):
            verb = "rose above" if above else ("hit the stop-loss at" if t == "stop_loss" else "fell below")
            return f"{sym} {verb} ₹{p['price']:,.2f}", f"Last price ₹{ltp:,.2f}.", data
        return None
    if t == "pct_change":
        if ltp is None or not q.get("prev_close"):
            return None
        chg = (ltp / float(q["prev_close"]) - 1) * 100
        d = p["direction"]
        cond = (
            chg >= p["threshold_pct"]
            if d == "up"
            else chg <= -p["threshold_pct"]
            if d == "down"
            else abs(chg) >= p["threshold_pct"]
        )
        data["change_pct"] = round(chg, 2)
        if _edge(alert, cond):
            return (
                f"{sym} moved {chg:+.2f}% today",
                f"Last price ₹{ltp:,.2f} vs previous close ₹{float(q['prev_close']):,.2f}.",
                data,
            )
        return None
    if t == "volume_spike":
        if not q or not q.get("volume") or not m or not m.avg_volume_20:
            return None
        ratio = float(q["volume"]) / m.avg_volume_20
        data["volume_ratio"] = round(ratio, 2)
        day = now.astimezone(IST).date().isoformat()
        if (alert.state or {}).get("day") != day:  # re-arm each session
            alert.state = {"day": day}
        if _edge(alert, ratio >= p["multiple"]):
            return (
                f"{sym} volume spike: {ratio:.1f}× average",
                f"Volume {float(q['volume']):,.0f} vs 20-day average {m.avg_volume_20:,.0f}.",
                data,
            )
        return None
    if t in ("rsi_above", "rsi_below", "macd_cross", "unusual_activity"):
        if m is None:
            return None
        data.update(
            {
                "data_status": "SIMULATED" if m.is_simulated else "HISTORICAL",
                "as_of": m.as_of.isoformat(),
                "source": "platform metrics",
            }
        )
        if t == "rsi_above" or t == "rsi_below":
            if m.rsi_14 is None:
                return None
            cond = m.rsi_14 >= p["level"] if t == "rsi_above" else m.rsi_14 <= p["level"]
            data["rsi_14"] = round(m.rsi_14, 2)
            if _edge(alert, cond):
                return (
                    f"{sym} RSI(14) is {m.rsi_14:.1f}",
                    f"Crossed {'above' if t == 'rsi_above' else 'below'} {p['level']:g} on daily bars.",
                    data,
                )
            return None
        if t == "macd_cross":
            if m.macd is None or m.macd_signal is None:
                return None
            bull = m.macd > m.macd_signal
            cond = bull if p["direction"] == "bullish" else not bull
            if _edge(alert, cond):
                return (
                    f"{sym} MACD {p['direction']} crossover",
                    f"MACD {m.macd:.3f} vs signal {m.macd_signal:.3f} (daily).",
                    data,
                )
            return None
        score = (m.scores or {}).get("anomaly_score")
        if score is None:
            return None
        data.update(
            {"anomaly_score": score, "source": "platform ML pipeline", "as_of": (m.scores or {}).get("ml_as_of")}
        )
        if _edge(alert, float(score) >= p["min_score"]):
            return (
                f"{sym} unusual activity ({float(score):.0f}th percentile)",
                "Flagged by the anomaly model on volume, move, range and gap features. "
                "This is a statistical flag, not a signal to trade.",
                data,
            )
        return None
    if t == "breakout":
        if ltp is None or m is None or m.high_52w is None or m.low_52w is None:
            return None
        cond = ltp > m.high_52w if p["direction"] == "up" else ltp < m.low_52w
        if _edge(alert, cond):
            ref = m.high_52w if p["direction"] == "up" else m.low_52w
            return (
                f"{sym} {'broke above its 52-week high' if p['direction'] == 'up' else 'broke below its 52-week low'}",
                f"Last price ₹{ltp:,.2f} vs 52-week {'high' if p['direction'] == 'up' else 'low'} ₹{ref:,.2f}.",
                data,
            )
        return None
    if t == "announcement":
        state = dict(alert.state or {})
        since = datetime.fromisoformat(state["since"]) if state.get("since") else alert.created_at
        stmt = (
            select(NewsArticle)
            .join(NewsArticleInstrument, NewsArticleInstrument.article_id == NewsArticle.id)
            .where(NewsArticleInstrument.instrument_id == alert.instrument_id, NewsArticle.fetched_at > since)
            .order_by(NewsArticle.published_at.desc())
            .limit(20)
        )
        arts = list((await db.scalars(stmt)).all())
        state["since"] = max([a.fetched_at for a in arts] + [since]).isoformat()
        alert.state = state
        kws = [k.lower() for k in p.get("keywords", [])]
        hits = [
            a
            for a in arts
            if (not kws or any(k in a.title.lower() for k in kws))
            and (not p.get("high_impact_only") or a.impact == "high")
        ]
        if not hits:
            return None
        a = hits[0]
        data.update(
            {
                "article_id": a.id,
                "url": a.url,
                "source": a.source_name,
                "as_of": a.published_at.isoformat(),
                "data_status": None,
                "sentiment": a.sentiment_label,
            }
        )
        more = f" (+{len(hits) - 1} more)" if len(hits) > 1 else ""
        return f"{sym} news: {a.title[:200]}", f"{a.source_name}, {a.published_at:%d %b %H:%M} UTC{more}.", data
    if t == "portfolio_drawdown":
        from app.services import portfolio as portfolio_service

        state = dict(alert.state or {})
        last = datetime.fromisoformat(state["evaluated_at"]) if state.get("evaluated_at") else None
        if last and now - last < timedelta(minutes=DRAWDOWN_EVAL_MINUTES):
            return None
        state["evaluated_at"] = now.isoformat()
        alert.state = state
        port = await db.get(Portfolio, alert.portfolio_id)
        if port is None:
            return None
        perf = await portfolio_service.performance(db, port, allow_fetch=False)
        dd = perf.get("current_drawdown_pct")
        if dd is None:
            return None
        data.update(
            {
                "portfolio": port.name,
                "drawdown_pct": round(dd, 2),
                "data_status": perf.get("data_status"),
                "as_of": perf["series"][-1]["date"] if perf.get("series") else None,
                "symbol": None,
            }
        )
        if _edge(alert, -dd >= p["threshold_pct"]):
            return (
                f"Portfolio '{port.name}' is {abs(dd):.1f}% below its peak",
                f"Drawdown threshold {p['threshold_pct']:g}% (time-weighted, daily closes).",
                data,
            )
        return None
    return None


SEVERITY = {
    "stop_loss": "critical",
    "portfolio_drawdown": "critical",
    "breakout": "warning",
    "pct_change": "warning",
    "volume_spike": "warning",
    "unusual_activity": "warning",
}


async def evaluate_alerts(db: AsyncSession, user_id: Any | None = None) -> int:
    """Evaluate active alerts (optionally one user's). Returns the number of notifications sent."""
    stmt = select(Alert).where(Alert.is_active.is_(True))
    if user_id is not None:
        stmt = stmt.where(Alert.user_id == user_id)
    alerts = list((await db.scalars(stmt)).all())
    if not alerts:
        return 0
    simulated = await is_simulated_mode(db)
    ids = sorted({a.instrument_id for a in alerts if a.instrument_id})
    quotes = await cached_quotes(db, ids)
    metrics = (
        {
            m.instrument_id: m
            for m in (
                await db.scalars(
                    select(InstrumentMetrics).where(
                        InstrumentMetrics.instrument_id.in_(ids), InstrumentMetrics.is_simulated.is_(simulated)
                    )
                )
            ).all()
        }
        if ids
        else {}
    )
    symbols = (
        dict((await db.execute(select(Instrument.id, Instrument.symbol).where(Instrument.id.in_(ids)))).all())
        if ids
        else {}
    )
    ctx = _Ctx(quotes, metrics, symbols)
    now = datetime.now(UTC)
    fired: list[tuple[Alert, str, str, dict]] = []
    for a in alerts:
        try:
            res = await _evaluate_one(db, a, ctx, now)
        except Exception:  # noqa: BLE001 - one bad alert must not stop the others
            logger.exception("Alert %s evaluation failed", a.id)
            continue
        if res is None:
            continue
        if a.last_triggered_at and now - a.last_triggered_at < timedelta(minutes=a.cooldown_minutes):
            continue
        a.last_triggered_at = now
        if a.trigger_once:
            a.is_active = False
        fired.append((a, *res))
    await db.commit()
    for a, title, body, data in fired:
        await notify.notify(
            db,
            a.user_id,
            title,
            body,
            severity=SEVERITY.get(a.alert_type, "info"),
            data={**data, "alert_name": a.name},
            channels=list(a.channels or ["in_app"]),
            alert_id=a.id,
        )
    return len(fired)
