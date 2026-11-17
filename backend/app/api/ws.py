"""Browser WebSocket: streams ticks, feed status and the user's notifications.

Auth uses a short-lived ticket from ``POST /api/v1/auth/ws-ticket`` because browsers cannot set
headers on WebSocket requests and cookies may not reach a backend on another origin.

Client messages:
  {"action": "subscribe", "instrument_ids": [1, 2, 3]}   - filter ticks to these instruments
  {"action": "subscribe_all"}                           - receive every tick
  {"action": "ping"}
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.core.bus import CH_FEED_STATUS, CH_NOTIFICATIONS, CH_TICKS, K_FEED_HEALTH, get_bus
from app.core.config import get_settings
from app.core.security import TokenError, decode_token

logger = logging.getLogger(__name__)
ws_router = APIRouter()
MAX_SUBSCRIPTIONS = 1000


@ws_router.websocket("/ws/stream")
async def stream(websocket: WebSocket) -> None:
    origin = websocket.headers.get("origin")
    settings = get_settings()
    if origin and origin not in settings.cors_origins:
        await websocket.close(code=4403)
        return
    ticket = websocket.query_params.get("ticket", "")
    try:
        claims = decode_token(ticket, "ws")
    except TokenError:
        await websocket.close(code=4401)
        return
    user_id = claims["sub"]
    await websocket.accept()

    bus = get_bus()
    wanted: set[int] | None = set()  # None = everything
    health = await bus.get_json(K_FEED_HEALTH)
    if health:
        await websocket.send_json({"type": "feed_status", "data": health})

    async def pump() -> None:
        async for channel, message in bus.subscribe(CH_TICKS, CH_FEED_STATUS, CH_NOTIFICATIONS):
            if channel == CH_TICKS:
                quotes = message.get("quotes", [])
                if wanted is not None:
                    quotes = [q for q in quotes if q.get("instrument_id") in wanted]
                if quotes:
                    await websocket.send_json({"type": "ticks", "data": quotes})
            elif channel == CH_FEED_STATUS:
                await websocket.send_json({"type": "feed_status", "data": message})
            elif channel == CH_NOTIFICATIONS and message.get("user_id") == user_id:
                await websocket.send_json({"type": "notification", "data": message})

    pump_task = asyncio.create_task(pump())
    try:
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except ValueError:
                continue
            action = msg.get("action")
            if action == "subscribe":
                ids = [int(i) for i in msg.get("instrument_ids", [])[:MAX_SUBSCRIPTIONS] if str(i).isdigit()]
                wanted = set(ids)
            elif action == "subscribe_all":
                wanted = None
            elif action == "ping":
                await websocket.send_json({"type": "pong"})
    except WebSocketDisconnect:
        pass
    except Exception:  # noqa: BLE001
        logger.exception("WebSocket session error")
    finally:
        pump_task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await pump_task
