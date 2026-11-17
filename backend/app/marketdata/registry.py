"""Provider registry and factory: builds a provider instance from a stored ``ProviderConfig``."""

from __future__ import annotations

from typing import Any

import httpx

from app.core.config import get_settings
from app.core.crypto import decrypt_json
from app.core.errors import AppError
from app.db.models import ProviderConfig
from app.marketdata.angelone import AngelOneProvider
from app.marketdata.base import MarketDataProvider, ProviderSettings
from app.marketdata.custom import CustomRestProvider, CustomWebSocketProvider
from app.marketdata.simulated import SimulatedProvider
from app.marketdata.upstox import UpstoxProvider

PROVIDERS: dict[str, type[MarketDataProvider]] = {
    p.provider_type: p
    for p in (UpstoxProvider, AngelOneProvider, CustomRestProvider, CustomWebSocketProvider, SimulatedProvider)
}


def provider_types() -> list[dict[str, Any]]:
    allow_sim = get_settings().allow_simulated_feed
    out = []
    for cls in PROVIDERS.values():
        if cls.is_simulated and not allow_sim:
            continue
        out.append(
            {
                "type": cls.provider_type,
                "name": cls.display_name,
                "supports_streaming": cls.supports_streaming,
                "is_simulated": cls.is_simulated,
                "default_rest_url": cls.default_rest_url,
                "default_ws_url": cls.default_ws_url,
                "docs_url": cls.docs_url,
                "notes": cls.notes,
                "credential_fields": [vars(f) | {} for f in cls.credential_fields],
            }
        )
    return out


def settings_from_config(cfg: ProviderConfig) -> ProviderSettings:
    return ProviderSettings(
        provider_type=cfg.provider_type,
        name=cfg.name,
        rest_url=cfg.rest_url,
        ws_url=cfg.ws_url,
        exchange=cfg.exchange,
        data_interval=cfg.data_interval,
        feed_type=cfg.feed_type,
        options=dict(cfg.options or {}),
    )


def build_provider(cfg: ProviderConfig, http: httpx.AsyncClient | None = None) -> MarketDataProvider:
    cls = PROVIDERS.get(cfg.provider_type)
    if cls is None:
        raise AppError(f"Unknown provider type '{cfg.provider_type}'")
    if cls.is_simulated and not get_settings().allow_simulated_feed:
        raise AppError(
            "The simulated feed is disabled on this server (ALLOW_SIMULATED_FEED=false)", code="simulated_disabled"
        )
    return cls(settings_from_config(cfg), decrypt_json(cfg.encrypted_credentials), http=http)
