"""Admin: market data provider configuration, connection tests, activation and Upstox OAuth."""

from __future__ import annotations

import asyncio
import ipaddress
import logging
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlencode, urlparse

from fastapi import APIRouter, Query, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select, update

from app.api.deps import DB, AdminUser
from app.core.bus import CH_COMMANDS, K_FEED_HEALTH, get_bus
from app.core.config import get_settings
from app.core.crypto import decrypt_json, encrypt_json, mask_secret
from app.core.errors import AppError, ConflictError, NotFoundError
from app.core.security import TokenError, create_signed_state, decode_signed_state
from app.db.models import ProviderConfig
from app.db.session import get_sessionmaker
from app.marketdata.base import AuthenticationRequired, ProviderError
from app.marketdata.registry import PROVIDERS, build_provider, provider_types
from app.marketdata.upstox import UpstoxProvider
from app.services import instruments as instrument_service
from app.services.audit import audit
from app.services.market_data import persist_credentials

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/admin/providers", tags=["admin: providers"])
K_SYNC = "mi:instrument_sync"

DEFAULT_TEST_KEYS = {"upstox": ["NSE_INDEX|Nifty 50"], "angelone": ["NSE:99926000"], "simulated": ["SIM:SIMNIFTY50"]}


def _validate_url(url: str | None, schemes: tuple[str, ...]) -> str | None:
    if not url:
        return None
    parsed = urlparse(url)
    if parsed.scheme not in schemes or not parsed.hostname:
        raise ValueError(f"URL must use one of: {', '.join(schemes)}")
    if get_settings().is_production:
        host = parsed.hostname
        if host in ("localhost",) or host.endswith(".internal") or host.endswith(".local"):
            raise ValueError("Internal hostnames are not allowed for provider URLs")
        try:
            ip = ipaddress.ip_address(host)
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
                raise ValueError("Private/loopback IP addresses are not allowed for provider URLs")
        except ValueError as exc:
            if "not allowed" in str(exc):
                raise
    return url


def _check_rest(v: str | None) -> str | None:
    return _validate_url(v, ("https",) if get_settings().is_production else ("https", "http"))


def _check_ws(v: str | None) -> str | None:
    return _validate_url(v, ("wss",) if get_settings().is_production else ("wss", "ws"))


class ProviderIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    provider_type: str
    rest_url: str | None = Field(default=None, max_length=500)
    ws_url: str | None = Field(default=None, max_length=500)
    exchange: str = Field(default="NSE", pattern="^(NSE|BSE)$")
    data_interval: str = Field(default="1minute", max_length=20)
    feed_type: str = Field(default="full", max_length=20)
    options: dict[str, Any] = Field(default_factory=dict)
    credentials: dict[str, str] = Field(default_factory=dict)

    @field_validator("provider_type")
    @classmethod
    def _known(cls, v: str) -> str:
        if v not in PROVIDERS:
            raise ValueError(f"Unknown provider type. Supported: {', '.join(PROVIDERS)}")
        return v

    _rest = field_validator("rest_url")(classmethod(lambda cls, v: _check_rest(v)))
    _ws = field_validator("ws_url")(classmethod(lambda cls, v: _check_ws(v)))


class ProviderPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    rest_url: str | None = Field(default=None, max_length=500)
    ws_url: str | None = Field(default=None, max_length=500)
    exchange: str | None = Field(default=None, pattern="^(NSE|BSE)$")
    data_interval: str | None = Field(default=None, max_length=20)
    feed_type: str | None = Field(default=None, max_length=20)
    options: dict[str, Any] | None = None
    credentials: dict[str, str] | None = None

    _rest = field_validator("rest_url")(classmethod(lambda cls, v: _check_rest(v)))
    _ws = field_validator("ws_url")(classmethod(lambda cls, v: _check_ws(v)))


def _serialize(cfg: ProviderConfig) -> dict[str, Any]:
    cls = PROVIDERS.get(cfg.provider_type)
    creds = decrypt_json(cfg.encrypted_credentials)
    fields = cls.credential_fields if cls else []
    masked = {f.key: (mask_secret(creds.get(f.key)) if f.secret else creds.get(f.key)) for f in fields}
    return {
        "id": cfg.id,
        "name": cfg.name,
        "provider_type": cfg.provider_type,
        "provider_name": cls.display_name if cls else cfg.provider_type,
        "rest_url": cfg.rest_url,
        "ws_url": cfg.ws_url,
        "exchange": cfg.exchange,
        "data_interval": cfg.data_interval,
        "feed_type": cfg.feed_type,
        "options": cfg.options,
        "credentials": masked,
        "credentials_set": {f.key: bool(creds.get(f.key)) for f in fields},
        "is_active": cfg.is_active,
        "token_expires_at": cfg.token_expires_at,
        "session_active": bool(creds.get("access_token") or creds.get("jwt_token")),
        "last_test_at": cfg.last_test_at,
        "last_test_ok": cfg.last_test_ok,
        "last_test_message": cfg.last_test_message,
        "last_test_latency_ms": cfg.last_test_latency_ms,
        "created_at": cfg.created_at,
        "updated_at": cfg.updated_at,
    }


def _merge_credentials(cfg: ProviderConfig, incoming: dict[str, str]) -> None:
    cls = PROVIDERS[cfg.provider_type]
    allowed = {f.key for f in cls.credential_fields}
    current = decrypt_json(cfg.encrypted_credentials)
    for k, v in incoming.items():
        if k not in allowed:
            raise AppError(f"Unknown credential field '{k}' for {cls.display_name}", status_code=422)
        if v == "":
            current.pop(k, None)
        elif v is not None:
            current[k] = v.strip()
    cfg.encrypted_credentials = encrypt_json(current) if current else None


async def _get(db: DB, provider_id: int) -> ProviderConfig:
    cfg = await db.get(ProviderConfig, provider_id)
    if cfg is None:
        raise NotFoundError("Provider not found")
    return cfg


async def _notify_reload() -> None:
    await get_bus().publish(CH_COMMANDS, {"cmd": "reload"})


@router.get("/types")
async def list_types(_: AdminUser) -> list[dict[str, Any]]:
    return provider_types()


@router.get("")
async def list_providers(_: AdminUser, db: DB) -> list[dict[str, Any]]:
    rows = (await db.scalars(select(ProviderConfig).order_by(ProviderConfig.created_at))).all()
    return [_serialize(r) for r in rows]


@router.post("", status_code=201)
async def create_provider(body: ProviderIn, request: Request, admin: AdminUser, db: DB) -> dict[str, Any]:
    cls = PROVIDERS[body.provider_type]
    if cls.is_simulated and not get_settings().allow_simulated_feed:
        raise AppError("The simulated feed is disabled on this server", code="simulated_disabled")
    if await db.scalar(select(ProviderConfig).where(ProviderConfig.name == body.name)):
        raise ConflictError("A provider with this name already exists")
    cfg = ProviderConfig(
        name=body.name,
        provider_type=body.provider_type,
        rest_url=body.rest_url or cls.default_rest_url,
        ws_url=body.ws_url or cls.default_ws_url,
        exchange=body.exchange,
        data_interval=body.data_interval,
        feed_type=body.feed_type,
        options=body.options,
        is_active=False,
    )
    db.add(cfg)
    _merge_credentials(cfg, body.credentials)
    await db.flush()
    await audit(
        db,
        action="provider.create",
        user_id=admin.id,
        request=request,
        entity_type="provider",
        entity_id=cfg.id,
        details={"type": cfg.provider_type, "credential_fields": sorted(body.credentials)},
    )
    await db.commit()
    await db.refresh(cfg)
    return _serialize(cfg)


@router.patch("/{provider_id}")
async def update_provider(
    provider_id: int, body: ProviderPatch, request: Request, admin: AdminUser, db: DB
) -> dict[str, Any]:
    cfg = await _get(db, provider_id)
    changes = body.model_dump(exclude_unset=True)
    creds = changes.pop("credentials", None)
    for k, v in changes.items():
        setattr(cfg, k, v)
    if creds:
        _merge_credentials(cfg, creds)
    await audit(
        db,
        action="provider.update",
        user_id=admin.id,
        request=request,
        entity_type="provider",
        entity_id=cfg.id,
        details={"fields": sorted(changes), "credential_fields": sorted(creds or {})},
    )
    await db.commit()
    await db.refresh(cfg)
    if cfg.is_active:
        await _notify_reload()
    return _serialize(cfg)


@router.delete("/{provider_id}", status_code=204)
async def delete_provider(provider_id: int, request: Request, admin: AdminUser, db: DB) -> None:
    cfg = await _get(db, provider_id)
    was_active = cfg.is_active
    await db.delete(cfg)
    await audit(
        db, action="provider.delete", user_id=admin.id, request=request, entity_type="provider", entity_id=provider_id
    )
    await db.commit()
    if was_active:
        await _notify_reload()


@router.post("/{provider_id}/test")
async def test_provider(provider_id: int, request: Request, admin: AdminUser, db: DB) -> dict[str, Any]:
    cfg = await _get(db, provider_id)
    provider = build_provider(cfg)
    sample = cfg.options.get("test_instrument_keys") or DEFAULT_TEST_KEYS.get(cfg.provider_type, [])
    try:
        result = await provider.test_connection(list(sample))
        await persist_credentials(db, cfg, provider)
    except AuthenticationRequired as exc:
        result_dict = {"ok": False, "message": f"Authentication required: {exc}", "latency_ms": None, "details": {}}
    except ProviderError as exc:
        result_dict = {"ok": False, "message": str(exc)[:500], "latency_ms": None, "details": {}}
    else:
        result_dict = {
            "ok": result.ok,
            "message": result.message,
            "latency_ms": result.latency_ms,
            "details": result.details,
        }
    finally:
        await provider.close()
    cfg.last_test_at = datetime.now(UTC)
    cfg.last_test_ok = result_dict["ok"]
    cfg.last_test_message = result_dict["message"]
    cfg.last_test_latency_ms = result_dict["latency_ms"]
    await audit(
        db,
        action="provider.test",
        user_id=admin.id,
        request=request,
        entity_type="provider",
        entity_id=cfg.id,
        details={"ok": result_dict["ok"]},
    )
    await db.commit()
    return result_dict


@router.post("/{provider_id}/activate")
async def activate_provider(provider_id: int, request: Request, admin: AdminUser, db: DB) -> dict[str, Any]:
    cfg = await _get(db, provider_id)
    build_provider(cfg)  # validates that the type is usable on this server
    await db.execute(update(ProviderConfig).where(ProviderConfig.id != cfg.id).values(is_active=False))
    cfg.is_active = True
    await audit(
        db,
        action="provider.activate",
        user_id=admin.id,
        request=request,
        entity_type="provider",
        entity_id=cfg.id,
        details={"type": cfg.provider_type},
    )
    await db.commit()
    await db.refresh(cfg)  # server-side onupdate values are expired after commit
    await _notify_reload()
    return _serialize(cfg)


@router.post("/{provider_id}/deactivate")
async def deactivate_provider(provider_id: int, request: Request, admin: AdminUser, db: DB) -> dict[str, Any]:
    cfg = await _get(db, provider_id)
    cfg.is_active = False
    await audit(
        db, action="provider.deactivate", user_id=admin.id, request=request, entity_type="provider", entity_id=cfg.id
    )
    await db.commit()
    await db.refresh(cfg)  # server-side onupdate values are expired after commit
    await _notify_reload()
    return _serialize(cfg)


# --- Upstox OAuth -----------------------------------------------------------------------


@router.get("/{provider_id}/oauth-url")
async def upstox_oauth_url(provider_id: int, admin: AdminUser, db: DB) -> dict[str, str]:
    cfg = await _get(db, provider_id)
    if cfg.provider_type != "upstox":
        raise AppError("OAuth login is only used by Upstox")
    provider = build_provider(cfg)
    try:
        assert isinstance(provider, UpstoxProvider)
        state = create_signed_state({"type": "upstox_oauth", "sub": str(admin.id), "pid": cfg.id})
        return {"url": provider.authorization_url(state)}
    except AuthenticationRequired as exc:
        raise AppError(str(exc)) from exc
    finally:
        await provider.close()


@router.get("/upstox/callback", include_in_schema=False)
async def upstox_callback(
    request: Request,
    admin: AdminUser,
    db: DB,
    code: str = Query(""),
    state: str = Query(""),
    error: str | None = Query(None),
) -> RedirectResponse:
    def back(**params: Any) -> RedirectResponse:
        return RedirectResponse(f"/settings/providers?{urlencode(params)}", status_code=303)

    if error or not code:
        return back(oauth_error=error or "missing_code")
    try:
        claims = decode_signed_state(state, "upstox_oauth")
    except TokenError:
        return back(oauth_error="invalid_state")
    if claims.get("sub") != str(admin.id):
        return back(oauth_error="state_user_mismatch")
    cfg = await db.get(ProviderConfig, int(claims["pid"]))
    if cfg is None or cfg.provider_type != "upstox":
        return back(oauth_error="provider_not_found")
    provider = build_provider(cfg)
    try:
        assert isinstance(provider, UpstoxProvider)
        await provider.exchange_code(code)
        await persist_credentials(db, cfg, provider)
    except (AuthenticationRequired, ProviderError) as exc:
        logger.warning("Upstox token exchange failed: %s", exc)
        return back(oauth_error="token_exchange_failed")
    finally:
        await provider.close()
    await audit(
        db,
        action="provider.oauth_connected",
        user_id=admin.id,
        request=request,
        entity_type="provider",
        entity_id=cfg.id,
    )
    await db.commit()
    if cfg.is_active:
        await _notify_reload()
    return back(connected=cfg.id)


# --- Instrument master sync -----------------------------------------------------------------


async def _run_sync(provider_id: int) -> None:
    bus = get_bus()
    started = datetime.now(UTC).isoformat()
    await bus.set_json(K_SYNC, {"state": "running", "provider_id": provider_id, "started_at": started})
    try:
        async with get_sessionmaker()() as db:
            cfg = await db.get(ProviderConfig, provider_id)
            if cfg is None:
                raise AppError("Provider not found")
            provider = build_provider(cfg)
            try:
                result = await instrument_service.sync_from_provider(db, provider)
            finally:
                await provider.close()
        await bus.set_json(
            K_SYNC,
            {
                "state": "completed",
                "provider_id": provider_id,
                "started_at": started,
                "finished_at": datetime.now(UTC).isoformat(),
                "result": result,
            },
        )
        await _notify_reload()
    except Exception as exc:  # noqa: BLE001
        logger.exception("Instrument sync failed")
        await bus.set_json(
            K_SYNC,
            {
                "state": "failed",
                "provider_id": provider_id,
                "started_at": started,
                "finished_at": datetime.now(UTC).isoformat(),
                "error": str(exc)[:500],
            },
        )


_sync_tasks: set[asyncio.Task] = set()


@router.post("/{provider_id}/sync-instruments", status_code=202)
async def sync_instruments(provider_id: int, request: Request, admin: AdminUser, db: DB) -> dict[str, Any]:
    await _get(db, provider_id)
    current = await get_bus().get_json(K_SYNC)
    if current and current.get("state") == "running":
        raise ConflictError("An instrument sync is already running")
    await audit(
        db,
        action="instruments.sync",
        user_id=admin.id,
        request=request,
        entity_type="provider",
        entity_id=provider_id,
        commit=True,
    )
    task = asyncio.create_task(_run_sync(provider_id))
    _sync_tasks.add(task)
    task.add_done_callback(_sync_tasks.discard)
    return {"state": "running"}


@router.get("/sync-status")
async def sync_status(_: AdminUser) -> dict[str, Any]:
    return await get_bus().get_json(K_SYNC) or {"state": "idle"}


@router.get("/feed-health")
async def feed_health(_: AdminUser) -> dict[str, Any]:
    return await get_bus().get_json(K_FEED_HEALTH) or {"state": "unknown", "detail": "Worker has not reported yet"}


@router.post("/feed/reload", status_code=202)
async def reload_feed(request: Request, admin: AdminUser, db: DB) -> dict[str, str]:
    await audit(db, action="provider.feed_reload", user_id=admin.id, request=request, commit=True)
    await _notify_reload()
    return {"status": "reload requested"}
