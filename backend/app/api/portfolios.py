from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Portfolio, Trade, PortfolioTrade, PortfolioSnapshot
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


@router.get("/portfolios", response_model=list[PortfolioResponse])
async def get_portfolios(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Portfolio).where(Portfolio.is_active == True))
    portfolios = result.scalars().all()

    responses = []
    for p in portfolios:
        # Get latest snapshot for equity
        snap_result = await db.execute(
            select(PortfolioSnapshot)
            .where(PortfolioSnapshot.portfolio_id == p.id)
            .order_by(PortfolioSnapshot.snapshot_time.desc())
            .limit(1)
        )
        latest = snap_result.scalar_one_or_none()

        equity = latest.equity if latest else p.initial_capital
        unrealized = latest.unrealized_pnl if latest else 0.0
        open_pos = latest.open_positions if latest else 0
        total_return = ((equity - p.initial_capital) / p.initial_capital * 100) if p.initial_capital > 0 else 0.0

        responses.append(PortfolioResponse(
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
        ))

    return responses


@router.get("/portfolios/{portfolio_id}", response_model=PortfolioResponse)
async def get_portfolio(portfolio_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Portfolio).where(Portfolio.id == portfolio_id))
    p = result.scalar_one_or_none()
    if not p:
        raise HTTPException(404, "Portfolio not found")

    snap_result = await db.execute(
        select(PortfolioSnapshot)
        .where(PortfolioSnapshot.portfolio_id == p.id)
        .order_by(PortfolioSnapshot.snapshot_time.desc())
        .limit(1)
    )
    latest = snap_result.scalar_one_or_none()

    equity = latest.equity if latest else p.initial_capital
    unrealized = latest.unrealized_pnl if latest else 0.0
    open_pos = latest.open_positions if latest else 0
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


@router.get("/portfolios/{portfolio_id}/positions", response_model=list[PositionResponse])
async def get_positions(portfolio_id: str, db: AsyncSession = Depends(get_db)):
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
