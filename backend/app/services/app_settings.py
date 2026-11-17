"""Typed access to admin-editable settings stored in ``app_settings``.

Secret values (SMTP password, Telegram bot token) are stored encrypted under the ``secrets`` key
and are never returned by the API - only whether they are set.
"""

from __future__ import annotations

import copy
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.crypto import decrypt_json, encrypt_json
from app.db.models import AppSetting

DEFAULT_DASHBOARD_INDICES = [
    {"code": "NIFTY50", "label": "NIFTY 50", "names": ["Nifty 50"], "exchange": "NSE"},
    {"code": "SENSEX", "label": "SENSEX", "names": ["SENSEX"], "exchange": "BSE"},
    {"code": "BANKNIFTY", "label": "BANK NIFTY", "names": ["Nifty Bank"], "exchange": "NSE"},
    {"code": "INDIAVIX", "label": "INDIA VIX", "names": ["India VIX"], "exchange": "NSE"},
    {"code": "NIFTYNEXT50", "label": "NIFTY NEXT 50", "names": ["Nifty Next 50"], "exchange": "NSE"},
    {"code": "NIFTYMIDCAP", "label": "NIFTY MIDCAP 100", "names": ["Nifty Midcap 100", "NIFTY MIDCAP 100"],
     "exchange": "NSE"},
    {"code": "NIFTYSMALLCAP", "label": "NIFTY SMALLCAP 100",
     "names": ["Nifty Smallcap 100", "NIFTY SMLCAP 100"], "exchange": "NSE"},
]

DEFAULTS: dict[str, dict[str, Any]] = {
    "ai": {
        "provider": "anthropic",
        "model": "claude-opus-5-5",
        "chat_effort": "medium",
        "report_effort": "high",
        "enabled": True,
        "use_ai_news_sentiment": False,
    },
    "refresh": {
        "stale_after_seconds": 30,
        "metrics_refresh_minutes": 15,
        "news_refresh_minutes": 15,
        "alert_eval_seconds": 30,
        "quote_poll_seconds": 5,
        "candle_backfill_days": 400,
    },
    "market": {
        "timezone": "Asia/Kolkata",
        "primary_exchange": "NSE",
        "benchmark_index": "NIFTY50",
        "dashboard_indices": DEFAULT_DASHBOARD_INDICES,
        "tracked_universe_index": "NIFTY50",
        "max_stream_instruments": 500,
    },
    "notifications": {
        "in_app": True,
        "email": {"enabled": False, "smtp_host": "", "smtp_port": 587, "smtp_username": "",
                  "from_address": "", "use_tls": True},
        "telegram": {"enabled": False, "chat_id": ""},
    },
    "ml": {
        "horizon_days": 5,
        "neutral_band_atr": 0.5,
        "min_training_rows": 500,
        "promotion_min_f1_gain": 0.0,
        "auto_retrain_weekly": True,
    },
}

SECRET_FIELDS = {"smtp_password", "telegram_bot_token"}


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


async def get_section(db: AsyncSession, key: str) -> dict[str, Any]:
    row = await db.get(AppSetting, key)
    base = DEFAULTS.get(key, {})
    if key == "ai":
        base = {**base, "model": get_settings().ai_default_model}
    return _deep_merge(base, row.value if row else {})


async def get_all(db: AsyncSession) -> dict[str, dict[str, Any]]:
    return {key: await get_section(db, key) for key in DEFAULTS}


async def update_section(db: AsyncSession, key: str, value: dict[str, Any], user_id: uuid.UUID | None) -> dict:
    if key not in DEFAULTS:
        raise KeyError(key)
    row = await db.get(AppSetting, key)
    merged = _deep_merge(row.value if row else {}, value)
    if row is None:
        db.add(AppSetting(key=key, value=merged, updated_by=user_id))
    else:
        row.value = merged
        row.updated_by = user_id
    await db.flush()
    return await get_section(db, key)


async def get_secrets(db: AsyncSession) -> dict[str, Any]:
    row = await db.get(AppSetting, "secrets")
    return decrypt_json(row.value.get("encrypted")) if row else {}


async def set_secrets(db: AsyncSession, values: dict[str, str | None], user_id: uuid.UUID | None) -> None:
    current = await get_secrets(db)
    for k, v in values.items():
        if k not in SECRET_FIELDS:
            continue
        if v:
            current[k] = v
        elif v == "":
            current.pop(k, None)
    row = await db.get(AppSetting, "secrets")
    payload = {"encrypted": encrypt_json(current)}
    if row is None:
        db.add(AppSetting(key="secrets", value=payload, updated_by=user_id))
    else:
        row.value = payload
        row.updated_by = user_id
    await db.flush()


async def secrets_status(db: AsyncSession) -> dict[str, bool]:
    current = await get_secrets(db)
    return {k: bool(current.get(k)) for k in sorted(SECRET_FIELDS)}
