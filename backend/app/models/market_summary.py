import uuid
from datetime import datetime, timezone
from sqlalchemy import String, Integer, JSON, Text
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base

class MarketSummary(Base):
    __tablename__ = "market_summaries"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    summary_type: Mapped[str] = mapped_column(String(20), nullable=False, index=True)  # "morning" / "nightly" / "alert_digest"
    scope: Mapped[str] = mapped_column(String(20), nullable=False)  # "portfolio" / "screener" / "combined"
    content: Mapped[str] = mapped_column(Text, nullable=False)
    tickers_analyzed: Mapped[dict | None] = mapped_column(JSON)
    generated_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(timezone.utc), index=True)
