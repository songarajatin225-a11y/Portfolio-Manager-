"""CSV imports for data the brokers don't provide (or that you licence separately).

Supported datasets and required columns (case-insensitive headers):

fundamentals         symbol, period_type (Q/A), period_end (YYYY-MM-DD), source, plus any of
                     revenue, ebitda, ebit, net_profit, eps, total_equity, total_debt, total_assets,
                     current_liabilities, cash, operating_cash_flow, capex, shares_outstanding,
                     dividend_per_share, statement_type, source_ref, reported_at
shareholding         symbol, period_end, source, promoter_pct, fii_pct, dii_pct, public_pct, others_pct
institutional_flows  date, category (FII/DII), source, buy_value, sell_value, net_value, segment
bulk_block_deals     date, symbol, deal_type (BULK/BLOCK), client_name, side (BUY/SELL), quantity, price, source
candles              symbol, date (or ts), open, high, low, close, volume, interval (default 1d)

Every row is validated; invalid rows are reported and skipped. Each record keeps its source.
"""

from __future__ import annotations

import csv
import io
import math
import uuid
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics.fundamentals import NUMERIC_FIELDS, Period, validate_period
from app.core.errors import AppError
from app.db.models import BulkBlockDeal, Fundamental, InstitutionalFlow, Instrument, Shareholding
from app.marketdata.calendar import IST
from app.marketdata.types import INTERVALS, CandleBar
from app.services.candles import upsert_candles

DATASETS = ("fundamentals", "shareholding", "institutional_flows", "bulk_block_deals", "candles")
MAX_ROWS = 50_000


class RowError(ValueError):
    pass


def _rows(content: bytes) -> list[dict[str, str]]:
    text = content.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise AppError("CSV has no header row")
    rows = [{(k or "").strip().lower(): (v or "").strip() for k, v in row.items()} for row in reader]
    if len(rows) > MAX_ROWS:
        raise AppError(f"At most {MAX_ROWS} rows per import")
    return rows


def _num(row: dict[str, str], key: str, required: bool = False) -> float | None:
    raw = row.get(key, "").replace(",", "")
    if raw in ("", "-", "NA", "N/A", "null"):
        if required:
            raise RowError(f"{key} is required")
        return None
    try:
        v = float(raw)
    except ValueError as exc:
        raise RowError(f"{key} is not a number: {raw!r}") from exc
    if not math.isfinite(v):
        raise RowError(f"{key} is not finite")
    return v


def _date(row: dict[str, str], key: str, required: bool = True) -> date | None:
    raw = row.get(key, "")
    if not raw:
        if required:
            raise RowError(f"{key} is required")
        return None
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%d-%b-%Y"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    raise RowError(f"{key} has an unrecognised date format: {raw!r}")


def _req(row: dict[str, str], key: str) -> str:
    v = row.get(key, "")
    if not v:
        raise RowError(f"{key} is required")
    return v


class _Resolver:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.cache: dict[str, int | None] = {}
        self.simulated: dict[int, bool] = {}

    async def __call__(self, symbol: str) -> int:
        key = symbol.upper()
        if key not in self.cache:
            row = (
                await self.db.execute(
                    select(Instrument.id, Instrument.is_simulated)
                    .where(func.upper(Instrument.symbol) == key, Instrument.expiry.is_(None))
                    .order_by((Instrument.exchange == "NSE").desc(), Instrument.is_simulated)
                    .limit(1)
                )
            ).first()
            self.cache[key] = row.id if row else None
            if row:
                self.simulated[row.id] = row.is_simulated
        iid = self.cache[key]
        if iid is None:
            raise RowError(f"unknown symbol {symbol!r} (sync the instrument master first)")
        return iid


async def import_csv(
    db: AsyncSession, dataset: str, content: bytes, filename: str, user_id: uuid.UUID | None
) -> dict[str, Any]:
    if dataset not in DATASETS:
        raise AppError(f"Unknown dataset. Choose one of: {', '.join(DATASETS)}")
    rows = _rows(content)
    resolve = _Resolver(db)
    ok, errors, warnings = 0, [], []
    candle_groups: dict[tuple[int, str], list[CandleBar]] = {}
    for n, row in enumerate(rows, start=2):  # header is line 1
        try:
            if dataset == "fundamentals":
                warn = await _fundamental(db, row, resolve, user_id)
                if warn:
                    warnings.append({"line": n, "warning": warn})
            elif dataset == "shareholding":
                await _shareholding(db, row, resolve)
            elif dataset == "institutional_flows":
                await _flow(db, row)
            elif dataset == "bulk_block_deals":
                await _deal(db, row, resolve)
            else:
                iid = await resolve(_req(row, "symbol"))
                interval = row.get("interval") or "1d"
                if interval not in INTERVALS:
                    raise RowError(f"interval must be one of {INTERVALS}")
                bar = _candle(row)
                candle_groups.setdefault((iid, interval), []).append(bar)
            ok += 1
        except RowError as exc:
            errors.append({"line": n, "error": str(exc)})
    stored = 0
    for (iid, interval), bars in candle_groups.items():
        # candles inherit the instrument's data mode so real and simulated data never mix
        stored += await upsert_candles(
            db, iid, interval, bars, f"csv:{filename[:50]}", simulated=resolve.simulated.get(iid, False)
        )
    await db.commit()
    result: dict[str, Any] = {
        "dataset": dataset,
        "rows": len(rows),
        "imported": ok,
        "rejected": len(errors),
        "errors": errors[:100],
        "warnings": warnings[:100],
    }
    if dataset == "candles":
        result["candles_stored"] = stored
    return result


async def _fundamental(
    db: AsyncSession, row: dict[str, str], resolve: _Resolver, user_id: uuid.UUID | None
) -> str | None:
    iid = await resolve(_req(row, "symbol"))
    ptype = _req(row, "period_type").upper()[:1]
    values = {f: _num(row, f) for f in NUMERIC_FIELDS}
    period = Period(
        period_type=ptype,
        period_end=_date(row, "period_end"),
        source=_req(row, "source"),  # type: ignore[arg-type]
        reported_at=_date(row, "reported_at", required=False),
        statement_type=(row.get("statement_type") or "consolidated").lower(),
        values=values,
    )
    errors = validate_period(period)
    stmt = insert(Fundamental).values(
        instrument_id=iid,
        period_type=ptype,
        period_end=period.period_end,
        statement_type=period.statement_type,
        source=period.source,
        source_ref=row.get("source_ref") or None,
        reported_at=period.reported_at,
        raw={k: v for k, v in row.items() if v},
        validation_errors=errors,
        created_by=user_id,
        **values,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["instrument_id", "period_type", "period_end", "statement_type"],
        set_={
            **{f: getattr(stmt.excluded, f) for f in NUMERIC_FIELDS},
            "source": stmt.excluded.source,
            "source_ref": stmt.excluded.source_ref,
            "reported_at": stmt.excluded.reported_at,
            "raw": stmt.excluded.raw,
            "validation_errors": stmt.excluded.validation_errors,
        },
    )
    await db.execute(stmt)
    return f"stored but excluded from ratios: {'; '.join(errors)}" if errors else None


async def _shareholding(db: AsyncSession, row: dict[str, str], resolve: _Resolver) -> None:
    iid = await resolve(_req(row, "symbol"))
    pcts = {k: _num(row, k) for k in ("promoter_pct", "fii_pct", "dii_pct", "public_pct", "others_pct")}
    for k, v in pcts.items():
        if v is not None and not 0 <= v <= 100:
            raise RowError(f"{k} must be between 0 and 100")
    total = sum(v for v in pcts.values() if v is not None)
    if total > 100.5:
        raise RowError(f"shareholding percentages sum to {total:.2f} (> 100)")
    stmt = insert(Shareholding).values(
        instrument_id=iid,
        period_end=_date(row, "period_end"),
        source=_req(row, "source"),
        reported_at=_date(row, "reported_at", required=False),
        raw=row,
        **pcts,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["instrument_id", "period_end"],
        set_={**{k: getattr(stmt.excluded, k) for k in pcts}, "source": stmt.excluded.source},
    )
    await db.execute(stmt)


async def _flow(db: AsyncSession, row: dict[str, str]) -> None:
    cat = _req(row, "category").upper()
    if cat not in ("FII", "DII"):
        raise RowError("category must be FII or DII")
    buy, sell, net = _num(row, "buy_value"), _num(row, "sell_value"), _num(row, "net_value")
    if net is None and buy is not None and sell is not None:
        net = buy - sell
    if net is None:
        raise RowError("net_value (or buy_value and sell_value) is required")
    stmt = insert(InstitutionalFlow).values(
        trade_date=_date(row, "date"),
        category=cat,
        segment=(row.get("segment") or "CASH").upper(),
        buy_value=buy,
        sell_value=sell,
        net_value=net,
        source=_req(row, "source"),
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["trade_date", "category", "segment"],
        set_={
            "buy_value": stmt.excluded.buy_value,
            "sell_value": stmt.excluded.sell_value,
            "net_value": stmt.excluded.net_value,
            "source": stmt.excluded.source,
        },
    )
    await db.execute(stmt)


async def _deal(db: AsyncSession, row: dict[str, str], resolve: _Resolver) -> None:
    iid = await resolve(_req(row, "symbol"))
    deal_type, side = _req(row, "deal_type").upper(), _req(row, "side").upper()
    if deal_type not in ("BULK", "BLOCK"):
        raise RowError("deal_type must be BULK or BLOCK")
    if side not in ("BUY", "SELL"):
        raise RowError("side must be BUY or SELL")
    qty, price = _num(row, "quantity", True), _num(row, "price", True)
    if qty <= 0 or price <= 0:  # type: ignore[operator]
        raise RowError("quantity and price must be positive")
    db.add(
        BulkBlockDeal(
            trade_date=_date(row, "date"),
            instrument_id=iid,
            deal_type=deal_type,
            client_name=_req(row, "client_name")[:300],
            side=side,
            quantity=qty,
            price=price,
            source=_req(row, "source"),
        )
    )


def _candle(row: dict[str, str]) -> CandleBar:
    if row.get("ts"):
        try:
            ts = datetime.fromisoformat(row["ts"].replace("Z", "+00:00"))
        except ValueError as exc:
            raise RowError(f"ts is not ISO-8601: {row['ts']!r}") from exc
        ts = ts if ts.tzinfo else ts.replace(tzinfo=IST)
    else:
        d = _date(row, "date")
        ts = datetime.combine(d, datetime.min.time(), IST)  # type: ignore[arg-type]
    bar = CandleBar(
        ts=ts.astimezone(UTC),
        open=_num(row, "open", True),
        high=_num(row, "high", True),  # type: ignore[arg-type]
        low=_num(row, "low", True),
        close=_num(row, "close", True),
        volume=_num(row, "volume") or 0.0,
    )  # type: ignore[arg-type]
    from app.marketdata.types import validate_candle

    problems = validate_candle(bar)
    if problems:
        raise RowError("; ".join(problems))
    return bar


TEMPLATES = {
    "fundamentals": "symbol,period_type,period_end,statement_type,revenue,ebitda,ebit,net_profit,eps,total_equity,"
    "total_debt,total_assets,current_liabilities,cash,operating_cash_flow,capex,shares_outstanding,"
    "dividend_per_share,source,source_ref,reported_at\n",
    "shareholding": "symbol,period_end,promoter_pct,fii_pct,dii_pct,public_pct,others_pct,source,reported_at\n",
    "institutional_flows": "date,category,segment,buy_value,sell_value,net_value,source\n",
    "bulk_block_deals": "date,symbol,deal_type,client_name,side,quantity,price,source\n",
    "candles": "symbol,interval,date,open,high,low,close,volume\n",
}
