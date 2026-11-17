from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.ai.assistant import chat
from app.ai.client import ai_configured
from app.ai.reports import market_summary, research_report
from app.api.deps import DB, CurrentUser, WriterUser
from app.core.errors import NotFoundError
from app.core.ratelimit import enforce_rate_limit
from app.db.models import AIConversation, AIMessage, AIReport, Instrument
from app.services import app_settings

router = APIRouter(prefix="/ai", tags=["ai"])
AI_RATE_PER_MIN = 15


def _report(r: AIReport, symbol: str | None = None) -> dict[str, Any]:
    return {
        "id": r.id,
        "kind": r.kind,
        "instrument_id": r.instrument_id,
        "symbol": symbol or r.content.get("symbol"),
        "model": r.model,
        "content": r.content,
        "sources": r.sources,
        "data_quality": r.data_quality,
        "usage": r.usage,
        "created_at": r.created_at,
    }


@router.get("/status")
async def status(_: CurrentUser, db: DB) -> dict[str, Any]:
    cfg = await app_settings.get_section(db, "ai")
    return {
        "configured": ai_configured(),
        "enabled": cfg.get("enabled", True),
        "model": cfg["model"],
        "provider": cfg.get("provider", "anthropic"),
    }


class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    conversation_id: int | None = None


@router.post("/chat")
async def chat_endpoint(body: ChatIn, user: WriterUser, db: DB) -> dict[str, Any]:
    await enforce_rate_limit(f"ai:{user.id}", AI_RATE_PER_MIN)
    return await chat(db, user.id, body.message.strip(), body.conversation_id)


@router.get("/conversations")
async def conversations(user: CurrentUser, db: DB) -> list[dict[str, Any]]:
    rows = (
        await db.scalars(
            select(AIConversation)
            .where(AIConversation.user_id == user.id)
            .order_by(AIConversation.updated_at.desc())
            .limit(50)
        )
    ).all()
    return [{"id": c.id, "title": c.title, "updated_at": c.updated_at} for c in rows]


@router.get("/conversations/{conversation_id}")
async def conversation(conversation_id: int, user: CurrentUser, db: DB) -> dict[str, Any]:
    conv = await db.get(AIConversation, conversation_id)
    if conv is None or conv.user_id != user.id:
        raise NotFoundError("Conversation not found")
    msgs = (
        await db.scalars(select(AIMessage).where(AIMessage.conversation_id == conv.id).order_by(AIMessage.id))
    ).all()
    return {
        "id": conv.id,
        "title": conv.title,
        "messages": [
            {
                "id": m.id,
                "role": m.role,
                "content": m.content,
                "sources": m.sources,
                "tool_calls": m.tool_calls,
                "created_at": m.created_at,
            }
            for m in msgs
        ],
    }


@router.delete("/conversations/{conversation_id}", status_code=204)
async def delete_conversation(conversation_id: int, user: WriterUser, db: DB) -> None:
    conv = await db.get(AIConversation, conversation_id)
    if conv is None or conv.user_id != user.id:
        raise NotFoundError("Conversation not found")
    await db.delete(conv)
    await db.commit()


class ReportIn(BaseModel):
    symbol: str = Field(min_length=1, max_length=80)


@router.post("/reports", status_code=201)
async def create_report(body: ReportIn, user: WriterUser, db: DB) -> dict[str, Any]:
    await enforce_rate_limit(f"ai:{user.id}", AI_RATE_PER_MIN)
    return _report(await research_report(db, user.id, body.symbol))


@router.get("/reports")
async def list_reports(
    user: CurrentUser,
    db: DB,
    symbol: str | None = Query(None, max_length=80),
    kind: str = Query("research_report", pattern="^(research_report|market_summary)$"),
) -> list:
    stmt = (
        select(AIReport, Instrument.symbol)
        .outerjoin(Instrument, Instrument.id == AIReport.instrument_id)
        .where(AIReport.user_id == user.id, AIReport.kind == kind)
    )
    if symbol:
        stmt = stmt.where(Instrument.symbol == symbol.upper())
    rows = (await db.execute(stmt.order_by(AIReport.created_at.desc()).limit(50))).all()
    return [
        {
            "id": r.id,
            "kind": r.kind,
            "symbol": sym,
            "model": r.model,
            "created_at": r.created_at,
            "headline": r.content.get("headline") or (r.content.get("investment_thesis") or "")[:160],
            "completeness": (r.data_quality or {}).get("completeness"),
        }
        for r, sym in rows
    ]


@router.get("/reports/{report_id}")
async def get_report(report_id: int, user: CurrentUser, db: DB) -> dict[str, Any]:
    r = await db.get(AIReport, report_id)
    if r is None or r.user_id != user.id:
        raise NotFoundError("Report not found")
    return _report(r)


@router.post("/market-summary", status_code=201)
async def create_market_summary(user: WriterUser, db: DB) -> dict[str, Any]:
    await enforce_rate_limit(f"ai:{user.id}", AI_RATE_PER_MIN)
    return _report(await market_summary(db, user.id))


@router.get("/market-summary/latest")
async def latest_market_summary(user: CurrentUser, db: DB) -> dict[str, Any] | None:
    r = await db.scalar(
        select(AIReport).where(AIReport.kind == "market_summary").order_by(AIReport.created_at.desc()).limit(1)
    )
    return _report(r) if r else None
