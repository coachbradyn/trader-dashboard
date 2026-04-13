"""
Trade Warnings — Concentration & Correlation
============================================

Pre-decision risk checks injected into the system prompt when Henry is
about to evaluate a trade. Pure Python — no AI cost. Designed to fail
loud (warning text in the prompt) so Henry can't ignore concentration
risk while reasoning about a new entry.

Rules (per the intelligence-upgrade brief, System 6):
  - ticker exposure > 15% of portfolio  → concentration warning
  - sector exposure > 35% of portfolio  → sector concentration warning
  - same-direction position by another strategy on same ticker → corr
  - high strategy correlation (≥ 70% historical agreement) overlap
    on a same-direction ticker → strategy overlap warning

The function is single-call: pass ticker + (optional) direction + strategy
+ db session, get back a list of formatted warning strings. The caller
decides whether to inject (today: only when ticker is in scope).
"""

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)


# Concentration thresholds — defaults only. Phase 7 routes the live
# values through runtime_config (function-local lookup shadows these).
TICKER_WARN_PCT_DEFAULT = 15.0
SECTOR_WARN_PCT_DEFAULT = 35.0
STRATEGY_CORR_WARN_PCT = 70.0


async def _resolve_sector_for_ticker(db, ticker: str) -> Optional[str]:
    """Look up sector from ticker_fundamentals. Returns None on miss."""
    try:
        from sqlalchemy import select
        from app.models import TickerFundamentals

        row = (
            await db.execute(
                select(TickerFundamentals.sector)
                .where(TickerFundamentals.ticker == ticker.upper())
                .limit(1)
            )
        ).scalar_one_or_none()
        return row if row else None
    except Exception:
        return None


async def _portfolio_holdings_with_value(db) -> list[dict]:
    """
    Returns active holdings enriched with current market value. Uses
    price_service for the live price; falls back to entry_price if the
    cache is empty for that ticker (e.g., after a fresh restart).
    """
    from sqlalchemy import select
    from app.models import PortfolioHolding
    from app.services.price_service import price_service

    out: list[dict] = []
    try:
        rows = list(
            (
                await db.execute(
                    select(PortfolioHolding).where(
                        PortfolioHolding.is_active == True
                    )
                )
            ).scalars().all()
        )
    except Exception as e:
        logger.debug(f"holdings fetch failed: {e}")
        return out

    for h in rows:
        cp = price_service.get_price(h.ticker) if price_service else None
        price = float(cp) if cp else float(h.entry_price or 0.0)
        out.append({
            "id": h.id,
            "portfolio_id": h.portfolio_id,
            "ticker": (h.ticker or "").upper(),
            "direction": (h.direction or "").lower(),
            "qty": float(h.qty or 0.0),
            "value": float(h.qty or 0.0) * price,
            "strategy_name": h.strategy_name,
        })
    return out


async def _strategy_correlations(db) -> dict[tuple[str, str], float]:
    """
    Return {(strategy_a, strategy_b): agreement_pct} from the latest
    HenryStats row of stat_type='strategy_correlation'. Sorted-tuple
    keys so lookups are direction-independent.
    """
    out: dict[tuple[str, str], float] = {}
    try:
        from sqlalchemy import select
        from app.models import HenryStats

        row = (
            await db.execute(
                select(HenryStats)
                .where(HenryStats.stat_type == "strategy_correlation")
                .order_by(HenryStats.computed_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if not row or not row.data:
            return out
        for k, v in row.data.items():
            if not isinstance(v, dict):
                continue
            pct = v.get("agreement_pct")
            if pct is None:
                continue
            parts = k.split("_", 1)
            if len(parts) != 2:
                continue
            key = tuple(sorted(parts))
            out[key] = float(pct)
    except Exception as e:
        logger.debug(f"strategy_correlations fetch failed: {e}")
    return out


async def compute_trade_warnings(
    db,
    ticker: str,
    direction: Optional[str] = None,
    strategy_id: Optional[str] = None,
    proposed_value_dollars: Optional[float] = None,
) -> list[str]:
    """
    Build the warning list for a trade evaluation. Returns 0+ strings,
    each a single-sentence warning suitable for direct prompt injection.

    Parameters mirror the eventual portfolio-action call site:
      - direction: 'long' / 'short' / None — if None, correlation checks
        flag any same-ticker overlap regardless of direction
      - strategy_id: the strategy under evaluation; used to look up the
        correlation matrix against currently-open strategies
      - proposed_value_dollars: if provided, exposure is calculated
        AFTER the proposed trade. Otherwise, current exposure only.

    Phase 7: ticker + sector concentration limits sourced from
    runtime_config (note the search-space values are FRACTIONS in
    [0, 1]; the warning thresholds are PERCENTS so we multiply ×100).
    """
    if not ticker:
        return []
    ticker = ticker.upper()
    direction_norm = (direction or "").lower() if direction else None
    warnings: list[str] = []

    # Resolve thresholds from runtime_config (fractions) → percents.
    from app.services import runtime_config as _rc
    ticker_warn_pct = float(
        await _rc.get_async("concentration_limit_ticker") or (TICKER_WARN_PCT_DEFAULT / 100.0)
    ) * 100.0
    sector_warn_pct = float(
        await _rc.get_async("concentration_limit_sector") or (SECTOR_WARN_PCT_DEFAULT / 100.0)
    ) * 100.0

    holdings = await _portfolio_holdings_with_value(db)
    if not holdings:
        return warnings  # Nothing held → nothing to warn about (yet)

    total_value = sum(h["value"] for h in holdings)
    if total_value <= 0:
        return warnings

    # ── Ticker concentration ──────────────────────────────────────────
    ticker_value = sum(h["value"] for h in holdings if h["ticker"] == ticker)
    proposed_ticker_value = ticker_value + (proposed_value_dollars or 0.0)
    proposed_total = total_value + (proposed_value_dollars or 0.0)
    ticker_pct = (proposed_ticker_value / proposed_total) * 100.0 if proposed_total > 0 else 0.0
    if ticker_pct > ticker_warn_pct:
        if proposed_value_dollars:
            warnings.append(
                f"WARNING: Adding to {ticker} would bring total exposure to "
                f"{ticker_pct:.1f}% of portfolio (concentration limit "
                f"{ticker_warn_pct:.0f}%)."
            )
        else:
            warnings.append(
                f"WARNING: {ticker} already at {ticker_pct:.1f}% of portfolio "
                f"(concentration limit {ticker_warn_pct:.0f}%). "
                f"Treat any add as breaching guidance."
            )

    # ── Sector concentration ──────────────────────────────────────────
    sector = await _resolve_sector_for_ticker(db, ticker)
    if sector:
        # Compute sector exposure across all holdings; resolve sectors
        # on the fly. Cache misses count as 'unknown' and are excluded.
        sector_value = 0.0
        for h in holdings:
            h_sector = (
                sector if h["ticker"] == ticker
                else await _resolve_sector_for_ticker(db, h["ticker"])
            )
            if h_sector and h_sector == sector:
                sector_value += h["value"]
        proposed_sector_value = sector_value + (proposed_value_dollars or 0.0)
        sector_pct = (proposed_sector_value / proposed_total) * 100.0 if proposed_total > 0 else 0.0
        if sector_pct > sector_warn_pct:
            verb = "would reach" if proposed_value_dollars else "is at"
            warnings.append(
                f"WARNING: {sector} sector exposure {verb} {sector_pct:.1f}% "
                f"of portfolio (limit {sector_warn_pct:.0f}%). "
                f"Consider sector diversification."
            )

    # ── Same-ticker / same-direction overlap ──────────────────────────
    same_ticker_others = [
        h for h in holdings
        if h["ticker"] == ticker
        and (
            strategy_id is None
            or (h["strategy_name"] or "").lower() != (strategy_id or "").lower()
        )
    ]
    if same_ticker_others and direction_norm:
        overlap = [
            h for h in same_ticker_others
            if (h["direction"] or "").lower() == direction_norm
        ]
        if overlap:
            others = ", ".join(
                f"{h['strategy_name'] or 'unknown'}"
                for h in overlap
            )
            warnings.append(
                f"WARNING: {others} already {direction_norm} on {ticker}. "
                f"Combined exposure builds on an existing position."
            )

    # ── Strategy correlation overlap ──────────────────────────────────
    if strategy_id and direction_norm:
        corrs = await _strategy_correlations(db)
        if corrs:
            # Find any other open strategy whose pair-correlation with
            # the evaluating strategy is high AND that's currently long
            # (or short) the same direction on any ticker.
            open_strategies = {
                (h["strategy_name"] or "").upper(): (h["direction"] or "").lower()
                for h in holdings
                if h["strategy_name"]
            }
            this_strat = (strategy_id or "").upper()
            for other_strat, other_dir in open_strategies.items():
                if other_strat == this_strat:
                    continue
                key = tuple(sorted([this_strat, other_strat]))
                pct = corrs.get(key)
                if pct is not None and pct >= STRATEGY_CORR_WARN_PCT and other_dir == direction_norm:
                    warnings.append(
                        f"WARNING: {other_strat} is already {other_dir} on a "
                        f"position; historical agreement with {this_strat} is "
                        f"{pct:.0f}%. Trade theses likely overlap."
                    )

    return warnings
