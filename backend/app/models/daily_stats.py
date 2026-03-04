import uuid
from datetime import date

from sqlalchemy import String, Float, Integer, Date, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class DailyStats(Base):
    __tablename__ = "daily_stats"
    __table_args__ = (UniqueConstraint("portfolio_id", "date"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    portfolio_id: Mapped[str] = mapped_column(ForeignKey("portfolios.id"), nullable=False, index=True)
    date: Mapped[date] = mapped_column(Date, nullable=False)
    starting_equity: Mapped[float] = mapped_column(Float, default=0.0)
    ending_equity: Mapped[float] = mapped_column(Float, default=0.0)
    daily_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    daily_pnl_pct: Mapped[float] = mapped_column(Float, default=0.0)
    trades_opened: Mapped[int] = mapped_column(Integer, default=0)
    trades_closed: Mapped[int] = mapped_column(Integer, default=0)
    wins: Mapped[int] = mapped_column(Integer, default=0)
    losses: Mapped[int] = mapped_column(Integer, default=0)
    gross_profit: Mapped[float] = mapped_column(Float, default=0.0)
    gross_loss: Mapped[float] = mapped_column(Float, default=0.0)
    max_drawdown_pct: Mapped[float] = mapped_column(Float, default=0.0)

    portfolio: Mapped["Portfolio"] = relationship(back_populates="daily_stats")
