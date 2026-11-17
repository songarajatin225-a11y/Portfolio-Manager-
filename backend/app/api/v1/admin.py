from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.api.deps import DB, AdminUser
from app.core.config import get_settings
from app.core.errors import NotFoundError
from app.db.models import AuditLog, User
from app.services import app_settings
from app.services.audit import audit

router = APIRouter(prefix="/admin", tags=["admin"])


def _infra_summary() -> dict[str, Any]:
    s = get_settings()
    db = urlparse(s.database_url.replace("+asyncpg", ""))
    return {
        "environment": s.environment,
        "database": {
            "host": db.hostname,
            "port": db.port,
            "name": (db.path or "").lstrip("/"),
            "configured_via": "DATABASE_URL environment variable",
        },
        "redis_configured": bool(s.redis_url),
        "worker_mode": "embedded in API" if s.run_worker_in_api else "separate process",
        "simulated_feed_allowed": s.allow_simulated_feed,
        "anthropic_key_configured": bool(s.anthropic_api_key),
        "public_registration": s.allow_public_registration,
    }


@router.get("/settings")
async def get_settings_all(_: AdminUser, db: DB) -> dict[str, Any]:
    return {
        "sections": await app_settings.get_all(db),
        "secrets": await app_settings.secrets_status(db),
        "infrastructure": _infra_summary(),
    }


class SectionUpdate(BaseModel):
    value: dict[str, Any]


@router.put("/settings/{section}")
async def update_section(section: str, body: SectionUpdate, request: Request, admin: AdminUser, db: DB) -> dict:
    if section not in app_settings.DEFAULTS:
        raise NotFoundError(f"Unknown settings section '{section}'")
    updated = await app_settings.update_section(db, section, body.value, admin.id)
    await audit(
        db,
        action="settings.update",
        user_id=admin.id,
        request=request,
        entity_type="settings",
        entity_id=section,
        details={"keys": sorted(body.value.keys())},
    )
    await db.commit()
    return updated


class SecretsUpdate(BaseModel):
    smtp_password: str | None = Field(default=None, max_length=500)
    telegram_bot_token: str | None = Field(default=None, max_length=500)


@router.put("/settings-secrets")
async def update_secrets(body: SecretsUpdate, request: Request, admin: AdminUser, db: DB) -> dict[str, bool]:
    values = body.model_dump(exclude_none=True)
    await app_settings.set_secrets(db, values, admin.id)
    await audit(
        db,
        action="settings.secrets_update",
        user_id=admin.id,
        request=request,
        entity_type="settings",
        entity_id="secrets",
        details={"fields": sorted(values)},
    )
    await db.commit()
    return await app_settings.secrets_status(db)


@router.get("/audit-logs")
async def audit_logs(
    _: AdminUser,
    db: DB,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    action: str | None = Query(None, max_length=100),
) -> dict[str, Any]:
    q = select(AuditLog, User.email).outerjoin(User, User.id == AuditLog.user_id)
    count_q = select(func.count()).select_from(AuditLog)
    if action:
        q = q.where(AuditLog.action.ilike(f"{action}%"))
        count_q = count_q.where(AuditLog.action.ilike(f"{action}%"))
    rows = (await db.execute(q.order_by(AuditLog.created_at.desc()).limit(limit).offset(offset))).all()
    return {
        "total": await db.scalar(count_q),
        "items": [
            {
                "id": log.id,
                "created_at": log.created_at,
                "user_email": email,
                "action": log.action,
                "entity_type": log.entity_type,
                "entity_id": log.entity_id,
                "ip": str(log.ip) if log.ip else None,
                "details": log.details,
            }
            for log, email in rows
        ],
    }
