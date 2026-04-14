import uuid
from app.utils.utc import utcnow
from datetime import datetime, timezone

from sqlalchemy import String, Float, Boolean, Text, Integer
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Portfolio(Base):
    __tablename__ = "portfolios"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    initial_capital: Mapped[float] = mapped_column(Float, default=0.0)
    cash: Mapped[float] = mapped_column(Float, default=0.0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    max_pct_per_trade: Mapped[float | None] = mapped_column(Float)
    max_open_positions: Mapped[int | None] = mapped_column(Integer)
    max_drawdown_pct: Mapped[float | None] = mapped_column(Float)
    is_ai_managed: Mapped[bool] = mapped_column(Boolean, default=False)
    ai_evaluation_enabled: Mapped[bool] = mapped_column(Boolean, default=False)  # Henry evaluates signals before execution
    status: Mapped[str] = mapped_column(String(20), default="active")  # "active" / "archived"
    execution_mode: Mapped[str] = mapped_column(String(10), default="local")  # local, paper, live
    alpaca_api_key: Mapped[str | None] = mapped_column(String(255))
    alpaca_secret_key: Mapped[str | None] = mapped_column(String(255))
    max_order_amount: Mapped[float | None] = mapped_column(Float, default=1000.0)

    # Options trading configuration. `options_level` is the hard gate —
    # 0 means options are disabled for this portfolio; 1/2/3 mirror the
    # standard brokerage approval tiers. The other three columns are
    # per-portfolio overrides for the global defaults stored in
    # henry_cache. Null here means "use global default".
    options_level: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_options_risk: Mapped[float | None] = mapped_column(Float)
    max_options_daily_trades: Mapped[int | None] = mapped_column(Integer)
    options_allocation_pct: Mapped[float] = mapped_column(Float, default=0.20, nullable=False)

    created_at: Mapped[datetime] = mapped_column(default=lambda: utcnow())

    @property
    def alpaca_api_key_decrypted(self) -> str | None:
        from app.utils.crypto import decrypt_value
        return decrypt_value(self.alpaca_api_key)

    @property
    def alpaca_secret_key_decrypted(self) -> str | None:
        from app.utils.crypto import decrypt_value
        return decrypt_value(self.alpaca_secret_key)

    strategies: Mapped[list["PortfolioStrategy"]] = relationship(back_populates="portfolio")
    portfolio_trades: Mapped[list["PortfolioTrade"]] = relationship(back_populates="portfolio")
    snapshots: Mapped[list["PortfolioSnapshot"]] = relationship(back_populates="portfolio")
    daily_stats: Mapped[list["DailyStats"]] = relationship(back_populates="portfolio")
