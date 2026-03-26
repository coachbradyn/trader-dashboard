from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Portfolio, Trade, PortfolioTrade, PortfolioSnapshot, PortfolioHolding
from app.schemas.portfolio import (
    PortfolioResponse,
    PositionResponse,
    PerformanceResponse,
    EquityPoint,
    DailyStatsResponse,
)
from app.services.performance_calc import calculate_performance, get_equity_history, get_daily_stats
from app.services.price_service import price_service

router = APIRouter()


async def _calc_holdings_value(portfolio_id: str, db: AsyncSession) -> tuple[float, float, int]:
    """Calculate total market value, unrealized P&L, and count from portfolio holdings.

    Returns (holdings_value, holdings_unrealized_pnl, holdings_count).
    holdings_value = sum of (current_price * qty) for all active holdings.
    holdings_unrealized_pnl = sum of per-holding unrealized P&L.
    """
    result = await db.execute(
        select(PortfolioHolding)
        .where(PortfolioHolding.portfolio_id == portfolio_id, PortfolioHolding.is_active == True)
    )
    holdings = result.scalars().all()

    if not holdings:
        return 0.0, 0.0, 0

    total_value = 0.0
    total_unrealized = 0.0
    count = 0

    for h in holdings:
        current_price = price_service.get_price(h.ticker)
        if current_price is None:
            # Fall back to entry price if no live price yet
            current_price = h.entry_price
            price_service.add_ticker(h.ticker)  # Register for next poll

        position_value = current_price * h.qty
        cost_basis = h.entry_price * h.qty

        if h.direction == "long":
            unrealized = position_value - cost_basis
        else:
            unrealized = cost_basis - position_value

        total_value += position_value
        total_unrealized += unrealized
        count += 1

    return total_value, total_unrealized, count


async def _build_portfolio_response(p: Portfolio, db: AsyncSession) -> PortfolioResponse:
    """Build a PortfolioResponse combining snapshot data + holdings data."""
    # Snapshot-based data (from webhook trades)
    snap_result = await db.execute(
        select(PortfolioSnapshot)
        .where(PortfolioSnapshot.portfolio_id == p.id)
        .order_by(PortfolioSnapshot.snapshot_time.desc())
        .limit(1)
    )
    latest = snap_result.scalar_one_or_none()

    snap_equity = latest.equity if latest else 0.0
    snap_unrealized = latest.unrealized_pnl if latest else 0.0
    snap_positions = latest.open_positions if latest else 0

    # Holdings-based data (from manual entries + portfolio manager)
    holdings_value, holdings_unrealized, holdings_count = await _calc_holdings_value(p.id, db)

    # Combine: total equity = cash + snapshot equity gains + holdings market value
    # If no snapshots and no holdings, show initial capital
    if not latest and holdings_count == 0:
        equity = p.initial_capital
        unrealized = 0.0
        open_pos = 0
    else:
        # Holdings value is the market value of manually tracked positions
        # Snapshot equity tracks webhook-driven trade P&L
        # Combine both sources
        equity = p.cash + holdings_value + (snap_equity - p.initial_capital if latest else 0.0)
        unrealized = snap_unrealized + holdings_unrealized
        open_pos = snap_positions + holdings_count

    total_return = ((equity - p.initial_capital) / p.initial_capital * 100) if p.initial_capital > 0 else 0.0

    return PortfolioResponse(
        id=p.id,
        name=p.name,
        description=p.description,
        initial_capital=p.initial_capital,
        cash=p.cash,
        equity=round(equity, 2),
        unrealized_pnl=round(unrealized, 2),
        total_return_pct=round(total_return, 2),
        open_positions=open_pos,
        is_active=p.is_active,
        created_at=p.created_at,
    )


@router.get("/portfolios", response_model=list[PortfolioResponse])
async def get_portfolios(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Portfolio).where(Portfolio.is_active == True))
    portfolios = result.scalars().all()

    responses = []
    for p in portfolios:
        responses.append(await _build_portfolio_response(p, db))

    return responses


@router.get("/portfolios/{portfolio_id}", response_model=PortfolioResponse)
async def get_portfolio(portfolio_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Portfolio).where(Portfolio.id == portfolio_id))
    p = result.scalar_one_or_none()
    if not p:
        raise HTTPException(404, "Portfolio not found")

    return await _build_portfolio_response(p, db)


@router.get("/portfolios/{portfolio_id}/positions", response_model=list[PositionResponse])
async def get_positions(portfolio_id: str, db: AsyncSession = Depends(get_db)):
    # Webhook-originated open trades
    result = await db.execute(
        select(Trade)
        .join(PortfolioTrade)
        .where(PortfolioTrade.portfolio_id == portfolio_id, Trade.status == "open")
    )
    open_trades = result.scalars().all()

    positions = []
    for t in open_trades:
        current_price = price_service.get_price(t.ticker)
        unrealized = None
        unrealized_pct = None

        if current_price is not None:
            if t.direction == "long":
                unrealized = (current_price - t.entry_price) * t.qty
            else:
                unrealized = (t.entry_price - current_price) * t.qty
            position_value = t.entry_price * t.qty
            unrealized_pct = (unrealized / position_value * 100) if position_value > 0 else 0.0

        positions.append(PositionResponse(
            trade_id=t.id,
            ticker=t.ticker,
            direction=t.direction,
            entry_price=t.entry_price,
            qty=t.qty,
            stop_price=t.stop_price,
            entry_time=t.entry_time,
            current_price=current_price,
            unrealized_pnl=round(unrealized, 2) if unrealized is not None else None,
            unrealized_pnl_pct=round(unrealized_pct, 2) if unrealized_pct is not None else None,
        ))

    # Also include active holdings as positions
    holdings_result = await db.execute(
        select(PortfolioHolding)
        .where(PortfolioHolding.portfolio_id == portfolio_id, PortfolioHolding.is_active == True)
    )
    holdings = holdings_result.scalars().all()

    for h in holdings:
        # Skip holdings that are linked to a trade (already in positions above)
        if h.trade_id is not None:
            continue

        current_price = price_service.get_price(h.ticker)
        unrealized = None
        unrealized_pct = None

        if current_price is not None:
            if h.direction == "long":
                unrealized = (current_price - h.entry_price) * h.qty
            else:
                unrealized = (h.entry_price - current_price) * h.qty
            position_value = h.entry_price * h.qty
            unrealized_pct = (unrealized / position_value * 100) if position_value > 0 else 0.0
        else:
            price_service.add_ticker(h.ticker)

        positions.append(PositionResponse(
            trade_id=h.id,  # Use holding ID as trade_id
            ticker=h.ticker,
            direction=h.direction,
            entry_price=h.entry_price,
            qty=h.qty,
            stop_price=None,
            entry_time=h.entry_date,
            current_price=current_price,
            unrealized_pnl=round(unrealized, 2) if unrealized is not None else None,
            unrealized_pnl_pct=round(unrealized_pct, 2) if unrealized_pct is not None else None,
        ))

    return positions


@router.get("/portfolios/{portfolio_id}/performance", response_model=PerformanceResponse)
async def get_performance(portfolio_id: str, db: AsyncSession = Depends(get_db)):
    return await calculate_performance(portfolio_id, db)


@router.get("/portfolios/{portfolio_id}/equity-history", response_model=list[EquityPoint])
async def get_equity(portfolio_id: str, db: AsyncSession = Depends(get_db)):
    return await get_equity_history(portfolio_id, db)


@router.get("/portfolios/{portfolio_id}/daily-stats", response_model=list[DailyStatsResponse])
async def get_daily(portfolio_id: str, db: AsyncSession = Depends(get_db)):
    return await get_daily_stats(portfolio_id, db)
