from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import BigInteger, Boolean, Date, DateTime, ForeignKey, Identity, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class AppSetting(TimestampMixin, Base):
    """Non-secret runtime settings editable from the admin panel."""

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[dict] = mapped_column(JSONB, default=dict)
    updated_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))


class ProviderConfig(TimestampMixin, Base):
    """A configured market data provider. Credentials are Fernet-encrypted."""

    __tablename__ = "provider_configs"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    provider_type: Mapped[str] = mapped_column(String(40))  # upstox | angelone | custom_rest | custom_ws | simulated
    rest_url: Mapped[str | None] = mapped_column(String(500))
    ws_url: Mapped[str | None] = mapped_column(String(500))
    exchange: Mapped[str] = mapped_column(String(20), default="NSE")
    data_interval: Mapped[str] = mapped_column(String(20), default="1minute")
    feed_type: Mapped[str] = mapped_column(String(20), default="full")
    options: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    encrypted_credentials: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False)
    token_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_test_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_test_ok: Mapped[bool | None] = mapped_column(Boolean)
    last_test_message: Mapped[str | None] = mapped_column(Text)
    last_test_latency_ms: Mapped[float | None] = mapped_column()


class MarketHoliday(Base):
    __tablename__ = "market_holidays"
    __table_args__ = (UniqueConstraint("exchange", "holiday_date"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    exchange: Mapped[str] = mapped_column(String(20), default="NSE")
    holiday_date: Mapped[date] = mapped_column(Date, index=True)
    description: Mapped[str] = mapped_column(String(200), default="")
    source: Mapped[str] = mapped_column(String(50), default="manual")
