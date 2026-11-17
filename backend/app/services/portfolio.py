"""Portfolio service: loads transactions, prices and history, and runs the pure analytics."""

from __future__ import annotations

import csv
import io
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics import portfolio as pa
from app.analytics import risk as ra
from app.core.errors import AppError, NotFoundError
from app.db.models import Instrument, Portfolio, Transaction
from app.services import instruments as instrument_service
from app.services.analysis import benchmark_instrument, load_daily
from app.services.candles import get_candles
from app.services.market_data import cached_quotes, data_mode

CSV_COLUMNS = ["date", "symbol", "exchange", "side", "quantity", "price", "charges", "category", "notes"]
MAX_IMPORT_ROWS = 5000


async def own_portfolio(db: AsyncSession, user_id: Any, portfolio_id: int) -> Portfolio:
    p = await db.get(Portfolio, portfolio_id)
    if p is None or p.user_id != user_id:
        raise NotFoundError("Portfolio not found")
    return p


async def load_transactions(db: AsyncSession, portfolio_id: int) -> list[tuple[Transaction, Instrument]]:
    rows = await db.execute(
        select(Transaction, Instrument)
        .join(Instrument, Instrument.id == Transaction.instrument_id)
        .where(Transaction.portfolio_id == portfolio_id)
        .order_by(Transaction.trade_date, Transaction.id)
    )
    return list(rows.all())


def to_txns(rows: list[tuple[Transaction, Instrument]]) -> list[pa.Txn]:
    return [
        pa.Txn(
            t.instrument_id,
            t.side,
            float(t.quantity),
            float(t.price),
            float(t.charges or 0),
            t.trade_date,
            t.category or "",
        )
        for t, _ in rows
    ]


def validate_ledger(txns: list[pa.Txn]) -> None:
    try:
        pa.build_positions(txns)
    except pa.LedgerError as exc:
        raise AppError(str(exc), code="invalid_ledger", status_code=422) from exc


async def _meta(db: AsyncSession, ids: list[int]) -> dict[int, dict[str, Any]]:
    if not ids:
        return {}
    rows = await db.scalars(select(Instrument).where(Instrument.id.in_(ids)))
    return {
        i.id: {
            "symbol": i.symbol,
            "name": i.name,
            "exchange": i.exchange,
            "sector": i.sector,
            "is_simulated": i.is_simulated,
        }
        for i in rows
    }


async def current_prices(db: AsyncSession, insts: dict[int, Instrument]) -> dict[int, dict[str, Any]]:
    """Latest price per instrument: the labelled cached quote, else the last stored daily close."""
    out: dict[int, dict[str, Any]] = {}
    quotes = await cached_quotes(db, list(insts))
    for iid, inst in insts.items():
        q = quotes.get(iid)
        if q and q.get("ltp"):
            out[iid] = {
                "price": float(q["ltp"]),
                "source": q.get("source"),
                "data_status": q["data_status"],
                "as_of": q.get("exchange_ts") or q.get("last_trade_time") or q.get("received_at"),
            }
            continue
        end = datetime.now(UTC)
        res = await get_candles(db, inst, "1d", end - timedelta(days=15), end, allow_fetch=False)
        if res.bars:
            bar = res.bars[-1]
            out[iid] = {
                "price": float(bar.close),
                "source": res.source,
                "data_status": res.data_status,
                "as_of": bar.ts.isoformat(),
            }
    return out


async def summary(db: AsyncSession, p: Portfolio) -> dict[str, Any]:
    rows = await load_transactions(db, p.id)
    txns = to_txns(rows)
    insts = {i.id: i for _, i in rows}
    positions = pa.build_positions(txns)
    held = {iid: insts[iid] for iid, pos in positions.items() if pos.quantity > 1e-9}
    prices = await current_prices(db, held)
    meta = await _meta(db, list(insts))
    out = pa.summarize(positions, prices, meta, pa.cash_flows(txns), date.today())
    statuses = sorted({h["price_status"] for h in out["holdings"] if h.get("price_status")})
    out.update(
        {
            "portfolio": portfolio_dict(p),
            "transaction_count": len(rows),
            "data_mode": await data_mode(db),
            "price_statuses": statuses,
            "first_trade_date": txns[0].trade_date.isoformat() if txns else None,
            "has_simulated_instruments": any(i.is_simulated for i in insts.values()),
        }
    )
    return out


def portfolio_dict(p: Portfolio) -> dict[str, Any]:
    return {
        "id": p.id,
        "name": p.name,
        "description": p.description,
        "benchmark_symbol": p.benchmark_symbol,
        "target_allocations": p.target_allocations or {},
        "created_at": p.created_at,
    }


async def _closes(
    db: AsyncSession, insts: dict[int, Instrument], start: date, allow_fetch: bool = True
) -> pd.DataFrame:
    days = max((date.today() - start).days + 10, 30)
    frames = {}
    for iid, inst in insts.items():
        df = await load_daily(db, inst, days=days, allow_fetch=allow_fetch)
        if not df.empty:
            s = df["close"].astype(float)
            s.index = s.index.normalize()
            frames[iid] = s[~s.index.duplicated(keep="last")]
    if not frames:
        return pd.DataFrame()
    return pd.DataFrame(frames).sort_index()


async def _benchmark(db: AsyncSession, p: Portfolio, days: int) -> tuple[pd.Series | None, str | None]:
    df = None
    name = None
    try:
        inst = await instrument_service.resolve_symbol(db, p.benchmark_symbol)
        df = await load_daily(db, inst, days=days)
        name = inst.symbol
    except NotFoundError:
        inst = await benchmark_instrument(db)
        if inst is not None:
            df = await load_daily(db, inst, days=days)
            name = inst.symbol
    if df is None or df.empty:
        return None, None
    s = df["close"].astype(float)
    s.index = s.index.normalize()
    return s[~s.index.duplicated(keep="last")], name


async def performance(db: AsyncSession, p: Portfolio, allow_fetch: bool = True) -> dict[str, Any]:
    rows = await load_transactions(db, p.id)
    txns = to_txns(rows)
    if not txns:
        return {"available": False, "reason": "No transactions yet", "series": []}
    insts = {i.id: i for _, i in rows}
    start = min(t.trade_date for t in txns)
    closes = await _closes(db, insts, start, allow_fetch)
    if closes.empty:
        return {"available": False, "reason": "No price history is stored for these holdings", "series": []}
    series = pa.valuation_series(txns, closes)
    bench, bench_name = await _benchmark(db, p, (date.today() - start).days + 10)
    perf = pa.performance(series, bench)
    missing = [insts[i].symbol for i in insts if i not in closes.columns]
    perf.update(
        {
            "available": True,
            "benchmark": bench_name,
            "start": start.isoformat(),
            "data_status": "SIMULATED" if any(i.is_simulated for i in insts.values()) else "HISTORICAL",
            "methodology": "Daily valuation from stored closing prices (forward-filled over missing days). Returns are "
            "time-weighted, so deposits and withdrawals do not distort them; CAGR is shown only after one "
            "year of history.",
            "missing_history": missing,
        }
    )
    return perf


async def risk(db: AsyncSession, p: Portfolio, lookback_days: int = 365) -> dict[str, Any]:
    s = await summary(db, p)
    holdings = s["holdings"]
    if not holdings:
        return {"available": False, "reason": "The portfolio has no open holdings", "summary": s}
    insts = {h["instrument_id"]: await db.get(Instrument, h["instrument_id"]) for h in holdings}
    closes = await _closes(db, insts, date.today() - timedelta(days=lookback_days))
    rets = closes.pct_change(fill_method=None).iloc[1:] if not closes.empty else pd.DataFrame()
    weights = {h["instrument_id"]: h["weight"] for h in holdings}
    value = s["current_value"]
    names = {h["instrument_id"]: h["symbol"] for h in holdings}
    port_ret = ra.portfolio_returns(weights, rets) if not rets.empty else pd.Series(dtype=float)
    var = ra.value_at_risk(port_ret, value)
    bench, bench_name = await _benchmark(db, p, lookback_days + 10)
    beta_map: dict[int, float] = {}
    port_beta = None
    if bench is not None and not rets.empty:
        b_ret = bench.pct_change(fill_method=None).reindex(rets.index)
        beta_map = ra.betas(rets, b_ret)
        pb = ra.betas(pd.DataFrame({"p": port_ret}), b_ret)
        port_beta = pb.get("p")
    daily_vol = (var.get("daily_vol_pct") or 0) / 100 if var.get("available") else None
    holdings_beta = {names[k]: v for k, v in beta_map.items()}
    sym_weights = {h["symbol"]: h["weight"] for h in holdings}
    eq = (1 + port_ret).cumprod() if len(port_ret) else pd.Series(dtype=float)
    mdd = float((eq / eq.cummax() - 1).min() * 100) if len(eq) else None
    vols = {}
    for iid in rets.columns:
        r = rets[iid].dropna()
        if len(r) > 20:
            vols[names[iid]] = round(float(r.std() * np.sqrt(252) * 100), 2)
    warnings = []
    for h in holdings:
        if h["weight"] > 0.2:
            warnings.append(f"{h['symbol']} is {h['weight'] * 100:.1f}% of the portfolio (single-position limit 20%).")
    for sec in s["allocation_by_sector"]:
        if sec["weight"] > 0.4 and sec["name"] != "Unclassified":
            warnings.append(f"{sec['name']} sector is {sec['weight'] * 100:.1f}% of the portfolio (sector limit 40%).")
    corr = ra.correlation_matrix(rets, names) if not rets.empty else {"labels": [], "matrix": [], "average": None}
    if corr.get("average") is not None and corr["average"] > 0.7:
        warnings.append(f"Average pairwise correlation is {corr['average']:.2f}: holdings tend to move together.")
    return {
        "available": True,
        "as_of": datetime.now(UTC).isoformat(),
        "value": value,
        "lookback_days": lookback_days,
        "benchmark": bench_name,
        "data_status": "SIMULATED" if s["has_simulated_instruments"] else "HISTORICAL",
        "var": var,
        "volatility": {"portfolio_annual_pct": var.get("annual_vol_pct"), "by_holding": vols},
        "max_drawdown_pct": mdd,
        "max_drawdown_note": "Drawdown of today's holdings over the look-back window (not the realised history).",
        "beta": round(port_beta, 3) if port_beta is not None else None,
        "betas": {k: round(v, 3) for k, v in holdings_beta.items()},
        "correlation": corr,
        "diversification_ratio": ra.diversification_ratio(weights, rets) if not rets.empty else None,
        "concentration": s["concentration"],
        "sector_exposure": s["allocation_by_sector"],
        "position_exposure": [
            {"symbol": h["symbol"], "weight": h["weight"], "value": h["market_value"]} for h in holdings
        ],
        "scenarios": ra.scenarios(value, port_beta, daily_vol, holdings_beta, sym_weights),
        "warnings": warnings,
        "disclaimer": "Risk figures are statistical estimates from historical prices. They are not a forecast or "
        "investment advice, and actual losses can be larger.",
    }


# ---------------------------------------------------------------- CSV import / export


def export_csv(rows: list[tuple[Transaction, Instrument]]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(CSV_COLUMNS)
    for t, i in rows:
        w.writerow(
            [
                t.trade_date.isoformat(),
                i.symbol,
                i.exchange,
                t.side,
                _num(t.quantity),
                _num(t.price),
                _num(t.charges),
                t.category,
                t.notes,
            ]
        )
    return buf.getvalue()


def _num(v: Decimal | None) -> str:
    if v is None:
        return ""
    s = format(v.normalize(), "f")
    return s


def _parse_date(v: str) -> date:
    v = v.strip()
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%d-%b-%Y", "%d %b %Y"):
        try:
            return datetime.strptime(v, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"unrecognised date '{v}' (use YYYY-MM-DD)")


async def import_csv(db: AsyncSession, p: Portfolio, content: bytes, dry_run: bool = False) -> dict[str, Any]:
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise AppError("File must be UTF-8 CSV", code="invalid_csv", status_code=422) from exc
    reader = csv.DictReader(io.StringIO(text))
    headers = {h.strip().lower() for h in (reader.fieldnames or [])}
    missing = {"date", "symbol", "side", "quantity", "price"} - headers
    if missing:
        raise AppError(f"Missing columns: {', '.join(sorted(missing))}", code="invalid_csv", status_code=422)
    parsed: list[Transaction] = []
    errors: list[dict[str, Any]] = []
    cache: dict[tuple[str, str], Instrument | None] = {}
    for n, raw in enumerate(reader, start=2):
        if n - 1 > MAX_IMPORT_ROWS:
            errors.append({"row": n, "error": f"Row limit of {MAX_IMPORT_ROWS} exceeded"})
            break
        row = {(k or "").strip().lower(): (v or "").strip() for k, v in raw.items()}
        try:
            side = row["side"].upper()
            if side not in ("BUY", "SELL", "DIVIDEND"):
                raise ValueError("side must be BUY, SELL or DIVIDEND")
            qty = Decimal(row["quantity"])
            price = Decimal(row["price"])
            charges = Decimal(row.get("charges") or "0")
            if qty <= 0 or price < 0 or charges < 0:
                raise ValueError("quantity must be positive; price and charges cannot be negative")
            exch = (row.get("exchange") or "").upper() or None
            key = (row["symbol"].upper(), exch or "")
            if key not in cache:
                try:
                    cache[key] = await instrument_service.resolve_symbol(db, row["symbol"], exch)
                except NotFoundError:
                    cache[key] = None
            inst = cache[key]
            if inst is None:
                raise ValueError(f"unknown symbol {row['symbol']}")
            d = _parse_date(row["date"])
            if d > date.today():
                raise ValueError("trade date is in the future")
            parsed.append(
                Transaction(
                    portfolio_id=p.id,
                    instrument_id=inst.id,
                    side=side,
                    quantity=qty,
                    price=price,
                    charges=charges,
                    trade_date=d,
                    category=(row.get("category") or "Long-term")[:50],
                    notes=(row.get("notes") or "")[:500],
                )
            )
        except (ValueError, InvalidOperation, KeyError) as exc:
            errors.append({"row": n, "error": str(exc) or "invalid number"})
    if errors:
        return {"imported": 0, "errors": errors[:100], "error_count": len(errors), "valid_rows": len(parsed)}
    existing = to_txns(await load_transactions(db, p.id))
    new = [
        pa.Txn(t.instrument_id, t.side, float(t.quantity), float(t.price), float(t.charges), t.trade_date, t.category)
        for t in parsed
    ]
    validate_ledger(existing + new)
    if not dry_run:
        db.add_all(parsed)
        await db.commit()
    return {
        "imported": 0 if dry_run else len(parsed),
        "valid_rows": len(parsed),
        "errors": [],
        "error_count": 0,
        "dry_run": dry_run,
    }
