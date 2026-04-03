import uuid
from app.utils.utc import utcnow
from datetime import datetime, timezone
from sqlalchemy import String, Text, Float, JSON, DateTime
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base


class NewsCache(Base):
    __tablename__ = "news_cache"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    alpaca_id: Mapped[str] = mapped_column(
        String(50), unique=True, index=True, nullable=False
    )
    headline: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str | None] = mapped_column(String(100), nullable=True)
    tickers: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # list of ticker strings
    published_at: Mapped[datetime | None] = mapped_column(DateTime, index=True, nullable=True)
    url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    sentiment_score: Mapped[float | None] = mapped_column(Float, nullable=True)  # -1.0 to 1.0
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: utcnow())
