import uuid
from app.utils.utc import utcnow
from datetime import datetime, timezone

from sqlalchemy import String, Float, Integer, Boolean, Text, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class PortfolioAction(Base):
    __tablename__ = "portfolio_actions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    portfolio_id: Mapped[str] = mapped_column(ForeignKey("portfolios.id"), nullable=False, index=True)

    ticker: Mapped[str] = mapped_column(String(10), nullable=False)
    direction: Mapped[str] = mapped_column(String(10), nullable=False)  # "long" / "short"
    action_type: Mapped[str] = mapped_column(String(20), nullable=False)  # BUY/SELL/TRIM/ADD/CLOSE/REBALANCE/DCA
    suggested_qty: Mapped[float | None] = mapped_column(Float)
    suggested_price: Mapped[float | None] = mapped_column(Float)
    current_price: Mapped[float | None] = mapped_column(Float)
    confidence: Mapped[int] = mapped_column(Integer, default=5)
    reasoning: Mapped[str] = mapped_column(Text, nullable=False)

    # Trigger info
    trigger_type: Mapped[str] = mapped_column(String(20), nullable=False, index=True)  # SIGNAL/THRESHOLD/SCHEDULED_REVIEW
    trigger_ref: Mapped[str | None] = mapped_column(String(36))  # trade_id or alert_id
    priority_score: Mapped[float] = mapped_column(Float, default=0.0, index=True)

    # Status
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)  # pending/approved/rejected/expired
    expires_at: Mapped[datetime | None] = mapped_column()
    resolved_at: Mapped[datetime | None] = mapped_column()
    reject_reason: Mapped[str | None] = mapped_column(Text)

    # Outcome tracking
    outcome_pnl: Mapped[float | None] = mapped_column(Float)
    outcome_correct: Mapped[bool | None] = mapped_column(Boolean)
    outcome_resolved_at: Mapped[datetime | None] = mapped_column()

    # Position sizing recommendation (intelligence upgrade Phase 4,
    # System 5). Computed by app.services.position_sizing.compute_size
    # using fractional Kelly + the conditional probability table when
    # both are available; falls back to a fixed % of equity otherwise.
    # All nullable — actions created before sizing was wired (or by
    # paths that don't compute it yet, like autonomous_trading) leave
    # these NULL and the UI shows "—".
    recommended_shares: Mapped[float | None] = mapped_column(Float)
    recommended_dollar_amount: Mapped[float | None] = mapped_column(Float)
    recommended_pct_of_equity: Mapped[float | None] = mapped_column(Float)
    sizing_method: Mapped[str | None] = mapped_column(String(30))  # kelly | fixed | insufficient_data | negative_ev

    created_at: Mapped[datetime] = mapped_column(default=lambda: utcnow())

    portfolio: Mapped["Portfolio"] = relationship()
