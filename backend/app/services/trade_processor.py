from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import Trader, Trade, Portfolio, PortfolioStrategy, PortfolioTrade, PortfolioSnapshot
from app.schemas.webhook import WebhookPayload
from app.utils.auth import verify_api_key
from app.services.price_service import price_service


async def process_webhook(payload: WebhookPayload, db: AsyncSession) -> Trade:
    # 1. Validate trader and API key
    result = await db.execute(select(Trader).where(Trader.trader_id == payload.trader))
    trader = result.scalar_one_or_none()
    if not trader:
        raise ValueError(f"Unknown trader: {payload.trader}")
    if not verify_api_key(payload.key, trader.api_key_hash):
        raise ValueError("Invalid API key")

    # 2. Process based on signal type
    if payload.signal == "entry":
        trade = await _process_entry(trader, payload, db)
    elif payload.signal == "exit":
        trade = await _process_exit(trader, payload, db)
    else:
        raise ValueError(f"Unknown signal type: {payload.signal}")

    await db.commit()
    return trade


async def _process_entry(trader: Trader, payload: WebhookPayload, db: AsyncSession) -> Trade:
    entry_time = (
        datetime.utcfromtimestamp(payload.time / 1000)
        if payload.time
        else datetime.utcnow()
    )

    trade = Trade(
        trader_id=trader.id,
        ticker=payload.ticker,
        direction=payload.dir,
        entry_price=payload.price,
        qty=payload.qty,
        entry_signal_strength=payload.sig,
        entry_adx=payload.adx,
        entry_atr=payload.atr,
        stop_price=payload.stop,
        timeframe=payload.tf,
        entry_time=entry_time,
        status="open",
        raw_entry_payload=payload.model_dump(),
    )
    db.add(trade)
    await db.flush()  # get trade.id

    # 3. Find all portfolios linked to this trader with matching direction
    result = await db.execute(
        select(PortfolioStrategy)
        .where(PortfolioStrategy.trader_id == trader.id)
        .options(selectinload(PortfolioStrategy.portfolio))
    )
    links = result.scalars().all()

    for link in links:
        # Check direction filter
        if link.direction_filter and link.direction_filter != payload.dir:
            continue

        # Create portfolio_trade entry
        pt = PortfolioTrade(portfolio_id=link.portfolio_id, trade_id=trade.id)
        db.add(pt)

        # Deduct position cost from portfolio cash
        position_cost = payload.price * payload.qty
        link.portfolio.cash -= position_cost

    return trade


async def _process_exit(trader: Trader, payload: WebhookPayload, db: AsyncSession) -> Trade:
    # Find matching open trade
    result = await db.execute(
        select(Trade)
        .where(
            Trade.trader_id == trader.id,
            Trade.direction == payload.dir,
            Trade.ticker == payload.ticker,
            Trade.status == "open",
        )
        .order_by(Trade.entry_time.desc())
        .limit(1)
    )
    trade = result.scalar_one_or_none()
    if not trade:
        raise ValueError(f"No open {payload.dir} trade found for {payload.ticker}")

    exit_time = (
        datetime.utcfromtimestamp(payload.time / 1000)
        if payload.time
        else datetime.utcnow()
    )

    trade.exit_price = payload.price
    trade.exit_reason = payload.exit_reason
    trade.exit_time = exit_time
    trade.bars_in_trade = payload.bars_in_trade
    trade.status = "closed"
    trade.raw_exit_payload = payload.model_dump()

    # Calculate P&L
    if trade.direction == "long":
        trade.pnl_dollars = (payload.price - trade.entry_price) * trade.qty
    else:
        trade.pnl_dollars = (trade.entry_price - payload.price) * trade.qty

    position_value = trade.entry_price * trade.qty
    trade.pnl_percent = (trade.pnl_dollars / position_value * 100) if position_value > 0 else 0.0

    # Credit P&L back to linked portfolios
    result = await db.execute(
        select(PortfolioTrade)
        .where(PortfolioTrade.trade_id == trade.id)
        .options(selectinload(PortfolioTrade.portfolio))
    )
    portfolio_trades = result.scalars().all()

    for pt in portfolio_trades:
        # Return position cost + P&L to cash
        pt.portfolio.cash += position_value + trade.pnl_dollars

    # Take snapshots for affected portfolios
    for pt in portfolio_trades:
        await _take_snapshot(pt.portfolio, db)

    return trade


async def _take_snapshot(portfolio: Portfolio, db: AsyncSession):
    # Get all open trades for this portfolio
    result = await db.execute(
        select(Trade)
        .join(PortfolioTrade)
        .where(PortfolioTrade.portfolio_id == portfolio.id, Trade.status == "open")
    )
    open_trades = result.scalars().all()

    # Calculate unrealized P&L using cached prices
    unrealized_pnl = 0.0
    for t in open_trades:
        current_price = price_service.get_price(t.ticker)
        if current_price:
            if t.direction == "long":
                unrealized_pnl += (current_price - t.entry_price) * t.qty
            else:
                unrealized_pnl += (t.entry_price - current_price) * t.qty

    # Get closed P&L
    result = await db.execute(
        select(Trade)
        .join(PortfolioTrade)
        .where(PortfolioTrade.portfolio_id == portfolio.id, Trade.status == "closed")
    )
    closed_trades = result.scalars().all()
    closed_pnl = sum(t.pnl_dollars or 0.0 for t in closed_trades)

    equity = portfolio.initial_capital + closed_pnl + unrealized_pnl

    # Track peak and drawdown
    result = await db.execute(
        select(PortfolioSnapshot)
        .where(PortfolioSnapshot.portfolio_id == portfolio.id)
        .order_by(PortfolioSnapshot.snapshot_time.desc())
        .limit(1)
    )
    last_snapshot = result.scalar_one_or_none()
    peak_equity = max(equity, last_snapshot.peak_equity if last_snapshot else portfolio.initial_capital)
    drawdown_pct = ((peak_equity - equity) / peak_equity * 100) if peak_equity > 0 else 0.0

    snapshot = PortfolioSnapshot(
        portfolio_id=portfolio.id,
        equity=equity,
        cash=portfolio.cash,
        unrealized_pnl=unrealized_pnl,
        open_positions=len(open_trades),
        drawdown_pct=drawdown_pct,
        peak_equity=peak_equity,
    )
    db.add(snapshot)
