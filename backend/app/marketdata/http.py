"""HTTP helpers for providers: token-bucket rate limiting and retries with backoff."""

from __future__ import annotations

import asyncio
import logging
import random
import time
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class RateLimiter:
    """Async token bucket. ``rate`` tokens per second, burst of ``max(1, rate)``."""

    def __init__(self, rate: float) -> None:
        self.rate = max(rate, 0.1)
        self.capacity = max(1.0, rate)
        self.tokens = self.capacity
        self.updated = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            while True:
                now = time.monotonic()
                self.tokens = min(self.capacity, self.tokens + (now - self.updated) * self.rate)
                self.updated = now
                if self.tokens >= 1:
                    self.tokens -= 1
                    return
                await asyncio.sleep((1 - self.tokens) / self.rate)


RETRY_STATUSES = {429, 500, 502, 503, 504}


async def request_with_retry(
    http: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    limiter: RateLimiter | None = None,
    retries: int = 3,
    base_delay: float = 0.5,
    **kwargs: Any,
) -> httpx.Response:
    """Send a request, retrying network errors, 429 and 5xx with exponential backoff + jitter.

    Raises the provider-neutral errors from ``app.marketdata.base`` so callers never deal with
    raw HTTP status codes.
    """
    from app.marketdata.base import AuthenticationRequired, ProviderError, ProviderRateLimited

    last_exc: Exception | None = None
    for attempt in range(retries + 1):
        if limiter:
            await limiter.acquire()
        try:
            resp = await http.request(method, url, **kwargs)
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            last_exc = exc
            logger.warning("Provider request failed (%s) attempt %d/%d", type(exc).__name__, attempt + 1, retries + 1)
        else:
            if resp.status_code in (401, 403):
                raise AuthenticationRequired(f"Provider rejected credentials (HTTP {resp.status_code})")
            if resp.status_code not in RETRY_STATUSES:
                if resp.status_code >= 400:
                    raise ProviderError(f"Provider returned HTTP {resp.status_code}: {resp.text[:300]}")
                return resp
            retry_after = resp.headers.get("retry-after")
            if resp.status_code == 429 and attempt == retries:
                raise ProviderRateLimited("Provider rate limit exceeded", float(retry_after) if retry_after else None)
            last_exc = ProviderError(f"HTTP {resp.status_code}")
            if retry_after:
                try:
                    await asyncio.sleep(min(float(retry_after), 30))
                    continue
                except ValueError:
                    pass
        if attempt < retries:
            await asyncio.sleep(base_delay * (2**attempt) + random.uniform(0, base_delay))
    raise ProviderError(f"Provider request failed after {retries + 1} attempts: {last_exc}")


def backoff_delays(base: float = 1.0, cap: float = 60.0):
    """Infinite generator of reconnection delays: exponential with full jitter."""
    attempt = 0
    while True:
        yield random.uniform(0, min(cap, base * (2**attempt)))
        attempt = min(attempt + 1, 16)
