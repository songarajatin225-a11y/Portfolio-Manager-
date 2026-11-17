"""Registry of supervised worker loops."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

logger = logging.getLogger(__name__)


async def _setting(section: str, key: str) -> float:
    from app.db.session import get_sessionmaker
    from app.services import app_settings

    async with get_sessionmaker()() as db:
        return float((await app_settings.get_section(db, section))[key])


async def metrics_loop() -> None:
    """Recompute the screener/heatmap metrics snapshot on the configured interval."""
    from app.api.v1.data import run_metrics_refresh

    await asyncio.sleep(20)  # let the feed connect and seed quotes first
    while True:
        n = await run_metrics_refresh()
        logger.info("Metrics refreshed for %d instruments", n)
        await asyncio.sleep(max(1.0, await _setting("refresh", "metrics_refresh_minutes")) * 60)


async def news_loop() -> None:
    from app.db.session import get_sessionmaker
    from app.news.ingest import ingest_all

    await asyncio.sleep(30)
    while True:
        async with get_sessionmaker()() as db:
            res = await ingest_all(db)
        if res["sources"]:
            logger.info("News ingestion: %d new articles from %d sources", res["added"], res["sources"])
        await asyncio.sleep(max(1.0, await _setting("refresh", "news_refresh_minutes")) * 60)


async def ml_loop() -> None:
    """Training on request (admin) or weekly when due; predictions and outcome evaluation every six hours.

    Retraining only replaces an active model after validation (see app.ml.service._register).
    """
    from sqlalchemy import func, select

    from app.core.bus import CH_COMMANDS, get_bus
    from app.db.models import MLModel
    from app.db.session import get_sessionmaker
    from app.ml import service as ml_service
    from app.services import app_settings
    from app.services.market_data import is_simulated_mode

    trigger = asyncio.Event()
    request = {"fast": False}

    async def listen() -> None:
        async for _, msg in get_bus().subscribe(CH_COMMANDS):
            if msg.get("cmd") == "ml_train":
                request["fast"] = bool(msg.get("fast"))
                trigger.set()

    listener = asyncio.create_task(listen())
    try:
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(trigger.wait(), timeout=120)
        while True:
            manual = trigger.is_set()
            trigger.clear()
            try:
                async with get_sessionmaker()() as db:
                    cfg = await app_settings.get_section(db, "ml")
                    sim = await is_simulated_mode(db)
                    last = await db.scalar(select(func.max(MLModel.created_at)).where(MLModel.is_simulated.is_(sim)))
                    due = last is None or datetime.now(UTC) - last > timedelta(days=7)
                    if manual or (cfg.get("auto_retrain_weekly", True) and due):
                        results = await ml_service.train_all(db, fast=request["fast"])
                        logger.info("ML training finished: %s", results)
                    else:
                        n = await ml_service.generate_predictions(db)
                        e = await ml_service.evaluate_realized(db)
                        logger.info("ML predictions refreshed (%d) and %d outcomes evaluated", n, e)
            except ValueError as exc:
                logger.info("ML cycle skipped: %s", exc)
                await get_bus().set_json(
                    ml_service.K_ML,
                    {"state": "failed", "error": str(exc), "finished_at": datetime.now(UTC).isoformat()},
                )
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(trigger.wait(), timeout=6 * 3600)
    finally:
        listener.cancel()


def registry() -> dict[str, Callable[[], Awaitable[None]]]:
    from app.marketdata.manager import run_feed_manager

    return {
        "feed_manager": run_feed_manager,
        "metrics": metrics_loop,
        "news": news_loop,
        "ml": ml_loop,
    }
