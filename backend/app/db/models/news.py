from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Float, ForeignKey, Identity, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class NewsSource(TimestampMixin, Base):
    __tablename__ = "news_sources"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    name: Mapped[str] = mapped_column(String(150))
    url: Mapped[str] = mapped_column(String(1000), unique=True)
    kind: Mapped[str] = mapped_column(String(20), default="rss")
    category: Mapped[str] = mapped_column(String(50), default="market")  # market | announcements | regulatory
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    etag: Mapped[str | None] = mapped_column(String(300))
    modified: Mapped[str | None] = mapped_column(String(300))


class NewsArticle(Base):
    __tablename__ = "news_articles"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    source_id: Mapped[int | None] = mapped_column(ForeignKey("news_sources.id", ondelete="SET NULL"), index=True)
    source_name: Mapped[str] = mapped_column(String(150))
    url: Mapped[str] = mapped_column(String(2000))
    url_hash: Mapped[str] = mapped_column(String(64), unique=True)
    title_hash: Mapped[str] = mapped_column(String(64), index=True)
    title: Mapped[str] = mapped_column(String(1000))
    summary: Mapped[str] = mapped_column(Text, default="")
    category: Mapped[str] = mapped_column(String(50), default="market")
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    sentiment_label: Mapped[str] = mapped_column(String(10), default="neutral")  # positive | negative | neutral
    sentiment_score: Mapped[float] = mapped_column(Float, default=0.0)
    sentiment_confidence: Mapped[float] = mapped_column(Float, default=0.0)
    sentiment_method: Mapped[str] = mapped_column(String(30), default="lexicon")
    impact: Mapped[str] = mapped_column(String(10), default="normal")  # high | normal


class NewsArticleInstrument(Base):
    __tablename__ = "news_article_instruments"

    article_id: Mapped[int] = mapped_column(ForeignKey("news_articles.id", ondelete="CASCADE"), primary_key=True)
    instrument_id: Mapped[int] = mapped_column(
        ForeignKey("instruments.id", ondelete="CASCADE"), primary_key=True, index=True
    )
    match_type: Mapped[str] = mapped_column(String(20), default="name")
