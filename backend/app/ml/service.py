"""ML orchestration: data loading, training runs, model registry, promotion, predictions, evaluation, drift."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy import delete, func, literal, select, update
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics import indicators as ind
from app.core.bus import get_bus
from app.db.models import (
    Instrument,
    InstrumentMetrics,
    MLModel,
    MLPrediction,
    NewsArticle,
    NewsArticleInstrument,
)
from app.ml.features import ANOMALY_FEATURES, FEATURES, LABELS, VOL_FEATURES, add_labels, build_panel
from app.ml.models import drift_status, dumps, loads, psi
from app.ml.pipeline import (
    TrainedModel,
    regime_features,
    train_anomaly,
    train_classifier,
    train_regime,
    train_volatility,
)
from app.services import app_settings
from app.services import instruments as instrument_service
from app.services.candles import get_candles
from app.services.market_data import is_simulated_mode

logger = logging.getLogger(__name__)
K_ML = "mi:ml_status"
HISTORY_DAYS = 365 * 6
MODEL_NAMES = {
    "trend_classifier": "trend",
    "momentum_classifier": "momentum",
    "volatility_forecaster": "volatility",
    "unusual_activity_detector": "anomaly",
    "market_regime": "regime",
}


async def _frame(db: AsyncSession, inst: Instrument, allow_fetch: bool) -> pd.DataFrame:
    end = datetime.now(UTC)
    res = await get_candles(db, inst, "1d", end - timedelta(days=HISTORY_DAYS), end, allow_fetch=allow_fetch)
    return ind.to_frame(res.bars)


async def load_dataset(db: AsyncSession, allow_fetch: bool = True, max_instruments: int = 300):
    simulated = await is_simulated_mode(db)
    ids = await instrument_service.tracked_universe(db, limit=5000)
    insts = (
        await db.scalars(
            select(Instrument).where(
                Instrument.id.in_(ids), Instrument.is_index.is_(False), Instrument.is_simulated.is_(simulated)
            )
        )
    ).all()
    insts = list(insts)[:max_instruments]
    frames = {i.id: await _frame(db, i, allow_fetch) for i in insts}
    bench = None
    code = (await app_settings.get_section(db, "market")).get("benchmark_index", "NIFTY50")
    for entry in await instrument_service.dashboard_indices(db):
        if entry["code"] == code and entry["instrument"] is not None:
            bench = await _frame(db, entry["instrument"], allow_fetch)
    rows = (
        await db.execute(
            select(NewsArticleInstrument.instrument_id, NewsArticle.published_at, NewsArticle.sentiment_score)
            .join(NewsArticle, NewsArticle.id == NewsArticleInstrument.article_id)
            .where(NewsArticleInstrument.instrument_id.in_([i.id for i in insts]))
        )
    ).all()
    sentiment: dict[int, pd.DataFrame] = {}
    for iid, published, score in rows:
        sentiment.setdefault(iid, []).append((published, score))  # type: ignore[arg-type]
    sentiment_df = {k: pd.DataFrame(v, columns=["published_at", "score"]) for k, v in sentiment.items()}
    sectors = {i.id: i.sector for i in insts}
    return frames, bench, sectors, sentiment_df, simulated


async def _next_version(db: AsyncSession, name: str) -> int:
    return int((await db.scalar(select(func.max(MLModel.version)).where(MLModel.name == name))) or 0) + 1


async def _active(db: AsyncSession, name: str, simulated: bool) -> MLModel | None:
    return await db.scalar(
        select(MLModel).where(MLModel.name == name, MLModel.status == "active", MLModel.is_simulated.is_(simulated))
    )


def _evaluate_on(
    model: MLModel, panel: pd.DataFrame, trained: TrainedModel, target: str, classes: list[int]
) -> float | None:
    """Score the currently active model on the candidate's test window (same rows) for a fair comparison."""
    from sklearn.metrics import f1_score

    if model.artifact is None or list(model.features) != trained.features or "test" not in trained.dates:
        return None
    lo, hi = trained.dates["test"]
    d = panel[(panel["date"].dt.date >= lo) & (panel["date"].dt.date <= hi) & (panel[target] >= 0)]
    d = d.dropna(subset=trained.features)
    if d.empty:
        return None
    est = loads(model.artifact)
    pred = np.asarray(classes)[est.predict_proba(d[trained.features]).argmax(axis=1)]
    return float(f1_score(d[target].astype(int), pred, labels=classes, average="macro", zero_division=0))


async def _register(
    db: AsyncSession, t: TrainedModel, simulated: bool, panel: pd.DataFrame | None, min_gain: float
) -> MLModel:
    row = MLModel(
        name=t.name,
        task=t.task,
        algorithm=t.algorithm,
        version=await _next_version(db, t.name),
        status="candidate",
        horizon_days=t.horizon_days,
        params=t.params,
        features=t.features,
        train_start=t.dates.get("train", (None, None))[0],
        train_end=t.dates.get("train", (None, None))[1],
        test_start=t.dates.get("test", (None, None))[0],
        test_end=t.dates.get("test", (None, None))[1],
        metrics=t.metrics,
        calibration=t.calibration,
        reference_stats=t.reference_stats,
        data_hash=t.data_hash,
        is_simulated=simulated,
        artifact=dumps(t.artifact),
    )
    db.add(row)
    await db.flush()
    active = await _active(db, t.name, simulated)
    promote, reason = False, ""
    if t.task in ("trend", "momentum"):
        target = "label" if t.task == "trend" else "outperform"
        classes = [0, 1, 2] if t.task == "trend" else [0, 1]
        cand_f1 = t.metrics["test"]["f1_macro"]
        if not t.metrics["beats_baseline"]:
            reason = (
                f"rejected: test macro-F1 {cand_f1} does not beat the majority-class baseline "
                f"{t.metrics['baseline']['f1_macro']}"
            )
        elif active is None:
            promote, reason = True, "promoted: first model that beats the baseline"
        else:
            active_f1 = _evaluate_on(active, panel, t, target, classes) if panel is not None else None
            if active_f1 is None or cand_f1 >= active_f1 + min_gain:
                promote = True
                reason = f"promoted: test macro-F1 {cand_f1} vs active {active_f1 if active_f1 is not None else 'n/a'}"
            else:
                reason = f"rejected: test macro-F1 {cand_f1} < active {round(active_f1, 4)} + {min_gain}"
    elif t.task == "volatility":
        if not t.metrics["beats_baseline"]:
            reason = "rejected: test MAE does not beat the naive (current volatility) forecast"
        elif active is None or t.metrics["test"]["mae"] <= active.metrics.get("test", {}).get("mae", np.inf):
            promote, reason = True, "promoted: lower test MAE than naive forecast and active model"
        else:
            reason = "rejected: test MAE worse than the active model"
    else:
        promote, reason = True, "promoted: unsupervised model refitted on the latest data"
    row.notes = reason
    if promote:
        if active is not None:
            active.status = "retired"
        row.status, row.promoted_at = "active", datetime.now(UTC)
    else:
        row.status = "rejected"
    return row


async def train_all(
    db: AsyncSession, *, fast: bool = False, allow_fetch: bool = True, n_folds: int = 4
) -> list[dict[str, Any]]:
    bus = get_bus()
    started = datetime.now(UTC).isoformat()
    await bus.set_json(K_ML, {"state": "running", "stage": "loading data", "started_at": started})
    cfg = await app_settings.get_section(db, "ml")
    horizon, band_k = int(cfg["horizon_days"]), float(cfg["neutral_band_atr"])
    frames, bench, sectors, sentiment, simulated = await load_dataset(db, allow_fetch)
    panel = build_panel(frames, bench, sectors, sentiment)
    if panel.empty:
        raise ValueError("No candle history available for training")
    labelled = await asyncio.to_thread(add_labels, panel, horizon, band_k, bench)
    if int((labelled["label"] >= 0).sum()) < int(cfg["min_training_rows"]):
        raise ValueError(f"Only {int((labelled['label'] >= 0).sum())} labelled rows; need {cfg['min_training_rows']}")
    jobs = [
        (
            "trend",
            lambda: train_classifier(
                labelled,
                name="trend_classifier",
                task="trend",
                target="label",
                classes=[0, 1, 2],
                positive_class=2,
                horizon=horizon,
                fast=fast,
                n_folds=n_folds,
            ),
        ),
        (
            "momentum",
            lambda: train_classifier(
                labelled,
                name="momentum_classifier",
                task="momentum",
                target="outperform",
                classes=[0, 1],
                positive_class=1,
                horizon=21,
                fast=fast,
                n_folds=n_folds,
            ),
        ),
        ("volatility", lambda: train_volatility(labelled, fast=fast)),
        ("anomaly", lambda: train_anomaly(labelled, fast=fast)),
    ]
    if bench is not None and not bench.empty:
        jobs.append(("regime", lambda: train_regime(bench)))
    results = []
    for task, fn in jobs:
        await bus.set_json(K_ML, {"state": "running", "stage": f"training {task}", "started_at": started})
        try:
            trained = await asyncio.to_thread(fn)
        except ValueError as exc:
            results.append({"task": task, "status": "skipped", "reason": str(exc)})
            continue
        row = await _register(db, trained, simulated, labelled, float(cfg["promotion_min_f1_gain"]))
        results.append(
            {
                "task": task,
                "name": row.name,
                "version": row.version,
                "status": row.status,
                "algorithm": row.algorithm,
                "reason": row.notes,
            }
        )
    await db.commit()
    await bus.set_json(K_ML, {"state": "running", "stage": "predicting", "started_at": started})
    n = await generate_predictions(db, panel=panel, bench=bench, simulated=simulated)
    await bus.set_json(
        K_ML,
        {
            "state": "completed",
            "started_at": started,
            "finished_at": datetime.now(UTC).isoformat(),
            "results": results,
            "predictions": n,
        },
    )
    return results


async def generate_predictions(
    db: AsyncSession,
    *,
    panel: pd.DataFrame | None = None,
    bench: pd.DataFrame | None = None,
    simulated: bool | None = None,
    allow_fetch: bool = True,
) -> int:
    """Run every active (validated) model on the latest feature row of each instrument.

    Models publish independently: if the trend classifier was rejected, the volatility, unusual-activity
    and regime models still produce their outputs, and probability fields stay empty.
    """
    if panel is None:
        frames, bench, sectors, sentiment, simulated = await load_dataset(db, allow_fetch)
        panel = build_panel(frames, bench, sectors, sentiment)
    if simulated is None:
        simulated = await is_simulated_mode(db)
    if panel.empty:
        return 0
    models = {name: await _active(db, name, simulated) for name in MODEL_NAMES}
    trend, mom = models["trend_classifier"], models["momentum_classifier"]
    vol, anom, reg = models["volatility_forecaster"], models["unusual_activity_detector"], models["market_regime"]
    host = trend or vol or anom or mom  # prediction rows reference one model; the trend model when active
    if host is None:
        return 0
    latest = panel.sort_values("date").groupby("instrument_id").tail(1).set_index("instrument_id")
    latest = latest.replace([np.inf, -np.inf], np.nan)
    out: dict[int, dict[str, Any]] = {int(iid): {} for iid in latest.index}

    def rows_for(model: MLModel) -> pd.DataFrame:
        X = latest[model.features]
        return X[X.notna().all(axis=1)]

    if trend is not None:
        X = rows_for(trend)
        if not X.empty:
            for iid, p in zip(X.index, loads(trend.artifact).predict_proba(X), strict=True):
                out[int(iid)].update(
                    prob_down=float(p[0]),
                    prob_neutral=float(p[1]),
                    prob_up=float(p[2]),
                    trend_class=LABELS[int(np.argmax(p))],
                    confidence=float(np.max(p)),
                )
    if mom is not None:
        X = rows_for(mom)
        if not X.empty:
            for iid, p in zip(X.index, loads(mom.artifact).predict_proba(X)[:, 1], strict=True):
                out[int(iid)]["momentum_prob"] = float(p)
    if vol is not None:
        X = rows_for(vol)
        if not X.empty:
            for iid, v in zip(X.index, np.exp(loads(vol.artifact).predict(X)), strict=True):
                out[int(iid)]["predicted_vol"] = float(v)
    if anom is not None:
        X = rows_for(anom)
        if not X.empty:
            art = loads(anom.artifact)
            pct = np.searchsorted(art["train_scores"], -art["model"].score_samples(X)) / len(art["train_scores"]) * 100
            for iid, s in zip(X.index, pct, strict=True):
                out[int(iid)]["anomaly_score"] = float(s)
    regime_label = None
    if reg is not None and bench is not None and not bench.empty:
        art = loads(reg.artifact)
        rf = regime_features(bench)
        if not rf.empty:
            regime_label = art["labels"][int(art["model"].predict(rf.tail(1).to_numpy())[0])]

    out = {iid: v for iid, v in out.items() if v}
    vols = np.sort([v["predicted_vol"] for v in out.values() if "predicted_vol" in v])
    fields = (
        "prob_up",
        "prob_down",
        "prob_neutral",
        "predicted_vol",
        "trend_class",
        "regime",
        "anomaly_score",
        "confidence",
    )
    for iid, v in out.items():
        as_of = latest.loc[iid, "date"].date()
        # one prediction per instrument and date: replace an unevaluated row hosted on a different model
        await db.execute(
            delete(MLPrediction).where(
                MLPrediction.instrument_id == iid,
                MLPrediction.as_of == as_of,
                MLPrediction.model_id != host.id,
                MLPrediction.evaluated_at.is_(None),
            )
        )
        stmt = insert(MLPrediction).values(
            model_id=host.id,
            instrument_id=iid,
            as_of=as_of,
            horizon_days=host.horizon_days,
            prob_up=v.get("prob_up"),
            prob_down=v.get("prob_down"),
            prob_neutral=v.get("prob_neutral"),
            predicted_vol=v.get("predicted_vol"),
            trend_class=v.get("trend_class"),
            regime=regime_label,
            anomaly_score=v.get("anomaly_score"),
            confidence=v.get("confidence"),
            is_simulated=simulated,
        )
        await db.execute(
            stmt.on_conflict_do_update(
                index_elements=["model_id", "instrument_id", "as_of"],
                set_={k: getattr(stmt.excluded, k) for k in fields},
            )
        )
        risk = float(np.searchsorted(vols, v["predicted_vol"]) / len(vols) * 100) if "predicted_vol" in v else None
        scores = {
            "ml_bullish_prob": round(v["prob_up"] * 100, 2) if "prob_up" in v else None,
            "ml_trend": v.get("trend_class"),
            "ml_confidence": round(v["confidence"], 4) if "confidence" in v else None,
            "ml_momentum_score": round(v["momentum_prob"] * 100, 2) if "momentum_prob" in v else None,
            "predicted_volatility": round(v["predicted_vol"] * 100, 2) if "predicted_vol" in v else None,
            "anomaly_score": round(v["anomaly_score"], 2) if "anomaly_score" in v else None,
            "risk_score": round(risk, 1) if risk is not None else None,
            "market_regime": regime_label,
            "ml_as_of": as_of.isoformat(),
        }
        await db.execute(
            update(InstrumentMetrics)
            .where(InstrumentMetrics.instrument_id == iid, InstrumentMetrics.is_simulated.is_(simulated))
            .values(scores=InstrumentMetrics.scores.op("||")(literal(scores, JSONB)))
        )
    await db.commit()
    return len(out)


async def evaluate_realized(db: AsyncSession, band_k: float | None = None) -> int:
    """Fill in realised outcomes for predictions whose horizon has elapsed (uses stored daily candles)."""
    if band_k is None:
        band_k = float((await app_settings.get_section(db, "ml"))["neutral_band_atr"])
    pending = (
        await db.scalars(
            select(MLPrediction)
            .where(MLPrediction.evaluated_at.is_(None))
            .order_by(MLPrediction.instrument_id, MLPrediction.as_of)
            .limit(5000)
        )
    ).all()
    by_inst: dict[int, list[MLPrediction]] = {}
    for p in pending:
        by_inst.setdefault(p.instrument_id, []).append(p)
    done = 0
    for iid, preds in by_inst.items():
        inst = await db.get(Instrument, iid)
        if inst is None:
            continue
        df = await _frame(db, inst, allow_fetch=False)
        if df.empty:
            continue
        df.index = df.index.normalize()
        atr_pct = ind.atr(df) / df["close"]
        dates = list(df.index.date)
        for p in preds:
            if p.as_of not in dates:
                continue
            i = dates.index(p.as_of)
            j = i + p.horizon_days
            if j >= len(df):
                continue
            ret = float(df["close"].iloc[j] / df["close"].iloc[i] - 1)
            band = band_k * float(atr_pct.iloc[i]) * np.sqrt(p.horizon_days)
            p.realized_return = ret
            p.realized_label = "bullish" if ret > band else "bearish" if ret < -band else "neutral"
            p.evaluated_at = datetime.now(UTC)
            done += 1
    await db.commit()
    return done


async def accuracy_summary(db: AsyncSession) -> dict[str, Any]:
    simulated = await is_simulated_mode(db)
    rows = (
        await db.execute(
            select(
                MLPrediction.as_of, MLPrediction.trend_class, MLPrediction.realized_label, MLPrediction.prob_up
            ).where(
                MLPrediction.evaluated_at.is_not(None),
                MLPrediction.is_simulated.is_(simulated),
                MLPrediction.trend_class.is_not(None),
            )
        )
    ).all()
    if not rows:
        return {"evaluated": 0, "hit_rate": None, "directional_hit_rate": None, "weekly": []}
    df = pd.DataFrame(rows, columns=["as_of", "pred", "real", "prob_up"])
    df["hit"] = df["pred"] == df["real"]
    directional = df[df["pred"].isin(["bullish", "bearish"]) & df["real"].isin(["bullish", "bearish"])]
    df["week"] = pd.to_datetime(df["as_of"]).dt.to_period("W").dt.start_time
    weekly = df.groupby("week").agg(n=("hit", "size"), hit_rate=("hit", "mean")).reset_index()
    return {
        "evaluated": len(df),
        "hit_rate": round(float(df["hit"].mean()), 4),
        "directional_hit_rate": round(float((directional["pred"] == directional["real"]).mean()), 4)
        if len(directional)
        else None,
        "weekly": [
            {"week": r.week.date().isoformat(), "n": int(r.n), "hit_rate": round(float(r.hit_rate), 4)}
            for r in weekly.itertuples()
        ],
    }


async def drift_report(db: AsyncSession, lookback_days: int = 60) -> dict[str, Any]:
    simulated = await is_simulated_mode(db)
    active = (
        await db.scalars(select(MLModel).where(MLModel.status == "active", MLModel.is_simulated.is_(simulated)))
    ).all()
    if not active:
        return {"models": []}
    frames, bench, sectors, sentiment, _ = await load_dataset(db, allow_fetch=False)
    panel = build_panel(frames, bench, sectors, sentiment)
    if panel.empty:
        return {"models": []}
    cutoff = panel["date"].max() - pd.Timedelta(days=lookback_days)
    recent = panel[panel["date"] >= cutoff].replace([np.inf, -np.inf], np.nan)
    out = []
    for m in active:
        ref = (m.reference_stats or {}).get("features", {})
        feats = []
        for f, edges in ref.items():
            if f in recent:
                v = psi(edges, recent[f].to_numpy(dtype=float))
                feats.append({"feature": f, "psi": None if np.isnan(v) else round(v, 4), "status": drift_status(v)})
        entry: dict[str, Any] = {
            "name": m.name,
            "version": m.version,
            "features": sorted(feats, key=lambda x: -(x["psi"] or 0)),
        }
        dist = (m.reference_stats or {}).get("class_distribution")
        if dist and m.task == "trend":
            preds = (
                (
                    await db.execute(
                        select(MLPrediction.trend_class).where(
                            MLPrediction.model_id == m.id, MLPrediction.as_of >= cutoff.date()
                        )
                    )
                )
                .scalars()
                .all()
            )
            if preds:
                cur = pd.Series(preds).value_counts(normalize=True)
                ref_p = np.array([dist.get(str(k), 1e-4) for k in (0, 1, 2)])
                cur_p = np.array([cur.get(LABELS[k], 1e-4) for k in (0, 1, 2)])
                ref_p, cur_p = np.clip(ref_p, 1e-4, None), np.clip(cur_p, 1e-4, None)
                pd_psi = float(np.sum((cur_p - ref_p) * np.log(cur_p / ref_p)))
                entry["prediction_drift"] = {
                    "psi": round(pd_psi, 4),
                    "status": drift_status(pd_psi),
                    "current": {LABELS[k]: round(float(cur_p[k]), 4) for k in (0, 1, 2)},
                }
        worst = max((f["psi"] or 0 for f in feats), default=0)
        entry["overall"] = drift_status(worst)
        out.append(entry)
    return {"lookback_days": lookback_days, "models": out}


def feature_lists() -> dict[str, list[str]]:
    return {"trend": FEATURES, "volatility": VOL_FEATURES, "anomaly": ANOMALY_FEATURES}


__all__ = ["accuracy_summary", "drift_report", "evaluate_realized", "generate_predictions", "train_all"]
