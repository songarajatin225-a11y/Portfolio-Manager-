from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any, Literal

from fastapi import APIRouter, File, Query, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.analytics import portfolio as pa
from app.analytics import risk as ra
from app.api.deps import DB, CurrentUser, WriterUser
from app.core.errors import AppError, ConflictError, NotFoundError
from app.db.models import Portfolio, Transaction
from app.services import instruments as instrument_service
from app.services import portfolio as svc
from app.services.audit import audit

router = APIRouter(tags=["portfolios"])
MAX_PORTFOLIOS = 50
MAX_IMPORT_BYTES = 2 * 1024 * 1024


class PortfolioIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=500)
    benchmark_symbol: str = Field(default="NIFTY 50", min_length=1, max_length=80)


class PortfolioPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=500)
    benchmark_symbol: str | None = Field(default=None, min_length=1, max_length=80)
    target_allocations: dict[str, float] | None = None

    @field_validator("target_allocations")
    @classmethod
    def _targets(cls, v: dict[str, float] | None) -> dict[str, float] | None:
        if v is None:
            return v
        if len(v) > 200 or any(w < 0 or w > 1 for w in v.values()):
            raise ValueError("targets must be weights between 0 and 1")
        if v and abs(sum(v.values()) - 1) > 0.01:
            raise ValueError("target weights must add up to 1 (100%)")
        return {k.upper()[:80]: float(w) for k, w in v.items()}


class TransactionIn(BaseModel):
    symbol: str | None = Field(default=None, max_length=80)
    instrument_id: int | None = None
    exchange: str | None = Field(default=None, pattern="^(NSE|BSE)$")
    side: Literal["BUY", "SELL", "DIVIDEND"]
    quantity: Decimal = Field(gt=0, max_digits=20, decimal_places=6)
    price: Decimal = Field(ge=0, max_digits=20, decimal_places=6)
    charges: Decimal = Field(default=Decimal(0), ge=0, max_digits=20, decimal_places=6)
    trade_date: date
    category: str = Field(default="Long-term", max_length=50)
    notes: str = Field(default="", max_length=500)

    @field_validator("trade_date")
    @classmethod
    def _not_future(cls, v: date) -> date:
        if v > date.today():
            raise ValueError("trade date cannot be in the future")
        return v


class PositionSizeIn(BaseModel):
    capital: float = Field(gt=0)
    risk_pct: float = Field(gt=0, le=100)
    entry: float = Field(gt=0)
    stop: float = Field(gt=0)
    target: float | None = Field(default=None, gt=0)
    lot_size: int = Field(default=1, ge=1, le=100000)
    max_position_pct: float | None = Field(default=None, gt=0, le=100)


def _txn_dict(t: Transaction, inst: Any) -> dict[str, Any]:
    return {
        "id": t.id,
        "instrument_id": t.instrument_id,
        "symbol": inst.symbol,
        "exchange": inst.exchange,
        "name": inst.name,
        "side": t.side,
        "quantity": float(t.quantity),
        "price": float(t.price),
        "charges": float(t.charges or 0),
        "trade_date": t.trade_date,
        "category": t.category,
        "notes": t.notes,
        "value": float(t.quantity * t.price),
        "created_at": t.created_at,
    }


async def _resolve(db: DB, body: TransactionIn) -> int:
    if body.instrument_id:
        inst = await instrument_service.resolve_symbol(db, str(body.instrument_id))
    elif body.symbol:
        inst = await instrument_service.resolve_symbol(db, body.symbol, body.exchange)
    else:
        raise AppError("symbol or instrument_id is required", code="validation_error", status_code=422)
    if inst.is_index:
        raise AppError("Indices cannot be held in a portfolio", code="validation_error", status_code=422)
    return inst.id


# ------------------------------------------------------------------ portfolios


@router.get("/portfolios")
async def list_portfolios(user: CurrentUser, db: DB) -> list[dict[str, Any]]:
    rows = (
        await db.execute(
            select(Portfolio, func.count(Transaction.id))
            .outerjoin(Transaction, Transaction.portfolio_id == Portfolio.id)
            .where(Portfolio.user_id == user.id)
            .group_by(Portfolio.id)
            .order_by(Portfolio.created_at)
        )
    ).all()
    out = []
    for p, n in rows:
        s = await svc.summary(db, p) if n else None
        out.append(
            {
                **svc.portfolio_dict(p),
                "transaction_count": n,
                "current_value": s["current_value"] if s else 0.0,
                "total_pnl": s["total_pnl"] if s else 0.0,
                "xirr_pct": s["xirr_pct"] if s else None,
                "holdings": len(s["holdings"]) if s else 0,
            }
        )
    return out


@router.post("/portfolios", status_code=201)
async def create_portfolio(body: PortfolioIn, user: WriterUser, db: DB) -> dict[str, Any]:
    count = await db.scalar(select(func.count(Portfolio.id)).where(Portfolio.user_id == user.id))
    if (count or 0) >= MAX_PORTFOLIOS:
        raise AppError(f"Limit of {MAX_PORTFOLIOS} portfolios reached", code="limit_reached", status_code=422)
    p = Portfolio(
        user_id=user.id,
        name=body.name.strip(),
        description=body.description,
        benchmark_symbol=body.benchmark_symbol.strip(),
        target_allocations={},
    )
    db.add(p)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise ConflictError("You already have a portfolio with this name") from exc
    await db.refresh(p)
    return svc.portfolio_dict(p)


@router.get("/portfolios/import-template")
async def import_template(user: CurrentUser) -> Response:
    body = ",".join(svc.CSV_COLUMNS) + "\n2024-01-15,RELIANCE,NSE,BUY,10,2500.50,20,Long-term,example row\n"
    return Response(
        body, media_type="text/csv", headers={"Content-Disposition": 'attachment; filename="portfolio_template.csv"'}
    )


@router.get("/portfolios/{portfolio_id}")
async def get_portfolio(portfolio_id: int, user: CurrentUser, db: DB) -> dict[str, Any]:
    p = await svc.own_portfolio(db, user.id, portfolio_id)
    return await svc.summary(db, p)


@router.patch("/portfolios/{portfolio_id}")
async def update_portfolio(portfolio_id: int, body: PortfolioPatch, user: WriterUser, db: DB) -> dict[str, Any]:
    p = await svc.own_portfolio(db, user.id, portfolio_id)
    data = body.model_dump(exclude_unset=True)
    for k, v in data.items():
        if v is not None:
            setattr(p, k, v.strip() if isinstance(v, str) else v)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise ConflictError("You already have a portfolio with this name") from exc
    await db.refresh(p)
    return svc.portfolio_dict(p)


@router.delete("/portfolios/{portfolio_id}", status_code=204)
async def delete_portfolio(portfolio_id: int, user: WriterUser, db: DB) -> Response:
    p = await svc.own_portfolio(db, user.id, portfolio_id)
    name = p.name
    await db.delete(p)
    await audit(
        db,
        user_id=user.id,
        action="portfolio.delete",
        entity_type="portfolio",
        entity_id=portfolio_id,
        details={"name": name},
    )
    await db.commit()
    return Response(status_code=204)


@router.get("/portfolios/{portfolio_id}/performance")
async def portfolio_performance(portfolio_id: int, user: CurrentUser, db: DB) -> dict[str, Any]:
    p = await svc.own_portfolio(db, user.id, portfolio_id)
    return await svc.performance(db, p)


@router.get("/portfolios/{portfolio_id}/risk")
async def portfolio_risk(
    portfolio_id: int, user: CurrentUser, db: DB, lookback_days: int = Query(365, ge=60, le=1825)
) -> dict[str, Any]:
    p = await svc.own_portfolio(db, user.id, portfolio_id)
    return await svc.risk(db, p, lookback_days)


@router.get("/portfolios/{portfolio_id}/rebalance")
async def portfolio_rebalance(
    portfolio_id: int,
    user: CurrentUser,
    db: DB,
    threshold_pp: float = Query(5.0, ge=0.5, le=50),
    max_weight_pct: float = Query(25.0, ge=1, le=100),
) -> dict[str, Any]:
    p = await svc.own_portfolio(db, user.id, portfolio_id)
    s = await svc.summary(db, p)
    return pa.rebalance(s["holdings"], p.target_allocations or {}, threshold_pp, max_weight_pct / 100)


# ------------------------------------------------------------------ transactions


@router.get("/portfolios/{portfolio_id}/transactions")
async def list_transactions(portfolio_id: int, user: CurrentUser, db: DB) -> list[dict[str, Any]]:
    await svc.own_portfolio(db, user.id, portfolio_id)
    rows = await svc.load_transactions(db, portfolio_id)
    return [_txn_dict(t, i) for t, i in reversed(rows)]


async def _check_ledger(db: DB, portfolio_id: int) -> None:
    await db.flush()
    svc.validate_ledger(svc.to_txns(await svc.load_transactions(db, portfolio_id)))


@router.post("/portfolios/{portfolio_id}/transactions", status_code=201)
async def add_transaction(portfolio_id: int, body: TransactionIn, user: WriterUser, db: DB) -> dict[str, Any]:
    await svc.own_portfolio(db, user.id, portfolio_id)
    iid = await _resolve(db, body)
    t = Transaction(
        portfolio_id=portfolio_id,
        instrument_id=iid,
        side=body.side,
        quantity=body.quantity,
        price=body.price,
        charges=body.charges,
        trade_date=body.trade_date,
        category=body.category.strip() or "Long-term",
        notes=body.notes,
    )
    db.add(t)
    try:
        await _check_ledger(db, portfolio_id)
    except AppError:
        await db.rollback()
        raise
    await db.commit()
    await db.refresh(t)
    return {"id": t.id}


@router.patch("/portfolios/{portfolio_id}/transactions/{txn_id}")
async def update_transaction(
    portfolio_id: int, txn_id: int, body: TransactionIn, user: WriterUser, db: DB
) -> dict[str, Any]:
    await svc.own_portfolio(db, user.id, portfolio_id)
    t = await db.get(Transaction, txn_id)
    if t is None or t.portfolio_id != portfolio_id:
        raise NotFoundError("Transaction not found")
    t.instrument_id = await _resolve(db, body)
    for k in ("side", "quantity", "price", "charges", "trade_date", "notes"):
        setattr(t, k, getattr(body, k))
    t.category = body.category.strip() or "Long-term"
    try:
        await _check_ledger(db, portfolio_id)
    except AppError:
        await db.rollback()
        raise
    await db.commit()
    return {"id": t.id}


@router.delete("/portfolios/{portfolio_id}/transactions/{txn_id}", status_code=204)
async def delete_transaction(portfolio_id: int, txn_id: int, user: WriterUser, db: DB) -> Response:
    await svc.own_portfolio(db, user.id, portfolio_id)
    t = await db.get(Transaction, txn_id)
    if t is None or t.portfolio_id != portfolio_id:
        raise NotFoundError("Transaction not found")
    await db.delete(t)
    try:
        await _check_ledger(db, portfolio_id)
    except AppError as exc:
        await db.rollback()
        raise AppError(
            f"Deleting this transaction would leave a sell without holdings: {exc}",
            code="invalid_ledger",
            status_code=422,
        ) from exc
    await db.commit()
    return Response(status_code=204)


@router.get("/portfolios/{portfolio_id}/export")
async def export_transactions(portfolio_id: int, user: CurrentUser, db: DB) -> Response:
    p = await svc.own_portfolio(db, user.id, portfolio_id)
    body = svc.export_csv(await svc.load_transactions(db, portfolio_id))
    safe = "".join(c if c.isalnum() else "_" for c in p.name)[:60] or "portfolio"
    return Response(
        body, media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="{safe}_transactions.csv"'}
    )


@router.post("/portfolios/{portfolio_id}/import")
async def import_transactions(
    portfolio_id: int, user: WriterUser, db: DB, file: UploadFile = File(...), dry_run: bool = Query(False)
) -> dict[str, Any]:
    p = await svc.own_portfolio(db, user.id, portfolio_id)
    content = await file.read(MAX_IMPORT_BYTES + 1)
    if len(content) > MAX_IMPORT_BYTES:
        raise AppError("File larger than 2 MB", code="file_too_large", status_code=413)
    result = await svc.import_csv(db, p, content, dry_run=dry_run)
    if result["imported"]:
        await audit(
            db,
            user_id=user.id,
            action="portfolio.import",
            entity_type="portfolio",
            entity_id=portfolio_id,
            details={"rows": result["imported"]},
        )
        await db.commit()
    return result


# ------------------------------------------------------------------ calculators


@router.post("/risk/position-size")
async def position_size(body: PositionSizeIn, user: CurrentUser) -> dict[str, Any]:
    try:
        out = ra.position_size(body.capital, body.risk_pct, body.entry, body.stop, body.lot_size, body.max_position_pct)
    except ValueError as exc:
        raise AppError(str(exc), code="validation_error", status_code=422) from exc
    if body.target is not None:
        if (body.target - body.entry) * (body.entry - body.stop) <= 0:
            raise AppError(
                "Target must be on the opposite side of the entry from the stop",
                code="validation_error",
                status_code=422,
            )
        out["risk_reward"] = ra.risk_reward(body.entry, body.stop, body.target)
        out["reward_amount"] = round(out["quantity"] * abs(body.target - body.entry), 2)
    return out
