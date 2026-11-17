from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Identity,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Fundamental(Base):
    """One reporting period of financial statement data. Values in INR crore unless ``units`` says otherwise."""

    __tablename__ = "fundamentals"
    __table_args__ = (UniqueConstraint("instrument_id", "period_type", "period_end", "statement_type"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id", ondelete="CASCADE"), index=True)
    period_type: Mapped[str] = mapped_column(String(1))  # Q (quarter) | A (annual)
    period_end: Mapped[date] = mapped_column(Date)
    statement_type: Mapped[str] = mapped_column(String(20), default="consolidated")
    units: Mapped[str] = mapped_column(String(20), default="INR_CRORE")
    revenue: Mapped[float | None] = mapped_column(Float)
    ebitda: Mapped[float | None] = mapped_column(Float)
    ebit: Mapped[float | None] = mapped_column(Float)
    net_profit: Mapped[float | None] = mapped_column(Float)
    eps: Mapped[float | None] = mapped_column(Float)
    total_equity: Mapped[float | None] = mapped_column(Float)
    total_debt: Mapped[float | None] = mapped_column(Float)
    total_assets: Mapped[float | None] = mapped_column(Float)
    current_liabilities: Mapped[float | None] = mapped_column(Float)
    cash: Mapped[float | None] = mapped_column(Float)
    operating_cash_flow: Mapped[float | None] = mapped_column(Float)
    capex: Mapped[float | None] = mapped_column(Float)
    shares_outstanding: Mapped[float | None] = mapped_column(Float)  # in crore shares
    dividend_per_share: Mapped[float | None] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(100))
    source_ref: Mapped[str | None] = mapped_column(String(500))
    reported_at: Mapped[date | None] = mapped_column(Date)
    raw: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    validation_errors: Mapped[list] = mapped_column(JSONB, default=list, server_default="[]")
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Shareholding(Base):
    __tablename__ = "shareholding"
    __table_args__ = (UniqueConstraint("instrument_id", "period_end"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id", ondelete="CASCADE"), index=True)
    period_end: Mapped[date] = mapped_column(Date)
    promoter_pct: Mapped[float | None] = mapped_column(Float)
    fii_pct: Mapped[float | None] = mapped_column(Float)
    dii_pct: Mapped[float | None] = mapped_column(Float)
    public_pct: Mapped[float | None] = mapped_column(Float)
    others_pct: Mapped[float | None] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(100))
    reported_at: Mapped[date | None] = mapped_column(Date)
    raw: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")


class InstitutionalFlow(Base):
    __tablename__ = "institutional_flows"
    __table_args__ = (UniqueConstraint("trade_date", "category", "segment"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    trade_date: Mapped[date] = mapped_column(Date, index=True)
    category: Mapped[str] = mapped_column(String(10))  # FII | DII
    segment: Mapped[str] = mapped_column(String(20), default="CASH")
    buy_value: Mapped[float | None] = mapped_column(Float)
    sell_value: Mapped[float | None] = mapped_column(Float)
    net_value: Mapped[float | None] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(100))


class BulkBlockDeal(Base):
    __tablename__ = "bulk_block_deals"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    trade_date: Mapped[date] = mapped_column(Date, index=True)
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id", ondelete="CASCADE"), index=True)
    deal_type: Mapped[str] = mapped_column(String(10))  # BULK | BLOCK
    client_name: Mapped[str] = mapped_column(String(300))
    side: Mapped[str] = mapped_column(String(4))  # BUY | SELL
    quantity: Mapped[float] = mapped_column(Float)
    price: Mapped[float] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(100))
