"""
Options API
===========
Data + portfolio-view endpoints for options trading. Order execution is
handled in `api/execution.py` under /api/execution/options-order so that
the existing execution surface remains the single enforcement point.
"""
from __future__ import annotations

import logging
from datetime import date as date_type
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.options_trade import OptionsTrade
from app.models.portfolio import Portfolio
from app.services import options_service


logger = logging.getLogger(__name__)
router = APIRouter(prefix="", tags=["options"])


# ── Options data ────────────────────────────────────────────────────

@router.get("/options/chain/{ticker}")
async def get_chain(
    ticker: str,
    expiration: Optional[str] = Query(None, description="ISO date YYYY-MM-DD"),
    max_expirations: int = Query(4, ge=1, le=12),
):
    """Return the options chain for a ticker. When `expiration` is set,
    only that one expiry is returned; otherwise the nearest N expirations."""
    # Normalise exchange prefix (NASDAQ:NVDA → NVDA)
    ticker = ticker.upper().strip().split(":")[-1]
    return await options_service.get_options_chain(
        ticker, expiration_date=expiration, max_expirations=max_expirations,
    )


@router.get("/options/expirations/{ticker}")
async def get_expirations(ticker: str):
    ticker = ticker.upper().strip().split(":")[-1]
    return {"ticker": ticker, "expirations": await options_service.get_expirations(ticker)}


@router.get("/options/quote/{option_symbol}")
async def get_quote(option_symbol: str):
    q = await options_service.get_option_quote(option_symbol)
    if q is None:
        raise HTTPException(status_code=404, detail="Quote not available")
    return q


# ── Per-portfolio options views ────────────────────────────────────

def _serialize_trade(t: OptionsTrade) -> dict:
    return {
        "id": t.id,
        "portfolio_id": t.portfolio_id,
        "ticker": t.ticker,
        "option_symbol": t.option_symbol,
        "option_type": t.option_type,
        "strike": t.strike,
        "expiration": t.expiration.isoformat() if t.expiration else None,
        "direction": t.direction,
        "quantity": t.quantity,
        "entry_premium": t.entry_premium,
        "entry_time": t.entry_time.isoformat() if t.entry_time else None,
        "underlying_price_at_entry": t.underlying_price_at_entry,
        "greeks_at_entry": t.greeks_at_entry,
        "iv_at_entry": t.iv_at_entry,
        "current_premium": t.current_premium,
        "greeks_current": t.greeks_current,
        "exit_premium": t.exit_premium,
        "exit_time": t.exit_time.isoformat() if t.exit_time else None,
        "pnl_dollars": t.pnl_dollars,
        "pnl_percent": t.pnl_percent,
        "status": t.status,
        "strategy_type": t.strategy_type,
        "spread_group_id": t.spread_group_id,
        "days_to_expiration": t.days_to_expiration,
    }


@router.get("/portfolios/{portfolio_id}/options")
async def list_open_options(
    portfolio_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Open options positions for a portfolio, grouped by spread_group_id."""
    result = await db.execute(
        select(OptionsTrade).where(
            and_(
                OptionsTrade.portfolio_id == portfolio_id,
                OptionsTrade.status == "open",
            )
        ).order_by(OptionsTrade.expiration.asc(), OptionsTrade.entry_time.desc())
    )
    trades = result.scalars().all()
    legs = [_serialize_trade(t) for t in trades]

    # Group by spread_group_id so the UI can show multi-leg strategies as a unit
    groups: dict[str, list[dict]] = {}
    singles: list[dict] = []
    for leg in legs:
        gid = leg.get("spread_group_id")
        if gid:
            groups.setdefault(gid, []).append(leg)
        else:
            singles.append(leg)

    grouped_out: list[dict] = []
    for gid, leg_list in groups.items():
        strategy = leg_list[0].get("strategy_type")
        net_pnl = sum((l.get("pnl_dollars") or 0) for l in leg_list)
        # Net P&L mark-to-market: (current - entry) × qty × 100, signed by direction
        def _leg_mtm(l: dict) -> float:
            cp = l.get("current_premium")
            if cp is None:
                return 0.0
            sign = 1 if l["direction"] == "long" else -1
            return (cp - l["entry_premium"]) * l["quantity"] * 100 * sign
        mtm = sum(_leg_mtm(l) for l in leg_list)
        grouped_out.append({
            "spread_group_id": gid,
            "strategy_type": strategy,
            "legs": leg_list,
            "net_pnl_dollars": round(mtm, 2),
            "realized_pnl_dollars": net_pnl,
        })

    return {
        "portfolio_id": portfolio_id,
        "single_legs": singles,
        "spreads": grouped_out,
    }


@router.get("/portfolios/{portfolio_id}/options/history")
async def list_closed_options(
    portfolio_id: str,
    limit: int = Query(50, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
):
    """Closed/expired options legs for a portfolio."""
    result = await db.execute(
        select(OptionsTrade).where(
            and_(
                OptionsTrade.portfolio_id == portfolio_id,
                OptionsTrade.status.in_(["closed", "expired", "assigned"]),
            )
        ).order_by(OptionsTrade.exit_time.desc().nullslast()).limit(limit)
    )
    return [_serialize_trade(t) for t in result.scalars().all()]


@router.get("/portfolios/{portfolio_id}/options/greeks")
async def aggregate_greeks(
    portfolio_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Portfolio-level aggregate Greeks across all open options legs.
    Short legs contribute negated Greek values. Values are reported in
    per-contract units (delta/theta/gamma/vega × qty × 100).
    """
    result = await db.execute(
        select(OptionsTrade).where(
            and_(
                OptionsTrade.portfolio_id == portfolio_id,
                OptionsTrade.status == "open",
            )
        )
    )
    rows = result.scalars().all()

    agg = {"delta": 0.0, "gamma": 0.0, "theta": 0.0, "vega": 0.0}
    open_legs = 0
    for t in rows:
        greeks = t.greeks_current or t.greeks_at_entry or {}
        if not greeks:
            continue
        sign = 1 if t.direction == "long" else -1
        for k in agg.keys():
            v = greeks.get(k)
            if v is None:
                continue
            agg[k] += sign * float(v) * float(t.quantity) * 100.0
        open_legs += 1

    return {
        "portfolio_id": portfolio_id,
        "open_legs": open_legs,
        "delta": round(agg["delta"], 2),
        "gamma": round(agg["gamma"], 4),
        "theta": round(agg["theta"], 2),
        "vega": round(agg["vega"], 2),
    }


# ── Per-portfolio options config ───────────────────────────────────

_ALLOWED_LEVELS = (0, 1, 2, 3)


async def _get_global_options_defaults() -> dict:
    """Read the global options defaults from henry_cache.  Falls back to
    spec defaults when no override is stored."""
    from app.models.henry_cache import HenryCache
    from app.database import async_session
    defaults = {
        "max_risk_per_trade": 2000.0,
        "max_daily_trades": 5,
        "min_dte": 7,
        "target_dte": 35,
        "strategy_min_score": 0.5,
    }
    try:
        async with async_session() as db:
            row = await db.execute(
                select(HenryCache).where(HenryCache.cache_key == "options:defaults")
            )
            cache = row.scalar_one_or_none()
            if cache and cache.content:
                content = cache.content
                # Tolerate either dict (PostgreSQL JSONB) or string (SQLite)
                if isinstance(content, str):
                    import json
                    try:
                        content = json.loads(content)
                    except Exception:
                        content = None
                if isinstance(content, dict):
                    defaults.update({k: v for k, v in content.items() if k in defaults})
    except Exception:
        pass
    return defaults


@router.get("/portfolios/{portfolio_id}/options-config")
async def get_options_config(
    portfolio_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Return the effective options configuration (portfolio override +
    global defaults) so the frontend always sees exactly what's in effect."""
    portfolio = (await db.execute(
        select(Portfolio).where(Portfolio.id == portfolio_id)
    )).scalar_one_or_none()
    if not portfolio:
        raise HTTPException(status_code=404, detail="Portfolio not found")

    defaults = await _get_global_options_defaults()
    level = getattr(portfolio, "options_level", 0) or 0
    return {
        "portfolio_id": portfolio_id,
        "portfolio_name": portfolio.name,
        "options_level": level,
        "max_options_risk": getattr(portfolio, "max_options_risk", None),
        "max_options_daily_trades": getattr(portfolio, "max_options_daily_trades", None),
        "options_allocation_pct": getattr(portfolio, "options_allocation_pct", 0.20),
        "effective": {
            "max_risk_per_trade": getattr(portfolio, "max_options_risk", None) or defaults["max_risk_per_trade"],
            "max_daily_trades": getattr(portfolio, "max_options_daily_trades", None) or defaults["max_daily_trades"],
            "min_dte": defaults["min_dte"],
            "target_dte": defaults["target_dte"],
            "strategy_min_score": defaults["strategy_min_score"],
        },
        "allowed_strategies": _strategies_for_level(level),
    }


def _strategies_for_level(level: int) -> list[str]:
    from app.models.options_trade import STRATEGY_MIN_LEVEL
    return sorted(s for s, L in STRATEGY_MIN_LEVEL.items() if L <= level)


@router.put("/portfolios/{portfolio_id}/options-config")
async def update_options_config(
    portfolio_id: str,
    body: dict,
    db: AsyncSession = Depends(get_db),
):
    """Update a portfolio's options level and risk overrides. Accepts any
    subset of the four configurable fields — omitted fields are left
    unchanged."""
    portfolio = (await db.execute(
        select(Portfolio).where(Portfolio.id == portfolio_id)
    )).scalar_one_or_none()
    if not portfolio:
        raise HTTPException(status_code=404, detail="Portfolio not found")

    if "options_level" in body:
        lvl = int(body["options_level"])
        if lvl not in _ALLOWED_LEVELS:
            raise HTTPException(status_code=400, detail=f"options_level must be one of {_ALLOWED_LEVELS}")
        portfolio.options_level = lvl

    if "max_options_risk" in body:
        v = body["max_options_risk"]
        portfolio.max_options_risk = float(v) if v is not None else None

    if "max_options_daily_trades" in body:
        v = body["max_options_daily_trades"]
        portfolio.max_options_daily_trades = int(v) if v is not None else None

    if "options_allocation_pct" in body:
        pct = float(body["options_allocation_pct"])
        if not 0.0 <= pct <= 1.0:
            raise HTTPException(status_code=400, detail="options_allocation_pct must be in [0, 1]")
        portfolio.options_allocation_pct = pct

    await db.commit()
    return await get_options_config(portfolio_id, db)


# ── Global defaults ────────────────────────────────────────────────

@router.get("/settings/options/defaults")
async def get_defaults():
    """Global options defaults. Falls back to spec values when nothing is stored."""
    return await _get_global_options_defaults()


@router.put("/settings/options/defaults")
async def update_defaults(body: dict):
    """Update global options defaults (stored in henry_cache)."""
    from app.models.henry_cache import HenryCache
    from app.database import async_session

    defaults = await _get_global_options_defaults()
    for k in ("max_risk_per_trade", "max_daily_trades", "min_dte", "target_dte", "strategy_min_score"):
        if k in body and body[k] is not None:
            try:
                defaults[k] = float(body[k]) if k == "strategy_min_score" else (
                    int(body[k]) if k in ("max_daily_trades", "min_dte", "target_dte")
                    else float(body[k])
                )
            except (TypeError, ValueError):
                raise HTTPException(status_code=400, detail=f"invalid value for {k}")

    async with async_session() as db:
        existing = (await db.execute(
            select(HenryCache).where(HenryCache.cache_key == "options:defaults")
        )).scalar_one_or_none()
        if existing:
            existing.content = defaults
            existing.is_stale = False
        else:
            db.add(HenryCache(
                cache_key="options:defaults",
                cache_type="system_config",
                content=defaults,
                is_stale=False,
            ))
        await db.commit()
    return defaults
