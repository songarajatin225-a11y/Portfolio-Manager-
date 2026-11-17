"""FastAPI application factory."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware

from app.api.router import api_router
from app.api.ws import ws_router
from app.core.bus import init_bus
from app.core.config import get_settings
from app.core.errors import install_exception_handlers
from app.core.logging import configure_logging, request_id_var
from app.db.session import dispose_engine, get_sessionmaker

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    bus = init_bus(settings.redis_url, allow_memory=settings.run_worker_in_api or settings.environment == "test")
    from app.services.auth import bootstrap_admin

    async with get_sessionmaker()() as db:
        await bootstrap_admin(db)

    worker_task: asyncio.Task | None = None
    if settings.run_worker_in_api:
        from app.workers.main import run_worker

        worker_task = asyncio.create_task(run_worker(embedded=True), name="embedded-worker")
        logger.info("Embedded worker started inside API process")
    try:
        yield
    finally:
        if worker_task:
            worker_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await worker_task
        await bus.close()
        await dispose_engine()


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)
    app = FastAPI(
        title=f"{settings.app_name} API",
        version="0.1.0",
        description="AI Stock Market Intelligence & Portfolio Management Platform",
        lifespan=lifespan,
        docs_url="/api/docs" if settings.docs_enabled else None,
        redoc_url="/api/redoc" if settings.docs_enabled else None,
        openapi_url="/api/openapi.json" if settings.docs_enabled else None,
    )
    install_exception_handlers(app)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-CSRF-Token", "X-Request-ID"],
    )

    @app.middleware("http")
    async def request_context(request: Request, call_next) -> Response:  # type: ignore[no-untyped-def]
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex
        token = request_id_var.set(rid[:64])
        try:
            response = await call_next(request)
        finally:
            request_id_var.reset(token)
        response.headers["X-Request-ID"] = rid[:64]
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Cache-Control"] = response.headers.get("Cache-Control", "no-store")
        if settings.is_production:
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response

    app.include_router(api_router, prefix="/api/v1")
    app.include_router(ws_router)
    return app


app = create_app()
