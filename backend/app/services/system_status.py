"""Aggregates the health of every subsystem for the System Status page."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.bus import K_FEED_HEALTH, K_WORKER_HEARTBEAT, get_bus
from app.core.config import get_settings
from app.db.models import MLModel, NewsSource, ProviderConfig


def _svc(name: str, status: str, detail: str = "", **extra: Any) -> dict[str, Any]:
    return {"name": name, "status": status, "detail": detail, **extra}


async def collect_status(db: AsyncSession) -> dict[str, Any]:
    settings = get_settings()
    services: list[dict[str, Any]] = []

    try:
        await db.execute(text("SELECT 1"))
        ts = (await db.execute(text("SELECT extversion FROM pg_extension WHERE extname='timescaledb'"))).scalar()
        services.append(_svc("database", "ok", f"PostgreSQL{' + TimescaleDB ' + ts if ts else ''}"))
    except Exception as exc:  # noqa: BLE001
        services.append(_svc("database", "error", type(exc).__name__))

    bus = get_bus()
    services.append(
        _svc(
            "message_bus",
            "ok" if await bus.ping() else "error",
            "Redis" if settings.redis_url else "in-process memory bus",
        )
    )

    hb = await bus.get_json(K_WORKER_HEARTBEAT)
    if hb:
        age = (datetime.now(UTC) - datetime.fromisoformat(hb["ts"])).total_seconds()
        services.append(
            _svc("worker", "ok" if age < 90 else "error", f"last heartbeat {int(age)}s ago", tasks=hb.get("tasks", {}))
        )
    else:
        services.append(_svc("worker", "error", "no heartbeat - worker process is not running"))

    provider = await db.scalar(select(ProviderConfig).where(ProviderConfig.is_active.is_(True)))
    health = await bus.get_json(K_FEED_HEALTH) or {}
    if provider is None:
        services.append(_svc("market_data", "not_configured", "No active market data provider"))
    else:
        state = health.get("state", "unknown")
        status = {"connected": "ok", "polling": "ok", "degraded": "warning", "market_closed": "ok"}.get(state, "error")
        services.append(
            _svc("market_data", status, f"{provider.name} ({provider.provider_type}): {state}", health=health)
        )

    ai_ok = bool(settings.anthropic_api_key)
    services.append(
        _svc(
            "ai",
            "ok" if ai_ok else "not_configured",
            "Claude API key configured" if ai_ok else "ANTHROPIC_API_KEY is not set",
        )
    )

    active_models = await db.scalar(select(func.count()).select_from(MLModel).where(MLModel.status == "active"))
    services.append(
        _svc(
            "ml",
            "ok" if active_models else "not_configured",
            f"{active_models} active model(s)" if active_models else "No trained models yet",
        )
    )

    sources = (await db.scalars(select(NewsSource).where(NewsSource.is_active.is_(True)))).all()
    if not sources:
        services.append(_svc("news", "not_configured", "No news feeds configured"))
    else:
        failing = [s.name for s in sources if s.last_error]
        services.append(
            _svc(
                "news",
                "warning" if failing else "ok",
                f"{len(sources)} feed(s)" + (f"; failing: {', '.join(failing)}" if failing else ""),
            )
        )

    overall = "ok"
    if any(s["status"] == "error" for s in services):
        overall = "error"
    elif any(s["status"] == "warning" for s in services):
        overall = "warning"
    return {
        "overall": overall,
        "environment": settings.environment,
        "simulated_feed_allowed": settings.allow_simulated_feed,
        "checked_at": datetime.now(UTC).isoformat(),
        "services": services,
    }
