import uuid
from datetime import datetime, timezone

from sqlalchemy import String, Float, Boolean, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Portfolio(Base):
    __tablename__ = "portfolios"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    initial_capital: Mapped[float] = mapped_column(Float, default=10000.0)
    cash: Mapped[float] = mapped_column(Float, default=10000.0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(timezone.utc))

    strategies: Mapped[list["PortfolioStrategy"]] = relationship(back_populates="portfolio")
    portfolio_trades: Mapped[list["PortfolioTrade"]] = relationship(back_populates="portfolio")
    snapshots: Mapped[list["PortfolioSnapshot"]] = relationship(back_populates="portfolio")
    daily_stats: Mapped[list["DailyStats"]] = relationship(back_populates="portfolio")
