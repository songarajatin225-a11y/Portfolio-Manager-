"""Registry of supervised worker loops. Each phase adds its loops here."""

from __future__ import annotations

from collections.abc import Awaitable, Callable


def registry() -> dict[str, Callable[[], Awaitable[None]]]:
    from app.marketdata.manager import run_feed_manager

    return {
        "feed_manager": run_feed_manager,
    }
