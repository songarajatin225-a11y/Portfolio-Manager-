"""Common market data provider interface.

Every provider implements the same contract so switching providers is a configuration change.
Providers are stateless apart from their credentials; anything that must survive restarts
(tokens obtained by logging in) is returned via ``updated_credentials`` and persisted encrypted.
"""

from __future__ import annotations

import abc
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, ClassVar

import httpx

from app.marketdata.http import RateLimiter
from app.marketdata.types import (
    CandleBar,
    ConnectionTestResult,
    HolidayRecord,
    InstrumentRecord,
    MarketStatusInfo,
    Quote,
)


class ProviderError(Exception):
    """Generic provider failure (network, unexpected payload)."""


class AuthenticationRequired(ProviderError):
    """Credentials are missing/expired and need human action (e.g. daily Upstox login)."""


class ProviderRateLimited(ProviderError):
    def __init__(self, message: str, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class NotSupported(ProviderError):
    """The provider does not offer this capability."""


@dataclass
class ProviderSettings:
    provider_type: str
    name: str
    rest_url: str | None = None
    ws_url: str | None = None
    exchange: str = "NSE"
    data_interval: str = "1minute"
    feed_type: str = "full"
    options: dict[str, Any] = field(default_factory=dict)


@dataclass
class CredentialField:
    key: str
    label: str
    secret: bool = True
    required: bool = True
    help: str = ""


class MarketDataProvider(abc.ABC):
    provider_type: ClassVar[str]
    display_name: ClassVar[str]
    supports_streaming: ClassVar[bool] = False
    is_simulated: ClassVar[bool] = False
    default_rest_url: ClassVar[str | None] = None
    default_ws_url: ClassVar[str | None] = None
    credential_fields: ClassVar[list[CredentialField]] = []
    docs_url: ClassVar[str | None] = None
    notes: ClassVar[str] = ""
    # Requests per second budget (conservative defaults; override with options.rate_limit_per_sec)
    default_rate_limit: ClassVar[float] = 5.0

    def __init__(
        self, settings: ProviderSettings, credentials: dict[str, Any], http: httpx.AsyncClient | None = None
    ) -> None:
        self.settings = settings
        self.credentials = dict(credentials)
        self._own_http = http is None
        self.http = http or httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=10.0))
        self.rate_limiter = RateLimiter(float(settings.options.get("rate_limit_per_sec", self.default_rate_limit)))
        self.updated_credentials: dict[str, Any] | None = None
        self.token_expires_at: datetime | None = None

    @property
    def rest_url(self) -> str:
        return (self.settings.rest_url or self.default_rest_url or "").rstrip("/")

    @property
    def ws_url(self) -> str:
        return self.settings.ws_url or self.default_ws_url or ""

    @property
    def source_name(self) -> str:
        return self.provider_type

    async def close(self) -> None:
        if self._own_http:
            await self.http.aclose()

    def _save_credentials(self, **values: Any) -> None:
        self.credentials.update(values)
        self.updated_credentials = dict(self.credentials)

    # --- Capabilities -----------------------------------------------------------------
    @abc.abstractmethod
    async def authenticate(self) -> None:
        """Ensure a valid session exists (login/refresh if possible) or raise AuthenticationRequired."""

    @abc.abstractmethod
    async def get_quotes(self, provider_keys: list[str]) -> list[Quote]: ...

    @abc.abstractmethod
    async def get_candles(self, provider_key: str, interval: str, start: datetime, end: datetime) -> list[CandleBar]:
        """Historical OHLCV bars, ascending by time. ``interval`` is one of types.INTERVALS."""

    async def get_instruments(self) -> list[InstrumentRecord]:
        raise NotSupported(f"{self.display_name} does not provide an instrument master")

    async def get_market_status(self, exchange: str) -> MarketStatusInfo | None:
        return None

    async def get_holidays(self) -> list[HolidayRecord]:
        return []

    def stream(self, provider_keys: list[str]) -> AsyncIterator[list[Quote] | MarketStatusInfo]:
        raise NotSupported(f"{self.display_name} does not support streaming")

    async def test_connection(self, sample_keys: list[str]) -> ConnectionTestResult:
        import time

        started = time.perf_counter()
        await self.authenticate()
        quotes = await self.get_quotes(sample_keys) if sample_keys else []
        latency = (time.perf_counter() - started) * 1000
        if sample_keys and not quotes:
            return ConnectionTestResult(
                False,
                "Authenticated, but no quotes were returned for the sample instruments",
                latency,
                {"sample_keys": sample_keys},
            )
        detail = {
            "sample": [
                {"key": q.provider_key, "ltp": q.ltp, "time": (q.exchange_ts or q.received_at).isoformat()}
                for q in quotes[:3]
            ]
        }
        return ConnectionTestResult(True, "Connection successful", round(latency, 1), detail)
