"""AI trading-journal coach: Claude reviews the user's own journal statistics and deterministic
behaviour findings. It sees only journal data, never invents trades, and does not give trade calls."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.assistant import ai_config, context_line
from app.ai.reports import _structured
from app.db.models import AIReport

COACH_SYSTEM = """You are a trading-performance coach reviewing one trader's own journal.

Rules:
1. Use only the statistics, findings and trades provided. Never invent trades, numbers or market events.
2. Quote the evidence (counts, P&L, R-multiples, trade ids) behind each observation.
3. Focus on process and behaviour: risk management, discipline, consistency, emotional patterns, strategy \
selection. Do not recommend specific securities or tell the trader to buy or sell anything.
4. If the sample is small (fewer than 20 closed trades), say that conclusions are tentative.
5. Be direct, specific and constructive. Amounts are in INR."""

STR = {"type": "string"}
COACH_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "summary": STR,
        "strengths": {"type": "array", "items": STR},
        "weaknesses": {"type": "array", "items": STR},
        "behavioural_patterns": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"pattern": STR, "evidence": STR, "suggestion": STR},
                "required": ["pattern", "evidence", "suggestion"],
                "additionalProperties": False,
            },
        },
        "strategy_review": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"strategy": STR, "assessment": STR},
                "required": ["strategy", "assessment"],
                "additionalProperties": False,
            },
        },
        "rules_to_follow": {"type": "array", "items": STR},
        "sample_size_note": STR,
    },
    "additionalProperties": False,
}
COACH_SCHEMA["required"] = list(COACH_SCHEMA["properties"])


async def journal_coach(
    db: AsyncSession,
    user_id: uuid.UUID,
    stats: dict[str, Any],
    findings: list[dict[str, Any]],
    trades: list[dict[str, Any]],
) -> AIReport:
    cfg = await ai_config(db)
    compact_stats = {k: v for k, v in stats.items() if k != "equity_curve"}
    prompt = (
        f"{await context_line(db)}\n\nReview this trading journal and coach the trader.\n\n"
        f"STATISTICS:\n{json.dumps(compact_stats, default=str)}\n\n"
        f"RULE-BASED FINDINGS:\n{json.dumps(findings, default=str)}\n\n"
        f"MOST RECENT CLOSED TRADES (newest first):\n{json.dumps(trades, default=str)}"
    )
    content, usage, model = await _structured(db, cfg, COACH_SYSTEM, prompt, COACH_SCHEMA)
    report = AIReport(
        user_id=user_id,
        kind="journal_coach",
        model=model,
        content={
            **content,
            "generated_at": datetime.now(UTC).isoformat(),
            "disclaimer": "Coaching on your own journal records. Not investment advice.",
        },
        sources=[
            {
                "type": "journal",
                "name": "Your trading journal",
                "source": "platform journal",
                "as_of": datetime.now(UTC).isoformat(),
                "data_status": None,
            }
        ],
        data_quality={"closed_trades": stats.get("trades", 0), "findings": len(findings)},
        usage=usage,
    )
    db.add(report)
    await db.commit()
    await db.refresh(report)
    return report
