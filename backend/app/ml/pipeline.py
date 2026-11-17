"""Training procedures (pure computation; no database access).

Classifier procedure (trend and momentum models)
1. Chronological train / validation / test split with an embargo of ``horizon`` days.
2. Walk-forward validation (expanding window, embargoed) on train+validation for every candidate
   algorithm; the algorithm with the best mean macro-F1 is selected.
3. The selected algorithm is refitted on the training period and probability-calibrated
   (sigmoid) on the validation period.
4. It is evaluated once on the untouched test period and compared with a majority-class baseline.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest
from sklearn.metrics import f1_score
from sklearn.mixture import GaussianMixture

from app.ml.features import ANOMALY_FEATURES, FEATURES, VOL_FEATURES, chronological_split, walk_forward_folds
from app.ml.models import (
    ALGORITHMS,
    baseline_metrics,
    calibrate,
    classification_metrics,
    make_classifier,
    make_vol_regressor,
    reference_bins,
    regression_metrics,
)

logger = logging.getLogger(__name__)


@dataclass
class TrainedModel:
    name: str
    task: str
    algorithm: str
    horizon_days: int
    features: list[str]
    artifact: Any
    metrics: dict[str, Any]
    calibration: dict[str, Any] = field(default_factory=dict)
    reference_stats: dict[str, Any] = field(default_factory=dict)
    params: dict[str, Any] = field(default_factory=dict)
    dates: dict[str, Any] = field(default_factory=dict)
    data_hash: str = ""


def data_fingerprint(df: pd.DataFrame, cols: list[str]) -> str:
    h = hashlib.sha256()
    h.update(pd.util.hash_pandas_object(df[cols].round(8), index=False).to_numpy().tobytes())
    return h.hexdigest()


def _clean(df: pd.DataFrame, features: list[str], target: str) -> pd.DataFrame:
    d = df[df[target] >= 0] if target in ("label", "outperform") else df[df[target].notna()]
    return d.dropna(subset=features).replace([np.inf, -np.inf], np.nan).dropna(subset=features)


def train_classifier(
    panel: pd.DataFrame,
    *,
    name: str,
    task: str,
    target: str,
    classes: list[int],
    positive_class: int,
    horizon: int,
    fast: bool = False,
    n_folds: int = 4,
) -> TrainedModel:
    data = _clean(panel, FEATURES, target)
    if len(data) < 300:
        raise ValueError(f"Not enough labelled rows to train {name} ({len(data)} < 300)")
    split = chronological_split(data["date"], embargo=horizon)
    X, y = data[FEATURES], data[target].to_numpy().astype(int)
    trval = split.train | split.val
    wf_dates = data.loc[trval, "date"]

    cv: dict[str, list[float]] = {}
    for algo in ALGORITHMS:
        scores = []
        for tr, va in walk_forward_folds(wf_dates, n_folds=n_folds, embargo=horizon):
            idx_tr, idx_va = np.where(trval)[0][tr], np.where(trval)[0][va]
            if len(np.unique(y[idx_tr])) < len(classes):
                continue
            m = make_classifier(algo, len(classes), fast)
            m.fit(X.iloc[idx_tr], y[idx_tr])
            pred = np.asarray(classes)[m.predict_proba(X.iloc[idx_va]).argmax(axis=1)]
            scores.append(f1_score(y[idx_va], pred, labels=classes, average="macro", zero_division=0))
        if scores:
            cv[algo] = scores
    if not cv:
        raise ValueError("Walk-forward validation produced no folds")
    best = max(cv, key=lambda a: float(np.mean(cv[a])))
    model = make_classifier(best, len(classes), fast)
    model.fit(X[split.train], y[split.train])
    model = calibrate(model, X[split.val], y[split.val])
    proba = model.predict_proba(X[split.test])
    test = classification_metrics(y[split.test], proba, classes, positive_class)
    base = baseline_metrics(y[split.train], y[split.test], classes)
    metrics = {
        "selected_algorithm": best,
        "walk_forward": {
            a: {"mean_f1_macro": round(float(np.mean(s)), 4), "folds": [round(float(x), 4) for x in s]}
            for a, s in cv.items()
        },
        "test": {k: v for k, v in test.items() if k != "calibration"},
        "baseline": base,
        "beats_baseline": test["f1_macro"] > base["f1_macro"],
        "class_balance_train": {str(c): int((y[split.train] == c).sum()) for c in classes},
        "rows": {"train": int(split.train.sum()), "val": int(split.val.sum()), "test": int(split.test.sum())},
    }
    return TrainedModel(
        name=name,
        task=task,
        algorithm=best,
        horizon_days=horizon,
        features=list(FEATURES),
        artifact=model,
        metrics=metrics,
        calibration=test["calibration"],
        reference_stats={
            "features": reference_bins(X[split.train]),
            "class_distribution": {str(c): float((proba.argmax(1) == i).mean()) for i, c in enumerate(classes)},
        },
        params={"embargo_days": horizon, "walk_forward_folds": n_folds, "fast": fast},
        dates={k: (v[0].date(), v[1].date()) for k, v in split.dates.items()},
        data_hash=data_fingerprint(data, FEATURES),
    )


def train_volatility(panel: pd.DataFrame, horizon: int = 21, fast: bool = False) -> TrainedModel:
    data = _clean(panel, VOL_FEATURES, "fwd_vol")
    if len(data) < 300:
        raise ValueError("Not enough rows for the volatility model")
    split = chronological_split(data["date"], embargo=horizon)
    X, y = data[VOL_FEATURES], data["fwd_vol"].to_numpy()
    naive_val = data["rv_21"].to_numpy()
    results: dict[str, dict[str, float]] = {}
    fitted: dict[str, Any] = {}
    for algo in ("har_linear", "lightgbm"):
        m = make_vol_regressor(algo, fast)
        m.fit(X[split.train], np.log(y[split.train]))
        pred = np.exp(m.predict(X[split.val]))
        results[algo] = regression_metrics(y[split.val], pred, naive_val[split.val])
        fitted[algo] = m
    best = min(results, key=lambda a: results[a]["mae"])
    model = make_vol_regressor(best, fast)
    model.fit(X[split.train | split.val], np.log(y[split.train | split.val]))
    test = regression_metrics(y[split.test], np.exp(model.predict(X[split.test])), naive_val[split.test])
    return TrainedModel(
        name="volatility_forecaster",
        task="volatility",
        algorithm=best,
        horizon_days=horizon,
        features=list(VOL_FEATURES),
        artifact=model,
        metrics={
            "selected_algorithm": best,
            "validation": results,
            "test": test,
            "beats_baseline": test["mae"] < test["naive_mae"],
            "target": f"annualised realised volatility over the next {horizon} trading days",
        },
        reference_stats={"features": reference_bins(X[split.train])},
        dates={k: (v[0].date(), v[1].date()) for k, v in split.dates.items()},
        data_hash=data_fingerprint(data, VOL_FEATURES),
    )


def train_anomaly(panel: pd.DataFrame, fast: bool = False) -> TrainedModel:
    data = panel.dropna(subset=ANOMALY_FEATURES).replace([np.inf, -np.inf], np.nan).dropna(subset=ANOMALY_FEATURES)
    if len(data) < 300:
        raise ValueError("Not enough rows for the anomaly model")
    dates = np.sort(data["date"].unique())
    cutoff = dates[int(len(dates) * 0.85)]
    train, test = data[data["date"] < cutoff], data[data["date"] >= cutoff]
    model = IsolationForest(n_estimators=100 if fast else 300, contamination="auto", random_state=42)
    model.fit(train[ANOMALY_FEATURES])
    train_scores = -model.score_samples(train[ANOMALY_FEATURES])
    test_scores = -model.score_samples(test[ANOMALY_FEATURES])
    threshold = float(np.quantile(train_scores, 0.99))
    artifact = {"model": model, "train_scores": np.sort(train_scores), "threshold_99": threshold}
    return TrainedModel(
        name="unusual_activity_detector",
        task="anomaly",
        algorithm="isolation_forest",
        horizon_days=1,
        features=list(ANOMALY_FEATURES),
        artifact=artifact,
        metrics={
            "type": "unsupervised",
            "train_rows": len(train),
            "test_rows": len(test),
            "test_flagged_rate": round(float((test_scores > threshold).mean()), 4),
            "note": "Scores rank how unusual a day's volume, move, range and gap are relative "
            "to history. There is no ground truth, so no accuracy is claimed.",
        },
        reference_stats={"features": reference_bins(train[ANOMALY_FEATURES])},
        dates={"train": (pd.Timestamp(dates[0]).date(), pd.Timestamp(cutoff).date())},
        data_hash=data_fingerprint(data, ANOMALY_FEATURES),
    )


def regime_features(bench: pd.DataFrame) -> pd.DataFrame:
    lr = np.log(bench["close"] / bench["close"].shift(1))
    return pd.DataFrame(
        {"ret_21": bench["close"].pct_change(21), "vol_21": lr.rolling(21).std() * np.sqrt(252)}
    ).dropna()


def train_regime(bench: pd.DataFrame) -> TrainedModel:
    f = regime_features(bench)
    if len(f) < 250:
        raise ValueError("Not enough benchmark history for regime detection")
    gm = GaussianMixture(n_components=3, covariance_type="full", random_state=42).fit(f.to_numpy())
    means = gm.means_
    order = np.argsort(means[:, 0])  # by mean 21d return
    names = {int(order[0]): "bearish", int(order[1]): "sideways", int(order[2]): "bullish"}
    median_vol = float(np.median(f["vol_21"]))
    labels = {k: f"{v} / {'high' if means[k, 1] > median_vol else 'low'} volatility" for k, v in names.items()}
    states = gm.predict(f.to_numpy())
    occupancy = {labels[int(k)]: round(float((states == k).mean()), 4) for k in range(3)}
    return TrainedModel(
        name="market_regime",
        task="regime",
        algorithm="gaussian_mixture",
        horizon_days=0,
        features=["ret_21", "vol_21"],
        artifact={"model": gm, "labels": labels},
        metrics={
            "type": "unsupervised",
            "occupancy": occupancy,
            "observations": len(f),
            "components": {
                labels[k]: {"mean_ret_21": round(float(means[k, 0]), 4), "mean_vol_21": round(float(means[k, 1]), 4)}
                for k in range(3)
            },
        },
        dates={"train": (f.index[0].date(), f.index[-1].date())},
        data_hash=data_fingerprint(f.reset_index(drop=True), ["ret_21", "vol_21"]),
    )
