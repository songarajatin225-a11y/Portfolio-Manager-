from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from fastapi.responses import Response
from sqlalchemy import select

from app.analytics import backtest as bt
from app.api.deps import DB, CurrentUser, WriterUser
from app.core.errors import NotFoundError
from app.core.ratelimit import enforce_rate_limit
from app.db.models import Backtest
from app.services.backtest import MAX_INSTRUMENTS, MAX_YEARS, BacktestIn, CostsIn, run_backtest

router = APIRouter(prefix="/backtests", tags=["backtests"])
RUNS_PER_MIN = 10


def _brief(b: Backtest) -> dict[str, Any]:
    m = (b.result or {}).get("metrics", {})
    return {
        "id": b.id,
        "name": b.name,
        "strategy": b.config.get("strategy"),
        "status": b.status,
        "created_at": b.created_at,
        "start": b.config.get("start"),
        "end": b.config.get("end"),
        "total_return_pct": m.get("total_return_pct"),
        "cagr_pct": m.get("cagr_pct"),
        "max_drawdown_pct": m.get("max_drawdown_pct"),
        "sharpe": m.get("sharpe"),
        "trades": m.get("trades"),
        "data_status": (b.result or {}).get("data_status"),
    }


@router.get("/strategies")
async def strategies(_: CurrentUser) -> dict[str, Any]:
    return {
        "strategies": bt.STRATEGIES,
        "operators": bt.OPERATORS,
        "series_help": bt.SERIES_HELP,
        "default_costs": CostsIn().model_dump(),
        "max_instruments": MAX_INSTRUMENTS,
        "max_years": MAX_YEARS,
    }


@router.post("", status_code=201)
async def create(body: BacktestIn, user: WriterUser, db: DB) -> dict[str, Any]:
    await enforce_rate_limit(f"backtest:{user.id}", RUNS_PER_MIN)
    result = await run_backtest(db, body)
    name = body.name.strip() or f"{bt.STRATEGIES[body.strategy]['label']} {body.start}→{body.end}"
    b = Backtest(
        user_id=user.id, name=name[:150], config=body.model_dump(mode="json"), status="completed", result=result
    )
    db.add(b)
    await db.commit()
    await db.refresh(b)
    return {**_brief(b), "config": b.config, "result": b.result}


@router.get("")
async def list_backtests(user: CurrentUser, db: DB) -> list[dict[str, Any]]:
    rows = await db.scalars(
        select(Backtest).where(Backtest.user_id == user.id).order_by(Backtest.created_at.desc()).limit(100)
    )
    return [_brief(b) for b in rows]


@router.get("/{backtest_id}")
async def get_backtest(backtest_id: int, user: CurrentUser, db: DB) -> dict[str, Any]:
    b = await db.get(Backtest, backtest_id)
    if b is None or b.user_id != user.id:
        raise NotFoundError("Backtest not found")
    return {**_brief(b), "config": b.config, "result": b.result}


@router.delete("/{backtest_id}", status_code=204)
async def delete_backtest(backtest_id: int, user: WriterUser, db: DB) -> Response:
    b = await db.get(Backtest, backtest_id)
    if b is None or b.user_id != user.id:
        raise NotFoundError("Backtest not found")
    await db.delete(b)
    await db.commit()
    return Response(status_code=204)
