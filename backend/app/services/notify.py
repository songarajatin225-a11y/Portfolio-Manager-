"""Notification delivery: in-app (stored + pushed over the WebSocket), email (SMTP) and Telegram.

Credentials (SMTP password, Telegram bot token) are read from encrypted settings on the backend only.
"""

from __future__ import annotations

import asyncio
import logging
import smtplib
import ssl
import uuid
from email.message import EmailMessage
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.bus import CH_NOTIFICATIONS, get_bus
from app.db.models import Notification, User
from app.services import app_settings

logger = logging.getLogger(__name__)
CHANNELS = ("in_app", "email", "telegram")


class DeliveryError(RuntimeError):
    pass


def _send_smtp(cfg: dict[str, Any], password: str | None, to: str, subject: str, body: str) -> None:
    msg = EmailMessage()
    msg["From"] = cfg["from_address"]
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    port = int(cfg.get("smtp_port") or 587)
    ctx = ssl.create_default_context()
    if port == 465:
        server: smtplib.SMTP = smtplib.SMTP_SSL(cfg["smtp_host"], port, context=ctx, timeout=15)
    else:
        server = smtplib.SMTP(cfg["smtp_host"], port, timeout=15)
    with server:
        if port != 465 and cfg.get("use_tls", True):
            server.starttls(context=ctx)
        if cfg.get("smtp_username"):
            server.login(cfg["smtp_username"], password or "")
        server.send_message(msg)


async def send_email(db: AsyncSession, to: str, subject: str, body: str) -> None:
    cfg = (await app_settings.get_section(db, "notifications"))["email"]
    if not cfg.get("enabled"):
        raise DeliveryError("Email notifications are disabled")
    if not cfg.get("smtp_host") or not cfg.get("from_address"):
        raise DeliveryError("SMTP host and from address are not configured")
    password = (await app_settings.get_secrets(db)).get("smtp_password")
    try:
        await asyncio.to_thread(_send_smtp, cfg, password, to, subject, body)
    except (OSError, smtplib.SMTPException) as exc:
        raise DeliveryError(f"SMTP error: {exc}") from exc


async def send_telegram(db: AsyncSession, text: str, client: httpx.AsyncClient | None = None) -> None:
    cfg = (await app_settings.get_section(db, "notifications"))["telegram"]
    if not cfg.get("enabled"):
        raise DeliveryError("Telegram notifications are disabled")
    token = (await app_settings.get_secrets(db)).get("telegram_bot_token")
    if not token or not cfg.get("chat_id"):
        raise DeliveryError("Telegram bot token and chat id are not configured")
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    own = client is None
    client = client or httpx.AsyncClient(timeout=10)
    try:
        r = await client.post(
            url, json={"chat_id": cfg["chat_id"], "text": text[:4000], "disable_web_page_preview": True}
        )
    except httpx.HTTPError as exc:
        raise DeliveryError(f"Telegram request failed: {type(exc).__name__}") from exc
    finally:
        if own:
            await client.aclose()
    if r.status_code != 200:
        # never echo the URL (it contains the token)
        desc = (
            r.json().get("description", "") if r.headers.get("content-type", "").startswith("application/json") else ""
        )
        raise DeliveryError(f"Telegram API returned {r.status_code} {desc}".strip())


async def notify(
    db: AsyncSession,
    user_id: uuid.UUID,
    title: str,
    body: str,
    *,
    severity: str = "info",
    data: dict[str, Any] | None = None,
    channels: list[str] | None = None,
    alert_id: int | None = None,
) -> Notification:
    """Store and push an in-app notification, then attempt the extra channels. Commits."""
    channels = channels or ["in_app"]
    data = dict(data or {})
    delivery: dict[str, str] = {"in_app": "delivered"}
    n = Notification(user_id=user_id, alert_id=alert_id, title=title[:300], body=body, severity=severity, data=data)
    db.add(n)
    await db.flush()
    prefix = f"[{data['data_status']}] " if data.get("data_status") == "SIMULATED" else ""
    if "email" in channels:
        user = await db.get(User, user_id)
        try:
            await send_email(db, user.email, f"{prefix}{title}", f"{body}\n\n— MarketIntel alert")
            delivery["email"] = "sent"
        except DeliveryError as exc:
            delivery["email"] = f"failed: {exc}"
    if "telegram" in channels:
        try:
            await send_telegram(db, f"{prefix}{title}\n{body}")
            delivery["telegram"] = "sent"
        except DeliveryError as exc:
            delivery["telegram"] = f"failed: {exc}"
    n.data = {**data, "delivery": delivery}
    await db.commit()
    await db.refresh(n)
    await get_bus().publish(
        CH_NOTIFICATIONS,
        {
            "user_id": str(user_id),
            "id": n.id,
            "title": n.title,
            "body": n.body,
            "severity": n.severity,
            "data": n.data,
            "created_at": n.created_at.isoformat(),
        },
    )
    return n


def notification_dict(n: Notification) -> dict[str, Any]:
    return {
        "id": n.id,
        "alert_id": n.alert_id,
        "title": n.title,
        "body": n.body,
        "severity": n.severity,
        "data": n.data,
        "read_at": n.read_at,
        "created_at": n.created_at,
    }
