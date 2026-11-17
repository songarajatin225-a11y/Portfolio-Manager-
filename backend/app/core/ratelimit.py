"""Fixed-window rate limiting backed by the bus key-value store."""

from __future__ import annotations

from app.core.bus import get_bus
from app.core.errors import RateLimited


async def enforce_rate_limit(key: str, limit: int, window_seconds: int = 60) -> None:
    count = await get_bus().incr_window(f"mi:rl:{key}", window_seconds)
    if count > limit:
        raise RateLimited("Too many requests - please wait and try again")
