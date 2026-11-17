"""Technical indicators on OHLCV data.

All functions take pandas Series/DataFrames indexed by time (ascending) and return values aligned
to the input index. Every value at bar *t* uses only bars <= *t* (no look-ahead). Warm-up periods
are NaN rather than partially computed. Wilder smoothing is used where the original definitions
call for it (RSI, ATR, ADX).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


def sma(close: pd.Series, period: int) -> pd.Series:
    return close.rolling(period, min_periods=period).mean()


def ema(close: pd.Series, period: int) -> pd.Series:
    """Exponential moving average seeded with the SMA of the first ``period`` values."""
    values = close.to_numpy(dtype=float)
    out = np.full(len(values), np.nan)
    if len(values) < period:
        return pd.Series(out, index=close.index)
    alpha = 2.0 / (period + 1)
    out[period - 1] = values[:period].mean()
    for i in range(period, len(values)):
        out[i] = alpha * values[i] + (1 - alpha) * out[i - 1]
    return pd.Series(out, index=close.index)


def wilder(values: pd.Series, period: int) -> pd.Series:
    """Wilder's smoothing (RMA): seed with the simple mean, then ((n-1)*prev + x) / n."""
    v = values.to_numpy(dtype=float)
    out = np.full(len(v), np.nan)
    valid = np.where(~np.isnan(v))[0]
    if len(valid) < period:
        return pd.Series(out, index=values.index)
    start = valid[0]
    seed_end = start + period
    if seed_end > len(v):
        return pd.Series(out, index=values.index)
    out[seed_end - 1] = np.nanmean(v[start:seed_end])
    for i in range(seed_end, len(v)):
        out[i] = (out[i - 1] * (period - 1) + v[i]) / period
    return pd.Series(out, index=values.index)


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = wilder(gain.iloc[1:], period).reindex(close.index)
    avg_loss = wilder(loss.iloc[1:], period).reindex(close.index)
    rs = avg_gain / avg_loss
    out = 100 - 100 / (1 + rs)
    out[(avg_loss == 0) & avg_gain.notna()] = 100.0
    out[(avg_loss == 0) & (avg_gain == 0)] = 50.0
    return out


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
    line = ema(close, fast) - ema(close, slow)
    sig = ema(line.dropna(), signal).reindex(close.index)
    return pd.DataFrame({"macd": line, "signal": sig, "hist": line - sig})


def bollinger(close: pd.Series, period: int = 20, mult: float = 2.0) -> pd.DataFrame:
    mid = sma(close, period)
    std = close.rolling(period, min_periods=period).std(ddof=0)
    return pd.DataFrame(
        {"middle": mid, "upper": mid + mult * std, "lower": mid - mult * std, "bandwidth": (2 * mult * std) / mid}
    )


def true_range(df: pd.DataFrame) -> pd.Series:
    prev_close = df["close"].shift(1)
    tr = pd.concat([df["high"] - df["low"], (df["high"] - prev_close).abs(), (df["low"] - prev_close).abs()], axis=1)
    out = tr.max(axis=1)
    out.iloc[0] = df["high"].iloc[0] - df["low"].iloc[0]
    return out


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    return wilder(true_range(df), period)


def adx(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    up = df["high"].diff()
    down = -df["low"].diff()
    plus_dm = pd.Series(np.where((up > down) & (up > 0), up, 0.0), index=df.index)
    minus_dm = pd.Series(np.where((down > up) & (down > 0), down, 0.0), index=df.index)
    plus_dm.iloc[0] = minus_dm.iloc[0] = np.nan
    tr = true_range(df)
    tr.iloc[0] = np.nan
    atr_w = wilder(tr, period)
    plus_di = 100 * wilder(plus_dm, period) / atr_w
    minus_di = 100 * wilder(minus_dm, period) / atr_w
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di)
    return pd.DataFrame({"adx": wilder(dx, period), "plus_di": plus_di, "minus_di": minus_di})


def supertrend(df: pd.DataFrame, period: int = 10, mult: float = 3.0) -> pd.DataFrame:
    """Supertrend line and direction (+1 uptrend, -1 downtrend)."""
    a = atr(df, period).to_numpy()
    hl2 = ((df["high"] + df["low"]) / 2).to_numpy()
    close = df["close"].to_numpy()
    n = len(df)
    upper = hl2 + mult * a
    lower = hl2 - mult * a
    fu, fl = np.full(n, np.nan), np.full(n, np.nan)
    line, direction = np.full(n, np.nan), np.full(n, np.nan)
    for i in range(n):
        if np.isnan(a[i]):
            continue
        if i == 0 or np.isnan(fu[i - 1]):
            fu[i], fl[i] = upper[i], lower[i]
            direction[i] = 1 if close[i] >= hl2[i] else -1
        else:
            fu[i] = upper[i] if upper[i] < fu[i - 1] or close[i - 1] > fu[i - 1] else fu[i - 1]
            fl[i] = lower[i] if lower[i] > fl[i - 1] or close[i - 1] < fl[i - 1] else fl[i - 1]
            if direction[i - 1] == -1 and close[i] > fu[i - 1]:
                direction[i] = 1
            elif direction[i - 1] == 1 and close[i] < fl[i - 1]:
                direction[i] = -1
            else:
                direction[i] = direction[i - 1]
        line[i] = fl[i] if direction[i] == 1 else fu[i]
    return pd.DataFrame({"supertrend": line, "direction": direction}, index=df.index)


def vwap(df: pd.DataFrame, session_key: pd.Series | None = None) -> pd.Series:
    """Volume-weighted average price, reset each session (defaults to calendar day of the index)."""
    typical = (df["high"] + df["low"] + df["close"]) / 3
    key = session_key if session_key is not None else pd.Series(df.index.date, index=df.index)
    pv = (typical * df["volume"]).groupby(key).cumsum()
    vol = df["volume"].groupby(key).cumsum()
    return pv / vol.replace(0, np.nan)


def returns(close: pd.Series, periods: int) -> float | None:
    if len(close) <= periods or close.iloc[-periods - 1] == 0:
        return None
    return float(close.iloc[-1] / close.iloc[-periods - 1] - 1) * 100


def annualized_volatility(close: pd.Series, window: int = 20, periods_per_year: int = 252) -> pd.Series:
    log_ret = np.log(close / close.shift(1))
    return log_ret.rolling(window, min_periods=window).std() * np.sqrt(periods_per_year) * 100


@dataclass
class FibLevels:
    swing_high: float
    swing_low: float
    trend: str  # "up" retracement from low->high, "down" from high->low
    levels: dict[str, float]


def fibonacci(df: pd.DataFrame, lookback: int = 120) -> FibLevels | None:
    window = df.tail(lookback)
    if len(window) < 10:
        return None
    hi_idx, lo_idx = window["high"].idxmax(), window["low"].idxmin()
    hi, lo = float(window["high"].max()), float(window["low"].min())
    if hi <= lo:
        return None
    trend = "up" if lo_idx < hi_idx else "down"
    ratios = {"0.0%": 0.0, "23.6%": 0.236, "38.2%": 0.382, "50.0%": 0.5, "61.8%": 0.618, "78.6%": 0.786, "100.0%": 1.0}
    rng = hi - lo
    if trend == "up":  # retracement levels measured down from the high
        levels = {k: round(hi - r * rng, 4) for k, r in ratios.items()}
    else:  # retracement levels measured up from the low
        levels = {k: round(lo + r * rng, 4) for k, r in ratios.items()}
    return FibLevels(swing_high=hi, swing_low=lo, trend=trend, levels=levels)


def support_resistance(
    df: pd.DataFrame, lookback: int = 250, window: int = 5, tolerance: float = 0.015, max_levels: int = 4
) -> dict[str, list[dict[str, float]]]:
    """Cluster swing highs/lows (local extrema over ±window bars) into price levels.

    A pivot at bar i is confirmed only with ``window`` bars after it, so the most recent ``window``
    bars never create levels (no look-ahead into unconfirmed swings).
    """
    data = df.tail(lookback)
    highs, lows = data["high"].to_numpy(), data["low"].to_numpy()
    pivots: list[float] = []
    for i in range(window, len(data) - window):
        seg_h, seg_l = highs[i - window : i + window + 1], lows[i - window : i + window + 1]
        if highs[i] == seg_h.max():
            pivots.append(float(highs[i]))
        if lows[i] == seg_l.min():
            pivots.append(float(lows[i]))
    clusters: list[list[float]] = []
    for p in sorted(pivots):
        if clusters and abs(p - np.mean(clusters[-1])) / np.mean(clusters[-1]) <= tolerance:
            clusters[-1].append(p)
        else:
            clusters.append([p])
    last = float(data["close"].iloc[-1]) if len(data) else 0.0
    levels = [{"price": round(float(np.mean(c)), 4), "touches": len(c)} for c in clusters if len(c) >= 2]
    support = sorted([lv for lv in levels if lv["price"] < last], key=lambda x: -x["price"])[:max_levels]
    resistance = sorted([lv for lv in levels if lv["price"] >= last], key=lambda x: x["price"])[:max_levels]
    return {"support": support, "resistance": resistance}


def volume_profile(df: pd.DataFrame, bins: int = 24) -> list[dict[str, float]]:
    """Volume traded per price bucket (using each bar's typical price)."""
    if df.empty or df["volume"].sum() <= 0:
        return []
    typical = (df["high"] + df["low"] + df["close"]) / 3
    lo, hi = float(df["low"].min()), float(df["high"].max())
    if hi <= lo:
        return []
    edges = np.linspace(lo, hi, bins + 1)
    idx = np.clip(np.digitize(typical, edges) - 1, 0, bins - 1)
    vol = np.bincount(idx, weights=df["volume"].to_numpy(dtype=float), minlength=bins)
    total = vol.sum()
    return [
        {
            "price_low": round(float(edges[i]), 4),
            "price_high": round(float(edges[i + 1]), 4),
            "volume": float(vol[i]),
            "share": float(vol[i] / total),
        }
        for i in range(bins)
    ]


def crossed_above(a: pd.Series, b: pd.Series, within: int = 1) -> bool:
    """True if ``a`` crossed above ``b`` within the last ``within`` bars."""
    diff = (a - b).dropna()
    if len(diff) < within + 1:
        return False
    recent = diff.iloc[-(within + 1) :]
    return any(recent.iloc[i - 1] <= 0 < recent.iloc[i] for i in range(1, len(recent)))


def crossed_below(a: pd.Series, b: pd.Series, within: int = 1) -> bool:
    return crossed_above(b, a, within)


def to_frame(bars: list) -> pd.DataFrame:  # noqa: ANN001 - list[CandleBar]
    if not bars:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    df = pd.DataFrame(
        [{"ts": b.ts, "open": b.open, "high": b.high, "low": b.low, "close": b.close, "volume": b.volume} for b in bars]
    ).set_index("ts")
    return df.sort_index()
