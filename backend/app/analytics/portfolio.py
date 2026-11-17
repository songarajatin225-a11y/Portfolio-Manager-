"""Portfolio accounting and performance analytics (pure functions).

* Cost basis uses FIFO lot matching; charges are added to the cost of buys and deducted from the
  proceeds of sells. Realised P&L = sell proceeds − charges − FIFO cost of the lots sold.
* XIRR is the money-weighted annual return of all external cash flows plus the current value.
* Time-weighted return (TWR) chains daily returns after removing the effect of deposits and
  withdrawals (buys/sells); annualised TWR is reported as the portfolio CAGR.
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import numpy as np
import pandas as pd
from scipy.optimize import brentq


@dataclass
class Txn:
    instrument_id: int
    side: str  # BUY | SELL | DIVIDEND
    quantity: float
    price: float
    charges: float
    trade_date: date
    category: str = ""


@dataclass
class Lot:
    quantity: float
    unit_cost: float  # includes buy charges
    trade_date: date


@dataclass
class Position:
    instrument_id: int
    lots: deque = field(default_factory=deque)
    realized_pnl: float = 0.0
    dividends: float = 0.0
    category: str = ""

    @property
    def quantity(self) -> float:
        return float(sum(lot.quantity for lot in self.lots))

    @property
    def cost(self) -> float:
        return float(sum(lot.quantity * lot.unit_cost for lot in self.lots))

    @property
    def avg_cost(self) -> float:
        q = self.quantity
        return self.cost / q if q > 0 else 0.0


class LedgerError(ValueError):
    pass


def build_positions(txns: list[Txn]) -> dict[int, Position]:
    positions: dict[int, Position] = {}
    for t in sorted(txns, key=lambda x: (x.trade_date, 0 if x.side == "BUY" else 1)):
        pos = positions.setdefault(t.instrument_id, Position(t.instrument_id, category=t.category))
        if t.side == "BUY":
            if t.quantity <= 0:
                raise LedgerError("Buy quantity must be positive")
            pos.lots.append(Lot(t.quantity, (t.quantity * t.price + t.charges) / t.quantity, t.trade_date))
            pos.category = t.category or pos.category
        elif t.side == "SELL":
            remaining = t.quantity
            if remaining > pos.quantity + 1e-9:
                raise LedgerError(f"Sell of {t.quantity} on {t.trade_date} exceeds holding of {pos.quantity}")
            cost = 0.0
            while remaining > 1e-12:
                lot = pos.lots[0]
                take = min(lot.quantity, remaining)
                cost += take * lot.unit_cost
                lot.quantity -= take
                remaining -= take
                if lot.quantity <= 1e-12:
                    pos.lots.popleft()
            pos.realized_pnl += t.quantity * t.price - t.charges - cost
        elif t.side == "DIVIDEND":
            pos.dividends += t.quantity * t.price - t.charges
        else:
            raise LedgerError(f"Unknown side {t.side}")
    return positions


def cash_flows(txns: list[Txn]) -> list[tuple[date, float]]:
    """Investor cash flows: buys negative, sells and dividends positive."""
    flows = []
    for t in txns:
        amount = t.quantity * t.price
        if t.side == "BUY":
            flows.append((t.trade_date, -(amount + t.charges)))
        else:
            flows.append((t.trade_date, amount - t.charges))
    return flows


def xirr(flows: list[tuple[date, float]]) -> float | None:
    """Annualised money-weighted return. None when undefined (no sign change or too little data)."""
    flows = [(d, a) for d, a in flows if a != 0]
    if len(flows) < 2 or not (any(a < 0 for _, a in flows) and any(a > 0 for _, a in flows)):
        return None
    d0 = min(d for d, _ in flows)
    years = np.array([(d - d0).days / 365.0 for d, _ in flows])
    amounts = np.array([a for _, a in flows])
    if years.max() < 1 / 365:
        return None

    def npv(rate: float) -> float:
        return float(np.sum(amounts / (1 + rate) ** years))

    try:
        return float(brentq(npv, -0.9999, 100.0, maxiter=500))
    except ValueError:
        return None


@dataclass
class Holding:
    instrument_id: int
    symbol: str
    name: str
    sector: str
    category: str
    quantity: float
    avg_cost: float
    invested: float
    price: float | None
    market_value: float
    unrealized_pnl: float
    unrealized_pct: float | None
    realized_pnl: float
    dividends: float
    weight: float = 0.0
    price_source: str | None = None
    price_status: str | None = None
    price_as_of: str | None = None


def summarize(
    positions: dict[int, Position],
    prices: dict[int, dict[str, Any]],
    meta: dict[int, dict[str, Any]],
    flows: list[tuple[date, float]],
    today: date,
) -> dict[str, Any]:
    holdings: list[Holding] = []
    realized = sum(p.realized_pnl for p in positions.values())
    dividends = sum(p.dividends for p in positions.values())
    for iid, pos in positions.items():
        if pos.quantity <= 1e-9:
            continue
        info = meta.get(iid, {})
        q = prices.get(iid) or {}
        price = q.get("price")
        mv = pos.quantity * price if price else pos.cost
        holdings.append(
            Holding(
                instrument_id=iid,
                symbol=info.get("symbol", str(iid)),
                name=info.get("name", ""),
                sector=info.get("sector") or "Unclassified",
                category=pos.category or "Uncategorised",
                quantity=pos.quantity,
                avg_cost=pos.avg_cost,
                invested=pos.cost,
                price=price,
                market_value=mv,
                unrealized_pnl=mv - pos.cost if price else 0.0,
                unrealized_pct=((mv / pos.cost - 1) * 100) if price and pos.cost else None,
                realized_pnl=pos.realized_pnl,
                dividends=pos.dividends,
                price_source=q.get("source"),
                price_status=q.get("data_status"),
                price_as_of=q.get("as_of"),
            )
        )
    total_value = sum(h.market_value for h in holdings)
    for h in holdings:
        h.weight = h.market_value / total_value if total_value else 0.0
    invested_now = sum(h.invested for h in holdings)
    unrealized = sum(h.unrealized_pnl for h in holdings)
    gross_invested = -sum(a for _, a in flows if a < 0)
    total_pnl = unrealized + realized + dividends
    xirr_value = xirr([*flows, (today, total_value)]) if total_value > 0 else xirr(flows)

    def group(key: str) -> list[dict[str, Any]]:
        g: dict[str, float] = defaultdict(float)
        for h in holdings:
            g[getattr(h, key)] += h.market_value
        return sorted(
            [{"name": k, "value": v, "weight": v / total_value if total_value else 0} for k, v in g.items()],
            key=lambda x: -x["value"],
        )

    contributions = sorted(
        [
            {
                "symbol": h.symbol,
                "pnl": h.unrealized_pnl + h.realized_pnl + h.dividends,
                "share": ((h.unrealized_pnl + h.realized_pnl + h.dividends) / total_pnl) if total_pnl else None,
            }
            for h in holdings
        ],
        key=lambda x: -x["pnl"],
    )
    missing_prices = [h.symbol for h in holdings if h.price is None]
    return {
        "current_value": total_value,
        "invested_in_holdings": invested_now,
        "total_invested": gross_invested,
        "unrealized_pnl": unrealized,
        "realized_pnl": realized,
        "dividends": dividends,
        "total_pnl": total_pnl,
        "absolute_return_pct": (total_pnl / gross_invested * 100) if gross_invested else None,
        "xirr_pct": xirr_value * 100 if xirr_value is not None else None,
        "holdings": [h.__dict__ for h in sorted(holdings, key=lambda x: -x.market_value)],
        "allocation_by_asset": [{"name": h.symbol, "value": h.market_value, "weight": h.weight} for h in holdings],
        "allocation_by_sector": group("sector"),
        "allocation_by_category": group("category"),
        "contribution": contributions,
        "concentration": concentration([h.weight for h in holdings], [h.symbol for h in holdings]),
        "missing_prices": missing_prices,
        "note": ("Holdings without a current price are valued at cost: " + ", ".join(missing_prices))
        if missing_prices
        else None,
    }


def concentration(weights: list[float], names: list[str]) -> dict[str, Any]:
    w = np.array(weights, dtype=float)
    if not len(w) or w.sum() <= 0:
        return {"hhi": None, "effective_holdings": None, "top5_weight": None, "largest": None}
    w = w / w.sum()
    hhi = float(np.sum(w**2))
    order = np.argsort(-w)
    return {
        "hhi": round(hhi, 4),
        "effective_holdings": round(1 / hhi, 2),
        "top5_weight": round(float(w[order[:5]].sum()), 4),
        "largest": {"name": names[int(order[0])], "weight": round(float(w[order[0]]), 4)},
        "holdings": len(w),
    }


def valuation_series(txns: list[Txn], closes: pd.DataFrame) -> pd.DataFrame:
    """Daily market value and external flows from transactions and a (date x instrument) close-price frame.

    Prices are forward-filled across days without a bar (holidays, missing data), never back-filled.
    """
    if closes.empty or not txns:
        return pd.DataFrame(columns=["value", "flow"])
    start = min(t.trade_date for t in txns)
    px = closes.sort_index()
    px = px[px.index.date >= start].ffill()
    qty = pd.DataFrame(0.0, index=px.index, columns=px.columns)
    flow = pd.Series(0.0, index=px.index)
    dates = px.index.date
    for t in sorted(txns, key=lambda x: x.trade_date):
        mask = dates >= t.trade_date
        if t.instrument_id not in qty.columns or not mask.any():
            continue
        if t.side == "DIVIDEND":  # income paid out to the investor counts as a withdrawal (it is part of return)
            flow.iloc[np.argmax(mask)] -= t.quantity * t.price - t.charges
            continue
        sign = 1 if t.side == "BUY" else -1
        qty.loc[mask, t.instrument_id] += sign * t.quantity
        first = np.argmax(mask)
        # net external flow: cash paid in for a buy (incl. charges), net proceeds taken out for a sell
        flow.iloc[first] += t.quantity * t.price + t.charges if t.side == "BUY" else -(t.quantity * t.price - t.charges)
    value = (qty * px).sum(axis=1, min_count=1).fillna(0.0)
    return pd.DataFrame({"value": value, "flow": flow})


def performance(series: pd.DataFrame, benchmark: pd.Series | None = None) -> dict[str, Any]:
    """Time-weighted daily returns: r_t = (V_t − F_t) / V_{t−1} − 1, where F_t is the net external flow."""
    if len(series) < 2:
        return {"twr_pct": None, "cagr_pct": None, "volatility_pct": None, "max_drawdown_pct": None, "series": []}
    v, f = series["value"].to_numpy(), series["flow"].to_numpy()
    rets = np.full(len(v), np.nan)
    for i in range(1, len(v)):
        if v[i - 1] > 0:
            rets[i] = (v[i] - f[i]) / v[i - 1] - 1
    r = pd.Series(rets, index=series.index).fillna(0.0)
    index = (1 + r).cumprod() * 100
    days = (series.index[-1] - series.index[0]).days
    twr = float(index.iloc[-1] / 100 - 1)
    cagr = (1 + twr) ** (365 / days) - 1 if days >= 365 else None
    active = r[series["value"].shift(1).fillna(0).to_numpy() > 0]
    vol = float(active.std() * np.sqrt(252)) if len(active) > 5 else None
    dd = index / index.cummax() - 1
    out: dict[str, Any] = {
        "twr_pct": twr * 100,
        "cagr_pct": cagr * 100 if cagr is not None else None,
        "volatility_pct": vol * 100 if vol is not None else None,
        "max_drawdown_pct": float(dd.min() * 100),
        "current_drawdown_pct": float(dd.iloc[-1] * 100),
    }
    bench_index = None
    if benchmark is not None and not benchmark.empty:
        b = benchmark.reindex(series.index).ffill().dropna()
        if len(b) > 1:
            bench_index = b / b.iloc[0] * 100
            out["benchmark_return_pct"] = float(bench_index.iloc[-1] - 100)
            br = bench_index.pct_change().reindex(r.index).fillna(0)
            out["excess_return_pct"] = out["twr_pct"] - out["benchmark_return_pct"]
            if br.std() > 0 and len(active) > 20:
                out["beta"] = float(np.cov(r, br)[0, 1] / br.var())
    out["series"] = [
        {
            "date": d.date().isoformat() if hasattr(d, "date") else str(d),
            "value": round(float(val), 2),
            "index": round(float(index.loc[d]), 4),
            "benchmark": round(float(bench_index.loc[d]), 4)
            if bench_index is not None and d in bench_index.index
            else None,
            "drawdown": round(float(dd.loc[d] * 100), 3),
        }
        for d, val in series["value"].items()
    ]
    return out


def rebalance(
    holdings: list[dict[str, Any]], targets: dict[str, float], threshold_pp: float = 5.0, max_weight: float = 0.25
) -> dict[str, Any]:
    """Trades that move current weights to targets (symbol -> weight). Equal weight when no targets are set."""
    total = sum(h["market_value"] for h in holdings)
    if total <= 0:
        return {"basis": "none", "suggestions": [], "warnings": []}
    basis = "custom targets"
    if not targets:
        targets = {h["symbol"]: 1 / len(holdings) for h in holdings}
        basis = "equal weight"
    s = sum(targets.values())
    targets = {k: v / s for k, v in targets.items()} if s > 0 else targets
    out, warnings = [], []
    for h in holdings:
        cur = h["market_value"] / total
        tgt = targets.get(h["symbol"], 0.0)
        drift_pp = (cur - tgt) * 100
        if cur > max_weight:
            warnings.append(
                f"{h['symbol']} is {cur * 100:.1f}% of the portfolio (above the {max_weight * 100:.0f}% cap)"
            )
        if abs(drift_pp) >= threshold_pp and h.get("price"):
            value = (tgt - cur) * total
            out.append(
                {
                    "symbol": h["symbol"],
                    "current_weight": round(cur, 4),
                    "target_weight": round(tgt, 4),
                    "drift_pp": round(drift_pp, 2),
                    "action": "BUY" if value > 0 else "SELL",
                    "value": round(abs(value), 2),
                    "quantity": int(abs(value) // h["price"]),
                }
            )
    return {
        "basis": basis,
        "threshold_pp": threshold_pp,
        "suggestions": sorted(out, key=lambda x: -abs(x["drift_pp"])),
        "warnings": warnings,
        "note": "Suggestions ignore taxes, exit loads and transaction costs; review before acting.",
    }
