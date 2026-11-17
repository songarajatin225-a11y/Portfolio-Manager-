"""Model factories, evaluation metrics, calibration and drift statistics."""

from __future__ import annotations

import io
from typing import Any

import joblib
import numpy as np
import pandas as pd
from lightgbm import LGBMClassifier, LGBMRegressor
from sklearn.calibration import CalibratedClassifierCV
from sklearn.ensemble import RandomForestClassifier
from sklearn.frozen import FrozenEstimator
from sklearn.linear_model import LinearRegression, LogisticRegression
from sklearn.metrics import (
    accuracy_score,
    f1_score,
    log_loss,
    mean_absolute_error,
    mean_squared_error,
    precision_recall_fscore_support,
    r2_score,
)
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from xgboost import XGBClassifier

ALGORITHMS = ("logistic_regression", "random_forest", "xgboost", "lightgbm")


def make_classifier(name: str, n_classes: int, fast: bool = False) -> Any:
    trees = 60 if fast else 300
    if name == "logistic_regression":
        return make_pipeline(StandardScaler(), LogisticRegression(max_iter=2000, C=0.5, class_weight="balanced"))
    if name == "random_forest":
        return RandomForestClassifier(
            n_estimators=trees,
            max_depth=8,
            min_samples_leaf=25,
            class_weight="balanced_subsample",
            n_jobs=-1,
            random_state=42,
        )
    if name == "xgboost":
        return XGBClassifier(
            n_estimators=trees,
            max_depth=4,
            learning_rate=0.05,
            subsample=0.8,
            colsample_bytree=0.8,
            objective="multi:softprob" if n_classes > 2 else "binary:logistic",
            eval_metric="mlogloss" if n_classes > 2 else "logloss",
            random_state=42,
            n_jobs=4,
            verbosity=0,
        )
    if name == "lightgbm":
        return LGBMClassifier(
            n_estimators=trees,
            num_leaves=15,
            learning_rate=0.05,
            subsample=0.8,
            subsample_freq=1,
            colsample_bytree=0.8,
            class_weight="balanced",
            random_state=42,
            verbose=-1,
        )
    raise ValueError(f"Unknown algorithm {name}")


def make_vol_regressor(name: str, fast: bool = False) -> Any:
    if name == "har_linear":
        return LinearRegression()
    if name == "lightgbm":
        return LGBMRegressor(
            n_estimators=60 if fast else 300, num_leaves=15, learning_rate=0.05, random_state=42, verbose=-1
        )
    raise ValueError(name)


def calibrate(model: Any, X_val: pd.DataFrame, y_val: np.ndarray) -> Any:
    """Sigmoid calibration fitted on the validation period only (the model itself is frozen)."""
    if len(np.unique(y_val)) < 2 or len(y_val) < 50:
        return model
    cal = CalibratedClassifierCV(FrozenEstimator(model), method="sigmoid")
    cal.fit(X_val, y_val)
    return cal


def reliability(y_true_bin: np.ndarray, prob: np.ndarray, bins: int = 10) -> dict[str, Any]:
    edges = np.linspace(0, 1, bins + 1)
    idx = np.clip(np.digitize(prob, edges) - 1, 0, bins - 1)
    curve, ece = [], 0.0
    for b in range(bins):
        mask = idx == b
        if not mask.any():
            continue
        mean_p, freq = float(prob[mask].mean()), float(y_true_bin[mask].mean())
        curve.append(
            {"bin": b, "mean_predicted": round(mean_p, 4), "observed": round(freq, 4), "count": int(mask.sum())}
        )
        ece += mask.mean() * abs(mean_p - freq)
    return {"curve": curve, "ece": round(float(ece), 4)}


def classification_metrics(
    y_true: np.ndarray, proba: np.ndarray, classes: list[int], positive_class: int
) -> dict[str, Any]:
    pred = np.asarray(classes)[proba.argmax(axis=1)]
    p, r, f, s = precision_recall_fscore_support(y_true, pred, labels=classes, zero_division=0)
    onehot = np.eye(len(classes))[np.searchsorted(classes, y_true)]
    out: dict[str, Any] = {
        "n": int(len(y_true)),
        "accuracy": round(float(accuracy_score(y_true, pred)), 4),
        "precision_macro": round(float(p.mean()), 4),
        "recall_macro": round(float(r.mean()), 4),
        "f1_macro": round(float(f1_score(y_true, pred, labels=classes, average="macro", zero_division=0)), 4),
        "per_class": {
            str(c): {
                "precision": round(float(p[i]), 4),
                "recall": round(float(r[i]), 4),
                "f1": round(float(f[i]), 4),
                "support": int(s[i]),
            }
            for i, c in enumerate(classes)
        },
        "brier": round(float(np.mean(np.sum((proba - onehot) ** 2, axis=1))), 4),
        "log_loss": round(float(log_loss(y_true, proba, labels=classes)), 4),
    }
    if len(classes) == 3:  # directional accuracy on up/down calls where the outcome was directional
        directional = np.isin(pred, [0, 2]) & np.isin(y_true, [0, 2])
        out["directional_accuracy"] = (
            round(float((pred[directional] == y_true[directional]).mean()), 4) if directional.any() else None
        )
        out["directional_coverage"] = round(float(np.isin(pred, [0, 2]).mean()), 4)
    else:
        out["directional_accuracy"] = out["accuracy"]
    pos_idx = classes.index(positive_class)
    out["calibration"] = reliability((y_true == positive_class).astype(float), proba[:, pos_idx])
    return out


def baseline_metrics(y_train: np.ndarray, y_test: np.ndarray, classes: list[int]) -> dict[str, float]:
    """Majority-class baseline: a model must beat this to be worth anything."""
    majority = pd.Series(y_train).mode().iloc[0]
    pred = np.full_like(y_test, majority)
    return {
        "accuracy": round(float(accuracy_score(y_test, pred)), 4),
        "f1_macro": round(float(f1_score(y_test, pred, labels=classes, average="macro", zero_division=0)), 4),
        "majority_class": int(majority),
    }


def regression_metrics(y_true: np.ndarray, y_pred: np.ndarray, naive: np.ndarray) -> dict[str, float]:
    return {
        "n": int(len(y_true)),
        "mae": round(float(mean_absolute_error(y_true, y_pred)), 4),
        "rmse": round(float(np.sqrt(mean_squared_error(y_true, y_pred))), 4),
        "r2": round(float(r2_score(y_true, y_pred)), 4),
        "naive_mae": round(float(mean_absolute_error(y_true, naive)), 4),
        "naive_rmse": round(float(np.sqrt(mean_squared_error(y_true, naive))), 4),
    }


# --- Drift -------------------------------------------------------------------------------


def reference_bins(X: pd.DataFrame, bins: int = 10) -> dict[str, list[float]]:
    out = {}
    for col in X.columns:
        q = np.unique(np.nanquantile(X[col].to_numpy(dtype=float), np.linspace(0, 1, bins + 1)))
        if len(q) > 2:
            out[col] = [float(x) for x in q]
    return out


def psi(reference_edges: list[float], current: np.ndarray, ref_count: int = 10) -> float:
    """Population Stability Index of ``current`` vs equal-frequency reference bins."""
    edges = np.array(reference_edges, dtype=float)
    inner = edges[1:-1]
    cur = current[~np.isnan(current)]
    if len(cur) == 0:
        return float("nan")
    expected = np.full(len(inner) + 1, 1.0 / (len(inner) + 1))
    actual = np.bincount(np.searchsorted(inner, cur, side="right"), minlength=len(inner) + 1) / len(cur)
    actual = np.clip(actual, 1e-4, None)
    expected = np.clip(expected, 1e-4, None)
    return float(np.sum((actual - expected) * np.log(actual / expected)))


def drift_status(value: float) -> str:
    if np.isnan(value):
        return "unknown"
    return "stable" if value < 0.1 else "moderate" if value < 0.25 else "significant"


def dumps(obj: Any) -> bytes:
    buf = io.BytesIO()
    joblib.dump(obj, buf, compress=3)
    return buf.getvalue()


def loads(data: bytes) -> Any:
    # Artifacts are produced by this application's own training job and stored in its database.
    return joblib.load(io.BytesIO(data))
