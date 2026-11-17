"""Feature engineering, labelling and leakage-safe splitting for the ML pipeline.

Leakage rules enforced here:
* Every feature at date t uses only data available at the close of t (trailing windows, shifted
  averages, news published on or before t).
* Labels are forward returns from t to t+h and are the ONLY place future data is used; rows whose
  label window runs past the data are dropped from training and used for prediction instead.
* Splits are chronological, with an embargo of ``h`` trading days at every boundary so a training
  label window never overlaps the validation/test period.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from app.analytics import indicators as ind

FEATURES = [
    "ret_1",
    "ret_5",
    "ret_10",
    "ret_21",
    "dist_sma20",
    "dist_sma50",
    "dist_sma200",
    "rsi_14",
    "macd_hist_pct",
    "atr_pct",
    "vol_ratio",
    "volatility_20",
    "rs_21",
    "rs_63",
    "range_pct",
    "gap_pct",
    "breadth_50",
    "sector_rel_21",
    "sentiment_7d",
]
VOL_FEATURES = ["rv_5", "rv_10", "rv_21", "rv_63", "abs_ret_1", "atr_pct", "vol_ratio"]
ANOMALY_FEATURES = ["log_vol_ratio", "ret_z", "range_ratio", "abs_gap"]
LABELS = {0: "bearish", 1: "neutral", 2: "bullish"}


def instrument_features(df: pd.DataFrame, bench_close: pd.Series | None) -> pd.DataFrame:
    close, high, low, vol = df["close"], df["high"], df["low"], df["volume"]
    log_ret = np.log(close / close.shift(1))
    out = pd.DataFrame(index=df.index)
    for n in (1, 5, 10, 21):
        out[f"ret_{n}"] = close.pct_change(n)
    out["dist_sma20"] = close / ind.sma(close, 20) - 1
    out["dist_sma50"] = close / ind.sma(close, 50) - 1
    out["dist_sma200"] = close / ind.sma(close, 200) - 1
    out["rsi_14"] = ind.rsi(close, 14)
    out["macd_hist_pct"] = ind.macd(close)["hist"] / close
    atr = ind.atr(df, 14)
    out["atr_pct"] = atr / close
    avg_vol = vol.rolling(20, min_periods=20).mean().shift(1)
    out["vol_ratio"] = (vol / avg_vol).replace([np.inf, -np.inf], np.nan)
    out["volatility_20"] = log_ret.rolling(20, min_periods=20).std() * np.sqrt(252)
    if bench_close is not None:
        b = bench_close.reindex(df.index).ffill()
        out["rs_21"] = out["ret_21"] - b.pct_change(21)
        out["rs_63"] = close.pct_change(63) - b.pct_change(63)
    else:
        out["rs_21"] = 0.0
        out["rs_63"] = 0.0
    out["range_pct"] = (high - low) / close
    out["gap_pct"] = df["open"] / close.shift(1) - 1
    # volatility-model and anomaly inputs (all trailing)
    for n in (5, 10, 21, 63):
        out[f"rv_{n}"] = log_ret.rolling(n, min_periods=n).std() * np.sqrt(252)
    out["abs_ret_1"] = log_ret.abs()
    out["log_vol_ratio"] = np.log(out["vol_ratio"].clip(lower=1e-3))
    daily_sigma = (out["volatility_20"] / np.sqrt(252)).replace(0, np.nan)
    out["ret_z"] = (log_ret / daily_sigma).abs()
    out["range_ratio"] = ((high - low) / atr.shift(1)).replace([np.inf, -np.inf], np.nan)
    out["abs_gap"] = out["gap_pct"].abs()
    out["close"] = close
    return out


def sentiment_series(articles: pd.DataFrame, index: pd.DatetimeIndex) -> pd.Series:
    """Trailing 7-day mean sentiment using only articles published on or before each date."""
    if articles.empty:
        return pd.Series(0.0, index=index)
    a = articles.copy()
    a["published_at"] = pd.to_datetime(a["published_at"], utc=True).dt.tz_convert(index.tz or "UTC")
    daily = a.set_index("published_at")["score"].resample("1D").mean()
    rolled = daily.rolling("7D", min_periods=1).mean()
    aligned = rolled.reindex(index.normalize(), method="ffill").fillna(0.0)
    aligned.index = index
    return aligned


def build_panel(
    frames: dict[int, pd.DataFrame],
    bench: pd.DataFrame | None,
    sectors: dict[int, str | None],
    sentiment: dict[int, pd.DataFrame] | None = None,
) -> pd.DataFrame:
    """Stack per-instrument features into a (date, instrument_id) panel and add cross-sectional features."""
    parts = []
    bench_close = bench["close"] if bench is not None and not bench.empty else None
    for iid, df in frames.items():
        if len(df) < 60:
            continue
        f = instrument_features(df, bench_close)
        f["sentiment_7d"] = sentiment_series(sentiment[iid], f.index) if sentiment and iid in sentiment else 0.0
        f["instrument_id"] = iid
        f["sector"] = sectors.get(iid) or "Unclassified"
        f["date"] = f.index.normalize()
        parts.append(f.reset_index(drop=True))
    if not parts:
        return pd.DataFrame()
    panel = pd.concat(parts, ignore_index=True)
    # cross-sectional features use only same-date values
    panel["above_50"] = (panel["dist_sma50"] > 0).astype(float).where(panel["dist_sma50"].notna())
    panel["breadth_50"] = panel.groupby("date")["above_50"].transform("mean")
    panel["sector_rel_21"] = panel["ret_21"] - panel.groupby(["date", "sector"])["ret_21"].transform("mean")
    return panel.drop(columns=["above_50"]).sort_values(["date", "instrument_id"]).reset_index(drop=True)


def add_labels(
    panel: pd.DataFrame, horizon: int, band_k: float, bench: pd.DataFrame | None, rel_horizon: int = 21
) -> pd.DataFrame:
    """Forward-looking targets. band = band_k × ATR% × √horizon defines the neutral zone."""
    p = panel.copy()
    g = p.groupby("instrument_id")["close"]
    p["fwd_ret"] = g.shift(-horizon) / p["close"] - 1
    band = band_k * p["atr_pct"] * np.sqrt(horizon)
    p["label"] = np.where(p["fwd_ret"] > band, 2, np.where(p["fwd_ret"] < -band, 0, 1))
    p.loc[p["fwd_ret"].isna(), "label"] = -1
    fwd_rel = g.shift(-rel_horizon) / p["close"] - 1
    if bench is not None and not bench.empty:
        bc = bench["close"]
        bench_fwd = bc.shift(-rel_horizon) / bc - 1
        bench_fwd.index = bench_fwd.index.normalize()
        fwd_b = p["date"].map(bench_fwd)
    else:
        fwd_b = pd.Series(np.nan, index=p.index)  # no benchmark -> no momentum label
    p["outperform"] = np.where(fwd_rel.isna() | fwd_b.isna(), -1, (fwd_rel > fwd_b).astype(int))
    # forward realised volatility (annualised) over the label horizon for the volatility model
    lr = np.log(p["close"] / g.shift(1))
    p["_lr"] = lr
    # rolling(n).std() at t+n covers returns t+1..t+n; shifting by -n assigns it to t
    fwd_vol = p.groupby("instrument_id")["_lr"].transform(
        lambda s: s.rolling(rel_horizon, min_periods=rel_horizon).std().shift(-rel_horizon)
    )
    p["fwd_vol"] = fwd_vol * np.sqrt(252)
    return p.drop(columns=["_lr"])


@dataclass
class Split:
    train: np.ndarray
    val: np.ndarray
    test: np.ndarray
    dates: dict[str, tuple[pd.Timestamp, pd.Timestamp]]


def _date_ints(dates: pd.Series) -> np.ndarray:
    """Dates as int64 nanoseconds (fast comparisons; tz-aware values would compare as Python objects)."""
    return pd.DatetimeIndex(dates).asi8


def _range_mask(d: np.ndarray, uniq: np.ndarray, lo: int, hi: int) -> np.ndarray:
    """Rows whose date is in uniq[lo:hi] (a contiguous block of the sorted unique dates)."""
    if hi <= lo or lo >= len(uniq):
        return np.zeros(len(d), dtype=bool)
    mask = d >= uniq[lo]
    if hi < len(uniq):
        mask &= d < uniq[hi]
    return mask


def chronological_split(dates: pd.Series, embargo: int, val_frac: float = 0.15, test_frac: float = 0.15) -> Split:
    """Boolean masks for train/val/test by date with ``embargo`` trading days removed before each boundary."""
    idx = pd.DatetimeIndex(dates)
    uniq_ts = idx.unique().sort_values()
    d, uniq = idx.asi8, uniq_ts.asi8  # same resolution for both
    n = len(uniq)
    if n < 60:
        raise ValueError("Not enough history for a chronological split")
    test_start = int(n * (1 - test_frac))
    val_start = int(n * (1 - test_frac - val_frac))
    bounds = {
        "train": (0, max(0, val_start - embargo)),
        "val": (val_start, max(val_start, test_start - embargo)),
        "test": (test_start, n),
    }
    masks = {k: _range_mask(d, uniq, lo, hi) for k, (lo, hi) in bounds.items()}
    split = Split(masks["train"], masks["val"], masks["test"], {})
    for name, (lo, hi) in bounds.items():
        if hi > lo:
            split.dates[name] = (uniq_ts[lo], uniq_ts[hi - 1])
    return split


def walk_forward_folds(dates: pd.Series, n_folds: int, embargo: int, min_train_frac: float = 0.4):
    """Expanding-window folds over the given dates: train on [0, k), validate on the next block."""
    d = _date_ints(dates)
    uniq = np.unique(d)
    n = len(uniq)
    start = int(n * min_train_frac)
    block = max(1, (n - start) // n_folds)
    for k in range(n_folds):
        val_lo = start + k * block
        val_hi = n if k == n_folds - 1 else val_lo + block
        train_hi = max(0, val_lo - embargo)
        if train_hi == 0 or val_hi <= val_lo:
            continue
        yield _range_mask(d, uniq, 0, train_hi), _range_mask(d, uniq, val_lo, val_hi)
