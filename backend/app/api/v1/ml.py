from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Query, Request
from sqlalchemy import func, select

from app.api.deps import DB, AdminUser, CurrentUser
from app.core.bus import CH_COMMANDS, K_WORKER_HEARTBEAT, get_bus
from app.core.errors import AppError, ConflictError, NotFoundError
from app.db.models import Instrument, InstrumentMetrics, MLModel, MLPrediction
from app.db.session import get_sessionmaker
from app.ml import service as ml_service
from app.services import instruments as instrument_service
from app.services.audit import audit
from app.services.market_data import is_simulated_mode

logger = logging.getLogger(__name__)
router = APIRouter(tags=["ml"])
DISCLAIMER = (
    "Model outputs are statistical probabilities estimated from historical patterns. They are not "
    "forecasts of certainty; markets can and do behave differently from any model."
)


def _model(m: MLModel) -> dict[str, Any]:
    return {
        "id": m.id,
        "name": m.name,
        "task": m.task,
        "algorithm": m.algorithm,
        "version": m.version,
        "status": m.status,
        "horizon_days": m.horizon_days,
        "features": m.features,
        "params": m.params,
        "train_start": m.train_start,
        "train_end": m.train_end,
        "test_start": m.test_start,
        "test_end": m.test_end,
        "metrics": m.metrics,
        "calibration": m.calibration,
        "notes": m.notes,
        "is_simulated": m.is_simulated,
        "created_at": m.created_at,
        "promoted_at": m.promoted_at,
        "data_hash": m.data_hash,
    }


@router.get("/ml/models")
async def list_models(_: CurrentUser, db: DB) -> list[dict[str, Any]]:
    sim = await is_simulated_mode(db)
    rows = (
        await db.scalars(
            select(MLModel).where(MLModel.is_simulated.is_(sim)).order_by(MLModel.name, MLModel.version.desc())
        )
    ).all()
    return [_model(m) for m in rows]


@router.get("/ml/models/{model_id}")
async def get_model(model_id: int, _: CurrentUser, db: DB) -> dict[str, Any]:
    m = await db.get(MLModel, model_id)
    if m is None:
        raise NotFoundError("Model not found")
    return _model(m)


async def _train_job(fast: bool) -> None:
    try:
        async with get_sessionmaker()() as db:
            await ml_service.train_all(db, fast=fast)
    except Exception as exc:  # noqa: BLE001
        logger.exception("ML training failed")
        await get_bus().set_json(
            ml_service.K_ML, {"state": "failed", "error": str(exc)[:500], "finished_at": datetime.now(UTC).isoformat()}
        )


_jobs: set[asyncio.Task] = set()


@router.post("/ml/train", status_code=202)
async def train(request: Request, admin: AdminUser, db: DB, fast: bool = Query(False)) -> dict[str, str]:
    state = await get_bus().get_json(ml_service.K_ML)
    if state and state.get("state") == "running":
        raise ConflictError("Training is already running")
    await audit(db, action="ml.train", user_id=admin.id, request=request, details={"fast": fast}, commit=True)
    bus = get_bus()
    await bus.set_json(
        ml_service.K_ML, {"state": "running", "stage": "queued", "started_at": datetime.now(UTC).isoformat()}
    )
    # Training is CPU-heavy: hand it to the worker when one is running, otherwise run it here.
    if await bus.get_json(K_WORKER_HEARTBEAT):
        await bus.publish(CH_COMMANDS, {"cmd": "ml_train", "fast": fast})
        return {"state": "running", "runner": "worker"}
    task = asyncio.create_task(_train_job(fast))
    _jobs.add(task)
    task.add_done_callback(_jobs.discard)
    return {"state": "running", "runner": "api"}


@router.get("/ml/status")
async def status(_: CurrentUser) -> dict[str, Any]:
    return await get_bus().get_json(ml_service.K_ML) or {"state": "idle"}


@router.post("/ml/models/{model_id}/promote")
async def promote(model_id: int, request: Request, admin: AdminUser, db: DB) -> dict[str, Any]:
    m = await db.get(MLModel, model_id)
    if m is None:
        raise NotFoundError("Model not found")
    if m.task in ("trend", "momentum") and not m.metrics.get("beats_baseline"):
        raise AppError("This model did not beat the baseline on its test period and cannot be promoted.")
    if m.task == "volatility" and not m.metrics.get("beats_baseline"):
        raise AppError("This model did not beat the naive volatility forecast and cannot be promoted.")
    current = await db.scalar(
        select(MLModel).where(
            MLModel.name == m.name, MLModel.status == "active", MLModel.is_simulated.is_(m.is_simulated)
        )
    )
    if current is not None and current.id != m.id:
        current.status = "retired"
    m.status, m.promoted_at = "active", datetime.now(UTC)
    m.notes = f"manually promoted by {admin.email}"
    await audit(db, action="ml.promote", user_id=admin.id, request=request, entity_type="ml_model", entity_id=m.id)
    await db.commit()
    return _model(m)


@router.post("/ml/predict", status_code=202)
async def predict(request: Request, admin: AdminUser, db: DB) -> dict[str, Any]:
    await audit(db, action="ml.predict", user_id=admin.id, request=request, commit=True)
    n = await ml_service.generate_predictions(db)
    evaluated = await ml_service.evaluate_realized(db)
    return {"predictions": n, "evaluated": evaluated}


@router.get("/ml/predictions")
async def predictions(
    _: CurrentUser,
    db: DB,
    limit: int = Query(200, ge=1, le=1000),
    trend: str | None = Query(None, pattern="^(bullish|bearish|neutral)$"),
) -> dict[str, Any]:
    sim = await is_simulated_mode(db)
    latest = (
        select(MLPrediction.instrument_id, func.max(MLPrediction.as_of).label("as_of"))
        .where(MLPrediction.is_simulated.is_(sim))
        .group_by(MLPrediction.instrument_id)
        .subquery()
    )
    stmt = (
        select(MLPrediction, Instrument, MLModel)
        .join(latest, (latest.c.instrument_id == MLPrediction.instrument_id) & (latest.c.as_of == MLPrediction.as_of))
        .join(Instrument, Instrument.id == MLPrediction.instrument_id)
        .join(MLModel, MLModel.id == MLPrediction.model_id)
        .where(MLModel.status == "active")
    )
    if trend:
        stmt = stmt.where(MLPrediction.trend_class == trend)
    rows = (
        await db.execute(stmt.order_by(MLPrediction.prob_up.desc().nulls_last(), Instrument.symbol).limit(limit))
    ).all()
    scores = {
        m.instrument_id: m.scores
        for m in (await db.scalars(select(InstrumentMetrics).where(InstrumentMetrics.is_simulated.is_(sim)))).all()
    }
    return {
        "disclaimer": DISCLAIMER,
        "data_status": "SIMULATED" if sim else "HISTORICAL",
        "items": [
            {
                "symbol": i.symbol,
                "name": i.name,
                "sector": i.sector,
                "as_of": p.as_of,
                "horizon_days": p.horizon_days,
                "prob_up": p.prob_up,
                "prob_neutral": p.prob_neutral,
                "prob_down": p.prob_down,
                "trend_class": p.trend_class,
                "confidence": p.confidence,
                "predicted_vol": p.predicted_vol,
                "anomaly_score": p.anomaly_score,
                "regime": p.regime,
                "momentum_score": (scores.get(i.id) or {}).get("ml_momentum_score"),
                "risk_score": (scores.get(i.id) or {}).get("risk_score"),
                "model": f"{m.name} v{m.version}",
                "created_at": p.created_at,
            }
            for p, i, m in rows
        ],
    }


@router.get("/stocks/{symbol}/prediction")
async def stock_prediction(symbol: str, _: CurrentUser, db: DB) -> dict[str, Any]:
    inst = await instrument_service.resolve_symbol(db, symbol)
    rows = (
        await db.execute(
            select(MLPrediction, MLModel)
            .join(MLModel, MLModel.id == MLPrediction.model_id)
            .where(MLPrediction.instrument_id == inst.id)
            .order_by(MLPrediction.as_of.desc(), MLPrediction.created_at.desc())
            .limit(90)
        )
    ).all()
    if not rows:
        return {"symbol": inst.symbol, "latest": None, "history": [], "disclaimer": DISCLAIMER}
    p, m = rows[0]
    hist = [
        {
            "as_of": r.as_of,
            "prob_up": r.prob_up,
            "trend_class": r.trend_class,
            "realized_label": r.realized_label,
            "realized_return": r.realized_return,
        }
        for r, _ in reversed(rows)
    ]
    evaluated = [h for h in hist if h["realized_label"]]
    return {
        "symbol": inst.symbol,
        "disclaimer": DISCLAIMER,
        "data_status": "SIMULATED" if p.is_simulated else "HISTORICAL",
        "latest": {
            "as_of": p.as_of,
            "horizon_days": p.horizon_days,
            "prob_up": p.prob_up,
            "prob_neutral": p.prob_neutral,
            "prob_down": p.prob_down,
            "trend_class": p.trend_class,
            "confidence": p.confidence,
            "predicted_vol": p.predicted_vol,
            "anomaly_score": p.anomaly_score,
            "regime": p.regime,
            "created_at": p.created_at,
        },
        "model": {
            "id": m.id,
            "name": m.name,
            "version": m.version,
            "algorithm": m.algorithm,
            "status": m.status,
            "test": m.metrics.get("test", {}),
            "baseline": m.metrics.get("baseline", {}),
        },
        "history": hist,
        "instrument_hit_rate": round(
            sum(h["trend_class"] == h["realized_label"] for h in evaluated) / len(evaluated), 4
        )
        if evaluated
        else None,
    }


@router.get("/ml/accuracy")
async def accuracy(_: CurrentUser, db: DB) -> dict[str, Any]:
    return await ml_service.accuracy_summary(db)


@router.get("/ml/drift")
async def drift(_: CurrentUser, db: DB) -> dict[str, Any]:
    return await ml_service.drift_report(db)


@router.get("/ml/sector-strength")
async def sector_strength(_: CurrentUser, db: DB) -> list[dict[str, Any]]:
    """Sector ranking by average relative strength, 1-month momentum and ML bullish probability."""
    sim = await is_simulated_mode(db)
    rows = (
        await db.execute(
            select(Instrument.sector, InstrumentMetrics)
            .join(InstrumentMetrics, InstrumentMetrics.instrument_id == Instrument.id)
            .where(InstrumentMetrics.is_simulated.is_(sim), Instrument.is_index.is_(False))
        )
    ).all()
    groups: dict[str, list[InstrumentMetrics]] = {}
    for sector, m in rows:
        groups.setdefault(sector or "Unclassified", []).append(m)

    def avg(vals: list[float | None]) -> float | None:
        v = [x for x in vals if x is not None]
        return round(sum(v) / len(v), 3) if v else None

    out = [
        {
            "sector": s,
            "count": len(ms),
            "avg_rel_strength": avg([m.rel_strength for m in ms]),
            "avg_ret_1m": avg([m.ret_1m for m in ms]),
            "avg_ret_3m": avg([m.ret_3m for m in ms]),
            "avg_ml_bullish_prob": avg([(m.scores or {}).get("ml_bullish_prob") for m in ms]),
            "pct_above_ema50": avg([1.0 if (m.flags or {}).get("above_ema50") else 0.0 for m in ms]),
        }
        for s, ms in groups.items()
    ]
    for key in ("avg_rel_strength", "avg_ret_1m", "avg_ml_bullish_prob"):
        ranked = sorted([o for o in out if o[key] is not None], key=lambda o: o[key])
        for i, o in enumerate(ranked):
            o.setdefault("_ranks", []).append((i + 1) / len(ranked))
    for o in out:
        ranks = o.pop("_ranks", [])
        o["strength_score"] = round(sum(ranks) / len(ranks) * 100, 1) if ranks else None
    return sorted(out, key=lambda o: -(o["strength_score"] or 0))
