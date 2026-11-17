from __future__ import annotations

import time
from datetime import UTC, datetime

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.api.deps import DB, CurrentUser
from app.services.system_status import collect_status

router = APIRouter(tags=["system"])
_started = time.time()


@router.get("/health", summary="Liveness probe")
async def health() -> dict:
    return {"status": "ok", "time": datetime.now(UTC).isoformat(), "uptime_seconds": round(time.time() - _started)}


@router.get("/health/ready", summary="Readiness probe (database + message bus)")
async def ready(db: DB) -> JSONResponse:
    from app.core.bus import get_bus

    checks: dict[str, str] = {}
    try:
        await db.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception:  # noqa: BLE001
        checks["database"] = "error"
    checks["bus"] = "ok" if await get_bus().ping() else "error"
    ok = all(v == "ok" for v in checks.values())
    return JSONResponse(
        status_code=200 if ok else 503, content={"status": "ok" if ok else "degraded", "checks": checks}
    )


@router.get("/system/status", summary="Per-service status for the System Status page")
async def system_status(_: CurrentUser, db: DB) -> dict:
    return await collect_status(db)
