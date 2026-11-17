from __future__ import annotations

import uuid
from collections.abc import Callable, Coroutine
from typing import Annotated, Any

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AuthError, PermissionDenied
from app.core.security import (
    ACCESS_COOKIE,
    CSRF_COOKIE,
    CSRF_HEADER,
    TokenError,
    constant_time_equals,
    decode_token,
)
from app.db.models import Role, User
from app.db.session import get_db

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}

DB = Annotated[AsyncSession, Depends(get_db)]


async def get_current_user(request: Request, db: DB) -> User:
    auth_header = request.headers.get("authorization", "")
    via_cookie = False
    if auth_header.lower().startswith("bearer "):
        token = auth_header[7:].strip()
    else:
        token = request.cookies.get(ACCESS_COOKIE, "")
        via_cookie = True
    if not token:
        raise AuthError("Not authenticated")
    try:
        payload = decode_token(token, "access")
    except TokenError as exc:
        raise AuthError("Session expired - please sign in again", code="token_expired") from exc

    # Cookie-authenticated state-changing requests must carry the double-submit CSRF token.
    if via_cookie and request.method not in SAFE_METHODS:
        cookie_token = request.cookies.get(CSRF_COOKIE, "")
        header_token = request.headers.get(CSRF_HEADER, "")
        if not cookie_token or not header_token or not constant_time_equals(cookie_token, header_token):
            raise PermissionDenied("CSRF token missing or invalid", code="csrf_failed")

    try:
        user_id = uuid.UUID(payload["sub"])
    except ValueError as exc:
        raise AuthError("Invalid token subject") from exc
    user = await db.get(User, user_id)
    if user is None or not user.is_active:
        raise AuthError("Account not found or disabled")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def require_roles(*roles: str) -> Callable[..., Coroutine[Any, Any, User]]:
    allowed = set(roles)

    async def _dep(user: CurrentUser) -> User:
        if user.role not in allowed:
            raise PermissionDenied("You do not have permission to perform this action")
        return user

    return _dep


AdminUser = Annotated[User, Depends(require_roles(Role.ADMIN))]
# Viewers are read-only; analysts and admins can create and modify their own data.
WriterUser = Annotated[User, Depends(require_roles(Role.ADMIN, Role.ANALYST))]
