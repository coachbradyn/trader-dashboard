import uuid

from sqlalchemy import String, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class PortfolioStrategy(Base):
    __tablename__ = "portfolio_strategies"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    portfolio_id: Mapped[str] = mapped_column(ForeignKey("portfolios.id"), nullable=False)
    trader_id: Mapped[str] = mapped_column(ForeignKey("traders.id"), nullable=False)
    # null = all directions, "long" = longs only, "short" = shorts only
    direction_filter: Mapped[str | None] = mapped_column(String(10))

    portfolio: Mapped["Portfolio"] = relationship(back_populates="strategies")
    trader: Mapped["Trader"] = relationship(back_populates="portfolio_strategies")
