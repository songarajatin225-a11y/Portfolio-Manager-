"""Worker process entrypoint.

Runs the long-lived background loops: market feed manager, candle builder, metrics refresh,
alert engine, news ingestion and ML scheduling. Each loop is supervised: if it crashes it is
logged and restarted with backoff so one failing subsystem never takes the others down.

Run standalone with ``python -m app.workers.main`` or embedded in the API with
``RUN_WORKER_IN_API=true``.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from app.core.bus import K_WORKER_HEARTBEAT, get_bus, init_bus
from app.core.config import get_settings
from app.core.logging import configure_logging

logger = logging.getLogger(__name__)

TaskFactory = Callable[[], Awaitable[None]]
_task_state: dict[str, dict] = {}


async def supervise(name: str, factory: TaskFactory, *, max_backoff: float = 60.0) -> None:
    backoff = 1.0
    while True:
        _task_state[name] = {"status": "running", "since": datetime.now(UTC).isoformat()}
        try:
            await factory()
            _task_state[name] = {"status": "stopped", "since": datetime.now(UTC).isoformat()}
            return
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.exception("Worker task %s crashed; restarting in %.0fs", name, backoff)
            _task_state[name] = {
                "status": "restarting",
                "error": type(exc).__name__,
                "since": datetime.now(UTC).isoformat(),
            }
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, max_backoff)


async def heartbeat_loop() -> None:
    bus = get_bus()
    while True:
        await bus.set_json(
            K_WORKER_HEARTBEAT, {"ts": datetime.now(UTC).isoformat(), "tasks": _task_state}, ttl_seconds=120
        )
        await asyncio.sleep(15)


def build_tasks() -> dict[str, TaskFactory]:
    from app.workers import tasks

    return tasks.registry()


async def run_worker(*, embedded: bool = False) -> None:
    tasks = {"heartbeat": heartbeat_loop, **build_tasks()}
    logger.info("Worker starting (%s) with tasks: %s", "embedded" if embedded else "standalone", ", ".join(tasks))
    running = [asyncio.create_task(supervise(name, fn), name=f"worker:{name}") for name, fn in tasks.items()]
    try:
        await asyncio.gather(*running)
    finally:
        for t in running:
            t.cancel()
        for t in running:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await t


async def _main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    init_bus(settings.redis_url, allow_memory=False)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop.set)
    worker = asyncio.create_task(run_worker())
    await stop.wait()
    worker.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await worker
    await get_bus().close()


if __name__ == "__main__":
    asyncio.run(_main())
