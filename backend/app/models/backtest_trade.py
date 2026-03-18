import uuid
from datetime import datetime

from sqlalchemy import String, Float, Integer, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class BacktestTrade(Base):
    __tablename__ = "backtest_trades"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    import_id: Mapped[str] = mapped_column(ForeignKey("backtest_imports.id"), nullable=False, index=True)

    trade_number: Mapped[int] = mapped_column(Integer, nullable=False)
    type: Mapped[str] = mapped_column(String(20), nullable=False)  # "Entry long", "Exit long", etc.
    direction: Mapped[str] = mapped_column(String(10), nullable=False)  # "long" / "short"
    signal: Mapped[str | None] = mapped_column(String(50))  # L Entry, ADX Fade, K-Reversal, Slope Flat
    price: Mapped[float] = mapped_column(Float, nullable=False)
    qty: Mapped[float | None] = mapped_column(Float)
    position_value: Mapped[float | None] = mapped_column(Float)

    net_pnl: Mapped[float | None] = mapped_column(Float)
    net_pnl_pct: Mapped[float | None] = mapped_column(Float)
    favorable_excursion: Mapped[float | None] = mapped_column(Float)
    favorable_excursion_pct: Mapped[float | None] = mapped_column(Float)
    adverse_excursion: Mapped[float | None] = mapped_column(Float)
    adverse_excursion_pct: Mapped[float | None] = mapped_column(Float)
    cumulative_pnl: Mapped[float | None] = mapped_column(Float)
    cumulative_pnl_pct: Mapped[float | None] = mapped_column(Float)

    trade_date: Mapped[datetime] = mapped_column(nullable=False)

    backtest_import: Mapped["BacktestImport"] = relationship(back_populates="trades")
