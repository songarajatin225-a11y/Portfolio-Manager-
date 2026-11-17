"""Registry of supervised worker loops. Each phase adds its loops here."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

logger = logging.getLogger(__name__)


async def metrics_loop() -> None:
    """Recompute the screener/heatmap metrics snapshot on the configured interval."""
    from app.api.v1.data import run_metrics_refresh
    from app.db.session import get_sessionmaker
    from app.services import app_settings

    await asyncio.sleep(20)  # let the feed connect and seed quotes first
    while True:
        n = await run_metrics_refresh()
        logger.info("Metrics refreshed for %d instruments", n)
        async with get_sessionmaker()() as db:
            minutes = float((await app_settings.get_section(db, "refresh"))["metrics_refresh_minutes"])
        await asyncio.sleep(max(1.0, minutes) * 60)


def registry() -> dict[str, Callable[[], Awaitable[None]]]:
    from app.marketdata.manager import run_feed_manager

    return {
        "feed_manager": run_feed_manager,
        "metrics": metrics_loop,
    }
