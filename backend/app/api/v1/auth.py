from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from fastapi.security import OAuth2PasswordRequestForm

from app.api.deps import DB, CurrentUser
from app.core.config import get_settings
from app.core.errors import AppError, AuthError, PermissionDenied
from app.core.ratelimit import enforce_rate_limit
from app.core.security import (
    REFRESH_COOKIE,
    create_access_token,
    create_ws_ticket,
    hash_password,
    validate_password_strength,
    verify_password,
)
from app.db.models import Role
from app.schemas.auth import (
    ChangePasswordRequest,
    LoginRequest,
    PreferencesUpdate,
    RegisterRequest,
    SessionOut,
    TokenOut,
    UserOut,
    WsTicketOut,
)
from app.services import auth as auth_service
from app.services.audit import audit, client_ip

router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/bootstrap-status")
async def bootstrap_status(db: DB) -> dict:
    """Tells the login page whether the first (admin) account still needs to be created."""
    settings = get_settings()
    count = await auth_service.user_count(db)
    return {"needs_setup": count == 0, "registration_open": count == 0 or settings.allow_public_registration}


@router.post("/register", response_model=SessionOut, status_code=201)
async def register(body: RegisterRequest, request: Request, response: Response, db: DB) -> SessionOut:
    await enforce_rate_limit(f"register:{client_ip(request)}", 5)
    count = await auth_service.user_count(db)
    if count > 0 and not get_settings().allow_public_registration:
        raise PermissionDenied("Registration is closed. Ask an administrator to create your account.")
    role = Role.ADMIN if count == 0 else Role.ANALYST
    user = await auth_service.create_user(
        db, email=body.email, password=body.password, full_name=body.full_name, role=role
    )
    user.last_login_at = datetime.now(UTC)
    csrf = await auth_service.issue_session(db, response, user, request)
    await audit(db, action="auth.register", user_id=user.id, request=request, details={"role": role})
    await db.commit()
    return SessionOut(user=UserOut.model_validate(user), csrf_token=csrf)


@router.post("/login", response_model=SessionOut)
async def login(body: LoginRequest, request: Request, response: Response, db: DB) -> SessionOut:
    limit = get_settings().login_rate_limit_per_minute
    await enforce_rate_limit(f"login:ip:{client_ip(request)}", limit * 3)
    await enforce_rate_limit(f"login:email:{body.email.lower()}", limit)
    user = await auth_service.authenticate(db, body.email, body.password)
    if user is None or not user.is_active:
        await audit(db, action="auth.login_failed", request=request, details={"email": body.email.lower()}, commit=True)
        raise AuthError("Invalid email or password", code="invalid_credentials")
    user.last_login_at = datetime.now(UTC)
    csrf = await auth_service.issue_session(db, response, user, request)
    await audit(db, action="auth.login", user_id=user.id, request=request)
    await db.commit()
    return SessionOut(user=UserOut.model_validate(user), csrf_token=csrf)


@router.post("/token", response_model=TokenOut, summary="Bearer token for API clients (OAuth2 password flow)")
async def token(form: Annotated[OAuth2PasswordRequestForm, Depends()], request: Request, db: DB) -> TokenOut:
    await enforce_rate_limit(f"login:email:{form.username.lower()}", get_settings().login_rate_limit_per_minute)
    user = await auth_service.authenticate(db, form.username, form.password)
    if user is None or not user.is_active:
        await audit(db, action="auth.token_failed", request=request, commit=True)
        raise AuthError("Invalid email or password", code="invalid_credentials")
    await audit(db, action="auth.token", user_id=user.id, request=request, commit=True)
    return TokenOut(
        access_token=create_access_token(user.id, user.role), expires_in=get_settings().access_token_ttl_minutes * 60
    )


@router.post("/refresh", response_model=SessionOut)
async def refresh(request: Request, response: Response, db: DB) -> SessionOut:
    raw = request.cookies.get(REFRESH_COOKIE)
    if not raw:
        raise AuthError("No session", code="no_session")
    user, csrf = await auth_service.rotate_refresh(db, response, request, raw)
    await db.commit()
    return SessionOut(user=UserOut.model_validate(user), csrf_token=csrf)


@router.post("/logout", status_code=204)
async def logout(request: Request, response: Response, db: DB) -> Response:
    await auth_service.revoke_refresh(db, request.cookies.get(REFRESH_COOKIE))
    await audit(db, action="auth.logout", request=request)
    await db.commit()
    auth_service.clear_session_cookies(response)
    response.status_code = 204
    return response


@router.get("/me", response_model=UserOut)
async def me(user: CurrentUser) -> UserOut:
    return UserOut.model_validate(user)


@router.patch("/me/preferences", response_model=UserOut)
async def update_preferences(body: PreferencesUpdate, user: CurrentUser, db: DB) -> UserOut:
    if len(str(body.preferences)) > 20_000:
        raise AppError("Preferences payload too large", status_code=413)
    user.preferences = {**(user.preferences or {}), **body.preferences}
    await db.commit()
    return UserOut.model_validate(user)


@router.post("/change-password", status_code=204)
async def change_password(body: ChangePasswordRequest, request: Request, user: CurrentUser, db: DB) -> Response:
    if not verify_password(body.current_password, user.password_hash):
        raise AuthError("Current password is incorrect", code="invalid_credentials")
    problems = validate_password_strength(body.new_password)
    if problems:
        raise AppError("; ".join(problems), code="weak_password", status_code=422)
    user.password_hash = hash_password(body.new_password)
    await audit(db, action="auth.password_changed", user_id=user.id, request=request)
    await db.commit()
    return Response(status_code=204)


@router.post("/ws-ticket", response_model=WsTicketOut)
async def ws_ticket(user: CurrentUser) -> WsTicketOut:
    """Short-lived ticket for authenticating the browser's WebSocket connection."""
    ttl = get_settings().ws_ticket_ttl_seconds
    return WsTicketOut(ticket=create_ws_ticket(user.id, user.role), expires_in=ttl)
