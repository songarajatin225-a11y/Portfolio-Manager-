"""Optional Claude-based news sentiment (enabled with the ``use_ai_news_sentiment`` AI setting).

New headlines are classified in batches with a strict JSON schema. The method recorded on each
article becomes ``claude:<model>``; articles Claude does not return keep their lexicon labels.
"""

from __future__ import annotations

import json
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.client import ai_configured, create_message, text_of
from app.core.errors import AppError
from app.db.models import NewsArticle
from app.services import app_settings

logger = logging.getLogger(__name__)
BATCH = 40

SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "label": {"type": "string", "enum": ["positive", "negative", "neutral"]},
                    "score": {"type": "number", "description": "-1 (very negative) to 1 (very positive)"},
                    "confidence": {"type": "number", "description": "0 to 1"},
                    "impact": {"type": "string", "enum": ["high", "normal"]},
                },
                "required": ["id", "label", "score", "confidence", "impact"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["items"],
    "additionalProperties": False,
}

SYSTEM = (
    "You classify Indian financial news headlines for equity investors. For each item give the sentiment "
    "for the company or market it concerns (positive, negative or neutral), a score from -1 to 1, your "
    "confidence from 0 to 1, and impact 'high' only for results, corporate actions, regulatory action, "
    "M&A, defaults/fraud, rating changes or macro policy. Use neutral when the headline is factual or "
    "ambiguous. Judge only from the text given."
)


async def classify_with_claude(db: AsyncSession, article_ids: list[int]) -> int:
    cfg = await app_settings.get_section(db, "ai")
    if not article_ids or not cfg.get("use_ai_news_sentiment") or not cfg.get("enabled", True) or not ai_configured():
        return 0
    updated = 0
    for i in range(0, len(article_ids), BATCH):
        rows = (await db.scalars(select(NewsArticle).where(NewsArticle.id.in_(article_ids[i : i + BATCH])))).all()
        items = [{"id": a.id, "headline": a.title, "summary": a.summary[:300]} for a in rows]
        try:
            resp = await create_message(
                model=cfg["model"],
                max_tokens=8000,
                system=SYSTEM,
                messages=[{"role": "user", "content": json.dumps(items)}],
                output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
            )
        except AppError as exc:
            logger.warning("Claude news sentiment skipped: %s", exc)
            return updated
        if resp.stop_reason != "end_turn":
            continue
        try:
            results = {r["id"]: r for r in json.loads(text_of(resp.content))["items"]}
        except (ValueError, KeyError):
            continue
        for a in rows:
            r = results.get(a.id)
            if r is None:
                continue
            a.sentiment_label = r["label"]
            a.sentiment_score = max(-1.0, min(1.0, float(r["score"])))
            a.sentiment_confidence = max(0.0, min(1.0, float(r["confidence"])))
            a.impact = r["impact"]
            a.sentiment_method = f"claude:{getattr(resp, 'model', cfg['model'])}"
            updated += 1
        await db.commit()
    return updated
