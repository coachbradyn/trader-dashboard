import uuid
from datetime import datetime

from sqlalchemy import String, Text, Integer, Float
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class HenryMemory(Base):
    """
    Henry's decision memory. Stores observations, lessons learned, and
    strategy-specific notes that Henry references in future analysis.

    Types:
      - "observation": Something Henry noticed (e.g., "S3 tends to fail on NVDA during high VIX")
      - "lesson": A data-backed conclusion from trade outcomes
      - "preference": User's stated preferences ("I prefer to cut losers fast")
      - "strategy_note": Strategy-specific insight from analyzing performance
      - "decision": A record of a specific decision and its reasoning
    """
    __tablename__ = "henry_memory"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    memory_type: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    # Which strategy this memory relates to (null = general/portfolio-wide)
    strategy_id: Mapped[str | None] = mapped_column(String(50), index=True)
    # Which ticker this memory relates to (null = general)
    ticker: Mapped[str | None] = mapped_column(String(10), index=True)
    # The actual memory content
    content: Mapped[str] = mapped_column(Text, nullable=False)
    # How confident/important this memory is (1-10)
    importance: Mapped[int] = mapped_column(Integer, default=5)
    # How many times this memory has been referenced in analysis
    reference_count: Mapped[int] = mapped_column(Integer, default=0)
    # Was this memory validated by outcomes? (null = not yet validated)
    validated: Mapped[bool | None] = mapped_column(default=None)
    # Source: "briefing", "signal_eval", "scheduled_review", "user", "outcome_tracking"
    source: Mapped[str] = mapped_column(String(30), default="system")

    created_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
