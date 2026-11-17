"""Authentication service: users, sessions and refresh-token rotation with reuse detection."""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta

from fastapi import Request, Response
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import AppError, AuthError, ConflictError
from app.core.security import (
    ACCESS_COOKIE,
    CSRF_COOKIE,
    REFRESH_COOKIE,
    create_access_token,
    hash_password,
    hash_token,
    new_csrf_token,
    new_refresh_token,
    password_needs_rehash,
    validate_password_strength,
    verify_password,
)
from app.db.models import RefreshToken, Role, User
from app.services.audit import audit

logger = logging.getLogger(__name__)
REFRESH_PATH = "/api/v1/auth"

# Dummy hash so failed lookups take as long as real verifications (prevents user enumeration).
_DUMMY_HASH = hash_password("dummy-password-for-timing-Aa1")


async def user_count(db: AsyncSession) -> int:
    return int((await db.execute(select(func.count()).select_from(User))).scalar_one())


async def create_user(db: AsyncSession, *, email: str, password: str, full_name: str, role: str) -> User:
    problems = validate_password_strength(password)
    if problems:
        raise AppError("; ".join(problems), code="weak_password", status_code=422)
    email = email.strip().lower()
    existing = await db.scalar(select(User).where(User.email == email))
    if existing:
        raise ConflictError("A user with this email already exists")
    user = User(email=email, password_hash=hash_password(password), full_name=full_name.strip(), role=role)
    db.add(user)
    await db.flush()
    return user


async def authenticate(db: AsyncSession, email: str, password: str) -> User | None:
    user = await db.scalar(select(User).where(User.email == email.strip().lower()))
    if user is None:
        verify_password(password, _DUMMY_HASH)
        return None
    if not verify_password(password, user.password_hash):
        return None
    if password_needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)
    return user


async def issue_session(db: AsyncSession, response: Response, user: User, request: Request,
                        family_id: uuid.UUID | None = None) -> str:
    """Create access + refresh tokens and set them as httpOnly cookies. Returns the CSRF token."""
    settings = get_settings()
    raw_refresh = new_refresh_token()
    token = RefreshToken(
        user_id=user.id,
        family_id=family_id or uuid.uuid4(),
        token_hash=hash_token(raw_refresh),
        expires_at=datetime.now(UTC) + timedelta(days=settings.refresh_token_ttl_days),
        user_agent=request.headers.get("user-agent", "")[:400],
    )
    db.add(token)
    await db.flush()

    access = create_access_token(user.id, user.role)
    csrf = new_csrf_token()
    common = {"secure": settings.secure_cookies, "domain": settings.cookie_domain}
    response.set_cookie(ACCESS_COOKIE, access, httponly=True, samesite="lax", path="/",
                        max_age=settings.access_token_ttl_minutes * 60, **common)
    response.set_cookie(REFRESH_COOKIE, raw_refresh, httponly=True, samesite="strict", path=REFRESH_PATH,
                        max_age=settings.refresh_token_ttl_days * 86400, **common)
    response.set_cookie(CSRF_COOKIE, csrf, httponly=False, samesite="lax", path="/",
                        max_age=settings.refresh_token_ttl_days * 86400, **common)
    return csrf


def clear_session_cookies(response: Response) -> None:
    settings = get_settings()
    for name, path in ((ACCESS_COOKIE, "/"), (REFRESH_COOKIE, REFRESH_PATH), (CSRF_COOKIE, "/")):
        response.delete_cookie(name, path=path, domain=settings.cookie_domain)


async def rotate_refresh(db: AsyncSession, response: Response, request: Request,
                         raw_refresh: str) -> tuple[User, str]:
    now = datetime.now(UTC)
    token = await db.scalar(select(RefreshToken).where(RefreshToken.token_hash == hash_token(raw_refresh)))
    if token is None:
        raise AuthError("Invalid session", code="invalid_refresh")
    if token.revoked_at is not None:
        # A revoked token was replayed: assume theft and revoke the whole family.
        await db.execute(
            update(RefreshToken)
            .where(RefreshToken.family_id == token.family_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=now)
        )
        await audit(db, action="auth.refresh_reuse_detected", user_id=token.user_id, request=request)
        await db.commit()
        logger.warning("Refresh token reuse detected; family revoked")
        raise AuthError("Session revoked - please sign in again", code="refresh_reused")
    if token.expires_at < now:
        raise AuthError("Session expired - please sign in again", code="refresh_expired")
    user = await db.get(User, token.user_id)
    if user is None or not user.is_active:
        raise AuthError("Account not found or disabled")
    token.revoked_at = now
    csrf = await issue_session(db, response, user, request, family_id=token.family_id)
    new_token = await db.scalar(
        select(RefreshToken).where(RefreshToken.family_id == token.family_id, RefreshToken.revoked_at.is_(None))
        .order_by(RefreshToken.id.desc()).limit(1)
    )
    token.replaced_by_id = new_token.id if new_token else None
    return user, csrf


async def revoke_refresh(db: AsyncSession, raw_refresh: str | None) -> None:
    if not raw_refresh:
        return
    token = await db.scalar(select(RefreshToken).where(RefreshToken.token_hash == hash_token(raw_refresh)))
    if token:
        await db.execute(
            update(RefreshToken)
            .where(RefreshToken.family_id == token.family_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=datetime.now(UTC))
        )


async def bootstrap_admin(db: AsyncSession) -> None:
    settings = get_settings()
    if not (settings.bootstrap_admin_email and settings.bootstrap_admin_password):
        return
    if await user_count(db) > 0:
        return
    user = await create_user(db, email=settings.bootstrap_admin_email, password=settings.bootstrap_admin_password,
                             full_name="Administrator", role=Role.ADMIN)
    await audit(db, action="auth.bootstrap_admin", user_id=user.id)
    await db.commit()
    logger.info("Bootstrap admin account created")
