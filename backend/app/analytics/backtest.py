"""Daily-bar backtesting engine (long-only, cash account).

Bias controls:
* Signals use only data up to the close of bar *t*; orders fill at the open of bar *t + 1*.
* Indicators are computed per instrument from past data only (warm-up bars precede the test start).
* Stop-loss and take-profit are checked intrabar against the bar's low/high. If both could have been
  hit in the same bar, the stop is assumed to fill first (the conservative choice). A gap through the
  stop fills at the open.
* Entries are allowed only while an instrument is eligible (for index universes, a point-in-time
  member); positions in instruments whose data ends are closed at their last close.
* Costs: brokerage (percentage with an optional per-order cap), statutory charges and slippage are
  applied to every fill.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import numpy as np
import pandas as pd

from app.analytics import indicators as ind

STRATEGIES: dict[str, dict[str, Any]] = {
    "ma_crossover": {
        "label": "Moving-average crossover",
        "description": "Buy when the fast average crosses above the slow one; sell on the opposite cross.",
        "params": {"fast": 20, "slow": 50, "ma_type": "ema"},
    },
    "rsi_reversion": {
        "label": "RSI mean reversion",
        "description": "Buy when RSI rises back above the oversold level; sell when it rises above overbought.",
        "params": {"period": 14, "oversold": 30, "overbought": 70},
    },
    "macd_cross": {
        "label": "MACD signal crossover",
        "description": "Buy when MACD crosses above its signal line; sell when it crosses below.",
        "params": {"fast": 12, "slow": 26, "signal": 9, "require_above_zero": False},
    },
    "breakout": {
        "label": "Donchian breakout",
        "description": "Buy on a close above the highest high of the previous N bars; sell on a close below the "
        "lowest low of the previous M bars.",
        "params": {"entry_lookback": 20, "exit_lookback": 10, "volume_confirm": 0.0},
    },
    "momentum": {
        "label": "Cross-sectional momentum",
        "description": "Each month, hold the top N instruments by trailing return (skipping the most recent "
        "weeks), equally weighted. Needs several instruments.",
        "params": {"lookback": 126, "skip": 21, "top_n": 5, "require_positive": True},
    },
    "custom": {
        "label": "Custom rules",
        "description": "Entry and exit conditions built from indicators, e.g. RSI 14 < 30 and close > SMA 200.",
        "params": {"entry": [], "exit": [], "entry_logic": "all", "exit_logic": "any"},
    },
}

OPERATORS = (">", "<", ">=", "<=", "crosses_above", "crosses_below")
SERIES_HELP = (
    "open, high, low, close, volume, sma_N, ema_N, rsi_N, macd, macd_signal, macd_hist, bb_upper_N, "
    "bb_lower_N, atr_N, adx_N, supertrend_dir, highest_N (prior N-bar high), lowest_N (prior N-bar low), "
    "ret_N (N-bar % return), vol_avg_N (prior N-bar average volume)"
)


class BacktestError(ValueError):
    pass


@dataclass
class Costs:
    brokerage_pct: float = 0.03
    brokerage_cap: float | None = 20.0
    statutory_pct: float = 0.11
    slippage_bps: float = 5.0

    def charges(self, value: float) -> float:
        b = value * self.brokerage_pct / 100
        if self.brokerage_cap is not None:
            b = min(b, self.brokerage_cap)
        return b + value * self.statutory_pct / 100


@dataclass
class Config:
    strategy: str
    params: dict[str, Any]
    start: date
    end: date
    initial_capital: float = 1_000_000.0
    sizing_method: str = "percent_equity"  # percent_equity | fixed_amount | risk_percent
    sizing_value: float = 20.0
    max_positions: int = 5
    stop_loss_pct: float | None = None
    take_profit_pct: float | None = None
    trailing_stop_pct: float | None = None
    costs: Costs = field(default_factory=Costs)
    risk_free_rate_pct: float = 6.5


# ------------------------------------------------------------------ rule expressions


def series(df: pd.DataFrame, name: str, cache: dict[str, pd.Series]) -> pd.Series:
    name = name.strip().lower()
    if name in cache:
        return cache[name]
    close = df["close"]
    base, _, arg = name.rpartition("_")
    n = int(arg) if arg.isdigit() else None
    if name in ("open", "high", "low", "close", "volume"):
        s = df[name]
    elif name in ("macd", "macd_signal", "macd_hist"):
        m = ind.macd(close)
        s = {"macd": m["macd"], "macd_signal": m["signal"], "macd_hist": m["hist"]}[name]
    elif name == "supertrend_dir":
        s = ind.supertrend(df)["direction"]
    elif n is None or not 1 <= n <= 400:
        raise BacktestError(f"Unknown series '{name}'. Available: {SERIES_HELP}")
    elif base == "sma":
        s = ind.sma(close, n)
    elif base == "ema":
        s = ind.ema(close, n)
    elif base == "rsi":
        s = ind.rsi(close, n)
    elif base == "bb_upper":
        s = ind.bollinger(close, n)["upper"]
    elif base == "bb_lower":
        s = ind.bollinger(close, n)["lower"]
    elif base == "atr":
        s = ind.atr(df, n)
    elif base == "adx":
        s = ind.adx(df, n)["adx"]
    elif base == "highest":
        s = df["high"].rolling(n, min_periods=n).max().shift(1)
    elif base == "lowest":
        s = df["low"].rolling(n, min_periods=n).min().shift(1)
    elif base == "ret":
        s = close.pct_change(n, fill_method=None) * 100
    elif base == "vol_avg":
        s = df["volume"].rolling(n, min_periods=n).mean().shift(1)
    else:
        raise BacktestError(f"Unknown series '{name}'. Available: {SERIES_HELP}")
    cache[name] = s.astype(float)
    return cache[name]


def condition(df: pd.DataFrame, cond: dict[str, Any], cache: dict[str, pd.Series]) -> pd.Series:
    op = cond.get("op")
    if op not in OPERATORS:
        raise BacktestError(f"Operator must be one of {', '.join(OPERATORS)}")
    left = series(df, str(cond.get("left", "")), cache)
    r = cond.get("right")
    if isinstance(r, int | float) and not isinstance(r, bool):
        right = pd.Series(float(r), index=df.index)
    elif isinstance(r, str) and _is_number(r):
        right = pd.Series(float(r), index=df.index)
    elif isinstance(r, str):
        right = series(df, r, cache)
    else:
        raise BacktestError("Each condition needs a right-hand value or series")
    if op == ">":
        out = left > right
    elif op == "<":
        out = left < right
    elif op == ">=":
        out = left >= right
    elif op == "<=":
        out = left <= right
    elif op == "crosses_above":
        out = (left > right) & (left.shift(1) <= right.shift(1))
    else:
        out = (left < right) & (left.shift(1) >= right.shift(1))
    return out.fillna(False).astype(bool)


def _is_number(s: str) -> bool:
    try:
        float(s)
    except ValueError:
        return False
    return True


def _combine(df: pd.DataFrame, conds: list[dict[str, Any]], logic: str, cache: dict[str, pd.Series]) -> pd.Series:
    if not conds:
        return pd.Series(False, index=df.index)
    parts = [condition(df, c, cache) for c in conds]
    out = parts[0]
    for p in parts[1:]:
        out = (out & p) if logic == "all" else (out | p)
    return out


def _cross_up(a: pd.Series, b: pd.Series) -> pd.Series:
    return ((a > b) & (a.shift(1) <= b.shift(1))).fillna(False)


def signals(df: pd.DataFrame, strategy: str, p: dict[str, Any]) -> tuple[pd.Series, pd.Series]:
    """Entry and exit booleans evaluated at each bar's close (to be executed at the next open)."""
    close = df["close"]
    if strategy == "ma_crossover":
        fast, slow = int(p.get("fast", 20)), int(p.get("slow", 50))
        if not 1 <= fast < slow <= 400:
            raise BacktestError("Need 1 ≤ fast < slow ≤ 400")
        f = ind.ema if p.get("ma_type", "ema") == "ema" else ind.sma
        a, b = f(close, fast), f(close, slow)
        return _cross_up(a, b), _cross_up(b, a)
    if strategy == "rsi_reversion":
        period, lo, hi = int(p.get("period", 14)), float(p.get("oversold", 30)), float(p.get("overbought", 70))
        if not (2 <= period <= 100 and 0 < lo < hi < 100):
            raise BacktestError("Need 2 ≤ period ≤ 100 and 0 < oversold < overbought < 100")
        r = ind.rsi(close, period)
        return _cross_up(r, pd.Series(lo, index=df.index)), _cross_up(r, pd.Series(hi, index=df.index))
    if strategy == "macd_cross":
        m = ind.macd(close, int(p.get("fast", 12)), int(p.get("slow", 26)), int(p.get("signal", 9)))
        entry = _cross_up(m["macd"], m["signal"])
        if p.get("require_above_zero"):
            entry &= m["macd"] > 0
        return entry, _cross_up(m["signal"], m["macd"])
    if strategy == "breakout":
        n_in, n_out = int(p.get("entry_lookback", 20)), int(p.get("exit_lookback", 10))
        if not (2 <= n_in <= 400 and 2 <= n_out <= 400):
            raise BacktestError("Lookbacks must be between 2 and 400 bars")
        hh = df["high"].rolling(n_in, min_periods=n_in).max().shift(1)
        ll = df["low"].rolling(n_out, min_periods=n_out).min().shift(1)
        entry = close > hh
        vc = float(p.get("volume_confirm", 0) or 0)
        if vc > 0:
            entry &= df["volume"] >= vc * df["volume"].rolling(20, min_periods=20).mean().shift(1)
        return entry.fillna(False), (close < ll).fillna(False)
    if strategy == "custom":
        cache: dict[str, pd.Series] = {}
        entry_rules, exit_rules = p.get("entry") or [], p.get("exit") or []
        if not entry_rules:
            raise BacktestError("Custom strategy needs at least one entry condition")
        if len(entry_rules) + len(exit_rules) > 20:
            raise BacktestError("At most 20 conditions")
        return (
            _combine(df, entry_rules, p.get("entry_logic", "all"), cache),
            _combine(df, exit_rules, p.get("exit_logic", "any"), cache),
        )
    raise BacktestError(f"Unknown strategy {strategy}")


# ------------------------------------------------------------------ engine


@dataclass
class _Pos:
    symbol: str
    qty: int
    entry_price: float
    entry_date: date
    entry_cost: float
    stop: float | None
    target: float | None
    peak: float


def _metrics(
    equity: pd.Series, trades: list[dict[str, Any]], exposure: float, rf_pct: float, initial: float
) -> dict[str, Any]:
    rets = equity.pct_change().dropna()
    days = (equity.index[-1] - equity.index[0]).days if len(equity) > 1 else 0
    final = float(equity.iloc[-1])
    total = final / initial - 1
    cagr = (final / initial) ** (365.25 / days) - 1 if days > 0 and final > 0 else None
    rf_daily = (1 + rf_pct / 100) ** (1 / 252) - 1
    ex = rets - rf_daily
    sharpe = float(ex.mean() / rets.std() * np.sqrt(252)) if len(rets) > 20 and rets.std() > 0 else None
    downside = np.sqrt(np.mean(np.minimum(ex, 0) ** 2)) if len(rets) > 20 else 0
    sortino = float(ex.mean() / downside * np.sqrt(252)) if downside > 0 else None
    dd = equity / equity.cummax() - 1
    mdd = float(dd.min())
    pnl = np.array([t["net_pnl"] for t in trades], dtype=float)
    wins, losses = pnl[pnl > 0], pnl[pnl <= 0]
    return {
        "initial_capital": initial,
        "final_equity": round(final, 2),
        "total_return_pct": round(total * 100, 3),
        "cagr_pct": round(cagr * 100, 3) if cagr is not None else None,
        "annual_volatility_pct": round(float(rets.std() * np.sqrt(252) * 100), 3) if len(rets) > 20 else None,
        "sharpe": round(sharpe, 3) if sharpe is not None else None,
        "sortino": round(sortino, 3) if sortino is not None else None,
        "max_drawdown_pct": round(mdd * 100, 3),
        "calmar": round(cagr / -mdd, 3) if cagr is not None and mdd < 0 else None,
        "trades": len(trades),
        "win_rate": round(len(wins) / len(pnl), 4) if len(pnl) else None,
        "profit_factor": round(float(wins.sum() / -losses.sum()), 3) if len(losses) and losses.sum() < 0 else None,
        "avg_trade_pct": round(float(np.mean([t["return_pct"] for t in trades])), 3) if trades else None,
        "avg_win": round(float(wins.mean()), 2) if len(wins) else None,
        "avg_loss": round(float(losses.mean()), 2) if len(losses) else None,
        "avg_holding_days": round(float(np.mean([t["holding_days"] for t in trades])), 2) if trades else None,
        "exposure_pct": round(exposure * 100, 2),
        "total_costs": round(sum(t["costs"] for t in trades), 2),
        "risk_free_rate_pct": rf_pct,
    }


def run(
    cfg: Config,
    frames: dict[str, pd.DataFrame],
    eligible: Callable[[str, date], bool] | None = None,
    benchmark: pd.Series | None = None,
) -> dict[str, Any]:
    """frames: symbol -> daily OHLCV indexed by date (including warm-up bars before cfg.start)."""
    if cfg.strategy not in STRATEGIES:
        raise BacktestError(f"Unknown strategy {cfg.strategy}")
    if cfg.start >= cfg.end:
        raise BacktestError("Start date must be before end date")
    if cfg.sizing_method == "risk_percent" and not cfg.stop_loss_pct:
        raise BacktestError("Risk-based sizing needs a stop-loss %")
    eligible = eligible or (lambda s, d: True)
    data: dict[str, pd.DataFrame] = {}
    for sym, df in frames.items():
        if df.empty:
            continue
        df = df.copy()
        df.index = pd.DatetimeIndex([pd.Timestamp(d.date() if hasattr(d, "date") else d) for d in df.index])
        df = df[~df.index.duplicated(keep="last")].sort_index()
        df = df[df.index.date <= cfg.end]
        if cfg.strategy != "momentum":
            e, x = signals(df, cfg.strategy, cfg.params)
            df["_entry"], df["_exit"] = e.to_numpy(), x.to_numpy()
            df["_rank"] = df["close"].pct_change(20, fill_method=None).fillna(0.0)
        data[sym] = df
    if not data:
        raise BacktestError("No price data for the selected instruments")
    calendar = sorted({d for df in data.values() for d in df.index if cfg.start <= d.date() <= cfg.end})
    if len(calendar) < 2:
        raise BacktestError("Not enough trading days in the selected period")

    mom = cfg.params if cfg.strategy == "momentum" else {}
    if cfg.strategy == "momentum":
        lookback, skip, top_n = int(mom.get("lookback", 126)), int(mom.get("skip", 21)), int(mom.get("top_n", 5))
        if not (20 <= lookback <= 504 and 0 <= skip < lookback and 1 <= top_n <= 50):
            raise BacktestError("Momentum needs 20 ≤ lookback ≤ 504, 0 ≤ skip < lookback, 1 ≤ top N ≤ 50")
        for df in data.values():
            df["_mom"] = df["close"].shift(skip) / df["close"].shift(lookback) - 1
        max_pos = top_n
    else:
        max_pos = max(1, int(cfg.max_positions))

    costs = cfg.costs
    slip = costs.slippage_bps / 10_000
    cash = float(cfg.initial_capital)
    positions: dict[str, _Pos] = {}
    trades: list[dict[str, Any]] = []
    equity_rows: list[tuple[pd.Timestamp, float, int]] = []
    pending_exit: set[str] = set()
    pending_entry: list[str] = []
    momentum_target: list[str] | None = None
    last_month = None
    last_close: dict[str, float] = {}
    skipped_entries = 0

    def bar(sym: str, d: pd.Timestamp) -> pd.Series | None:
        df = data[sym]
        return df.loc[d] if d in df.index else None

    def equity_now() -> float:
        return cash + sum(p.qty * last_close.get(s, p.entry_price) for s, p in positions.items())

    def close_pos(sym: str, d: pd.Timestamp, raw_price: float, reason: str, apply_slip: bool = True) -> None:
        nonlocal cash
        p = positions.pop(sym)
        price = raw_price * (1 - slip) if apply_slip else raw_price
        value = p.qty * price
        ch = costs.charges(value)
        cash += value - ch
        net = value - ch - (p.qty * p.entry_price + p.entry_cost)
        trades.append(
            {
                "symbol": sym,
                "entry_date": p.entry_date.isoformat(),
                "exit_date": d.date().isoformat(),
                "entry_price": round(p.entry_price, 4),
                "exit_price": round(price, 4),
                "quantity": p.qty,
                "net_pnl": round(net, 2),
                "return_pct": round(net / (p.qty * p.entry_price + p.entry_cost) * 100, 3),
                "holding_days": (d.date() - p.entry_date).days,
                "costs": round(p.entry_cost + ch, 2),
                "exit_reason": reason,
            }
        )

    def open_pos(sym: str, d: pd.Timestamp, open_px: float, budget: float | None = None) -> bool:
        nonlocal cash, skipped_entries
        price = open_px * (1 + slip)
        eq = equity_now()
        if budget is None:
            if cfg.sizing_method == "percent_equity":
                budget = eq * cfg.sizing_value / 100
            elif cfg.sizing_method == "fixed_amount":
                budget = cfg.sizing_value
            else:  # risk_percent
                risk_per_share = price * cfg.stop_loss_pct / 100
                budget = math.floor(eq * cfg.sizing_value / 100 / risk_per_share) * price
        budget = min(budget, cash)
        qty = int(budget // (price * (1 + (costs.brokerage_pct + costs.statutory_pct) / 100)))
        if qty < 1:
            skipped_entries += 1
            return False
        value = qty * price
        ch = costs.charges(value)
        if value + ch > cash:
            qty -= 1
            if qty < 1:
                skipped_entries += 1
                return False
            value, ch = qty * price, costs.charges(qty * price)
        cash -= value + ch
        stop = price * (1 - cfg.stop_loss_pct / 100) if cfg.stop_loss_pct else None
        target = price * (1 + cfg.take_profit_pct / 100) if cfg.take_profit_pct else None
        positions[sym] = _Pos(sym, qty, price, d.date(), ch, stop, target, price)
        return True

    for idx, d in enumerate(calendar):
        # 1) orders from the previous close fill at this open
        for sym in sorted(pending_exit):
            b = bar(sym, d)
            if sym in positions and b is not None:
                close_pos(sym, d, float(b["open"]), "signal")
        pending_exit.clear()

        if cfg.strategy == "momentum" and momentum_target is not None:
            for sym in [s for s in positions if s not in momentum_target]:
                b = bar(sym, d)
                if b is not None:
                    close_pos(sym, d, float(b["open"]), "rebalance")
            new = [s for s in momentum_target if s not in positions and bar(s, d) is not None]
            if new:
                slots = max_pos - len(positions)
                per = equity_now() / max_pos
                for sym in new[:slots]:
                    open_pos(sym, d, float(bar(sym, d)["open"]), budget=per)
            momentum_target = None
        else:
            for sym in pending_entry:
                if len(positions) >= max_pos:
                    skipped_entries += 1
                    continue
                b = bar(sym, d)
                if sym not in positions and b is not None and eligible(sym, d.date()):
                    open_pos(sym, d, float(b["open"]))
        pending_entry = []

        # 2) intrabar stop / target / trailing stop
        for sym in list(positions):
            b = bar(sym, d)
            if b is None:
                continue
            p = positions[sym]
            o, h, lo = float(b["open"]), float(b["high"]), float(b["low"])
            stop = p.stop
            if cfg.trailing_stop_pct:
                trail = p.peak * (1 - cfg.trailing_stop_pct / 100)
                stop = max(stop, trail) if stop is not None else trail
            if stop is not None and lo <= stop:
                close_pos(sym, d, min(o, stop), "trailing stop" if stop != p.stop else "stop-loss")
                continue
            if p.target is not None and h >= p.target:
                close_pos(sym, d, max(o, p.target), "take-profit")
                continue
            p.peak = max(p.peak, h)

        # 3) mark to market
        for sym, df in data.items():
            if d in df.index:
                last_close[sym] = float(df.at[d, "close"])
        # instruments whose data has ended (e.g. delisted) are closed at their last close
        for sym in list(positions):
            df = data[sym]
            if df.index[-1] < d:
                close_pos(sym, d, last_close[sym], "data ended (possible delisting)", apply_slip=True)
        equity_rows.append((d, equity_now(), len(positions)))

        # 4) signals at this close for the next open
        if cfg.strategy == "momentum":
            month = (d.year, d.month)
            nxt = calendar[idx + 1] if idx + 1 < len(calendar) else None
            if nxt is not None and (nxt.year, nxt.month) != month and month != last_month:
                last_month = month
                ranked = []
                for sym, df in data.items():
                    if d in df.index and eligible(sym, nxt.date()):
                        m = df.at[d, "_mom"]
                        if pd.notna(m) and (not mom.get("require_positive", True) or m > 0):
                            ranked.append((float(m), sym))
                momentum_target = [s for _, s in sorted(ranked, reverse=True)[:max_pos]]
        else:
            for sym, df in data.items():
                if d not in df.index:
                    continue
                if sym in positions and bool(df.at[d, "_exit"]):
                    pending_exit.add(sym)
                elif sym not in positions and bool(df.at[d, "_entry"]):
                    pending_entry.append(sym)
            # deterministic priority when signals exceed free slots: strongest 20-day momentum first
            pending_entry.sort(key=lambda s: (-float(data[s].at[d, "_rank"]), s))

    last = calendar[-1]
    for sym in list(positions):
        close_pos(sym, last, last_close[sym], "end of test")
    equity = pd.Series([e for _, e, _ in equity_rows], index=[d for d, _, _ in equity_rows])
    if equity_rows:
        equity.iloc[-1] = cash  # after final liquidation costs
    exposure = float(np.mean([n > 0 for _, _, n in equity_rows])) if equity_rows else 0.0
    metrics = _metrics(equity, trades, exposure, cfg.risk_free_rate_pct, cfg.initial_capital)
    dd = equity / equity.cummax() - 1
    bench_curve = None
    if benchmark is not None and not benchmark.empty:
        b = benchmark.copy()
        b.index = pd.DatetimeIndex([pd.Timestamp(x.date() if hasattr(x, "date") else x) for x in b.index])
        b = b[~b.index.duplicated(keep="last")].reindex(equity.index).ffill()
        if b.notna().sum() > 1:
            first = b.first_valid_index()
            bench_curve = b / b.loc[first] * cfg.initial_capital
            days = (equity.index[-1] - first).days
            br = float(b.iloc[-1] / b.loc[first] - 1)
            metrics["benchmark_return_pct"] = round(br * 100, 3)
            metrics["benchmark_cagr_pct"] = round(((1 + br) ** (365.25 / days) - 1) * 100, 3) if days > 0 else None
    step = max(1, len(equity) // 1500)
    curve = [
        {
            "date": d.date().isoformat(),
            "equity": round(float(v), 2),
            "drawdown_pct": round(float(dd.loc[d]) * 100, 3),
            "benchmark": round(float(bench_curve.loc[d]), 2)
            if bench_curve is not None and pd.notna(bench_curve.loc[d])
            else None,
        }
        for i, (d, v) in enumerate(equity.items())
        if i % step == 0 or i == len(equity) - 1
    ]
    monthly = (equity.resample("ME").last().pct_change().dropna() * 100).round(3)
    return {
        "metrics": metrics,
        "equity_curve": curve,
        "trades": trades[-2000:],
        "monthly_returns": [{"month": d.strftime("%Y-%m"), "return_pct": float(v)} for d, v in monthly.items()],
        "skipped_entries": skipped_entries,
        "period": {"start": calendar[0].date().isoformat(), "end": last.date().isoformat(), "bars": len(calendar)},
    }
