import uuid
from datetime import datetime, timezone

from sqlalchemy import String, Float, Integer, ForeignKey, JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Trade(Base):
    __tablename__ = "trades"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    trader_id: Mapped[str] = mapped_column(ForeignKey("traders.id"), nullable=False, index=True)

    # Entry fields
    ticker: Mapped[str] = mapped_column(String(20), nullable=False)
    direction: Mapped[str] = mapped_column(String(5), nullable=False)  # "long" or "short"
    entry_price: Mapped[float] = mapped_column(Float, nullable=False)
    qty: Mapped[float] = mapped_column(Float, nullable=False)
    entry_signal_strength: Mapped[float | None] = mapped_column(Float)
    entry_adx: Mapped[float | None] = mapped_column(Float)
    entry_atr: Mapped[float | None] = mapped_column(Float)
    stop_price: Mapped[float | None] = mapped_column(Float)
    timeframe: Mapped[str | None] = mapped_column(String(10))
    entry_time: Mapped[datetime] = mapped_column(nullable=False)

    # Exit fields (null while open)
    exit_price: Mapped[float | None] = mapped_column(Float)
    exit_reason: Mapped[str | None] = mapped_column(String(50))
    exit_time: Mapped[datetime | None] = mapped_column()
    bars_in_trade: Mapped[int | None] = mapped_column(Integer)

    # Computed on exit
    pnl_dollars: Mapped[float | None] = mapped_column(Float)
    pnl_percent: Mapped[float | None] = mapped_column(Float)

    # Status
    status: Mapped[str] = mapped_column(String(10), default="open", index=True)

    # Raw webhook data
    raw_entry_payload: Mapped[dict | None] = mapped_column(JSON)
    raw_exit_payload: Mapped[dict | None] = mapped_column(JSON)

    created_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(timezone.utc))

    trader: Mapped["Trader"] = relationship(back_populates="trades")
    portfolio_trades: Mapped[list["PortfolioTrade"]] = relationship(back_populates="trade")

    @property
    def is_open(self) -> bool:
        return self.status == "open"

    @property
    def is_winner(self) -> bool | None:
        if self.pnl_dollars is None:
            return None
        return self.pnl_dollars > 0
