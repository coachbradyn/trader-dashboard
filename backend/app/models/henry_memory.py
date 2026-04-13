import uuid
from app.utils.utc import utcnow
from datetime import datetime, timezone

from sqlalchemy import String, Text, Integer, Float, JSON
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
    # SHA-256 of (ticker, strategy_id, normalized content) for dedup
    content_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    # Source: "briefing", "signal_eval", "scheduled_review", "user", "outcome_tracking"
    source: Mapped[str] = mapped_column(String(30), default="system")

    # Semantic embedding of `content` — list[float] stored as JSON. Null when
    # embedding provider is disabled or the write path failed. Used by top-K
    # retrieval in ai_service._build_system_prompt to filter memories
    # semantically instead of injecting everything importance>=6.
    embedding: Mapped[list | None] = mapped_column(JSON, nullable=True, default=None)
    # Which model produced `embedding`. Vectors from different models are NOT
    # comparable — retrieval must filter to matching model_name before ranking.
    embedding_model: Mapped[str | None] = mapped_column(String(50), nullable=True, default=None)
    # Gaussian mixture cluster assignment from memory_clustering.fit_clusters.
    # Null when unclustered. Retrieval blends P(cluster | query) into ranking.
    cluster_id: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None, index=True)
    # Silhouette-like score in [-1, 1] — how well this memory fits its
    # cluster vs the next-nearest one. Populated by fit_memory_clusters.
    # Drives the "silhouette coloring" viz mode (outliers desaturated).
    cluster_silhouette: Mapped[float | None] = mapped_column(Float, nullable=True, default=None)

    created_at: Mapped[datetime] = mapped_column(default=lambda: utcnow())
    updated_at: Mapped[datetime] = mapped_column(default=lambda: utcnow(), onupdate=lambda: utcnow())
