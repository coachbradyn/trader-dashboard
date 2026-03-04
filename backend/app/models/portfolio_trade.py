import uuid

from sqlalchemy import String, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class PortfolioTrade(Base):
    __tablename__ = "portfolio_trades"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    portfolio_id: Mapped[str] = mapped_column(ForeignKey("portfolios.id"), nullable=False, index=True)
    trade_id: Mapped[str] = mapped_column(ForeignKey("trades.id"), nullable=False, index=True)

    portfolio: Mapped["Portfolio"] = relationship(back_populates="portfolio_trades")
    trade: Mapped["Trade"] = relationship(back_populates="portfolio_trades")
