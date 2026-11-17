from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import func, select, update

from app.api.deps import DB, AdminUser, CurrentUser, WriterUser
from app.core.errors import AppError, NotFoundError
from app.core.ratelimit import enforce_rate_limit
from app.db.models import Alert, Instrument, Notification
from app.services import instruments as instrument_service
from app.services import notify
from app.services.alerts import ALERT_TYPES, describe, evaluate_alerts, validate_params
from app.services.portfolio import own_portfolio

router = APIRouter(tags=["alerts"])
MAX_ALERTS = 200


class AlertIn(BaseModel):
    name: str = Field(default="", max_length=150)
    alert_type: str
    symbol: str | None = Field(default=None, max_length=80)
    portfolio_id: int | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    channels: list[Literal["in_app", "email", "telegram"]] = Field(default_factory=lambda: ["in_app"])
    trigger_once: bool = False
    cooldown_minutes: int = Field(60, ge=1, le=10080)
    is_active: bool = True

    @model_validator(mode="after")
    def _check(self) -> AlertIn:
        try:
            self.params = validate_params(self.alert_type, self.params)
        except ValueError as exc:
            raise ValueError(str(exc)) from exc
        needs = ALERT_TYPES[self.alert_type]["needs"]
        if needs == "instrument" and not self.symbol:
            raise ValueError("this alert type needs a symbol")
        if needs == "portfolio" and not self.portfolio_id:
            raise ValueError("this alert type needs a portfolio")
        if "in_app" not in self.channels:
            self.channels = ["in_app", *self.channels]
        self.channels = list(dict.fromkeys(self.channels))
        return self


class AlertPatch(BaseModel):
    is_active: bool | None = None
    name: str | None = Field(default=None, max_length=150)
    channels: list[Literal["in_app", "email", "telegram"]] | None = None
    cooldown_minutes: int | None = Field(default=None, ge=1, le=10080)


def _alert_dict(a: Alert, symbol: str | None) -> dict[str, Any]:
    return {
        "id": a.id,
        "name": a.name,
        "alert_type": a.alert_type,
        "type_label": ALERT_TYPES[a.alert_type]["label"],
        "basis": ALERT_TYPES[a.alert_type]["basis"],
        "instrument_id": a.instrument_id,
        "symbol": symbol,
        "portfolio_id": a.portfolio_id,
        "params": a.params,
        "channels": a.channels,
        "is_active": a.is_active,
        "trigger_once": a.trigger_once,
        "cooldown_minutes": a.cooldown_minutes,
        "last_triggered_at": a.last_triggered_at,
        "created_at": a.created_at,
        "description": describe(a.alert_type, a.params, symbol),
    }


@router.get("/alerts/types")
async def alert_types(_: CurrentUser, db: DB) -> dict[str, Any]:
    from app.services import app_settings

    cfg = await app_settings.get_section(db, "notifications")
    return {
        "types": ALERT_TYPES,
        "channels": {
            "in_app": True,
            "email": bool(cfg["email"].get("enabled")),
            "telegram": bool(cfg["telegram"].get("enabled")),
        },
    }


@router.get("/alerts")
async def list_alerts(user: CurrentUser, db: DB) -> list[dict[str, Any]]:
    rows = await db.execute(
        select(Alert, Instrument.symbol)
        .outerjoin(Instrument, Instrument.id == Alert.instrument_id)
        .where(Alert.user_id == user.id)
        .order_by(Alert.created_at.desc())
    )
    return [_alert_dict(a, s) for a, s in rows.all()]


@router.post("/alerts", status_code=201)
async def create_alert(body: AlertIn, user: WriterUser, db: DB) -> dict[str, Any]:
    n = await db.scalar(select(func.count(Alert.id)).where(Alert.user_id == user.id))
    if (n or 0) >= MAX_ALERTS:
        raise AppError(f"Limit of {MAX_ALERTS} alerts reached", code="limit_reached", status_code=422)
    inst = None
    if body.symbol and ALERT_TYPES[body.alert_type]["needs"] == "instrument":
        inst = await instrument_service.resolve_symbol(db, body.symbol)
    if body.portfolio_id:
        await own_portfolio(db, user.id, body.portfolio_id)
    a = Alert(
        user_id=user.id,
        name=body.name.strip() or describe(body.alert_type, body.params, inst.symbol if inst else None),
        alert_type=body.alert_type,
        instrument_id=inst.id if inst else None,
        portfolio_id=body.portfolio_id if ALERT_TYPES[body.alert_type]["needs"] == "portfolio" else None,
        params=body.params,
        channels=body.channels,
        is_active=body.is_active,
        trigger_once=body.trigger_once,
        cooldown_minutes=body.cooldown_minutes,
        state={},
    )
    db.add(a)
    await db.commit()
    await db.refresh(a)
    return _alert_dict(a, inst.symbol if inst else None)


async def _own(db: DB, user_id: Any, alert_id: int) -> Alert:
    a = await db.get(Alert, alert_id)
    if a is None or a.user_id != user_id:
        raise NotFoundError("Alert not found")
    return a


@router.patch("/alerts/{alert_id}")
async def patch_alert(alert_id: int, body: AlertPatch, user: WriterUser, db: DB) -> dict[str, Any]:
    a = await _own(db, user.id, alert_id)
    if body.is_active is not None:
        a.is_active = body.is_active
        if body.is_active:
            a.state = {k: v for k, v in (a.state or {}).items() if k in ("since",)}  # re-arm on reactivation
    if body.name is not None:
        a.name = body.name.strip() or a.name
    if body.channels is not None:
        a.channels = list(dict.fromkeys(["in_app", *body.channels]))
    if body.cooldown_minutes is not None:
        a.cooldown_minutes = body.cooldown_minutes
    await db.commit()
    await db.refresh(a)
    sym = (await db.get(Instrument, a.instrument_id)).symbol if a.instrument_id else None
    return _alert_dict(a, sym)


@router.delete("/alerts/{alert_id}", status_code=204)
async def delete_alert(alert_id: int, user: WriterUser, db: DB) -> Response:
    a = await _own(db, user.id, alert_id)
    await db.delete(a)
    await db.commit()
    return Response(status_code=204)


@router.post("/alerts/evaluate")
async def evaluate_now(user: WriterUser, db: DB) -> dict[str, Any]:
    """Evaluate the caller's alerts immediately (the worker also does this on its interval)."""
    await enforce_rate_limit(f"alerts-eval:{user.id}", 6)
    return {"triggered": await evaluate_alerts(db, user.id)}


# ------------------------------------------------------------------ notifications


@router.get("/notifications")
async def list_notifications(
    user: CurrentUser, db: DB, unread_only: bool = False, limit: int = Query(50, ge=1, le=200)
) -> dict[str, Any]:
    stmt = select(Notification).where(Notification.user_id == user.id)
    if unread_only:
        stmt = stmt.where(Notification.read_at.is_(None))
    rows = await db.scalars(stmt.order_by(Notification.created_at.desc()).limit(limit))
    unread = await db.scalar(
        select(func.count(Notification.id)).where(Notification.user_id == user.id, Notification.read_at.is_(None))
    )
    return {"items": [notify.notification_dict(n) for n in rows], "unread": unread or 0}


@router.post("/notifications/{notification_id}/read")
async def mark_read(notification_id: int, user: CurrentUser, db: DB) -> dict[str, Any]:
    n = await db.get(Notification, notification_id)
    if n is None or n.user_id != user.id:
        raise NotFoundError("Notification not found")
    n.read_at = n.read_at or datetime.now(UTC)
    await db.commit()
    return {"ok": True}


@router.post("/notifications/read-all")
async def mark_all_read(user: CurrentUser, db: DB) -> dict[str, Any]:
    res = await db.execute(
        update(Notification)
        .where(Notification.user_id == user.id, Notification.read_at.is_(None))
        .values(read_at=datetime.now(UTC))
    )
    await db.commit()
    return {"updated": res.rowcount}


class TestChannelIn(BaseModel):
    channel: Literal["email", "telegram"]


@router.post("/admin/notifications/test")
async def test_channel(body: TestChannelIn, user: AdminUser, db: DB) -> dict[str, Any]:
    """Send a test message through a configured channel (Test Connection for notifications)."""
    await enforce_rate_limit(f"notify-test:{user.id}", 5)
    try:
        if body.channel == "email":
            await notify.send_email(
                db, user.email, "MarketIntel test notification", "Email notifications are configured correctly."
            )
        else:
            await notify.send_telegram(db, "MarketIntel test notification: Telegram is configured correctly.")
    except notify.DeliveryError as exc:
        return {"ok": False, "channel": body.channel, "error": str(exc)}
    return {"ok": True, "channel": body.channel}
