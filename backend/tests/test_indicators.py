"""Indicator correctness: published reference values, independent loop implementations, no look-ahead."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.analytics import indicators as ind

# Wilder's RSI example as published by StockCharts (closes and RSI(14) values).
WILDER_CLOSES = [
    44.34,
    44.09,
    44.15,
    43.61,
    44.33,
    44.83,
    45.10,
    45.42,
    45.84,
    46.08,
    45.89,
    46.03,
    45.61,
    46.28,
    46.28,
    46.00,
    46.03,
    46.41,
    46.22,
    45.64,
    46.21,
    46.25,
    45.71,
    46.45,
    45.78,
    45.35,
    44.03,
    44.18,
    44.22,
    44.57,
    43.42,
    42.66,
    43.13,
]
WILDER_RSI = [
    70.53,
    66.32,
    66.55,
    69.41,
    66.36,
    57.97,
    62.93,
    63.26,
    56.06,
    62.38,
    54.71,
    50.42,
    39.99,
    41.46,
    41.87,
    45.46,
    37.30,
    33.08,
    37.77,
]


def ohlc(n: int = 300, seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 100 * np.exp(np.cumsum(rng.normal(0.0005, 0.015, n)))
    open_ = close * (1 + rng.normal(0, 0.004, n))
    high = np.maximum(open_, close) * (1 + np.abs(rng.normal(0, 0.006, n)))
    low = np.minimum(open_, close) * (1 - np.abs(rng.normal(0, 0.006, n)))
    vol = rng.integers(1_000, 50_000, n).astype(float)
    idx = pd.date_range("2024-01-01", periods=n, freq="B", tz="UTC")
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "volume": vol}, index=idx)


def test_rsi_matches_wilder_published_example() -> None:
    out = ind.rsi(pd.Series(WILDER_CLOSES), 14)
    assert out.iloc[:14].isna().all()
    # Exact first value by hand: over the first 14 changes gains sum to 3.34 and losses to 1.40.
    assert out.iloc[14] == pytest.approx(100 - 100 / (1 + 3.34 / 1.40), abs=1e-9)
    # The published table rounds intermediate averages to 2 dp, so it differs by < 0.1.
    np.testing.assert_allclose(out.iloc[14:].to_numpy(), WILDER_RSI, atol=0.1)


def test_sma_and_ema() -> None:
    s = pd.Series(np.arange(1.0, 11.0))
    assert ind.sma(s, 3).iloc[2:].tolist() == pytest.approx([2, 3, 4, 5, 6, 7, 8, 9])
    e = ind.ema(s, 3)  # alpha = 0.5, seeded at SMA(1,2,3)=2
    assert e.iloc[:2].isna().all()
    assert e.iloc[2:].tolist() == pytest.approx([2, 3, 4, 5, 6, 7, 8, 9])
    noisy = pd.Series([10.0, 11, 12, 11, 13, 14, 13, 15])
    ref, alpha = [np.mean([10, 11, 12])], 0.5
    for x in noisy.iloc[3:]:
        ref.append(alpha * x + (1 - alpha) * ref[-1])
    assert ind.ema(noisy, 3).iloc[2:].tolist() == pytest.approx(ref)


def _ref_atr(df: pd.DataFrame, n: int) -> list[float]:
    trs = []
    for i in range(len(df)):
        h, low = df["high"].iloc[i], df["low"].iloc[i]
        if i == 0:
            trs.append(h - low)
        else:
            pc = df["close"].iloc[i - 1]
            trs.append(max(h - low, abs(h - pc), abs(low - pc)))
    out = [np.nan] * len(df)
    out[n - 1] = float(np.mean(trs[:n]))
    for i in range(n, len(df)):
        out[i] = (out[i - 1] * (n - 1) + trs[i]) / n
    return out


def test_atr_matches_loop_reference() -> None:
    df = ohlc(80)
    np.testing.assert_allclose(ind.atr(df, 14).to_numpy(), _ref_atr(df, 14), equal_nan=True, rtol=1e-12)


def _ref_adx(df: pd.DataFrame, n: int) -> float:
    pdm, mdm, trs = [], [], []
    for i in range(1, len(df)):
        up = df["high"].iloc[i] - df["high"].iloc[i - 1]
        dn = df["low"].iloc[i - 1] - df["low"].iloc[i]
        pdm.append(up if up > dn and up > 0 else 0.0)
        mdm.append(dn if dn > up and dn > 0 else 0.0)
        h, low, pc = df["high"].iloc[i], df["low"].iloc[i], df["close"].iloc[i - 1]
        trs.append(max(h - low, abs(h - pc), abs(low - pc)))

    def rma(x: list[float]) -> list[float]:
        out = [float(np.mean(x[:n]))]
        for v in x[n:]:
            out.append((out[-1] * (n - 1) + v) / n)
        return out

    tr_s, p_s, m_s = rma(trs), rma(pdm), rma(mdm)
    dx = [100 * abs(p / t - m / t) / (p / t + m / t) for p, m, t in zip(p_s, m_s, tr_s, strict=True)]
    return rma(dx)[-1]


def test_adx_matches_loop_reference() -> None:
    df = ohlc(120)
    assert ind.adx(df, 14)["adx"].iloc[-1] == pytest.approx(_ref_adx(df, 14), rel=1e-9)


def test_macd_bollinger_vwap() -> None:
    df = ohlc(200)
    m = ind.macd(df["close"])
    np.testing.assert_allclose(m["macd"], ind.ema(df["close"], 12) - ind.ema(df["close"], 26), equal_nan=True)
    np.testing.assert_allclose(m["hist"], m["macd"] - m["signal"], equal_nan=True)
    assert m["signal"].first_valid_index() == df.index[25 + 8]

    bb = ind.bollinger(df["close"], 20, 2)
    window = df["close"].iloc[-20:]
    assert bb["middle"].iloc[-1] == pytest.approx(window.mean())
    assert bb["upper"].iloc[-1] == pytest.approx(window.mean() + 2 * window.std(ddof=0))

    intraday = df.iloc[:6].copy()
    intraday.index = pd.to_datetime(
        [
            "2026-11-16 04:00",
            "2026-11-16 04:01",
            "2026-11-16 04:02",
            "2026-11-17 04:00",
            "2026-11-17 04:01",
            "2026-11-17 04:02",
        ],
        utc=True,
    )
    v = ind.vwap(intraday)
    tp = (intraday["high"] + intraday["low"] + intraday["close"]) / 3
    day2 = intraday.iloc[3:]
    tp2 = tp.iloc[3:]
    assert v.iloc[3] == pytest.approx(tp2.iloc[0])  # resets at the new session
    assert v.iloc[5] == pytest.approx((tp2 * day2["volume"]).sum() / day2["volume"].sum())


def test_supertrend_follows_trend() -> None:
    n = 120
    idx = pd.date_range("2024-01-01", periods=n, freq="B")
    up = np.linspace(100, 200, n)
    df = pd.DataFrame({"open": up, "high": up * 1.01, "low": up * 0.99, "close": up, "volume": 1.0}, index=idx)
    st = ind.supertrend(df, 10, 3)
    assert st["direction"].iloc[-1] == 1 and st["supertrend"].iloc[-1] < df["close"].iloc[-1]
    down = df.iloc[::-1].copy()
    down.index = idx
    st_down = ind.supertrend(down, 10, 3)
    assert st_down["direction"].iloc[-1] == -1 and st_down["supertrend"].iloc[-1] > down["close"].iloc[-1]


@pytest.mark.parametrize("name", ["sma", "ema", "rsi", "macd", "bollinger", "atr", "adx", "supertrend", "vol"])
def test_no_lookahead(name: str) -> None:
    """The value at bar t must not change when future bars are appended."""
    df = ohlc(260, seed=11)
    fns = {
        "sma": lambda d: ind.sma(d["close"], 20),
        "ema": lambda d: ind.ema(d["close"], 20),
        "rsi": lambda d: ind.rsi(d["close"], 14),
        "macd": lambda d: ind.macd(d["close"])["signal"],
        "bollinger": lambda d: ind.bollinger(d["close"])["upper"],
        "atr": lambda d: ind.atr(d, 14),
        "adx": lambda d: ind.adx(d, 14)["adx"],
        "supertrend": lambda d: ind.supertrend(d)["supertrend"],
        "vol": lambda d: ind.annualized_volatility(d["close"]),
    }
    full = fns[name](df)
    for cut in (120, 180, 259):
        partial = fns[name](df.iloc[: cut + 1])
        assert partial.iloc[-1] == pytest.approx(full.iloc[cut], rel=1e-10, nan_ok=True)


def test_fibonacci_support_resistance_volume_profile() -> None:
    idx = pd.date_range("2024-01-01", periods=60, freq="B")
    close = np.concatenate([np.linspace(100, 150, 30), np.linspace(150, 130, 30)])
    df = pd.DataFrame({"open": close, "high": close + 1, "low": close - 1, "close": close, "volume": 1000.0}, index=idx)
    fib = ind.fibonacci(df, 60)
    assert fib is not None and fib.trend == "up"
    assert fib.levels["0.0%"] == pytest.approx(151) and fib.levels["100.0%"] == pytest.approx(99)
    assert fib.levels["50.0%"] == pytest.approx(125)

    t = np.arange(300)
    wave = 110 + 10 * np.sin(t / 8)  # oscillates between ~100 and ~120
    osc = pd.DataFrame(
        {"open": wave, "high": wave + 0.5, "low": wave - 0.5, "close": wave, "volume": 1.0},
        index=pd.date_range("2024-01-01", periods=300, freq="B"),
    )
    sr = ind.support_resistance(osc, lookback=300, window=5)
    assert any(abs(s["price"] - 99.5) < 1.5 for s in sr["support"])
    assert any(abs(r["price"] - 120.5) < 1.5 for r in sr["resistance"])

    vp = ind.volume_profile(df, bins=10)
    assert sum(b["share"] for b in vp) == pytest.approx(1.0)
    assert ind.crossed_above(pd.Series([1.0, 2, 3]), pd.Series([2.0, 2, 2]), within=2)
    assert not ind.crossed_above(pd.Series([3.0, 3, 3]), pd.Series([2.0, 2, 2]), within=2)


def test_to_frame_uses_ist_trading_dates() -> None:
    """A daily bar stamped 00:00 IST (18:30 UTC the day before) must map to its IST trading date."""
    from datetime import UTC, date, datetime

    from app.marketdata.types import CandleBar

    df = ind.to_frame([CandleBar(datetime(2026, 11, 16, 18, 30, tzinfo=UTC), 1, 2, 0.5, 1.5, 10)])
    assert df.index[0].date() == date(2026, 11, 17)
