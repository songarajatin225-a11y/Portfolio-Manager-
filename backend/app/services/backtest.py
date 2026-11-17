"""Backtest orchestration: resolves the universe (with point-in-time index membership), loads
daily history including warm-up bars, and runs the engine off the event loop."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, Literal

import pandas as pd
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics import backtest as bt
from app.analytics.indicators import to_frame
from app.core.errors import AppError
from app.db.models import IndexConstituent, Instrument
from app.marketdata.calendar import IST
from app.services import instruments as instrument_service
from app.services.analysis import benchmark_instrument
from app.services.candles import get_candles
from app.services.market_data import is_simulated_mode

MAX_INSTRUMENTS = 100
MAX_YEARS = 15
WARMUP_DAYS = 420


class CostsIn(BaseModel):
    brokerage_pct: float = Field(0.03, ge=0, le=5)
    brokerage_cap: float | None = Field(20.0, ge=0)
    statutory_pct: float = Field(0.11, ge=0, le=5)
    slippage_bps: float = Field(5.0, ge=0, le=500)


class UniverseIn(BaseModel):
    type: Literal["symbols", "index"] = "symbols"
    symbols: list[str] = Field(default_factory=list, max_length=MAX_INSTRUMENTS)
    index_code: str | None = Field(default=None, max_length=40)


class BacktestIn(BaseModel):
    name: str = Field(default="", max_length=150)
    strategy: str
    params: dict[str, Any] = Field(default_factory=dict)
    universe: UniverseIn
    start: date
    end: date
    initial_capital: float = Field(1_000_000, gt=0, le=1e12)
    sizing_method: Literal["percent_equity", "fixed_amount", "risk_percent"] = "percent_equity"
    sizing_value: float = Field(20.0, gt=0)
    max_positions: int = Field(5, ge=1, le=50)
    stop_loss_pct: float | None = Field(None, gt=0, lt=100)
    take_profit_pct: float | None = Field(None, gt=0, le=1000)
    trailing_stop_pct: float | None = Field(None, gt=0, lt=100)
    costs: CostsIn = Field(default_factory=CostsIn)
    risk_free_rate_pct: float = Field(6.5, ge=0, le=30)

    @model_validator(mode="after")
    def _check(self) -> BacktestIn:
        if self.strategy not in bt.STRATEGIES:
            raise ValueError(f"strategy must be one of {', '.join(bt.STRATEGIES)}")
        if self.end > date.today():
            self.end = date.today()
        if self.start >= self.end:
            raise ValueError("start must be before end")
        if (self.end - self.start).days > 366 * MAX_YEARS:
            raise ValueError(f"period is limited to {MAX_YEARS} years")
        if self.sizing_method == "percent_equity" and self.sizing_value > 100:
            raise ValueError("percent of equity cannot exceed 100")
        if self.sizing_method == "risk_percent" and (self.sizing_value > 10 or not self.stop_loss_pct):
            raise ValueError("risk-based sizing needs a stop-loss % and risk per trade of at most 10%")
        if self.universe.type == "symbols" and not self.universe.symbols:
            raise ValueError("choose at least one symbol")
        if self.universe.type == "index" and not self.universe.index_code:
            raise ValueError("choose an index")
        return self


async def _universe(db: AsyncSession, body: BacktestIn) -> tuple[list[Instrument], Any, list[str]]:
    warnings: list[str] = []
    if body.universe.type == "symbols":
        insts: list[Instrument] = []
        missing = []
        for s in dict.fromkeys(x.strip().upper() for x in body.universe.symbols if x.strip()):
            try:
                insts.append(await instrument_service.resolve_symbol(db, s))
            except Exception:  # noqa: BLE001 - reported to the user
                missing.append(s)
        if missing:
            raise AppError(f"Unknown symbols: {', '.join(missing)}", code="validation_error", status_code=422)
        warnings.append(
            "Hand-picked instruments: choosing symbols with hindsight (for example today's winners) "
            "biases results upwards. An index universe with point-in-time membership avoids this."
        )
        return insts, None, warnings

    simulated = await is_simulated_mode(db)
    code = body.universe.index_code.upper()
    stored = f"SIM:{code}" if simulated else code
    rows = (
        (
            await db.execute(
                select(IndexConstituent).where(
                    IndexConstituent.index_code == stored,
                    IndexConstituent.start_date <= body.end,
                    or_(IndexConstituent.end_date.is_(None), IndexConstituent.end_date >= body.start),
                )
            )
        )
        .scalars()
        .all()
    )
    if not rows:
        raise AppError(
            f"No constituent history for {code}. Import constituents in System Administration → Data.",
            code="validation_error",
            status_code=422,
        )
    spans: dict[int, list[tuple[date, date | None]]] = defaultdict(list)
    for r in rows:
        spans[r.instrument_id].append((r.start_date, r.end_date))
    earliest = min(r.start_date for r in rows)
    if earliest > body.start:
        warnings.append(
            f"Membership history for {code} starts on {earliest}. Before that date the earliest known "
            "members are used, which introduces survivorship bias for that part of the test."
        )
    else:
        warnings.append(f"Entries are limited to stocks that were members of {code} on each date (point-in-time).")
    if len(spans) > MAX_INSTRUMENTS:
        raise AppError(
            f"Universe has {len(spans)} instruments; the limit is {MAX_INSTRUMENTS}",
            code="validation_error",
            status_code=422,
        )
    insts = list((await db.scalars(select(Instrument).where(Instrument.id.in_(list(spans))))).all())
    by_symbol = {i.symbol: i.id for i in insts}
    first_members = {iid for iid, ss in spans.items() if any(s == earliest for s, _ in ss)}

    def eligible(sym: str, d: date) -> bool:
        iid = by_symbol.get(sym)
        if iid is None:
            return False
        if d < earliest:
            return iid in first_members
        return any(s <= d and (e is None or d <= e) for s, e in spans[iid])

    return insts, eligible, warnings


async def run_backtest(db: AsyncSession, body: BacktestIn) -> dict[str, Any]:
    insts, eligible, warnings = await _universe(db, body)
    if body.strategy == "momentum" and len(insts) < 3:
        raise AppError("Momentum ranking needs at least 3 instruments", code="validation_error", status_code=422)
    start_dt = datetime.combine(body.start - timedelta(days=WARMUP_DAYS), time.min, IST).astimezone(UTC)
    end_dt = datetime.combine(body.end, time.max, IST).astimezone(UTC)
    frames: dict[str, pd.DataFrame] = {}
    no_data = []
    statuses = set()
    sources = set()
    for inst in insts:
        res = await get_candles(db, inst, "1d", start_dt, end_dt)
        df = to_frame(res.bars)
        if df.empty:
            no_data.append(inst.symbol)
            continue
        frames[inst.symbol] = df
        statuses.add(res.data_status)
        sources.add(res.source)
    if not frames:
        raise AppError("No daily history is available for the selected instruments", code="no_data", status_code=422)
    if no_data:
        warnings.append(f"No history for: {', '.join(no_data[:20])}{'…' if len(no_data) > 20 else ''}")
    late = [s for s, df in frames.items() if df.index[0].date() > body.start]
    if late:
        warnings.append(
            f"{len(late)} instruments have history starting after the test start "
            f"({', '.join(late[:10])}); they trade only once data exists."
        )
    bench_close = None
    bench_inst = await benchmark_instrument(db)
    if bench_inst is not None:
        bench = to_frame((await get_candles(db, bench_inst, "1d", start_dt, end_dt)).bars)
        bench_close = bench["close"] if not bench.empty else None
    cfg = bt.Config(
        strategy=body.strategy,
        params={**bt.STRATEGIES[body.strategy]["params"], **body.params},
        start=body.start,
        end=body.end,
        initial_capital=body.initial_capital,
        sizing_method=body.sizing_method,
        sizing_value=body.sizing_value,
        max_positions=body.max_positions,
        stop_loss_pct=body.stop_loss_pct,
        take_profit_pct=body.take_profit_pct,
        trailing_stop_pct=body.trailing_stop_pct,
        costs=bt.Costs(**body.costs.model_dump()),
        risk_free_rate_pct=body.risk_free_rate_pct,
    )
    try:
        result = await asyncio.to_thread(bt.run, cfg, frames, eligible, bench_close)
    except bt.BacktestError as exc:
        raise AppError(str(exc), code="validation_error", status_code=422) from exc
    if bench_close is None:
        warnings.append("No benchmark history is available, so there is no benchmark comparison.")
    elif bench_close.index[-1].date() < body.end - timedelta(days=10) or bench_close.index[
        0
    ].date() > body.start + timedelta(days=10):
        warnings.append("Benchmark history does not cover the whole test period; the comparison is partial.")
    if result["metrics"]["trades"] < 30:
        warnings.append(f"Only {result['metrics']['trades']} trades: too few for statistically reliable conclusions.")
    result.update(
        {
            "data_status": "SIMULATED" if "SIMULATED" in statuses else "HISTORICAL",
            "data_sources": sorted(sources),
            "warnings": warnings,
            "assumptions": [
                "Signals are computed at each daily close and filled at the next session's open.",
                f"Slippage of {body.costs.slippage_bps} bps is applied against every fill.",
                f"Brokerage {body.costs.brokerage_pct}% per order"
                + (f" capped at ₹{body.costs.brokerage_cap:g}" if body.costs.brokerage_cap is not None else "")
                + f", plus statutory charges of {body.costs.statutory_pct}% per order (STT, exchange, GST, stamp duty "
                "approximated).",
                "Stops and targets are checked against each bar's low and high; if both are touched in the same "
                "bar the stop is assumed to fill first, and gaps through the stop fill at the open.",
                "Long-only, whole shares, no leverage; taxes on gains are not modelled.",
                "Past performance in a backtest does not predict future results.",
            ],
            "universe_size": len(frames),
        }
    )
    return result
