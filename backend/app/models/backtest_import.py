import uuid
from datetime import datetime, timezone

from sqlalchemy import String, Float, Integer
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class BacktestImport(Base):
    __tablename__ = "backtest_imports"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    strategy_name: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    strategy_version: Mapped[str | None] = mapped_column(String(20))
    exchange: Mapped[str | None] = mapped_column(String(20))
    ticker: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)

    # Computed summary stats
    trade_count: Mapped[int] = mapped_column(Integer, default=0)
    win_rate: Mapped[float | None] = mapped_column(Float)
    profit_factor: Mapped[float | None] = mapped_column(Float)
    avg_gain_pct: Mapped[float | None] = mapped_column(Float)
    avg_loss_pct: Mapped[float | None] = mapped_column(Float)
    max_drawdown_pct: Mapped[float | None] = mapped_column(Float)
    max_adverse_excursion_pct: Mapped[float | None] = mapped_column(Float)
    avg_hold_days: Mapped[float | None] = mapped_column(Float)
    total_pnl_pct: Mapped[float | None] = mapped_column(Float)

    imported_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(timezone.utc))

    trades: Mapped[list["BacktestTrade"]] = relationship(back_populates="backtest_import", cascade="all, delete-orphan")
