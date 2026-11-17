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
    LargeBinary,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class MLModel(Base):
    __tablename__ = "ml_models"
    __table_args__ = (UniqueConstraint("name", "version"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    name: Mapped[str] = mapped_column(String(100), index=True)  # e.g. trend_classifier
    task: Mapped[str] = mapped_column(String(50))  # trend | volatility | anomaly | regime
    algorithm: Mapped[str] = mapped_column(String(50))
    version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default="candidate")  # candidate | active | retired | rejected
    horizon_days: Mapped[int] = mapped_column(Integer, default=5)
    params: Mapped[dict] = mapped_column(JSONB, default=dict)
    features: Mapped[list] = mapped_column(JSONB, default=list)
    train_start: Mapped[date | None] = mapped_column(Date)
    train_end: Mapped[date | None] = mapped_column(Date)
    test_start: Mapped[date | None] = mapped_column(Date)
    test_end: Mapped[date | None] = mapped_column(Date)
    metrics: Mapped[dict] = mapped_column(JSONB, default=dict)
    calibration: Mapped[dict] = mapped_column(JSONB, default=dict)
    reference_stats: Mapped[dict] = mapped_column(JSONB, default=dict)
    data_hash: Mapped[str | None] = mapped_column(String(64))
    is_simulated: Mapped[bool] = mapped_column(Boolean, default=False)
    artifact: Mapped[bytes | None] = mapped_column(LargeBinary)
    notes: Mapped[str] = mapped_column(String(1000), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    promoted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class MLPrediction(Base):
    __tablename__ = "ml_predictions"
    __table_args__ = (
        UniqueConstraint("model_id", "instrument_id", "as_of"),
        Index("ix_ml_predictions_instrument_asof", "instrument_id", "as_of"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    model_id: Mapped[int] = mapped_column(ForeignKey("ml_models.id", ondelete="CASCADE"), index=True)
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id", ondelete="CASCADE"))
    as_of: Mapped[date] = mapped_column(Date)  # date of the last bar used for features
    horizon_days: Mapped[int] = mapped_column(Integer)
    prob_up: Mapped[float | None] = mapped_column(Float)
    prob_down: Mapped[float | None] = mapped_column(Float)
    prob_neutral: Mapped[float | None] = mapped_column(Float)
    predicted_vol: Mapped[float | None] = mapped_column(Float)
    trend_class: Mapped[str | None] = mapped_column(String(20))
    regime: Mapped[str | None] = mapped_column(String(30))
    anomaly_score: Mapped[float | None] = mapped_column(Float)
    confidence: Mapped[float | None] = mapped_column(Float)
    realized_label: Mapped[str | None] = mapped_column(String(20))
    realized_return: Mapped[float | None] = mapped_column(Float)
    evaluated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_simulated: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
