from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import Trader, Trade, Portfolio, PortfolioStrategy, PortfolioTrade, PortfolioSnapshot
from app.schemas.webhook import WebhookPayload
from app.utils.auth import verify_api_key
from app.services.price_service import price_service


async def process_webhook(payload: WebhookPayload, db: AsyncSession) -> Trade:
    # 1. Try to find existing trader
    result = await db.execute(select(Trader).where(Trader.trader_id == payload.trader))
    trader = result.scalar_one_or_none()

    if trader:
        # Known trader — verify API key
        if not verify_api_key(payload.key, trader.api_key_hash):
            raise ValueError("Invalid API key")
    else:
        # Unknown trader — check allowlisted keys
        from app.models.allowlisted_key import AllowlistedKey
        result = await db.execute(
            select(AllowlistedKey).where(AllowlistedKey.claimed_by_id.is_(None))
        )
        unclaimed_keys = result.scalars().all()

        matched_key = None
        for ak in unclaimed_keys:
            if verify_api_key(payload.key, ak.api_key_hash):
                matched_key = ak
                break

        if not matched_key:
            raise ValueError(f"Unknown trader '{payload.trader}' and no matching allowlisted key")

        # Auto-create trader from allowlisted key
        from app.utils.auth import hash_api_key
        trader = Trader(
            trader_id=payload.trader,
            display_name=matched_key.label or f"Strategy ({payload.trader})",
            api_key_hash=matched_key.api_key_hash,
        )
        db.add(trader)
        await db.flush()
        matched_key.claimed_by_id = trader.id

    # Update last webhook timestamp
    trader.last_webhook_at = datetime.now(timezone.utc)

    # 2. Process based on signal type
    ai_eval_portfolios = []
    if payload.signal == "entry":
        trade, ai_eval_portfolios = await _process_entry(trader, payload, db)
    elif payload.signal == "exit":
        trade = await _process_exit(trader, payload, db)
    else:
        raise ValueError(f"Unknown signal type: {payload.signal}")

    await db.commit()

    # Route to Henry for AI-evaluation-enabled portfolios (background)
    if ai_eval_portfolios and payload.signal == "entry":
        import asyncio
        from app.services.ai_portfolio import evaluate_signal_for_portfolio
        for portfolio in ai_eval_portfolios:
            asyncio.create_task(evaluate_signal_for_portfolio(
                trade, trader, payload.model_dump(), portfolio
            ))

    # Auto-add traded ticker to watchlist (non-blocking)
    try:
        from app.models.watchlist_ticker import WatchlistTicker
        wl_result = await db.execute(
            select(WatchlistTicker).where(WatchlistTicker.ticker == payload.ticker.upper())
        )
        wl_existing = wl_result.scalar_one_or_none()
        if wl_existing:
            if not wl_existing.is_active:
                wl_existing.is_active = True
                wl_existing.removed_at = None
        else:
            db.add(WatchlistTicker(ticker=payload.ticker.upper()))
        await db.commit()
    except Exception:
        pass

    # Portfolio manager hooks (non-blocking)
    try:
        from app.services.portfolio_analysis import evaluate_signal, link_trade_to_holding
        if payload.signal == "entry":
            await link_trade_to_holding(trade, db)
            await evaluate_signal(trade, db)
            await db.commit()
        elif payload.signal == "exit":
            from app.services.portfolio_analysis import track_action_outcome
            await track_action_outcome(trade, db)

            # Save outcome context (non-blocking)
            import asyncio
            from app.services.ai_service import save_context
            pnl_pct = trade.pnl_percent or 0.0
            asyncio.create_task(save_context(
                content=f"CLOSED {trade.ticker} {trade.direction} | PnL: {pnl_pct:+.2f}% | Bars: {trade.bars_in_trade or '?'} | Exit: {trade.exit_reason or 'unknown'}",
                context_type="outcome",
                ticker=trade.ticker,
                trade_id=trade.id,
            ))

            # Mark linked holdings as inactive
            from app.models import PortfolioHolding
            result = await db.execute(
                select(PortfolioHolding).where(
                    PortfolioHolding.trade_id == trade.id,
                    PortfolioHolding.is_active == True,
                )
            )
            for h in result.scalars().all():
                h.is_active = False
            await db.commit()
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"Portfolio manager hook failed (non-blocking): {e}")

    return trade


async def _process_entry(trader: Trader, payload: WebhookPayload, db: AsyncSession) -> Trade:
    entry_time = (
        datetime.fromtimestamp(payload.time / 1000, tz=timezone.utc)
        if payload.time
        else datetime.now(timezone.utc)
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

    linked_count = 0
    ai_eval_portfolios = []  # Portfolios where Henry evaluates first

    for link in links:
        # Check direction filter
        if link.direction_filter and link.direction_filter != payload.dir:
            continue

        # AI-managed or AI-evaluation-enabled portfolios: Henry evaluates first
        ai_enabled = getattr(link.portfolio, "ai_evaluation_enabled", False)
        if link.portfolio.is_ai_managed or ai_enabled:
            ai_eval_portfolios.append(link.portfolio)
            continue

        # Regular portfolio: add trade directly
        pt = PortfolioTrade(portfolio_id=link.portfolio_id, trade_id=trade.id)
        db.add(pt)

        # Deduct position cost from portfolio cash
        position_cost = payload.price * payload.qty
        link.portfolio.cash -= position_cost
        linked_count += 1

    if linked_count == 0 and not ai_eval_portfolios:
        logger.warning(
            f"Trade {trade.ticker} ({payload.trader}) not linked to any portfolio. "
            f"Found {len(links)} strategy links but all filtered out."
        )

    return trade, ai_eval_portfolios


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
        datetime.fromtimestamp(payload.time / 1000, tz=timezone.utc)
        if payload.time
        else datetime.now(timezone.utc)
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
