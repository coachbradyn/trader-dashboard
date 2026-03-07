import uuid
from datetime import datetime
from sqlalchemy import String, Float, Integer, JSON
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base

class IndicatorAlert(Base):
    __tablename__ = "indicator_alerts"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    ticker: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    indicator: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    value: Mapped[float] = mapped_column(Float, nullable=False)
    signal: Mapped[str] = mapped_column(String(20), nullable=False)  # "bullish" / "bearish" / "neutral"
    timeframe: Mapped[str | None] = mapped_column(String(10))
    metadata_extra: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow, index=True)
