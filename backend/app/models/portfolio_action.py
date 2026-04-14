import uuid
from app.utils.utc import utcnow
from datetime import datetime, timezone

from sqlalchemy import String, Float, Integer, Boolean, Text, ForeignKey, JSON
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

    # Adaptive Kelly bookkeeping (Phase 6, System 9). Records the f_base in
    # effect at decision time + the f_effective after calibration scaling
    # so System 10 (Bayesian) can audit whether the adaptive logic helps.
    kelly_f_base: Mapped[float | None] = mapped_column(Float)
    kelly_f_effective: Mapped[float | None] = mapped_column(Float)

    # Outcome linkage for memory decay (Phase 6, System 7). List of
    # HenryMemory.id values that were injected into the system prompt of
    # the AI call that generated this action. When the action's outcome
    # resolves (outcome_correct flips), each linked memory's importance
    # gets nudged up (win) or down (loss).
    injected_memory_ids: Mapped[list | None] = mapped_column(JSON, nullable=True, default=None)

    # Options routing (Step 2C). `instrument_type` is the switch: "equity"
    # (default, backwards-compatible with every historical row) or "options".
    # When options, `options_strategy` holds the full recommendation dict
    # from options_strategy.select_options_strategy — legs, strikes, expiry,
    # Greeks, max risk/reward — so the executor can submit the multi-leg
    # order without re-scoring.
    instrument_type: Mapped[str | None] = mapped_column(String(10), default="equity", nullable=True)
    options_strategy: Mapped[dict | None] = mapped_column(JSON, nullable=True, default=None)

    created_at: Mapped[datetime] = mapped_column(default=lambda: utcnow())

    portfolio: Mapped["Portfolio"] = relationship()
