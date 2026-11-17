"""Message bus and small key-value cache.

Two implementations share one interface:

* ``RedisBus`` - production. Pub/sub between the worker and API processes, latest-quote cache,
  rate-limit counters.
* ``MemoryBus`` - single-process mode (``RUN_WORKER_IN_API=true`` without Redis) and tests.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from collections import defaultdict
from collections.abc import AsyncIterator
from typing import Any, Protocol

logger = logging.getLogger(__name__)

# Channel names
CH_TICKS = "mi:ticks"
CH_FEED_STATUS = "mi:feed_status"
CH_COMMANDS = "mi:commands"
CH_NOTIFICATIONS = "mi:notifications"

# Key prefixes
K_QUOTE = "mi:quote:"  # + instrument_id
K_FEED_HEALTH = "mi:feed_health"
K_WORKER_HEARTBEAT = "mi:worker_heartbeat"


class Bus(Protocol):
    async def publish(self, channel: str, message: dict[str, Any]) -> None: ...
    def subscribe(self, *channels: str) -> AsyncIterator[tuple[str, dict[str, Any]]]: ...
    async def set_json(self, key: str, value: Any, ttl_seconds: int | None = None) -> None: ...
    async def get_json(self, key: str) -> Any | None: ...
    async def mget_json(self, keys: list[str]) -> list[Any | None]: ...
    async def incr_window(self, key: str, window_seconds: int) -> int: ...
    async def ping(self) -> bool: ...
    async def close(self) -> None: ...


class MemoryBus:
    def __init__(self) -> None:
        self._subscribers: dict[str, set[asyncio.Queue]] = defaultdict(set)
        self._store: dict[str, tuple[Any, float | None]] = {}

    async def publish(self, channel: str, message: dict[str, Any]) -> None:
        for queue in list(self._subscribers.get(channel, ())):
            if queue.qsize() > 10_000:  # slow consumer protection
                continue
            queue.put_nowait((channel, message))

    async def subscribe(self, *channels: str) -> AsyncIterator[tuple[str, dict[str, Any]]]:
        queue: asyncio.Queue = asyncio.Queue()
        for ch in channels:
            self._subscribers[ch].add(queue)
        try:
            while True:
                yield await queue.get()
        finally:
            for ch in channels:
                self._subscribers[ch].discard(queue)

    def _alive(self, key: str) -> Any | None:
        item = self._store.get(key)
        if item is None:
            return None
        value, expires = item
        if expires is not None and expires < time.monotonic():
            self._store.pop(key, None)
            return None
        return value

    async def set_json(self, key: str, value: Any, ttl_seconds: int | None = None) -> None:
        self._store[key] = (value, time.monotonic() + ttl_seconds if ttl_seconds else None)

    async def get_json(self, key: str) -> Any | None:
        return self._alive(key)

    async def mget_json(self, keys: list[str]) -> list[Any | None]:
        return [self._alive(k) for k in keys]

    async def incr_window(self, key: str, window_seconds: int) -> int:
        bucket = f"{key}:{int(time.time() // window_seconds)}"
        current = (self._alive(bucket) or 0) + 1
        self._store[bucket] = (current, time.monotonic() + window_seconds)
        return current

    async def ping(self) -> bool:
        return True

    async def close(self) -> None:
        self._subscribers.clear()


class RedisBus:
    def __init__(self, url: str) -> None:
        from redis import asyncio as aioredis

        self._redis = aioredis.from_url(url, decode_responses=True, health_check_interval=30)

    async def publish(self, channel: str, message: dict[str, Any]) -> None:
        await self._redis.publish(channel, json.dumps(message, default=str))

    async def subscribe(self, *channels: str) -> AsyncIterator[tuple[str, dict[str, Any]]]:
        pubsub = self._redis.pubsub()
        await pubsub.subscribe(*channels)
        try:
            while True:
                msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                if msg is None:
                    continue
                try:
                    yield msg["channel"], json.loads(msg["data"])
                except (ValueError, TypeError):
                    logger.warning("Dropping malformed bus message on %s", msg.get("channel"))
        finally:
            with contextlib.suppress(Exception):
                await pubsub.unsubscribe(*channels)
                await pubsub.aclose()

    async def set_json(self, key: str, value: Any, ttl_seconds: int | None = None) -> None:
        await self._redis.set(key, json.dumps(value, default=str), ex=ttl_seconds)

    async def get_json(self, key: str) -> Any | None:
        raw = await self._redis.get(key)
        return json.loads(raw) if raw is not None else None

    async def mget_json(self, keys: list[str]) -> list[Any | None]:
        if not keys:
            return []
        raws = await self._redis.mget(keys)
        return [json.loads(r) if r is not None else None for r in raws]

    async def incr_window(self, key: str, window_seconds: int) -> int:
        bucket = f"{key}:{int(time.time() // window_seconds)}"
        async with self._redis.pipeline(transaction=True) as pipe:
            pipe.incr(bucket)
            pipe.expire(bucket, window_seconds)
            count, _ = await pipe.execute()
        return int(count)

    async def ping(self) -> bool:
        try:
            return bool(await self._redis.ping())
        except Exception:  # noqa: BLE001
            return False

    async def close(self) -> None:
        await self._redis.aclose()


_bus: Bus | None = None


def init_bus(redis_url: str | None, *, allow_memory: bool) -> Bus:
    global _bus
    if redis_url:
        _bus = RedisBus(redis_url)
    elif allow_memory:
        logger.warning("REDIS_URL not set - using in-process memory bus (single-process mode only)")
        _bus = MemoryBus()
    else:
        raise RuntimeError("REDIS_URL is required unless the worker runs inside the API process")
    return _bus


def set_bus(bus: Bus) -> None:
    global _bus
    _bus = bus


def get_bus() -> Bus:
    if _bus is None:
        raise RuntimeError("Bus not initialised")
    return _bus
