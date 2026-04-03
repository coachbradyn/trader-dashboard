import uuid
from datetime import datetime

from sqlalchemy import String, Text, Integer, Float, ForeignKey, DateTime, Index
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class HenryContext(Base):
    """
    Henry's accumulated notes -- condensed conclusions from each AI interaction.
    Tagged by ticker/strategy/type for efficient retrieval into Claude prompts.

    Context types:
      - "recommendation": Henry recommended an action (BUY/SELL/TRIM etc.)
      - "outcome": A trade closed -- ground truth P&L linked to prior recommendation
      - "observation": A pattern or insight noticed during analysis
      - "pattern": A recurring pattern across multiple observations
      - "portfolio_note": Portfolio-level note (concentration, risk, rebalancing)
      - "user_decision": User approved or rejected a recommended action
    """
    __tablename__ = "henry_context"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))

    # Scoping -- what is this note about?
    ticker: Mapped[str | None] = mapped_column(String(20), index=True)
    strategy: Mapped[str | None] = mapped_column(String(50), index=True)
    portfolio_id: Mapped[str | None] = mapped_column(ForeignKey("portfolios.id"))

    # What kind of note?
    context_type: Mapped[str] = mapped_column(String(30), nullable=False, index=True)

    # The actual content -- Henry's condensed conclusion
    content: Mapped[str] = mapped_column(Text, nullable=False)

    # Metadata
    confidence: Mapped[int | None] = mapped_column(Integer)
    action_id: Mapped[str | None] = mapped_column(ForeignKey("portfolio_actions.id"))
    trade_id: Mapped[str | None] = mapped_column(ForeignKey("trades.id"))

    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime)

    # Relationships
    portfolio: Mapped["Portfolio | None"] = relationship()
    action: Mapped["PortfolioAction | None"] = relationship()
    trade: Mapped["Trade | None"] = relationship()

    __table_args__ = (
        Index("ix_henry_context_ticker_strategy_created", "ticker", "strategy", "created_at"),
    )
