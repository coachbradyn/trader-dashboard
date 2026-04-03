import uuid
from app.utils.utc import utcnow
from datetime import datetime, date, timezone

from sqlalchemy import String, Float, Boolean, Text, Date, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class PortfolioHolding(Base):
    __tablename__ = "portfolio_holdings"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    portfolio_id: Mapped[str] = mapped_column(ForeignKey("portfolios.id"), nullable=False, index=True)
    trade_id: Mapped[str | None] = mapped_column(ForeignKey("trades.id"), index=True)  # null = manual entry

    ticker: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    direction: Mapped[str] = mapped_column(String(10), nullable=False)  # "long" / "short"
    entry_price: Mapped[float] = mapped_column(Float, nullable=False)
    qty: Mapped[float] = mapped_column(Float, nullable=False)
    entry_date: Mapped[datetime] = mapped_column(nullable=False)
    strategy_name: Mapped[str | None] = mapped_column(String(50))
    notes: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)

    # Position archetype fields
    position_type: Mapped[str] = mapped_column(String(20), default="momentum")  # momentum, accumulation, catalyst, conviction
    thesis: Mapped[str | None] = mapped_column(Text)
    catalyst_date: Mapped[date | None] = mapped_column(Date)
    catalyst_description: Mapped[str | None] = mapped_column(String(200))
    max_allocation_pct: Mapped[float | None] = mapped_column(Float)
    dca_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    dca_threshold_pct: Mapped[float | None] = mapped_column(Float)
    avg_cost: Mapped[float | None] = mapped_column(Float)
    total_shares: Mapped[float | None] = mapped_column(Float)

    created_at: Mapped[datetime] = mapped_column(default=lambda: utcnow())

    portfolio: Mapped["Portfolio"] = relationship()
    trade: Mapped["Trade | None"] = relationship()
