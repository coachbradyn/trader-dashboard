import uuid
from datetime import datetime

from sqlalchemy import String, Float, Integer, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class PortfolioSnapshot(Base):
    __tablename__ = "portfolio_snapshots"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    portfolio_id: Mapped[str] = mapped_column(ForeignKey("portfolios.id"), nullable=False, index=True)
    equity: Mapped[float] = mapped_column(Float, nullable=False)
    cash: Mapped[float] = mapped_column(Float, nullable=False)
    unrealized_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    open_positions: Mapped[int] = mapped_column(Integer, default=0)
    drawdown_pct: Mapped[float] = mapped_column(Float, default=0.0)
    peak_equity: Mapped[float] = mapped_column(Float, default=0.0)
    snapshot_time: Mapped[datetime] = mapped_column(default=datetime.utcnow)

    portfolio: Mapped["Portfolio"] = relationship(back_populates="snapshots")
