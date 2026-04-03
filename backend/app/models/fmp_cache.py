"""
FMP API Cache
=============
Caches raw FMP API responses to minimize API calls.
Cache tiers: realtime (60s), intraday (3600s), daily (86400s).
"""

import uuid
from datetime import datetime

from sqlalchemy import String, Text, DateTime, Index, JSON
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class FmpCache(Base):
    __tablename__ = "fmp_cache"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    endpoint: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    params_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    response_data: Mapped[dict | None] = mapped_column(JSON)
    cached_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    cache_tier: Mapped[str] = mapped_column(String(20), nullable=False, default="daily")  # realtime/intraday/daily

    __table_args__ = (
        Index("ix_fmp_cache_endpoint_params", "endpoint", "params_hash", unique=True),
    )
