"""
Position Sizing — Fractional Kelly + Conditional Probability Table
==================================================================

Pure-math utility (no AI calls). Consumes the conditional probability
table from henry_stats_engine._compute_conditional_probability and the
portfolio's risk parameters to produce a recommended size for a
proposed trade.

Used by AI signal evaluation paths to populate the new
PortfolioAction.recommended_* fields. Pricing comes from price_service
when available, falls back to the trade's entry_price.

Sizing rules (per the intelligence-upgrade brief):
  - Full Kelly:   f* = (win_rate / avg_loss_pct) - (loss_rate / avg_gain_pct)
  - Default 0.25× multiplier (quarter-Kelly — standard for volatile assets)
  - Confidence < 5 → additional 0.5× multiplier (treat as exploratory)
  - Cap at portfolio.max_pct_per_trade
  - Subtract existing exposure on this ticker so result is INCREMENTAL
  - Negative Kelly → flag as 'negative_ev' with min sizing
  - No probability data → fall back to FALLBACK_PCT_OF_EQUITY
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


KELLY_MULTIPLIER = 0.25            # Quarter Kelly default
LOW_CONFIDENCE_MULTIPLIER = 0.5    # Confidence <5 gets extra damping
LOW_CONFIDENCE_THRESHOLD = 5
FALLBACK_PCT_OF_EQUITY = 2.0       # When no probability table available
NEGATIVE_EV_PCT_OF_EQUITY = 0.5    # Min sizing for thesis-testing negative-EV trades


@dataclass
class SizingResult:
    """Result of a sizing computation. All fields populated even on
    fallback paths so the caller can persist a complete record."""
    recommended_shares: float
    recommended_dollar_amount: float
    recommended_pct_of_equity: float
    sizing_method: str  # kelly | fixed | insufficient_data | negative_ev | capped
    notes: str          # Human-readable explanation suitable for prompt context

    def to_db_dict(self) -> dict:
        return {
            "recommended_shares": round(self.recommended_shares, 2),
            "recommended_dollar_amount": round(self.recommended_dollar_amount, 2),
            "recommended_pct_of_equity": round(self.recommended_pct_of_equity, 2),
            "sizing_method": self.sizing_method,
        }


def _compute_kelly_fraction(
    win_rate_pct: float,
    avg_gain_pct: float,
    avg_loss_pct: float,
) -> Optional[float]:
    """
    Returns the Kelly-optimal fraction in [-inf, 1]. None when inputs
    are insufficient (zero gain/loss magnitudes).

    f* = b * p - q  (in compact form, where b = avg_gain/|avg_loss|)
    Equivalently the brief's formula:
    f* = (win_rate / |avg_loss|) - (loss_rate / avg_gain)
    Both produce the same number; we use the brief's form for clarity.
    """
    p = max(0.0, min(1.0, win_rate_pct / 100.0))
    q = 1.0 - p
    avg_gain = abs(avg_gain_pct)
    avg_loss = abs(avg_loss_pct)
    if avg_gain <= 0 or avg_loss <= 0:
        return None
    return (p / avg_loss) - (q / avg_gain)


async def compute_size(
    db,
    portfolio,
    ticker: str,
    direction: str,
    strategy_id: Optional[str],
    confidence: int,
    current_price: Optional[float] = None,
) -> SizingResult:
    """
    Compute recommended size for a proposed trade.

    Args:
        db: AsyncSession (used to look up conditional probability + holdings)
        portfolio: Portfolio model instance (needs .id, .cash,
                   .initial_capital, .max_pct_per_trade)
        ticker, direction, strategy_id: the proposed trade
        confidence: integer 1-10 (Henry's signal-eval confidence)
        current_price: optional override; if None, falls back to
                       price_service then to portfolio's average entry

    Returns SizingResult with all fields populated.
    """
    from sqlalchemy import select
    from app.models import HenryStats, PortfolioHolding
    from app.services.price_service import price_service

    ticker = (ticker or "").upper()

    # ── Equity reference ──────────────────────────────────────────────
    # Use cash as an approximation of available equity; falls back to
    # initial_capital if cash isn't tracked. Total portfolio value would
    # be ideal but requires price-resolving every holding which is too
    # expensive to do on every signal eval — cash is conservative enough.
    equity = float(getattr(portfolio, "cash", 0.0) or 0.0)
    if equity <= 0:
        equity = float(getattr(portfolio, "initial_capital", 0.0) or 0.0)
    if equity <= 0:
        return SizingResult(
            recommended_shares=0,
            recommended_dollar_amount=0,
            recommended_pct_of_equity=0,
            sizing_method="insufficient_data",
            notes="Portfolio equity is zero or unknown — cannot size.",
        )

    # ── Risk cap ──────────────────────────────────────────────────────
    cap_pct = float(getattr(portfolio, "max_pct_per_trade", 0.0) or 0.0)
    if cap_pct <= 0:
        cap_pct = 5.0  # Defensive default — 5% of equity per trade
    cap_pct = min(cap_pct, 25.0)  # Hard ceiling — never recommend > 25% no matter what

    # ── Price resolution ──────────────────────────────────────────────
    price = float(current_price or 0.0)
    if price <= 0 and price_service is not None:
        cached = price_service.get_price(ticker)
        if cached:
            price = float(cached)
    if price <= 0:
        return SizingResult(
            recommended_shares=0,
            recommended_dollar_amount=0,
            recommended_pct_of_equity=0,
            sizing_method="insufficient_data",
            notes=f"No current price for {ticker} — cannot convert sizing to shares.",
        )

    # ── Conditional probability lookup ────────────────────────────────
    # Falls back to fixed % of equity if no row exists for this strat×ticker.
    cond_row = None
    if strategy_id:
        cond_row = (
            await db.execute(
                select(HenryStats)
                .where(HenryStats.stat_type == "conditional_probability")
                .where(HenryStats.strategy == strategy_id)
                .where(HenryStats.ticker == ticker)
                .order_by(HenryStats.computed_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()

    unconditional = None
    if cond_row and cond_row.data:
        unconditional = (cond_row.data or {}).get("unconditional")

    if not unconditional or not unconditional.get("n"):
        # Fixed-percent fallback
        target_pct = min(FALLBACK_PCT_OF_EQUITY, cap_pct)
        return _build_result(
            equity=equity,
            target_pct=target_pct,
            ticker=ticker,
            price=price,
            sizing_method="insufficient_data",
            notes=(
                f"No conditional probability row for {strategy_id or '?'}×{ticker}. "
                f"Using fixed {target_pct:.1f}% of equity as exploratory sizing."
            ),
        )

    # ── Kelly computation ─────────────────────────────────────────────
    win_rate = float(unconditional.get("win_rate") or 0.0)
    avg_gain = float(unconditional.get("avg_gain_pct") or 0.0)
    avg_loss = float(unconditional.get("avg_loss_pct") or 0.0)
    n_trades = int(unconditional.get("n") or 0)
    ev_pct = float(unconditional.get("ev_pct") or 0.0)

    kelly = _compute_kelly_fraction(win_rate, avg_gain, avg_loss)
    if kelly is None:
        target_pct = min(FALLBACK_PCT_OF_EQUITY, cap_pct)
        return _build_result(
            equity=equity,
            target_pct=target_pct,
            ticker=ticker,
            price=price,
            sizing_method="insufficient_data",
            notes=(
                f"Probability table for {strategy_id}×{ticker} has zero avg "
                f"gain or loss — cannot compute Kelly. Falling back to "
                f"{target_pct:.1f}%."
            ),
        )

    if kelly <= 0:
        # EV-negative — minimum thesis-testing size only.
        target_pct = min(NEGATIVE_EV_PCT_OF_EQUITY, cap_pct)
        return _build_result(
            equity=equity,
            target_pct=target_pct,
            ticker=ticker,
            price=price,
            sizing_method="negative_ev",
            notes=(
                f"Kelly criterion is negative ({kelly:.3f}) — "
                f"{strategy_id}×{ticker} is EV-negative ({ev_pct:.2f}%/trade "
                f"over {n_trades} trades). Sizing dropped to thesis-testing "
                f"minimum {target_pct:.1f}%."
            ),
        )

    # Apply quarter Kelly + low-confidence damping
    multiplier = KELLY_MULTIPLIER
    confidence = max(1, min(10, int(confidence or 5)))
    if confidence < LOW_CONFIDENCE_THRESHOLD:
        multiplier *= LOW_CONFIDENCE_MULTIPLIER

    raw_pct = kelly * multiplier * 100.0  # convert fraction → percent of equity
    capped_pct = min(raw_pct, cap_pct)
    method = "kelly" if capped_pct == raw_pct else "capped"

    # ── Subtract existing exposure on this ticker so result is incremental ──
    existing_value = await _existing_ticker_value(db, ticker)
    existing_pct = (existing_value / equity) * 100.0 if equity > 0 else 0.0
    incremental_pct = max(0.0, capped_pct - existing_pct)

    if incremental_pct <= 0:
        return _build_result(
            equity=equity,
            target_pct=0.0,
            ticker=ticker,
            price=price,
            sizing_method="capped",
            notes=(
                f"Kelly target {capped_pct:.1f}% of equity already met by "
                f"existing {ticker} exposure ({existing_pct:.1f}%). No add."
            ),
        )

    notes = (
        f"Quarter-Kelly sizing: f*={kelly:.3f}, multiplier={multiplier:.2f} "
        f"(confidence {confidence}/10) → {raw_pct:.1f}% target, "
        f"capped at {capped_pct:.1f}% (limit {cap_pct:.1f}%). "
        f"Incremental {incremental_pct:.1f}% after existing {existing_pct:.1f}%. "
        f"Based on {strategy_id}×{ticker}: {win_rate:.1f}% win, "
        f"+{avg_gain:.2f}% / {avg_loss:.2f}% over {n_trades} trades."
    )
    return _build_result(
        equity=equity,
        target_pct=incremental_pct,
        ticker=ticker,
        price=price,
        sizing_method=method,
        notes=notes,
    )


async def _existing_ticker_value(db, ticker: str) -> float:
    """Sum of current market value of all active holdings on `ticker`."""
    from sqlalchemy import select
    from app.models import PortfolioHolding
    from app.services.price_service import price_service

    rows = list(
        (
            await db.execute(
                select(PortfolioHolding).where(
                    PortfolioHolding.is_active == True,
                    PortfolioHolding.ticker == ticker.upper(),
                )
            )
        ).scalars().all()
    )
    total = 0.0
    for h in rows:
        cached = price_service.get_price(ticker) if price_service else None
        p = float(cached) if cached else float(h.entry_price or 0.0)
        total += float(h.qty or 0.0) * p
    return total


def _build_result(
    equity: float,
    target_pct: float,
    ticker: str,
    price: float,
    sizing_method: str,
    notes: str,
) -> SizingResult:
    target_pct = max(0.0, target_pct)
    dollar = equity * (target_pct / 100.0)
    shares = dollar / price if price > 0 else 0.0
    return SizingResult(
        recommended_shares=shares,
        recommended_dollar_amount=dollar,
        recommended_pct_of_equity=target_pct,
        sizing_method=sizing_method,
        notes=notes,
    )


# ─── Convenience: mutate a PortfolioAction in place ─────────────────────────


# Action types that warrant sizing (we only size adds, not exits).
SIZE_ACTION_TYPES = {"BUY", "ADD", "DCA"}


async def apply_sizing_to_action(
    db,
    action,                 # PortfolioAction (already added to session)
    strategy_id: Optional[str] = None,
) -> Optional[SizingResult]:
    """
    Look up the portfolio for an action, compute size via compute_size,
    and write the result onto the action's recommended_* + sizing_method
    fields. No-op for SELL/TRIM/CLOSE/REBALANCE.

    Returns the SizingResult so the caller can include `notes` in
    Henry's reasoning if desired. None when sizing was skipped or
    failed to compute.
    """
    if not action or not getattr(action, "action_type", None):
        return None
    if action.action_type.upper() not in SIZE_ACTION_TYPES:
        return None

    try:
        from sqlalchemy import select
        from app.models import Portfolio

        portfolio = (
            await db.execute(
                select(Portfolio).where(Portfolio.id == action.portfolio_id)
            )
        ).scalar_one_or_none()
        if not portfolio:
            return None

        result = await compute_size(
            db,
            portfolio=portfolio,
            ticker=action.ticker,
            direction=action.direction,
            strategy_id=strategy_id,
            confidence=int(action.confidence or 5),
            current_price=float(action.current_price or 0.0) or None,
        )
        for k, v in result.to_db_dict().items():
            setattr(action, k, v)
        return result
    except Exception as e:
        logger.debug(f"apply_sizing_to_action failed: {e}")
        return None
