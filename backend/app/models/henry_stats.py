import uuid
from datetime import datetime

from sqlalchemy import String, Integer, ForeignKey, DateTime, JSON, Index
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class HenryStats(Base):
    """
    Pre-computed analytics injected into Henry's prompts.
    Computed by background job (every 2h during market, daily after close),
    not on-the-fly -- keeps prompt assembly fast and cheap.

    Stat types:
      - "strategy_performance": per-strategy win rate, avg gain/loss, profit factor, streak
      - "ticker_performance": per-ticker performance across strategies
      - "strategy_correlation": agreement/disagreement matrix between strategy pairs
      - "exit_reason_analysis": count, avg P&L, win rate by exit reason
      - "henry_hit_rate": Henry's recommendation accuracy overall and by confidence bucket
      - "hold_time_analysis": hold time distributions for winners vs losers
      - "portfolio_risk": concentration, exposure, unrealized P&L distribution
      - "screener_accuracy": screener alert hit rate vs subsequent price action
    """
    __tablename__ = "henry_stats"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))

    # Scoping
    stat_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    ticker: Mapped[str | None] = mapped_column(String(20), index=True)
    strategy: Mapped[str | None] = mapped_column(String(50), index=True)
    portfolio_id: Mapped[str | None] = mapped_column(ForeignKey("portfolios.id"))

    # The computed data -- JSON blob
    data: Mapped[dict] = mapped_column(JSON, nullable=False)

    # Freshness
    period_days: Mapped[int] = mapped_column(Integer, default=30)
    computed_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)

    # Relationships
    portfolio: Mapped["Portfolio | None"] = relationship()

    __table_args__ = (
        Index("ix_henry_stats_type_ticker_strategy", "stat_type", "ticker", "strategy"),
    )
