"""Portfolio risk measurement (pure functions). Historical estimates describe the past; they do not
guarantee future losses will stay within them."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
from scipy.stats import norm

VAR_METHODOLOGY = (
    "Value at Risk is the loss not expected to be exceeded on {conf:.0%} of days, estimated from the last {n} "
    "daily portfolio returns using today's holdings. Historical VaR uses the empirical return quantile; "
    "parametric VaR assumes normally distributed returns (mean and standard deviation of the same window). "
    "Expected shortfall (CVaR) is the average loss on days worse than the VaR. Multi-day figures scale the "
    "1-day value by the square root of time, which assumes independent returns. These are statistical "
    "estimates from past data: real losses can exceed them, especially in market stress."
)


def portfolio_returns(weights: dict[int, float], returns: pd.DataFrame) -> pd.Series:
    cols = [c for c in returns.columns if c in weights]
    if not cols:
        return pd.Series(dtype=float)
    w = np.array([weights[c] for c in cols])
    w = w / w.sum()
    return returns[cols].dropna().dot(w)


def value_at_risk(
    port_ret: pd.Series,
    value: float,
    confidences: tuple[float, ...] = (0.95, 0.99),
    horizons: tuple[int, ...] = (1, 10),
) -> dict[str, Any]:
    r = port_ret.dropna().to_numpy()
    if len(r) < 30:
        return {"available": False, "reason": f"Needs at least 30 days of return history (have {len(r)})"}
    mu, sigma = float(r.mean()), float(r.std(ddof=1))
    rows = []
    for c in confidences:
        q = float(np.quantile(r, 1 - c))
        hist = -q
        tail = r[r <= q]
        cvar = -float(tail.mean()) if len(tail) else hist
        param = -(mu + sigma * norm.ppf(1 - c))
        for h in horizons:
            scale = np.sqrt(h)
            rows.append(
                {
                    "confidence": c,
                    "horizon_days": h,
                    "historical_pct": round(hist * scale * 100, 3),
                    "historical_value": round(hist * scale * value, 2),
                    "parametric_pct": round(param * scale * 100, 3),
                    "parametric_value": round(param * scale * value, 2),
                    "expected_shortfall_pct": round(cvar * scale * 100, 3),
                    "expected_shortfall_value": round(cvar * scale * value, 2),
                }
            )
    return {
        "available": True,
        "observations": len(r),
        "daily_mean_pct": round(mu * 100, 4),
        "daily_vol_pct": round(sigma * 100, 4),
        "annual_vol_pct": round(sigma * np.sqrt(252) * 100, 2),
        "table": rows,
        "methodology": VAR_METHODOLOGY.format(conf=confidences[0], n=len(r)),
    }


def correlation_matrix(returns: pd.DataFrame, names: dict[int, str]) -> dict[str, Any]:
    clean = returns.dropna(how="all").dropna(axis=1, thresh=30)
    if clean.shape[1] < 2:
        return {"labels": [], "matrix": [], "average": None}
    corr = clean.corr(min_periods=30)
    labels = [names.get(c, str(c)) for c in corr.columns]
    m = corr.to_numpy()
    iu = np.triu_indices_from(m, k=1)
    avg = float(np.nanmean(m[iu])) if len(iu[0]) else None
    return {
        "labels": labels,
        "matrix": [[None if np.isnan(x) else round(float(x), 3) for x in row] for row in m],
        "average": round(avg, 3) if avg is not None else None,
    }


def betas(returns: pd.DataFrame, bench_ret: pd.Series) -> dict[int, float]:
    out = {}
    b = bench_ret.dropna()
    for c in returns.columns:
        pair = pd.concat([returns[c], b], axis=1).dropna()
        if len(pair) >= 30 and pair.iloc[:, 1].var() > 0:
            out[c] = float(np.cov(pair.iloc[:, 0], pair.iloc[:, 1])[0, 1] / pair.iloc[:, 1].var())
    return out


def diversification_ratio(weights: dict[int, float], returns: pd.DataFrame) -> float | None:
    cols = [c for c in returns.columns if c in weights]
    if len(cols) < 2:
        return None
    clean = returns[cols].dropna()
    if len(clean) < 30:
        return None
    w = np.array([weights[c] for c in cols])
    w = w / w.sum()
    vols = clean.std().to_numpy()
    port_vol = float(np.sqrt(w @ clean.cov().to_numpy() @ w))
    return round(float(w @ vols) / port_vol, 3) if port_vol > 0 else None


def scenarios(
    value: float,
    portfolio_beta: float | None,
    daily_vol: float | None,
    holdings_beta: dict[str, float],
    weights: dict[str, float],
) -> dict[str, Any]:
    """Market-decline scenarios via beta, and volatility-shock scenarios scaling 1-day 99% parametric VaR."""
    market = []
    for shock in (-0.05, -0.10, -0.20, -0.30):
        if portfolio_beta is None:
            market.append({"market_move_pct": shock * 100, "estimated_move_pct": None, "estimated_value_change": None})
            continue
        move = portfolio_beta * shock
        worst = sorted(
            ((s, holdings_beta.get(s, 1.0) * shock * weights.get(s, 0) * value) for s in weights), key=lambda x: x[1]
        )[:3]
        market.append(
            {
                "market_move_pct": shock * 100,
                "estimated_move_pct": round(move * 100, 2),
                "estimated_value_change": round(move * value, 2),
                "largest_contributors": [{"symbol": s, "value_change": round(v, 2)} for s, v in worst],
            }
        )
    vol = []
    if daily_vol:
        for mult in (1.5, 2.0, 3.0):
            var99 = norm.ppf(0.99) * daily_vol * mult
            vol.append(
                {
                    "volatility_multiplier": mult,
                    "one_day_var99_pct": round(var99 * 100, 3),
                    "one_day_var99_value": round(var99 * value, 2),
                }
            )
    return {
        "market_decline": market,
        "volatility_shock": vol,
        "assumptions": "Market scenarios apply each holding's historical beta to the benchmark move (beta 1.0 "
        "where unknown). Volatility shocks scale current daily volatility. Real moves in stress "
        "can differ greatly from these linear estimates.",
    }


def position_size(
    capital: float, risk_pct: float, entry: float, stop: float, lot_size: int = 1, max_position_pct: float | None = None
) -> dict[str, Any]:
    """Fixed-fractional sizing: risk a fixed share of capital between entry and stop."""
    if capital <= 0 or entry <= 0 or stop <= 0 or risk_pct <= 0:
        raise ValueError("capital, entry, stop and risk % must be positive")
    per_share = abs(entry - stop)
    if per_share == 0:
        raise ValueError("entry and stop cannot be equal")
    risk_amount = capital * risk_pct / 100
    qty = int(risk_amount // per_share)
    qty -= qty % max(lot_size, 1)
    capped = False
    if max_position_pct is not None and qty * entry > capital * max_position_pct / 100:
        qty = int(capital * max_position_pct / 100 // entry)
        qty -= qty % max(lot_size, 1)
        capped = True
    return {
        "quantity": qty,
        "position_value": round(qty * entry, 2),
        "risk_amount": round(qty * per_share, 2),
        "risk_per_share": round(per_share, 4),
        "position_pct_of_capital": round(qty * entry / capital * 100, 2),
        "direction": "LONG" if stop < entry else "SHORT",
        "capped_by_max_position": capped,
    }


def risk_reward(entry: float, stop: float | None, target: float | None) -> float | None:
    if stop is None or target is None or entry == stop:
        return None
    return round(abs(target - entry) / abs(entry - stop), 2)
