"""Research assistant: a Claude tool-use loop over the platform's own data.

The system prompt and tool list are fixed for every request (so earlier turns stay valid and the
prompt prefix is cached); per-turn context such as the current time goes into the user message.
Earlier turns are replayed as plain text; within a turn, assistant content is echoed back unchanged.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import tools as ai_tools
from app.ai.client import block_type, create_message, echo_content, text_of, usage_dict
from app.core.errors import AppError, NotFoundError
from app.db.models import AIConversation, AIMessage
from app.marketdata.calendar import IST
from app.services import app_settings
from app.services.market_data import data_mode

SYSTEM_PROMPT = """You are MarketIntel's research assistant for Indian equity markets (NSE and BSE).

Ground rules:
1. Use only data returned by your tools in this conversation. Never invent prices, financial statements, \
corporate announcements, analyst estimates or news. If a tool returns no data or an error, say what is missing \
instead of estimating it.
2. Cite what you rely on: for each figure give its source and timestamp, and its data label (LIVE, DELAYED, \
HISTORICAL or SIMULATED). If any data is SIMULATED, say plainly that it is simulated and not real market data.
3. Separate facts from interpretation. Machine-learning outputs are statistical probabilities from models with \
stated out-of-sample accuracy - never present them as certain predictions.
4. Provide research and education, not investment advice: do not tell the user to buy, sell or hold. Discuss \
drivers, scenarios and risks, and mention material risks.
5. Times are IST unless stated. Use ₹ and Indian conventions (lakh, crore) where natural.
6. Be concise and structured: short paragraphs or bullets, and tables when comparing companies."""

MAX_TOOL_ROUNDS = 8
HISTORY_TURNS = 10


def _dedupe_sources(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen, out = set(), []
    for s in items:
        key = (s.get("type"), s.get("name"), s.get("as_of"))
        if key not in seen:
            seen.add(key)
            out.append(s)
    return out


async def run_tool_loop(
    db: AsyncSession,
    user_id: uuid.UUID,
    *,
    model: str,
    effort: str,
    messages: list[dict[str, Any]],
    system: str = SYSTEM_PROMPT,
) -> dict[str, Any]:
    sources: list[dict[str, Any]] = []
    calls: list[dict[str, Any]] = []
    usage: dict[str, int] = {}
    served_by = model
    for _ in range(MAX_TOOL_ROUNDS):
        resp = await create_message(
            model=model,
            max_tokens=16000,
            system=system,
            tools=ai_tools.definitions(),
            messages=messages,
            output_config={"effort": effort},
            cache_control={"type": "ephemeral"},
        )
        for k, v in usage_dict(resp).items():
            usage[k] = usage.get(k, 0) + int(v)
        served_by = getattr(resp, "model", model) or model
        if resp.stop_reason == "refusal":
            details = getattr(resp, "stop_details", None)
            category = getattr(details, "category", None) if details else None
            return {
                "answer": "Claude declined to answer this request" + (f" ({category})." if category else "."),
                "refused": True,
                "sources": _dedupe_sources(sources),
                "tool_calls": calls,
                "usage": usage,
                "model": served_by,
            }
        tool_uses = [b for b in resp.content if block_type(b) == "tool_use"]
        if resp.stop_reason == "tool_use" and tool_uses:
            messages.append({"role": "assistant", "content": echo_content(resp.content)})
            results = []
            for b in tool_uses:
                args = b.input if isinstance(b.input, dict) else json.loads(b.input or "{}")
                output, is_error, srcs = await ai_tools.execute(db, user_id, b.name, args)
                sources.extend(srcs)
                calls.append({"tool": b.name, "input": args, "is_error": is_error})
                results.append({"type": "tool_result", "tool_use_id": b.id, "content": output, "is_error": is_error})
            messages.append({"role": "user", "content": results})
            continue
        if resp.stop_reason == "pause_turn":
            messages.append({"role": "assistant", "content": echo_content(resp.content)})
            continue
        answer = text_of(resp.content)
        if resp.stop_reason == "max_tokens":
            answer += "\n\n_(Response truncated at the length limit.)_"
        return {
            "answer": answer,
            "refused": False,
            "sources": _dedupe_sources(sources),
            "tool_calls": calls,
            "usage": usage,
            "model": served_by,
        }
    return {
        "answer": "I could not complete this request within the tool-call limit. Try a narrower question.",
        "refused": False,
        "sources": _dedupe_sources(sources),
        "tool_calls": calls,
        "usage": usage,
        "model": served_by,
    }


async def ai_config(db: AsyncSession) -> dict[str, Any]:
    cfg = await app_settings.get_section(db, "ai")
    if not cfg.get("enabled", True):
        raise AppError("AI features are disabled in platform settings", code="ai_disabled", status_code=403)
    return cfg


async def context_line(db: AsyncSession) -> str:
    now = datetime.now(UTC).astimezone(IST)
    mode = await data_mode(db)
    note = " All market data on this instance is SIMULATED." if mode == "simulated" else ""
    return f"[Context: current time {now:%Y-%m-%d %H:%M} IST; data mode: {mode}.{note}]"


async def chat(db: AsyncSession, user_id: uuid.UUID, message: str, conversation_id: int | None) -> dict[str, Any]:
    cfg = await ai_config(db)
    if conversation_id is not None:
        conv = await db.get(AIConversation, conversation_id)
        if conv is None or conv.user_id != user_id:
            raise NotFoundError("Conversation not found")
    else:
        conv = AIConversation(user_id=user_id, title=message[:80])
        db.add(conv)
        await db.flush()
    history = (
        await db.scalars(
            select(AIMessage)
            .where(AIMessage.conversation_id == conv.id)
            .order_by(AIMessage.id.desc())
            .limit(HISTORY_TURNS * 2)
        )
    ).all()
    messages: list[dict[str, Any]] = [{"role": m.role, "content": m.content} for m in reversed(history)]
    messages.append({"role": "user", "content": f"{await context_line(db)}\n\n{message}"})
    result = await run_tool_loop(db, user_id, model=cfg["model"], effort=cfg["chat_effort"], messages=messages)
    db.add(AIMessage(conversation_id=conv.id, role="user", content=message))
    db.add(
        AIMessage(
            conversation_id=conv.id,
            role="assistant",
            content=result["answer"],
            sources=result["sources"],
            tool_calls=result["tool_calls"],
        )
    )
    conv.updated_at = datetime.now(UTC)
    await db.commit()
    return {"conversation_id": conv.id, **result}
