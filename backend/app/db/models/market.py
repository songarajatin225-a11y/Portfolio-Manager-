from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Identity,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class Instrument(TimestampMixin, Base):
    __tablename__ = "instruments"
    __table_args__ = (
        UniqueConstraint(
            "exchange",
            "segment",
            "symbol",
            "expiry",
            "strike",
            "option_type",
            name="uq_instruments_identity",
            postgresql_nulls_not_distinct=True,
        ),
        Index("ix_instruments_symbol", "symbol"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    symbol: Mapped[str] = mapped_column(String(80))
    exchange: Mapped[str] = mapped_column(String(10))  # NSE | BSE
    segment: Mapped[str] = mapped_column(String(10))  # EQ | INDEX | FO
    name: Mapped[str] = mapped_column(String(200), default="")
    isin: Mapped[str | None] = mapped_column(String(20), index=True)
    instrument_type: Mapped[str] = mapped_column(String(20), default="EQ")  # EQ, INDEX, FUT, CE, PE
    lot_size: Mapped[int] = mapped_column(Integer, default=1)
    tick_size: Mapped[float] = mapped_column(Float, default=0.05)
    expiry: Mapped[date | None] = mapped_column(Date)
    strike: Mapped[float | None] = mapped_column(Float)
    option_type: Mapped[str | None] = mapped_column(String(2))
    underlying: Mapped[str | None] = mapped_column(String(80))
    sector: Mapped[str | None] = mapped_column(String(100), index=True)
    industry: Mapped[str | None] = mapped_column(String(150))
    is_index: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_simulated: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")


class InstrumentProviderKey(Base):
    __tablename__ = "instrument_provider_keys"
    __table_args__ = (
        UniqueConstraint("provider_type", "provider_key"),
        UniqueConstraint("instrument_id", "provider_type"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id", ondelete="CASCADE"), index=True)
    provider_type: Mapped[str] = mapped_column(String(40))
    provider_key: Mapped[str] = mapped_column(String(120))  # e.g. NSE_EQ|INE002A01018 or NSE:2885
    provider_token: Mapped[str | None] = mapped_column(String(60))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class IndexConstituent(Base):
    __tablename__ = "index_constituents"
    __table_args__ = (UniqueConstraint("index_code", "instrument_id", "start_date"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    index_code: Mapped[str] = mapped_column(String(40), index=True)
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id", ondelete="CASCADE"), index=True)
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)
    weight: Mapped[float | None] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(100), default="csv_import")


class Candle(Base):
    """OHLCV bars. Becomes a TimescaleDB hypertable on ``ts`` when the extension exists."""

    __tablename__ = "candles"

    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id", ondelete="CASCADE"), primary_key=True)
    interval: Mapped[str] = mapped_column(String(10), primary_key=True)  # 1m, 5m, 15m, 30m, 1h, 1d, 1w, 1mo
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    is_simulated: Mapped[bool] = mapped_column(Boolean, primary_key=True, default=False)
    open: Mapped[float] = mapped_column(Float)
    high: Mapped[float] = mapped_column(Float)
    low: Mapped[float] = mapped_column(Float)
    close: Mapped[float] = mapped_column(Float)
    volume: Mapped[float] = mapped_column(Float, default=0)
    oi: Mapped[float | None] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(60))


class InstrumentMetrics(Base):
    """Latest computed technical snapshot per instrument (feeds screener, heatmap, breadth)."""

    __tablename__ = "instrument_metrics"

    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id", ondelete="CASCADE"), primary_key=True)
    is_simulated: Mapped[bool] = mapped_column(Boolean, primary_key=True, default=False)
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_close: Mapped[float | None] = mapped_column(Float)
    prev_close: Mapped[float | None] = mapped_column(Float)
    change_pct: Mapped[float | None] = mapped_column(Float)
    volume: Mapped[float | None] = mapped_column(Float)
    avg_volume_20: Mapped[float | None] = mapped_column(Float)
    volume_ratio: Mapped[float | None] = mapped_column(Float)
    rsi_14: Mapped[float | None] = mapped_column(Float)
    macd: Mapped[float | None] = mapped_column(Float)
    macd_signal: Mapped[float | None] = mapped_column(Float)
    macd_hist: Mapped[float | None] = mapped_column(Float)
    ema_20: Mapped[float | None] = mapped_column(Float)
    ema_50: Mapped[float | None] = mapped_column(Float)
    ema_200: Mapped[float | None] = mapped_column(Float)
    sma_50: Mapped[float | None] = mapped_column(Float)
    sma_200: Mapped[float | None] = mapped_column(Float)
    atr_14: Mapped[float | None] = mapped_column(Float)
    adx_14: Mapped[float | None] = mapped_column(Float)
    supertrend_dir: Mapped[int | None] = mapped_column(Integer)
    ret_1w: Mapped[float | None] = mapped_column(Float)
    ret_1m: Mapped[float | None] = mapped_column(Float)
    ret_3m: Mapped[float | None] = mapped_column(Float)
    ret_6m: Mapped[float | None] = mapped_column(Float)
    ret_1y: Mapped[float | None] = mapped_column(Float)
    rel_strength: Mapped[float | None] = mapped_column(Float)
    high_52w: Mapped[float | None] = mapped_column(Float)
    low_52w: Mapped[float | None] = mapped_column(Float)
    volatility_20: Mapped[float | None] = mapped_column(Float)
    flags: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    bars_used: Mapped[int] = mapped_column(Integer, default=0)
    source: Mapped[str] = mapped_column(String(60), default="")
