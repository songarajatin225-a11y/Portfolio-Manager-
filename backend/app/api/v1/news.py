from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, Field, HttpUrl
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.api.deps import DB, AdminUser, CurrentUser
from app.core.errors import ConflictError, NotFoundError
from app.db.models import Instrument, NewsArticle, NewsArticleInstrument, NewsSource
from app.news.ingest import ingest_all
from app.services import instruments as instrument_service
from app.services.audit import audit

router = APIRouter(prefix="/news", tags=["news"])


@router.get("")
async def list_news(
    _: CurrentUser,
    db: DB,
    symbol: str | None = Query(None, max_length=80),
    sentiment: str | None = Query(None, pattern="^(positive|negative|neutral)$"),
    impact: str | None = Query(None, pattern="^(high|normal)$"),
    category: str | None = Query(None, max_length=50),
    q: str | None = Query(None, max_length=100),
    days: int = Query(7, ge=1, le=90),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    stmt = select(NewsArticle).where(NewsArticle.published_at >= datetime.now(UTC) - timedelta(days=days))
    if symbol:
        inst = await instrument_service.resolve_symbol(db, symbol)
        stmt = stmt.join(NewsArticleInstrument, NewsArticleInstrument.article_id == NewsArticle.id).where(
            NewsArticleInstrument.instrument_id == inst.id
        )
    if sentiment:
        stmt = stmt.where(NewsArticle.sentiment_label == sentiment)
    if impact:
        stmt = stmt.where(NewsArticle.impact == impact)
    if category:
        stmt = stmt.where(NewsArticle.category == category)
    if q:
        stmt = stmt.where(NewsArticle.title.ilike(f"%{q}%"))
    total = await db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = (await db.scalars(stmt.order_by(NewsArticle.published_at.desc()).limit(limit).offset(offset))).all()
    tags = (
        await db.execute(
            select(NewsArticleInstrument.article_id, Instrument.symbol)
            .join(Instrument, Instrument.id == NewsArticleInstrument.instrument_id)
            .where(NewsArticleInstrument.article_id.in_([a.id for a in rows]))
        )
    ).all()
    by_article: dict[int, list[str]] = {}
    for aid, sym in tags:
        by_article.setdefault(aid, []).append(sym)
    sentiment_counts = dict(
        (
            await db.execute(
                select(NewsArticle.sentiment_label, func.count())
                .where(NewsArticle.published_at >= datetime.now(UTC) - timedelta(days=days))
                .group_by(NewsArticle.sentiment_label)
            )
        ).all()
    )
    return {
        "total": total,
        "sentiment_counts": sentiment_counts,
        "items": [
            {
                "id": a.id,
                "title": a.title,
                "summary": a.summary,
                "url": a.url,
                "source": a.source_name,
                "category": a.category,
                "published_at": a.published_at,
                "sentiment": a.sentiment_label,
                "sentiment_score": a.sentiment_score,
                "sentiment_confidence": a.sentiment_confidence,
                "sentiment_method": a.sentiment_method,
                "impact": a.impact,
                "symbols": by_article.get(a.id, []),
            }
            for a in rows
        ],
    }


class SourceIn(BaseModel):
    name: str = Field(min_length=1, max_length=150)
    url: HttpUrl
    category: str = Field(default="market", pattern="^(market|announcements|regulatory|economy|company)$")
    is_active: bool = True


def _source(s: NewsSource) -> dict[str, Any]:
    return {
        "id": s.id,
        "name": s.name,
        "url": s.url,
        "category": s.category,
        "is_active": s.is_active,
        "last_fetched_at": s.last_fetched_at,
        "last_error": s.last_error,
    }


@router.get("/sources")
async def sources(_: CurrentUser, db: DB) -> list[dict[str, Any]]:
    return [_source(s) for s in (await db.scalars(select(NewsSource).order_by(NewsSource.name))).all()]


@router.post("/sources", status_code=201)
async def add_source(body: SourceIn, request: Request, admin: AdminUser, db: DB) -> dict[str, Any]:
    s = NewsSource(name=body.name, url=str(body.url), category=body.category, is_active=body.is_active)
    db.add(s)
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        raise ConflictError("This feed URL is already configured") from exc
    await audit(db, action="news.source_add", user_id=admin.id, request=request, details={"url": s.url})
    await db.commit()
    return _source(s)


@router.patch("/sources/{source_id}")
async def update_source(source_id: int, body: SourceIn, admin: AdminUser, db: DB) -> dict[str, Any]:
    s = await db.get(NewsSource, source_id)
    if s is None:
        raise NotFoundError("Source not found")
    s.name, s.url, s.category, s.is_active = body.name, str(body.url), body.category, body.is_active
    await db.commit()
    return _source(s)


@router.delete("/sources/{source_id}", status_code=204)
async def delete_source(source_id: int, request: Request, admin: AdminUser, db: DB) -> None:
    s = await db.get(NewsSource, source_id)
    if s is None:
        raise NotFoundError("Source not found")
    await db.delete(s)
    await audit(db, action="news.source_delete", user_id=admin.id, request=request, entity_id=source_id)
    await db.commit()


@router.post("/ingest")
async def ingest(request: Request, admin: AdminUser, db: DB) -> dict[str, Any]:
    await audit(db, action="news.ingest", user_id=admin.id, request=request, commit=True)
    return await ingest_all(db)
