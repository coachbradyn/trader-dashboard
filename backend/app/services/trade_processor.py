import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import Trader, Trade, Portfolio, PortfolioStrategy, PortfolioTrade, PortfolioSnapshot
from app.utils.utc import utcnow
from app.schemas.webhook import WebhookPayload
from app.utils.auth import verify_api_key
from app.utils.api_key_cache import (
    get_cached_trader_id,
    remember as remember_key,
    bcrypt_check,
)
from app.services.price_service import price_service

logger = logging.getLogger(__name__)


async def process_webhook(payload: WebhookPayload, db: AsyncSession) -> Trade:
    # Phase 2 Fix 1 — cached key verification. Repeat webhooks from the
    # same strategy skip bcrypt entirely after the first successful auth.
    # Unavoidable bcrypt calls run via asyncio.to_thread so they don't
    # block the event loop for other concurrent requests.
    cached_id = get_cached_trader_id(payload.key)
    trader = None
    if cached_id:
        trader = (
            await db.execute(select(Trader).where(Trader.id == cached_id).limit(1))
        ).scalar_one_or_none()
        # If the cached trader was deleted or the strategy was renamed,
        # fall through and re-authenticate.
        if trader and trader.trader_id != payload.trader:
            trader = None

    if trader is None:
        # 1. Try to find existing trader by slug
        result = await db.execute(select(Trader).where(Trader.trader_id == payload.trader))
        trader = result.scalar_one_or_none()

        if trader:
            # Check if strategy is paused
            if not trader.is_active:
                raise ValueError(f"Strategy '{payload.trader}' is paused")
            # Known trader — verify API key (non-blocking bcrypt)
            if not await bcrypt_check(payload.key, trader.api_key_hash):
                raise ValueError("Invalid API key")
            await remember_key(payload.key, trader.id)
        else:
            # Unknown trader — check allowlisted keys
            from app.models.allowlisted_key import AllowlistedKey
            result = await db.execute(
                select(AllowlistedKey).where(AllowlistedKey.claimed_by_id.is_(None))
            )
            unclaimed_keys = result.scalars().all()

            matched_key = None
            for ak in unclaimed_keys:
                if await bcrypt_check(payload.key, ak.api_key_hash):
                    matched_key = ak
                    break

            if not matched_key:
                raise ValueError(f"Unknown trader '{payload.trader}' and no matching allowlisted key")

            # Auto-create trader from allowlisted key
            trader = Trader(
                trader_id=payload.trader,
                display_name=matched_key.label or f"Strategy ({payload.trader})",
                api_key_hash=matched_key.api_key_hash,
            )
            db.add(trader)
            await db.flush()
            matched_key.claimed_by_id = trader.id
            await remember_key(payload.key, trader.id)
    else:
        # Cached trader — still enforce the paused flag because that
        # can be flipped between requests without rotating the key.
        if not trader.is_active:
            raise ValueError(f"Strategy '{payload.trader}' is paused")

    # Update last webhook timestamp
    trader.last_webhook_at = utcnow()

    # 2. Process based on signal type
    ai_eval_portfolios = []
    if payload.signal == "entry":
        trade, ai_eval_portfolios = await _process_entry(trader, payload, db)
    elif payload.signal == "exit":
        trade = await _process_exit(trader, payload, db)
    else:
        raise ValueError(f"Unknown signal type: {payload.signal}")

    await db.commit()

    # ── Everything below runs in background — return trade to caller immediately ──
    # Capture values needed by background tasks before returning
    _trade_id = trade.id
    _trade_ticker = trade.ticker
    _trade_direction = trade.direction
    _trade_entry_price = trade.entry_price
    _trade_qty = trade.qty
    _trade_exit_price = getattr(trade, "exit_price", None)
    _trade_pnl_pct = getattr(trade, "pnl_percent", None) or 0.0
    _trade_bars = getattr(trade, "bars_in_trade", None)
    _trade_exit_reason = getattr(trade, "exit_reason", None)
    _trader_id = trader.id
    _payload_signal = payload.signal
    _payload_dir = payload.dir
    _payload_qty = payload.qty
    _payload_price = payload.price
    _payload_dict = payload.model_dump()

    async def _post_commit_tasks():
        """All non-critical work after the trade is committed."""
        from app.database import async_session as _async_session

        # 1. Route to Henry for AI-evaluation-enabled portfolios
        if ai_eval_portfolios and _payload_signal == "entry":
            from app.services.ai_portfolio import evaluate_signal_for_portfolio
            for portfolio in ai_eval_portfolios:
                asyncio.create_task(evaluate_signal_for_portfolio(
                    trade, trader, _payload_dict, portfolio
                ))

        # 2. Auto-execute on Alpaca for live/paper non-AI portfolios
        try:
            async with _async_session() as alpaca_db:
                linked_result = await alpaca_db.execute(
                    select(PortfolioStrategy)
                    .where(PortfolioStrategy.trader_id == _trader_id)
                    .options(selectinload(PortfolioStrategy.portfolio))
                )
                for link in linked_result.scalars().all():
                    port = link.portfolio
                    if port.execution_mode not in ("paper", "live"):
                        continue
                    if not port.alpaca_api_key:
                        continue
                    if port.is_ai_managed or getattr(port, "ai_evaluation_enabled", False):
                        continue

                    if _payload_signal == "entry":
                        asyncio.create_task(_execute_on_alpaca(
                            port, _trade_ticker, _trade_qty, "buy", _trade_entry_price,
                        ))
                    elif _payload_signal == "exit":
                        sell_qty = _trade_qty if _trade_qty and _trade_qty > 0 else _payload_qty
                        asyncio.create_task(_execute_on_alpaca(
                            port, _trade_ticker, sell_qty, "sell", _payload_price,
                        ))
        except Exception as e:
            logger.warning(f"Alpaca auto-execute routing failed: {e}")

        # 3. Auto-add traded ticker to watchlist
        try:
            from app.models.watchlist_ticker import WatchlistTicker
            async with _async_session() as wl_db:
                wl_result = await wl_db.execute(
                    select(WatchlistTicker).where(WatchlistTicker.ticker == _trade_ticker.upper())
                )
                wl_existing = wl_result.scalar_one_or_none()
                if wl_existing:
                    if not wl_existing.is_active:
                        wl_existing.is_active = True
                        wl_existing.removed_at = None
                else:
                    wl_db.add(WatchlistTicker(ticker=_trade_ticker.upper()))
                await wl_db.commit()
        except Exception:
            pass

        # 4. Portfolio manager hooks
        try:
            async with _async_session() as pm_db:
                from app.services.portfolio_analysis import evaluate_signal, link_trade_to_holding
                # Re-fetch trade in this session
                tr_result = await pm_db.execute(select(Trade).where(Trade.id == _trade_id))
                pm_trade = tr_result.scalar_one_or_none()
                if pm_trade and _payload_signal == "entry":
                    await link_trade_to_holding(pm_trade, pm_db)
                    await evaluate_signal(pm_trade, pm_db)
                    await pm_db.commit()
                elif pm_trade and _payload_signal == "exit":
                    from app.services.portfolio_analysis import track_action_outcome
                    await track_action_outcome(pm_trade, pm_db)

                    from app.services.ai_service import save_context
                    asyncio.create_task(save_context(
                        content=f"CLOSED {_trade_ticker} {_trade_direction} | PnL: {_trade_pnl_pct:+.2f}% | Bars: {_trade_bars or '?'} | Exit: {_trade_exit_reason or 'unknown'}",
                        context_type="outcome",
                        ticker=_trade_ticker,
                        trade_id=_trade_id,
                    ))

                    from app.models import PortfolioHolding
                    result = await pm_db.execute(
                        select(PortfolioHolding).where(
                            PortfolioHolding.trade_id == _trade_id,
                            PortfolioHolding.is_active == True,
                        )
                    )
                    for h in result.scalars().all():
                        h.is_active = False
                    await pm_db.commit()
        except Exception as e:
            logger.warning(f"Portfolio manager hook failed (non-blocking): {e}")

    asyncio.create_task(_post_commit_tasks())

    return trade


async def _process_entry(trader: Trader, payload: WebhookPayload, db: AsyncSession) -> Trade:
    entry_time = (
        datetime.fromtimestamp(payload.time / 1000, tz=timezone.utc).replace(tzinfo=None)
        if payload.time
        else utcnow()
    )

    # If price is 0 (e.g. from strategy alert text), look up current price
    entry_price = payload.price
    if not entry_price or entry_price <= 0:
        try:
            current = price_service.get_price(payload.ticker)
            if current and current > 0:
                entry_price = current
        except Exception:
            pass

    # Snapshot the entry-time market regime so conditional probability
    # stats (henry_stats_engine._compute_conditional_probability) can
    # split this trade's outcome by VIX bucket / SPY trend / ADX regime.
    # Reads from the in-process market_regime cache populated by the
    # pre-market + EOD scheduled jobs — no FMP round-trip on the hot
    # webhook path. None when regime hasn't been computed yet.
    entry_vix = entry_spy_close = entry_spy_20ema = entry_spy_adx = None
    try:
        from app.services.market_regime import current_regime_classification
        regime = await current_regime_classification()
        if regime:
            entry_vix = regime.get("vix")
            entry_spy_close = regime.get("spy_close")
            entry_spy_20ema = regime.get("spy_20ema")
            entry_spy_adx = regime.get("spy_adx")
    except Exception:
        pass  # Snapshot is best-effort; missing fields handled by stats engine

    trade = Trade(
        trader_id=trader.id,
        ticker=payload.ticker,
        direction=payload.dir,
        entry_price=entry_price,
        qty=payload.qty,
        entry_signal_strength=payload.sig,
        entry_adx=payload.adx,
        entry_atr=payload.atr,
        stop_price=payload.stop,
        timeframe=payload.tf,
        entry_time=entry_time,
        status="open",
        entry_vix=entry_vix,
        entry_spy_close=entry_spy_close,
        entry_spy_20ema=entry_spy_20ema,
        entry_spy_adx=entry_spy_adx,
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

        # Deduct position cost from portfolio cash (use trade's resolved price, not payload)
        position_cost = trade.entry_price * trade.qty
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
        datetime.fromtimestamp(payload.time / 1000, tz=timezone.utc).replace(tzinfo=None)
        if payload.time
        else utcnow()
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

    # Auto-sell on Alpaca for live/paper portfolios
    for pt in portfolio_trades:
        port = pt.portfolio
        if port.execution_mode in ("paper", "live") and port.alpaca_api_key:
            asyncio.create_task(_execute_on_alpaca(
                port, trade.ticker, trade.qty, "sell", trade.exit_price,
            ))

    return trade


async def _execute_on_alpaca(
    portfolio: Portfolio,
    ticker: str,
    qty: float,
    side: str,
    price: float | None = None,
) -> None:
    """
    Submit a market order to Alpaca for a live/paper portfolio, poll for fill,
    and update the PortfolioHolding in the DB on confirmation.

    Runs as a background task — never raises into the caller.
    """
    from app.services.alpaca_service import alpaca_service
    from app.database import async_session
    from app.models.portfolio_holding import PortfolioHolding

    try:
        is_paper = portfolio.execution_mode == "paper"

        # For buys, respect max_order_amount
        order_qty = qty
        if side == "buy" and price and price > 0:
            max_amt = portfolio.max_order_amount or 1000.0
            max_qty = max_amt / price
            order_qty = min(qty, max_qty)
        order_qty = round(order_qty, 4)

        if order_qty <= 0:
            return

        result = await alpaca_service.submit_order(
            api_key=portfolio.alpaca_api_key_decrypted,
            secret_key=portfolio.alpaca_secret_key_decrypted,
            paper=is_paper,
            ticker=ticker,
            qty=order_qty,
            side=side,
        )

        if result.get("status") == "error":
            logger.error(f"Alpaca order failed: {side} {ticker} x{order_qty} on {portfolio.name} — {result.get('message')}")
            return

        order_id = result.get("order_id")
        logger.info(f"Alpaca auto-{side}: {ticker} x{order_qty} on {portfolio.name} ({portfolio.execution_mode}) — submitted {order_id}")

        if not order_id:
            return

        # Poll for fill: 6 × 0.5s inline, then 30 × 2s background = ~63s total
        fill_price = None
        fill_qty = 0.0
        for _ in range(6):
            await asyncio.sleep(0.5)
            status = await alpaca_service.get_order_status(
                api_key=portfolio.alpaca_api_key_decrypted,
                secret_key=portfolio.alpaca_secret_key_decrypted,
                paper=is_paper,
                order_id=order_id,
            )
            if status.get("status") == "filled":
                fill_price = status.get("filled_price", price)
                fill_qty = status.get("filled_qty", order_qty)
                break
            if status.get("status") in ("canceled", "expired", "rejected"):
                logger.warning(f"Alpaca order {order_id} was {status.get('status')} for {ticker} on {portfolio.name}")
                return

        if fill_qty and fill_qty > 0:
            # Update holding in DB
            async with async_session() as db:
                await _update_holding_from_fill(db, portfolio.id, ticker, fill_qty, side, fill_price)
            logger.info(f"Alpaca fill confirmed: {side} {ticker} x{fill_qty} @ ${fill_price:.2f} on {portfolio.name}")
            try:
                from app.services.henry_activity import log_activity
                await log_activity(
                    f"Alpaca {side}: {ticker} x{fill_qty:.4f} @ ${fill_price:.2f} on {portfolio.name}",
                    "trade_execute", ticker=ticker,
                )
            except Exception:
                pass
        else:
            # Extended polling in background for slow fills
            logger.info(f"Alpaca order {order_id} not yet filled — continuing background poll")
            asyncio.create_task(_poll_for_fill(
                portfolio, order_id, ticker, order_qty, side, is_paper,
            ))

    except Exception as e:
        logger.error(f"Alpaca auto-order failed for {ticker} on {portfolio.name}: {e}")


async def _poll_for_fill(
    portfolio: Portfolio,
    order_id: str,
    ticker: str,
    expected_qty: float,
    side: str,
    is_paper: bool,
) -> None:
    """Extended background poll for delayed Alpaca fills (up to 60s)."""
    from app.services.alpaca_service import alpaca_service
    from app.database import async_session

    try:
        for _ in range(30):
            await asyncio.sleep(2)
            status = await alpaca_service.get_order_status(
                api_key=portfolio.alpaca_api_key_decrypted,
                secret_key=portfolio.alpaca_secret_key_decrypted,
                paper=is_paper,
                order_id=order_id,
            )
            if status.get("status") == "filled":
                fill_price = status.get("filled_price")
                fill_qty = status.get("filled_qty", expected_qty)
                if fill_qty and fill_qty > 0:
                    async with async_session() as db:
                        await _update_holding_from_fill(db, portfolio.id, ticker, fill_qty, side, fill_price)
                    logger.info(f"Delayed fill confirmed: {side} {ticker} x{fill_qty} @ ${fill_price:.2f} on {portfolio.name}")
                    try:
                        from app.services.henry_activity import log_activity
                        await log_activity(
                            f"Delayed fill: {side} {ticker} x{fill_qty:.4f} @ ${fill_price:.2f} on {portfolio.name}",
                            "trade_execute", ticker=ticker,
                        )
                    except Exception:
                        pass
                return
            if status.get("status") in ("canceled", "expired", "rejected"):
                logger.warning(f"Alpaca order {order_id} was {status.get('status')} for {ticker}")
                return

        logger.warning(f"Alpaca order {order_id} for {ticker} did not fill within 60s — verify manually")
    except Exception as e:
        logger.error(f"Background fill poll failed for {order_id}: {e}")


async def _update_holding_from_fill(
    db: AsyncSession,
    portfolio_id: str,
    ticker: str,
    qty: float,
    side: str,
    fill_price: float | None,
) -> None:
    """Create or update a PortfolioHolding after an Alpaca fill."""
    from app.models.portfolio_holding import PortfolioHolding

    ticker = ticker.upper()

    if side.lower() == "buy":
        existing = await db.execute(
            select(PortfolioHolding).where(
                PortfolioHolding.portfolio_id == portfolio_id,
                PortfolioHolding.ticker == ticker,
                PortfolioHolding.is_active == True,
            )
        )
        holding = existing.scalar_one_or_none()

        if holding:
            total_cost = holding.entry_price * holding.qty + (fill_price or holding.entry_price) * qty
            total_qty = holding.qty + qty
            holding.entry_price = total_cost / total_qty if total_qty > 0 else 0
            holding.qty = total_qty
        else:
            db.add(PortfolioHolding(
                portfolio_id=portfolio_id,
                ticker=ticker,
                direction="long",
                entry_price=fill_price or 0,
                qty=qty,
                entry_date=utcnow(),
                is_active=True,
                notes="alpaca_auto",
            ))
    else:
        existing = await db.execute(
            select(PortfolioHolding).where(
                PortfolioHolding.portfolio_id == portfolio_id,
                PortfolioHolding.ticker == ticker,
                PortfolioHolding.is_active == True,
            )
        )
        holding = existing.scalar_one_or_none()
        if holding:
            if qty >= holding.qty:
                holding.is_active = False
            else:
                holding.qty -= qty

    await db.commit()


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
