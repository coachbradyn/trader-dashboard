"""
OptionsTrade model
==================
Tracks open and closed options positions. Each row is a single *leg* of an
options strategy. Multi-leg strategies (spreads, iron condors) share a
spread_group_id so the UI and stats engine can display/compute them as a
unit while still tracking each leg for P&L, greeks, and assignment risk.

Kept separate from the equity `trades` table because:
- Options have fundamentally different fields (strike/expiry/greeks/DTE).
- Conflating the two would pollute every equity query with nullable columns.
- The reporting/stats pipelines already assume the trades table is equity.

Option lifecycle:
  status = "open"    — leg is live, price poller updates current_premium
                       and greeks_current every poll cycle.
  status = "closed"  — user/Henry closed the position; exit_premium and
                       pnl_* are populated.
  status = "expired" — option reached expiration OTM; full premium
                       recorded as loss (for long legs) or full credit kept
                       (for short legs inside a covered strategy).
  status = "assigned"— short leg was assigned early; position converts to
                       an equity change (follow-up handled elsewhere).
"""
import uuid
from app.utils.utc import utcnow
from datetime import datetime, date as date_type

from sqlalchemy import String, Float, Integer, Date, ForeignKey, JSON, Text, Index
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


# Supported strategy_type values. Kept in Python (not an Enum column) so
# schema migrations aren't needed to add new strategies later.
STRATEGY_TYPES = (
    "long_call",
    "long_put",
    "covered_call",
    "cash_secured_put",
    "protective_put",
    "bull_call_spread",
    "bear_put_spread",
    "bull_put_spread",
    "bear_call_spread",
    "iron_condor",
    "calendar_spread",
)

# Levels that permit each strategy. Enforced by the strategy selector,
# the trade builder UI, and the execution endpoint.
STRATEGY_MIN_LEVEL = {
    "covered_call": 1,
    "cash_secured_put": 1,
    "protective_put": 1,
    "long_call": 2,
    "long_put": 2,
    "bull_call_spread": 3,
    "bear_put_spread": 3,
    "bull_put_spread": 3,
    "bear_call_spread": 3,
    "iron_condor": 3,
    "calendar_spread": 3,
}


class OptionsTrade(Base):
    __tablename__ = "options_trades"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    portfolio_id: Mapped[str] = mapped_column(
        ForeignKey("portfolios.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # Contract identity
    ticker: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    option_symbol: Mapped[str] = mapped_column(String(40), nullable=False, index=True)  # OCC
    option_type: Mapped[str] = mapped_column(String(4), nullable=False)  # "call" or "put"
    strike: Mapped[float] = mapped_column(Float, nullable=False)
    expiration: Mapped[date_type] = mapped_column(Date, nullable=False, index=True)

    # Leg direction & size
    direction: Mapped[str] = mapped_column(String(5), nullable=False)  # "long" or "short"
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)     # contracts

    # Entry
    entry_premium: Mapped[float] = mapped_column(Float, nullable=False)  # per share
    entry_time: Mapped[datetime] = mapped_column(default=lambda: utcnow(), nullable=False)
    underlying_price_at_entry: Mapped[float | None] = mapped_column(Float)
    greeks_at_entry: Mapped[dict | None] = mapped_column(JSON)
    iv_at_entry: Mapped[float | None] = mapped_column(Float)

    # Live (updated by price poller while status == "open")
    current_premium: Mapped[float | None] = mapped_column(Float)
    greeks_current: Mapped[dict | None] = mapped_column(JSON)

    # Exit (null while open)
    exit_premium: Mapped[float | None] = mapped_column(Float)
    exit_time: Mapped[datetime | None] = mapped_column()
    pnl_dollars: Mapped[float | None] = mapped_column(Float)
    pnl_percent: Mapped[float | None] = mapped_column(Float)

    # Status + strategy grouping
    status: Mapped[str] = mapped_column(String(10), default="open", index=True)
    strategy_type: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    spread_group_id: Mapped[str | None] = mapped_column(String(36), index=True)

    # Alpaca order id(s) for audit trail
    alpaca_order_id: Mapped[str | None] = mapped_column(String(64))

    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(default=lambda: utcnow())

    __table_args__ = (
        Index("ix_options_trades_portfolio_status", "portfolio_id", "status"),
        Index("ix_options_trades_expiration_status", "expiration", "status"),
    )

    @property
    def is_open(self) -> bool:
        return self.status == "open"

    @property
    def days_to_expiration(self) -> int | None:
        if not self.expiration:
            return None
        return (self.expiration - date_type.today()).days

    @property
    def notional_exposure(self) -> float:
        """Strike × 100 × qty — useful for Greeks aggregation but NOT for
        sizing. Options risk is capped at premium for long legs; sizing
        should use max_risk from the strategy, not this figure."""
        return float(self.strike) * 100.0 * float(self.quantity)
