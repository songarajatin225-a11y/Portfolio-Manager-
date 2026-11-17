"""ML pipeline: leakage safety, labels, splits, training, promotion rules and the end-to-end service."""

from __future__ import annotations

from datetime import timedelta

import numpy as np
import pandas as pd
import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.db.models import Instrument, InstrumentMetrics, MLModel, MLPrediction
from app.ml import service as ml_service
from app.ml.features import FEATURES, add_labels, build_panel, chronological_split, walk_forward_folds
from app.ml.models import dumps, psi
from app.ml.pipeline import train_classifier
from tests.test_market_api import setup_simulated


def synthetic_frames(n_inst: int = 12, n_days: int = 700, signal: float = 0.0, seed: int = 3) -> dict:
    """Price paths; with ``signal`` > 0 future returns depend on the trailing 5-day return (planted momentum)."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2022-01-03", periods=n_days, freq="B", tz="UTC")
    frames = {}
    for i in range(n_inst):
        r = np.zeros(n_days)
        noise = rng.normal(0, 0.012, n_days)
        for t in range(n_days):
            past = r[max(0, t - 5) : t].sum()
            r[t] = signal * past / 5 + noise[t]  # AR on the trailing 5-day mean (stationary for signal < 1)
        close = 100 * np.exp(np.cumsum(r))
        high = close * (1 + np.abs(rng.normal(0, 0.004, n_days)))
        low = close * (1 - np.abs(rng.normal(0, 0.004, n_days)))
        frames[i + 1] = pd.DataFrame(
            {
                "open": np.r_[close[0], close[:-1]],
                "high": np.maximum(high, close),
                "low": np.minimum(low, close),
                "close": close,
                "volume": rng.integers(1000, 9000, n_days).astype(float),
            },
            index=idx,
        )
    return frames


def test_features_have_no_lookahead() -> None:
    frames = synthetic_frames(4, 400)
    full = build_panel(frames, None, {})
    cut_date = full["date"].sort_values().unique()[300]
    truncated = build_panel({k: v[v.index.normalize() <= cut_date] for k, v in frames.items()}, None, {})
    a = full[full["date"] <= cut_date].set_index(["date", "instrument_id"])[FEATURES].sort_index()
    b = truncated.set_index(["date", "instrument_id"])[FEATURES].sort_index()
    pd.testing.assert_frame_equal(a, b, check_exact=False, rtol=1e-12)


def test_labels_use_forward_window_only() -> None:
    frames = synthetic_frames(2, 120)
    panel = add_labels(build_panel(frames, None, {}), horizon=5, band_k=0.5, bench=None)
    one = panel[panel["instrument_id"] == 1].reset_index(drop=True)
    t = 60
    assert one.loc[t, "fwd_ret"] == pytest.approx(one.loc[t + 5, "close"] / one.loc[t, "close"] - 1)
    band = 0.5 * one.loc[t, "atr_pct"] * np.sqrt(5)
    expected = 2 if one.loc[t, "fwd_ret"] > band else 0 if one.loc[t, "fwd_ret"] < -band else 1
    assert one.loc[t, "label"] == expected
    assert (one["label"].tail(5) == -1).all()  # no future -> no label
    lr = np.log(one["close"] / one["close"].shift(1))
    assert one.loc[t, "fwd_vol"] == pytest.approx(lr.iloc[t + 1 : t + 22].std() * np.sqrt(252))


def test_chronological_split_and_walk_forward_are_embargoed() -> None:
    dates = pd.Series(pd.date_range("2024-01-01", periods=500, freq="B"))
    s = chronological_split(dates, embargo=5)
    tr, va, te = dates[s.train], dates[s.val], dates[s.test]
    assert tr.max() < va.min() and va.max() < te.min()
    uniq = list(dates)
    assert uniq.index(va.min()) - uniq.index(tr.max()) > 5
    assert uniq.index(te.min()) - uniq.index(va.max()) > 5
    for train_mask, val_mask in walk_forward_folds(dates, n_folds=4, embargo=5):
        assert dates[train_mask].max() < dates[val_mask].min()
        assert uniq.index(dates[val_mask].min()) - uniq.index(dates[train_mask].max()) > 5


def test_classifier_learns_planted_signal_and_reports_metrics() -> None:
    panel = add_labels(build_panel(synthetic_frames(12, 700, signal=0.6), None, {}), horizon=5, band_k=0.5, bench=None)
    t = train_classifier(
        panel,
        name="trend_classifier",
        task="trend",
        target="label",
        classes=[0, 1, 2],
        positive_class=2,
        horizon=5,
        fast=True,
        n_folds=3,
    )
    m = t.metrics
    assert m["beats_baseline"] is True and m["test"]["f1_macro"] > m["baseline"]["f1_macro"]
    assert set(m["walk_forward"]) <= {"logistic_regression", "random_forest", "xgboost", "lightgbm"}
    assert m["selected_algorithm"] == t.algorithm
    for key in ("precision_macro", "recall_macro", "f1_macro", "directional_accuracy", "brier", "log_loss"):
        assert m["test"][key] is not None
    assert t.calibration["curve"] and 0 <= t.calibration["ece"] <= 1
    assert t.dates["train"][1] < t.dates["val"][0] < t.dates["test"][0]
    proba = t.artifact.predict_proba(panel.dropna(subset=FEATURES)[FEATURES].head(5))
    assert np.allclose(proba.sum(axis=1), 1)


def test_psi() -> None:
    rng = np.random.default_rng(0)
    ref = rng.normal(0, 1, 5000)
    edges = list(np.quantile(ref, np.linspace(0, 1, 11)))
    assert psi(edges, rng.normal(0, 1, 2000)) < 0.1
    assert psi(edges, rng.normal(1.5, 1, 2000)) > 0.25


async def test_ml_service_end_to_end(admin_client: AsyncClient, db) -> None:
    await setup_simulated(admin_client)
    from app.api.v1.data import run_metrics_refresh

    await run_metrics_refresh()
    results = await ml_service.train_all(db, fast=True, n_folds=2)
    by_task = {r["task"]: r for r in results}
    assert set(by_task) == {"trend", "momentum", "volatility", "anomaly", "regime"}
    for task in ("trend", "momentum", "volatility"):
        assert by_task[task]["status"] in ("active", "rejected") and by_task[task]["reason"]
    assert by_task["anomaly"]["status"] == "active" and by_task["regime"]["status"] == "active"

    # SIM prices have no exploitable signal, so the trend model may be rejected. Install a model trained on a
    # planted signal (same feature set) to exercise predictions, scores, evaluation and drift.
    # validated models publish independently, even when the trend classifier was rejected
    if by_task["trend"]["status"] == "rejected":
        assert await ml_service.generate_predictions(db) >= 30
        only = (await admin_client.get("/api/v1/ml/predictions")).json()["items"]
        assert all(i["prob_up"] is None and i["trend_class"] is None for i in only)
        assert any(i["predicted_vol"] is not None for i in only) and only[0]["regime"]

    trend_active = await db.scalar(
        select(MLModel).where(MLModel.name == "trend_classifier", MLModel.status == "active")
    )
    if trend_active is None:
        planted = add_labels(build_panel(synthetic_frames(10, 600, signal=0.6), None, {}), 5, 0.5, None)
        t = train_classifier(
            planted,
            name="trend_classifier",
            task="trend",
            target="label",
            classes=[0, 1, 2],
            positive_class=2,
            horizon=5,
            fast=True,
            n_folds=3,
        )
        db.add(
            MLModel(
                name="trend_classifier",
                task="trend",
                algorithm=t.algorithm,
                version=99,
                status="active",
                horizon_days=5,
                features=t.features,
                metrics=t.metrics,
                calibration=t.calibration,
                reference_stats=t.reference_stats,
                is_simulated=True,
                artifact=dumps(t.artifact),
            )
        )
        await db.commit()
    n = await ml_service.generate_predictions(db)
    assert n >= 30
    preds = (await admin_client.get("/api/v1/ml/predictions")).json()
    assert preds["data_status"] == "SIMULATED" and "not forecasts of certainty" in preds["disclaimer"]
    item = preds["items"][0]
    assert item["prob_up"] + item["prob_neutral"] + item["prob_down"] == pytest.approx(1, abs=1e-6)
    assert item["regime"] and item["anomaly_score"] is not None
    met = await db.scalar(
        select(InstrumentMetrics)
        .join(Instrument, Instrument.id == InstrumentMetrics.instrument_id)
        .where(Instrument.symbol == "SIMBANKA")
    )
    await db.refresh(met)
    assert met.scores["ml_trend"] in ("bullish", "bearish", "neutral") and met.scores["risk_score"] is not None
    screen = (
        await admin_client.post(
            "/api/v1/screener/run", json={"filters": [{"field": "ml_bullish_prob", "op": "gte", "value": 0}]}
        )
    ).json()
    assert screen["matched"] >= 30

    # exactly one prediction per instrument and date, even after the host model changed
    dup = (
        await db.execute(
            select(MLPrediction.instrument_id, MLPrediction.as_of)
            .group_by(MLPrediction.instrument_id, MLPrediction.as_of)
            .having(__import__("sqlalchemy").func.count() > 1)
        )
    ).all()
    assert dup == []
    stock = (await admin_client.get("/api/v1/stocks/SIMBANKA/prediction")).json()
    assert stock["latest"]["trend_class"] and stock["model"]["test"]

    # a back-dated prediction is evaluated against the realised move
    inst = await db.scalar(select(Instrument).where(Instrument.symbol == "SIMBANKA"))
    latest = await db.scalar(select(MLPrediction).where(MLPrediction.instrument_id == inst.id))
    old = MLPrediction(
        model_id=latest.model_id,
        instrument_id=inst.id,
        as_of=latest.as_of - timedelta(days=42),
        horizon_days=5,
        prob_up=0.5,
        prob_down=0.2,
        prob_neutral=0.3,
        trend_class="bullish",
        confidence=0.5,
        is_simulated=True,
    )
    while old.as_of.weekday() >= 5:
        old.as_of -= timedelta(days=1)
    db.add(old)
    await db.commit()
    assert await ml_service.evaluate_realized(db) >= 1
    acc = (await admin_client.get("/api/v1/ml/accuracy")).json()
    assert acc["evaluated"] >= 1 and acc["weekly"]

    drift = (await admin_client.get("/api/v1/ml/drift")).json()
    assert drift["models"] and all(
        m["overall"] in ("stable", "moderate", "significant", "unknown") for m in drift["models"]
    )
    sectors = (await admin_client.get("/api/v1/ml/sector-strength")).json()
    assert sectors[0]["strength_score"] is not None
    models = (await admin_client.get("/api/v1/ml/models")).json()
    assert all("artifact" not in m for m in models)

    rejected = await db.scalar(select(MLModel).where(MLModel.status == "rejected", MLModel.task == "trend"))
    if rejected is not None:
        r = await admin_client.post(f"/api/v1/ml/models/{rejected.id}/promote")
        assert r.status_code == 400 and "baseline" in r.json()["error"]["message"]
