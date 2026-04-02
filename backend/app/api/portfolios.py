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


async def _calc_holdings_value(portfolio_id: str, db: AsyncSession) -> tuple[float, float, float, int]:
    """Calculate holdings metrics for portfolio display.

    Returns (holdings_cost_basis, holdings_unrealized_pnl, holdings_market_value, holdings_count).
    - holdings_cost_basis = sum of (entry_price * qty) — capital deployed, NOT a gain.
    - holdings_unrealized_pnl = sum of per-holding unrealized P&L — actual performance.
    - holdings_market_value = sum of (current_price * qty) — for display purposes.
    """
    result = await db.execute(
        select(PortfolioHolding)
        .where(PortfolioHolding.portfolio_id == portfolio_id, PortfolioHolding.is_active == True)
    )
    holdings = result.scalars().all()

    if not holdings:
        return 0.0, 0.0, 0.0, 0

    total_cost_basis = 0.0
    total_market_value = 0.0
    total_unrealized = 0.0
    count = 0

    for h in holdings:
        current_price = price_service.get_price(h.ticker)
        if current_price is None:
            current_price = h.entry_price
            price_service.add_ticker(h.ticker)

        position_value = current_price * h.qty
        cost_basis = h.entry_price * h.qty

        if h.direction == "long":
            unrealized = position_value - cost_basis
        else:
            unrealized = cost_basis - position_value

        total_cost_basis += cost_basis
        total_market_value += position_value
        total_unrealized += unrealized
        count += 1

    return total_cost_basis, total_unrealized, total_market_value, count


async def _build_portfolio_response(p: Portfolio, db: AsyncSession) -> PortfolioResponse:
    """Build a PortfolioResponse combining snapshot data + holdings data."""
    # Holdings-based data (from manual entries + portfolio manager)
    holdings_cost_basis, holdings_unrealized, holdings_market_value, holdings_count = await _calc_holdings_value(p.id, db)

    # Webhook trades: compute unrealized P&L from open trades linked to this portfolio
    from app.models import Trade, PortfolioTrade
    webhook_unrealized = 0.0
    webhook_open = 0
    try:
        trade_result = await db.execute(
            select(Trade)
            .join(PortfolioTrade)
            .where(
                PortfolioTrade.portfolio_id == p.id,
                Trade.status == "open",
                Trade.is_simulated == False,
            )
        )
        open_trades = trade_result.scalars().all()
        webhook_open = len(open_trades)
        for t in open_trades:
            cp = price_service.get_price(t.ticker) or t.entry_price
            if t.direction == "long":
                webhook_unrealized += (cp - t.entry_price) * t.qty
            else:
                webhook_unrealized += (t.entry_price - cp) * t.qty
    except Exception:
        pass

    # Equity = cash + holdings market value + open trade unrealized P&L
    # This is the true portfolio value — what you'd have if you liquidated everything
    equity = p.cash + holdings_market_value + webhook_unrealized
    unrealized = holdings_unrealized + webhook_unrealized
    open_pos = holdings_count + webhook_open

    # Return % = total gains / total capital deployed
    # Gains = equity - initial_capital (deposits don't create gains, they increase both)
    # This gives the correct return on invested capital
    if p.initial_capital > 0:
        # Net gain = equity minus all money put in
        net_gain = equity - p.initial_capital
        total_return = (net_gain / p.initial_capital * 100)
    else:
        total_return = 0.0

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
        execution_mode=p.execution_mode or "local",
        max_order_amount=p.max_order_amount,
        has_alpaca_credentials=bool(p.alpaca_api_key and p.alpaca_secret_key),
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


@router.post("/portfolios/{portfolio_id}/deposit")
async def deposit(portfolio_id: str, body: dict, db: AsyncSession = Depends(get_db)):
    """Add cash to a portfolio (simulates a deposit)."""
    amount = body.get("amount", 0)
    if amount <= 0:
        raise HTTPException(400, "Amount must be positive")

    result = await db.execute(select(Portfolio).where(Portfolio.id == portfolio_id))
    portfolio = result.scalar_one_or_none()
    if not portfolio:
        raise HTTPException(404, "Portfolio not found")

    portfolio.cash = (portfolio.cash or 0) + amount
    portfolio.initial_capital = (portfolio.initial_capital or 0) + amount
    await db.commit()

    return {
        "status": "deposited",
        "amount": amount,
        "new_cash": portfolio.cash,
        "new_initial_capital": portfolio.initial_capital,
    }


@router.post("/portfolios/{portfolio_id}/withdraw")
async def withdraw(portfolio_id: str, body: dict, db: AsyncSession = Depends(get_db)):
    """Remove cash from a portfolio (simulates a withdrawal)."""
    amount = body.get("amount", 0)
    if amount <= 0:
        raise HTTPException(400, "Amount must be positive")

    result = await db.execute(select(Portfolio).where(Portfolio.id == portfolio_id))
    portfolio = result.scalar_one_or_none()
    if not portfolio:
        raise HTTPException(404, "Portfolio not found")

    if amount > (portfolio.cash or 0):
        raise HTTPException(400, f"Insufficient cash. Available: ${portfolio.cash:.2f}")

    portfolio.cash = (portfolio.cash or 0) - amount
    portfolio.initial_capital = max(0, (portfolio.initial_capital or 0) - amount)
    await db.commit()

    return {
        "status": "withdrawn",
        "amount": amount,
        "new_cash": portfolio.cash,
        "new_initial_capital": portfolio.initial_capital,
    }
