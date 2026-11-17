"""Configurable providers for any JSON REST or JSON WebSocket market data API.

Everything provider-specific lives in ``options`` (editable in the admin panel):

REST options
  quote_path        "/v1/quotes?symbols={keys}"     ({keys} = comma-joined provider keys)
  quote_items_path  "data"                           (list, or dict keyed by symbol)
  quote_map         {"key": "symbol", "ltp": "last", "prev_close": "close", ...}
  candles_path      "/v1/candles/{key}?interval={interval}&from={start}&to={end}"
  candles_items_path "data.candles"
  candle_columns    ["ts", "open", "high", "low", "close", "volume"]  (row arrays) or
  candle_map        {"ts": "t", "open": "o", ...}                      (row objects)
  interval_map      {"1d": "day", "1m": "minute"}
  auth_header       "Authorization" / "X-API-Key"; auth_scheme "Bearer" or "" ; credential "api_key"
  delayed           true if the vendor's data is delayed (labels quotes DELAYED)
  max_keys_per_request 50

WebSocket options (plus quote_map / delayed)
  subscribe_message  {"action": "subscribe", "symbols": "{keys_list}"}  ({keys_list} -> JSON array)
  ws_items_path      "data"
  heartbeat_message  "ping"; heartbeat_seconds 20
  ws_auth_query      "token"  (append ?token=<credential> to the URL) or ws_auth_header like auth_header
"""

from __future__ import annotations

import asyncio
import contextlib
import copy
import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qsl, quote, urlencode, urlparse, urlunparse

import websockets

from app.marketdata.base import CredentialField, MarketDataProvider, NotSupported, ProviderError
from app.marketdata.http import request_with_retry
from app.marketdata.mapping import DEFAULT_QUOTE_MAP, extract_items, get_path, map_quote, parse_timestamp
from app.marketdata.types import CandleBar, Quote


class _CustomBase(MarketDataProvider):
    credential_fields = [
        CredentialField("api_key", "API key / token", required=False),
        CredentialField("api_secret", "API secret", required=False),
        CredentialField("access_token", "Access token", required=False),
    ]

    @property
    def opts(self) -> dict[str, Any]:
        return self.settings.options

    @property
    def source_name(self) -> str:
        return f"custom:{self.settings.name}"

    def _credential(self) -> str | None:
        name = self.opts.get("credential", "api_key")
        return self.credentials.get(name) or self.credentials.get("access_token") or self.credentials.get("api_key")

    def _auth_headers(self, header_opt: str = "auth_header") -> dict[str, str]:
        header = self.opts.get(header_opt)
        cred = self._credential()
        if not header or not cred:
            return {}
        scheme = self.opts.get("auth_scheme", "Bearer" if header.lower() == "authorization" else "")
        return {header: f"{scheme} {cred}".strip()}

    def _quote_map(self) -> dict[str, str]:
        return {**DEFAULT_QUOTE_MAP, **(self.opts.get("quote_map") or {})}

    async def authenticate(self) -> None:
        return None  # static API keys; failures surface as AuthenticationRequired on the first request


class CustomRestProvider(_CustomBase):
    provider_type = "custom_rest"
    display_name = "Custom REST provider"
    supports_streaming = False
    notes = "Polls any JSON REST API. Configure paths and field mappings in Options."

    async def get_quotes(self, provider_keys: list[str]) -> list[Quote]:
        path_tpl = self.opts.get("quote_path")
        if not path_tpl:
            raise ProviderError("quote_path option is not configured")
        batch = int(self.opts.get("max_keys_per_request", 50))
        out: list[Quote] = []
        for i in range(0, len(provider_keys), batch):
            keys = provider_keys[i : i + batch]
            path = path_tpl.replace("{keys}", quote(",".join(keys), safe=",|:"))
            resp = await request_with_retry(
                self.http,
                "GET",
                f"{self.rest_url}{path}",
                limiter=self.rate_limiter,
                headers={"Accept": "application/json", **self._auth_headers()},
            )
            now = datetime.now(UTC)
            for dict_key, item in extract_items(resp.json(), self.opts.get("quote_items_path")):
                key_override = dict_key if self.opts.get("quote_key_from_dict") else None
                q = map_quote(
                    item,
                    self._quote_map(),
                    source=self.source_name,
                    received_at=now,
                    key_override=key_override,
                    delayed=bool(self.opts.get("delayed")),
                )
                if q:
                    out.append(q)
        return out

    async def get_candles(self, provider_key: str, interval: str, start: datetime, end: datetime) -> list[CandleBar]:
        tpl = self.opts.get("candles_path")
        if not tpl:
            raise NotSupported("candles_path option is not configured")
        vendor_interval = (self.opts.get("interval_map") or {}).get(interval, interval)
        fmt = self.opts.get("date_format", "%Y-%m-%d")
        path = (
            tpl.replace("{key}", quote(provider_key, safe=""))
            .replace("{interval}", vendor_interval)
            .replace("{start}", start.strftime(fmt))
            .replace("{end}", end.strftime(fmt))
        )
        resp = await request_with_retry(
            self.http,
            "GET",
            f"{self.rest_url}{path}",
            limiter=self.rate_limiter,
            headers={"Accept": "application/json", **self._auth_headers()},
        )
        rows = get_path(resp.json(), self.opts.get("candles_items_path")) or []
        cols = self.opts.get("candle_columns") or ["ts", "open", "high", "low", "close", "volume"]
        cmap = self.opts.get("candle_map") or {}
        bars: list[CandleBar] = []
        for row in rows:
            try:
                if isinstance(row, list):
                    rec = dict(zip(cols, row, strict=False))
                else:
                    rec = {k: get_path(row, cmap.get(k, k)) for k in ("ts", "open", "high", "low", "close", "volume")}
                ts = parse_timestamp(rec["ts"])
                if ts is None:
                    continue
                bars.append(
                    CandleBar(
                        ts=ts.astimezone(UTC),
                        open=float(rec["open"]),
                        high=float(rec["high"]),
                        low=float(rec["low"]),
                        close=float(rec["close"]),
                        volume=float(rec.get("volume") or 0),
                    )
                )
            except (KeyError, TypeError, ValueError):
                continue
        return sorted(bars, key=lambda b: b.ts)


class CustomWebSocketProvider(CustomRestProvider):
    provider_type = "custom_ws"
    display_name = "Custom WebSocket provider"
    supports_streaming = True
    notes = "Streams any JSON WebSocket feed; optional REST paths are used for snapshots and candles."

    async def get_quotes(self, provider_keys: list[str]) -> list[Quote]:
        if not self.opts.get("quote_path"):
            return []  # snapshot is optional for streaming-only vendors
        return await super().get_quotes(provider_keys)

    def _ws_url(self) -> str:
        url = self.ws_url
        if not url:
            raise ProviderError("WebSocket URL is not configured")
        q_name = self.opts.get("ws_auth_query")
        cred = self._credential()
        if q_name and cred:
            parts = urlparse(url)
            query = dict(parse_qsl(parts.query))
            query[q_name] = cred
            url = urlunparse(parts._replace(query=urlencode(query)))
        return url

    def _subscribe_payload(self, keys: list[str]) -> str:
        template = copy.deepcopy(
            self.opts.get("subscribe_message") or {"action": "subscribe", "symbols": "{keys_list}"}
        )

        def fill(node: Any) -> Any:
            if isinstance(node, dict):
                return {k: fill(v) for k, v in node.items()}
            if isinstance(node, list):
                return [fill(v) for v in node]
            if node == "{keys_list}":
                return keys
            if isinstance(node, str):
                return node.replace("{keys}", ",".join(keys))
            return node

        return json.dumps(fill(template))

    async def stream(self, provider_keys: list[str]) -> AsyncIterator[list[Quote]]:  # type: ignore[override]
        headers = self._auth_headers("ws_auth_header") or None
        async with websockets.connect(
            self._ws_url(), additional_headers=headers, ping_interval=20, open_timeout=15
        ) as ws:
            await ws.send(self._subscribe_payload(provider_keys))
            hb_msg = self.opts.get("heartbeat_message")
            hb_task = None
            if hb_msg:

                async def heartbeat() -> None:
                    while True:
                        await asyncio.sleep(float(self.opts.get("heartbeat_seconds", 20)))
                        await ws.send(hb_msg)

                hb_task = asyncio.create_task(heartbeat())
            try:
                async for frame in ws:
                    try:
                        body = json.loads(frame)
                    except (ValueError, TypeError):
                        continue
                    now = datetime.now(UTC)
                    quotes = [
                        q
                        for _, item in extract_items(body, self.opts.get("ws_items_path"))
                        if (
                            q := map_quote(
                                item,
                                self._quote_map(),
                                source=self.source_name,
                                received_at=now,
                                delayed=bool(self.opts.get("delayed")),
                            )
                        )
                    ]
                    if quotes:
                        yield quotes
            finally:
                if hb_task:
                    hb_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await hb_task
