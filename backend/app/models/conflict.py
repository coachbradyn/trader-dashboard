import uuid
from app.utils.utc import utcnow
from datetime import datetime, timezone

from sqlalchemy import String, Float, Integer, JSON, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class ConflictResolution(Base):
    __tablename__ = "conflict_resolutions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    ticker: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    strategies: Mapped[str] = mapped_column(Text, nullable=False)  # JSON string of strategy names
    recommendation: Mapped[str] = mapped_column(String(20), nullable=False)  # LONG, SHORT, STAY_FLAT
    confidence: Mapped[int] = mapped_column(Integer, nullable=False)
    reasoning: Mapped[str] = mapped_column(Text, nullable=False)
    signals: Mapped[dict | None] = mapped_column(JSON)  # Raw conflicting signals
    created_at: Mapped[datetime] = mapped_column(default=lambda: utcnow(), index=True)
