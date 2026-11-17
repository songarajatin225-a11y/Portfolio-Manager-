from __future__ import annotations

import csv
import io
from datetime import UTC, date, datetime, time
from decimal import Decimal
from typing import Any, Literal

from fastapi import APIRouter, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select

from app.ai.coach import journal_coach
from app.analytics import journal as ja
from app.api.deps import DB, CurrentUser, WriterUser
from app.api.v1.ai import AI_RATE_PER_MIN, _report
from app.core.errors import AppError, NotFoundError
from app.core.ratelimit import enforce_rate_limit
from app.db.models import AIReport, Instrument, JournalTrade
from app.marketdata.calendar import IST
from app.services import instruments as instrument_service
from app.services.market_data import cached_quotes

router = APIRouter(prefix="/journal", tags=["journal"])
MAX_TRADES_QUERY = 5000
EMOTIONS = ["Calm", "Confident", "Fearful", "Greedy", "FOMO", "Revenge", "Bored", "Impatient", "Uncertain"]


class TradeIn(BaseModel):
    symbol: str = Field(min_length=1, max_length=80)
    exchange: str | None = Field(default=None, pattern="^(NSE|BSE)$")
    direction: Literal["LONG", "SHORT"]
    entry_at: datetime
    entry_price: Decimal = Field(gt=0, max_digits=20, decimal_places=6)
    quantity: Decimal = Field(gt=0, max_digits=20, decimal_places=6)
    exit_at: datetime | None = None
    exit_price: Decimal | None = Field(default=None, gt=0, max_digits=20, decimal_places=6)
    stop_loss: Decimal | None = Field(default=None, gt=0, max_digits=20, decimal_places=6)
    target: Decimal | None = Field(default=None, gt=0, max_digits=20, decimal_places=6)
    strategy: str = Field(default="", max_length=100)
    brokerage: Decimal = Field(default=Decimal(0), ge=0, max_digits=20, decimal_places=6)
    taxes: Decimal = Field(default=Decimal(0), ge=0, max_digits=20, decimal_places=6)
    entry_reason: str = Field(default="", max_length=4000)
    exit_reason: str = Field(default="", max_length=4000)
    tags: list[str] = Field(default_factory=list, max_length=20)
    emotion: str = Field(default="", max_length=50)

    @model_validator(mode="after")
    def _check(self) -> TradeIn:
        now = datetime.now(UTC)
        for name in ("entry_at", "exit_at"):
            v = getattr(self, name)
            if v is not None and v.tzinfo is None:
                setattr(self, name, v.replace(tzinfo=IST))
        if self.entry_at > now:
            raise ValueError("entry time cannot be in the future")
        if (self.exit_at is None) != (self.exit_price is None):
            raise ValueError("exit time and exit price must be given together")
        if self.exit_at is not None and (self.exit_at < self.entry_at or self.exit_at > now):
            raise ValueError("exit time must be after entry and not in the future")
        sign = 1 if self.direction == "LONG" else -1
        if self.stop_loss is not None and (self.entry_price - self.stop_loss) * sign <= 0:
            raise ValueError("stop-loss must be below entry for a long trade and above entry for a short")
        if self.target is not None and (self.target - self.entry_price) * sign <= 0:
            raise ValueError("target must be above entry for a long trade and below entry for a short")
        self.tags = [t.strip()[:40] for t in self.tags if t.strip()]
        return self


class CloseIn(BaseModel):
    exit_at: datetime
    exit_price: Decimal = Field(gt=0, max_digits=20, decimal_places=6)
    exit_reason: str = Field(default="", max_length=4000)
    brokerage: Decimal | None = Field(default=None, ge=0, max_digits=20, decimal_places=6)
    taxes: Decimal | None = Field(default=None, ge=0, max_digits=20, decimal_places=6)


def _f(v: Decimal | None) -> float | None:
    return float(v) if v is not None else None


def to_jtrade(t: JournalTrade, symbol: str) -> ja.JTrade:
    return ja.JTrade(
        id=t.id,
        symbol=symbol,
        direction=t.direction,
        status=t.status,
        entry_at=t.entry_at,
        entry_price=float(t.entry_price),
        quantity=float(t.quantity),
        exit_at=t.exit_at,
        exit_price=_f(t.exit_price),
        stop_loss=_f(t.stop_loss),
        target=_f(t.target),
        strategy=t.strategy,
        costs=float((t.brokerage or 0) + (t.taxes or 0)),
        emotion=t.emotion,
        tags=tuple(t.tags or ()),
    )


def _trade_dict(t: JournalTrade, inst: Instrument, quote: dict[str, Any] | None = None) -> dict[str, Any]:
    j = to_jtrade(t, inst.symbol)
    out = {
        "id": t.id,
        "instrument_id": t.instrument_id,
        "symbol": inst.symbol,
        "exchange": inst.exchange,
        "direction": t.direction,
        "status": t.status,
        "entry_at": t.entry_at,
        "entry_price": j.entry_price,
        "quantity": j.quantity,
        "exit_at": t.exit_at,
        "exit_price": j.exit_price,
        "stop_loss": j.stop_loss,
        "target": j.target,
        "strategy": t.strategy,
        "brokerage": float(t.brokerage or 0),
        "taxes": float(t.taxes or 0),
        "entry_reason": t.entry_reason,
        "exit_reason": t.exit_reason,
        "tags": t.tags or [],
        "emotion": t.emotion,
        "net_pnl": j.net_pnl,
        "return_pct": j.return_pct,
        "r_multiple": j.r_multiple,
        "planned_rr": j.planned_rr,
        "holding_days": j.holding_days,
    }
    if t.status == "OPEN" and quote and quote.get("ltp"):
        ltp = float(quote["ltp"])
        out["unrealized_pnl"] = (ltp - j.entry_price) * j.quantity * j.sign
        out["current_price"] = ltp
        out["price_status"] = quote.get("data_status")
    return out


async def _load(
    db: DB,
    user_id: Any,
    *,
    status: str | None = None,
    strategy: str | None = None,
    start: date | None = None,
    end: date | None = None,
) -> list[tuple[JournalTrade, Instrument]]:
    stmt = (
        select(JournalTrade, Instrument)
        .join(Instrument, Instrument.id == JournalTrade.instrument_id)
        .where(JournalTrade.user_id == user_id)
    )
    if status:
        stmt = stmt.where(JournalTrade.status == status)
    if strategy:
        stmt = stmt.where(JournalTrade.strategy == strategy)
    if start:
        stmt = stmt.where(JournalTrade.entry_at >= datetime.combine(start, time.min, IST))
    if end:
        stmt = stmt.where(JournalTrade.entry_at <= datetime.combine(end, time.max, IST))
    rows = await db.execute(stmt.order_by(JournalTrade.entry_at.desc()).limit(MAX_TRADES_QUERY))
    return list(rows.all())


async def _own(db: DB, user_id: Any, trade_id: int) -> JournalTrade:
    t = await db.get(JournalTrade, trade_id)
    if t is None or t.user_id != user_id:
        raise NotFoundError("Trade not found")
    return t


def _apply(t: JournalTrade, body: TradeIn, instrument_id: int) -> None:
    t.instrument_id = instrument_id
    for k in (
        "direction",
        "entry_at",
        "entry_price",
        "quantity",
        "exit_at",
        "exit_price",
        "stop_loss",
        "target",
        "brokerage",
        "taxes",
        "entry_reason",
        "exit_reason",
        "tags",
        "emotion",
    ):
        setattr(t, k, getattr(body, k))
    t.strategy = body.strategy.strip()
    t.status = "CLOSED" if body.exit_price is not None else "OPEN"


@router.get("/meta")
async def meta(user: CurrentUser, db: DB) -> dict[str, Any]:
    rows = await _load(db, user.id)
    strategies = sorted({t.strategy for t, _ in rows if t.strategy})
    tags = sorted({tag for t, _ in rows for tag in (t.tags or [])})
    return {"strategies": strategies, "tags": tags, "emotions": EMOTIONS}


@router.get("/trades")
async def list_trades(
    user: CurrentUser,
    db: DB,
    status: Literal["OPEN", "CLOSED"] | None = None,
    strategy: str | None = Query(None, max_length=100),
    start: date | None = None,
    end: date | None = None,
) -> list[dict[str, Any]]:
    rows = await _load(db, user.id, status=status, strategy=strategy, start=start, end=end)
    quotes = await cached_quotes(db, list({t.instrument_id for t, _ in rows if t.status == "OPEN"}))
    return [_trade_dict(t, i, quotes.get(t.instrument_id)) for t, i in rows]


@router.post("/trades", status_code=201)
async def create_trade(body: TradeIn, user: WriterUser, db: DB) -> dict[str, Any]:
    inst = await instrument_service.resolve_symbol(db, body.symbol, body.exchange)
    t = JournalTrade(user_id=user.id)
    _apply(t, body, inst.id)
    db.add(t)
    await db.commit()
    await db.refresh(t)
    return _trade_dict(t, inst)


@router.put("/trades/{trade_id}")
async def update_trade(trade_id: int, body: TradeIn, user: WriterUser, db: DB) -> dict[str, Any]:
    t = await _own(db, user.id, trade_id)
    inst = await instrument_service.resolve_symbol(db, body.symbol, body.exchange)
    _apply(t, body, inst.id)
    await db.commit()
    await db.refresh(t)
    return _trade_dict(t, inst)


@router.post("/trades/{trade_id}/close")
async def close_trade(trade_id: int, body: CloseIn, user: WriterUser, db: DB) -> dict[str, Any]:
    t = await _own(db, user.id, trade_id)
    if t.status == "CLOSED":
        raise AppError("Trade is already closed", code="conflict", status_code=409)
    exit_at = body.exit_at if body.exit_at.tzinfo else body.exit_at.replace(tzinfo=IST)
    if exit_at < t.entry_at or exit_at > datetime.now(UTC):
        raise AppError("Exit time must be after entry and not in the future", code="validation_error", status_code=422)
    t.exit_at, t.exit_price, t.status = exit_at, body.exit_price, "CLOSED"
    t.exit_reason = body.exit_reason
    if body.brokerage is not None:
        t.brokerage = body.brokerage
    if body.taxes is not None:
        t.taxes = body.taxes
    await db.commit()
    await db.refresh(t)
    return _trade_dict(t, await db.get(Instrument, t.instrument_id))


@router.delete("/trades/{trade_id}", status_code=204)
async def delete_trade(trade_id: int, user: WriterUser, db: DB) -> Response:
    t = await _own(db, user.id, trade_id)
    await db.delete(t)
    await db.commit()
    return Response(status_code=204)


@router.get("/stats")
async def stats(
    user: CurrentUser,
    db: DB,
    strategy: str | None = Query(None, max_length=100),
    start: date | None = None,
    end: date | None = None,
) -> dict[str, Any]:
    rows = await _load(db, user.id, strategy=strategy, start=start, end=end)
    trades = [to_jtrade(t, i.symbol) for t, i in rows]
    return {
        "statistics": ja.statistics(trades),
        "findings": ja.behaviour_findings(trades),
        "methodology": "Net P&L = (exit − entry) × quantity × direction − brokerage − taxes. R-multiple = net P&L "
        "÷ planned risk (|entry − stop| × quantity); trades without a stop have no R. Profit factor "
        "= gross wins ÷ gross losses. Drawdown is measured on cumulative closed-trade P&L.",
    }


@router.get("/coach")
async def latest_coach(user: CurrentUser, db: DB) -> dict[str, Any] | None:
    r = await db.scalar(
        select(AIReport)
        .where(AIReport.user_id == user.id, AIReport.kind == "journal_coach")
        .order_by(AIReport.created_at.desc())
        .limit(1)
    )
    return _report(r) if r else None


@router.post("/coach", status_code=201)
async def run_coach(user: WriterUser, db: DB) -> dict[str, Any]:
    await enforce_rate_limit(f"ai:{user.id}", AI_RATE_PER_MIN)
    rows = await _load(db, user.id)
    trades = [to_jtrade(t, i.symbol) for t, i in rows]
    closed = [t for t in trades if t.net_pnl is not None]
    if len(closed) < 5:
        raise AppError("The coach needs at least 5 closed trades to review", code="insufficient_data", status_code=422)
    recent = [
        {
            "id": t.id,
            "symbol": t.symbol,
            "direction": t.direction,
            "strategy": t.strategy,
            "entry_at": t.entry_at,
            "exit_at": t.exit_at,
            "net_pnl": round(t.net_pnl, 2),
            "r_multiple": round(t.r_multiple, 2) if t.r_multiple is not None else None,
            "planned_rr": round(t.planned_rr, 2) if t.planned_rr is not None else None,
            "holding_days": round(t.holding_days, 2) if t.holding_days is not None else None,
            "had_stop": t.stop_loss is not None,
            "emotion": t.emotion,
            "tags": list(t.tags),
        }
        for t in sorted(closed, key=lambda x: x.exit_at or x.entry_at, reverse=True)[:60]
    ]
    reasons = {t.id: (t.entry_reason[:300], t.exit_reason[:300]) for t, _ in rows}
    for r in recent:
        r["entry_reason"], r["exit_reason"] = reasons.get(r["id"], ("", ""))
    report = await journal_coach(db, user.id, ja.statistics(trades), ja.behaviour_findings(trades), recent)
    return _report(report)


@router.get("/export")
async def export(user: CurrentUser, db: DB) -> Response:
    rows = await _load(db, user.id)
    buf = io.StringIO()
    w = csv.writer(buf)
    cols = [
        "symbol",
        "exchange",
        "direction",
        "status",
        "entry_at",
        "entry_price",
        "quantity",
        "exit_at",
        "exit_price",
        "stop_loss",
        "target",
        "strategy",
        "brokerage",
        "taxes",
        "net_pnl",
        "r_multiple",
        "emotion",
        "tags",
        "entry_reason",
        "exit_reason",
    ]
    w.writerow(cols)
    for t, i in reversed(rows):
        d = _trade_dict(t, i)
        d["tags"] = ";".join(d["tags"])
        w.writerow([d.get(c) if d.get(c) is not None else "" for c in cols])
    return Response(
        buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="trading_journal.csv"'},
    )
