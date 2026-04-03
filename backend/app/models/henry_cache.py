import uuid
from app.utils.utc import utcnow
from datetime import datetime, timezone
from sqlalchemy import String, Integer, Boolean, Text, JSON
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base


class HenryCache(Base):
    __tablename__ = "henry_cache"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    cache_key: Mapped[str] = mapped_column(String(200), nullable=False, unique=True, index=True)
    cache_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)  # ticker_analysis, signal_eval, review, etc.
    content: Mapped[dict] = mapped_column(JSON, nullable=False)
    ticker: Mapped[str | None] = mapped_column(String(20), index=True)
    strategy: Mapped[str | None] = mapped_column(String(50))
    is_stale: Mapped[bool] = mapped_column(Boolean, default=False)
    generated_at: Mapped[datetime] = mapped_column(default=lambda: utcnow())
    data_hash: Mapped[str | None] = mapped_column(String(64))  # hash of input data to detect changes
