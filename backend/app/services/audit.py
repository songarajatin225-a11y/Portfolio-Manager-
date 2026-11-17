from __future__ import annotations

import uuid
from typing import Any

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AuditLog


def client_ip(request: Request | None) -> str | None:
    if request is None or request.client is None:
        return None
    host = request.client.host
    # INET column rejects non-IP hosts such as the test client's "testclient".
    return host if host and (host.count(".") == 3 or ":" in host) else None


async def audit(
    db: AsyncSession,
    *,
    action: str,
    user_id: uuid.UUID | None = None,
    request: Request | None = None,
    entity_type: str | None = None,
    entity_id: str | int | None = None,
    details: dict[str, Any] | None = None,
    commit: bool = False,
) -> None:
    db.add(
        AuditLog(
            user_id=user_id,
            action=action,
            entity_type=entity_type,
            entity_id=str(entity_id) if entity_id is not None else None,
            ip=client_ip(request),
            user_agent=(request.headers.get("user-agent", "")[:400] if request else None),
            details=details or {},
        )
    )
    if commit:
        await db.commit()
