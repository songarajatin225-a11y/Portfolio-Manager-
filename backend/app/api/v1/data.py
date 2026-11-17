"""Market overview statistics, admin data imports and the metrics refresh trigger."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, File, Request, UploadFile
from fastapi.responses import PlainTextResponse

from app.api.deps import DB, AdminUser, CurrentUser
from app.core.bus import get_bus
from app.core.errors import AppError, ConflictError, NotFoundError
from app.db.session import get_sessionmaker
from app.services import market_stats
from app.services.analysis import refresh_metrics
from app.services.audit import audit
from app.services.data_import import DATASETS, TEMPLATES, import_csv

logger = logging.getLogger(__name__)
router = APIRouter(tags=["market data"])
MAX_UPLOAD = 20 * 1024 * 1024
K_METRICS = "mi:metrics_refresh"


@router.get("/market/overview")
async def overview(_: CurrentUser, db: DB) -> dict[str, Any]:
    data = await market_stats.overview(db)
    data["metrics_refresh"] = await get_bus().get_json(K_METRICS)
    return data


@router.get("/market/heatmap")
async def heatmap(_: CurrentUser, db: DB) -> dict[str, Any]:
    return await market_stats.heatmap(db)


@router.get("/admin/data-import/templates/{dataset}", response_class=PlainTextResponse)
async def template(dataset: str, _: AdminUser) -> str:
    if dataset not in TEMPLATES:
        raise NotFoundError("Unknown dataset")
    return TEMPLATES[dataset]


@router.post("/admin/data-import/{dataset}")
async def data_import(
    dataset: str, request: Request, admin: AdminUser, db: DB, file: UploadFile = File(...)
) -> dict[str, Any]:
    if dataset not in DATASETS:
        raise NotFoundError(f"Unknown dataset. Choose one of: {', '.join(DATASETS)}")
    content = await file.read(MAX_UPLOAD + 1)
    if len(content) > MAX_UPLOAD:
        raise AppError("File too large (max 20 MB)", status_code=413)
    result = await import_csv(db, dataset, content, file.filename or "upload.csv", admin.id)
    await audit(
        db,
        action="data.import",
        user_id=admin.id,
        request=request,
        entity_type="dataset",
        entity_id=dataset,
        details={"file": file.filename, "imported": result["imported"], "rejected": result["rejected"]},
        commit=True,
    )
    return result


async def run_metrics_refresh(allow_fetch: bool = True) -> int:
    bus = get_bus()
    started = datetime.now(UTC).isoformat()
    await bus.set_json(K_METRICS, {"state": "running", "started_at": started})
    try:
        async with get_sessionmaker()() as db:
            n = await refresh_metrics(db, allow_fetch=allow_fetch)
        await bus.set_json(
            K_METRICS,
            {
                "state": "completed",
                "started_at": started,
                "instruments": n,
                "finished_at": datetime.now(UTC).isoformat(),
            },
        )
        return n
    except Exception as exc:  # noqa: BLE001
        logger.exception("Metrics refresh failed")
        await bus.set_json(
            K_METRICS,
            {
                "state": "failed",
                "started_at": started,
                "error": str(exc)[:300],
                "finished_at": datetime.now(UTC).isoformat(),
            },
        )
        return 0


_tasks: set[asyncio.Task] = set()


@router.post("/admin/metrics/refresh", status_code=202)
async def trigger_refresh(request: Request, admin: AdminUser, db: DB) -> dict[str, str]:
    state = await get_bus().get_json(K_METRICS)
    if state and state.get("state") == "running":
        raise ConflictError("A metrics refresh is already running")
    await audit(db, action="metrics.refresh", user_id=admin.id, request=request, commit=True)
    task = asyncio.create_task(run_metrics_refresh())
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return {"state": "running"}


@router.get("/admin/metrics/status")
async def refresh_status(_: AdminUser) -> dict[str, Any]:
    return await get_bus().get_json(K_METRICS) or {"state": "idle"}
