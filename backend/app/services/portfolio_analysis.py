"""
Portfolio Analysis Service
==========================
Henry's portfolio management brain. Three analysis tiers:
1. evaluate_signal()        — on every webhook, Claude call
2. evaluate_thresholds()    — hourly, pure Python, no Claude
3. scheduled_review()       — daily, full Claude analysis
"""

import json
from app.utils.utc import utcnow
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import (
    PortfolioAction, PortfolioHolding, BacktestImport, BacktestTrade,
    Portfolio, Trade, PortfolioStrategy,
)
from app.services.price_service import price_service

logger = logging.getLogger(__name__)

# Urgency weights for priority score
URGENCY_WEIGHTS = {
    "THRESHOLD": 3.0,
    "SIGNAL": 2.0,
    "SCHEDULED_REVIEW": 1.0,
}

# Expiry durations
EXPIRY_HOURS = {
    "SIGNAL": 4,
    "THRESHOLD": 8,
    "SCHEDULED_REVIEW": 24,
}


def _compute_priority(trigger_type: str, confidence: int) -> float:
    weight = URGENCY_WEIGHTS.get(trigger_type, 1.0)
    return round(weight * confidence, 1)


def _create_action(
    portfolio_id: str,
    ticker: str,
    direction: str,
    action_type: str,
    confidence: int,
    reasoning: str,
    trigger_type: str,
    trigger_ref: str | None = None,
    suggested_qty: float | None = None,
    current_price: float | None = None,
) -> PortfolioAction:
    expiry_h = EXPIRY_HOURS.get(trigger_type, 24)
    return PortfolioAction(
        portfolio_id=portfolio_id,
        ticker=ticker,
        direction=direction,
        action_type=action_type,
        suggested_qty=suggested_qty,
        suggested_price=current_price,
        current_price=current_price,
        confidence=confidence,
        reasoning=reasoning,
        trigger_type=trigger_type,
        trigger_ref=trigger_ref,
        priority_score=_compute_priority(trigger_type, confidence),
        expires_at=utcnow() + timedelta(hours=expiry_h),
    )


# ══════════════════════════════════════════════════════════════════════
# 1. SIGNAL EVALUATION — called on every webhook
# ══════════════════════════════════════════════════════════════════════

async def evaluate_signal(trade: Trade, db: AsyncSession):
    """
    When a new trade comes in via webhook, evaluate whether to recommend
    portfolio actions based on backtest data and current holdings.
    """
    try:
        from app.services.ai_service import _call_claude_async, extract_and_save_memories

        ticker = trade.ticker
        direction = trade.direction

        # Get all portfolios linked to this trader
        result = await db.execute(
            select(PortfolioStrategy)
            .where(PortfolioStrategy.trader_id == trade.trader_id)
            .options(selectinload(PortfolioStrategy.portfolio))
        )
        links = result.scalars().all()
        if not links:
            return

        # Get current holdings across all portfolios
        result = await db.execute(
            select(PortfolioHolding)
            .where(PortfolioHolding.is_active == True)
        )
        all_holdings = result.scalars().all()

        # Get backtest stats for this strategy+ticker
        trader_result = await db.execute(
            select(Trade).where(Trade.id == trade.id).options(selectinload(Trade.trader))
        )
        trade_with_trader = trader_result.scalar_one()
        strategy_slug = trade_with_trader.trader.trader_id

        # Find matching backtest import
        result = await db.execute(
            select(BacktestImport).where(BacktestImport.ticker == ticker)
        )
        backtest_imports = result.scalars().all()

        backtest_context = ""
        for bi in backtest_imports:
            backtest_context += (
                f"  {bi.strategy_name} {bi.strategy_version or ''} on {bi.ticker}: "
                f"{bi.trade_count} trades, {bi.win_rate or 0:.1f}% win rate, "
                f"PF {bi.profit_factor or 0:.2f}, avg gain {bi.avg_gain_pct or 0:.2f}%, "
                f"avg loss {bi.avg_loss_pct or 0:.2f}%, MAE {bi.max_adverse_excursion_pct or 0:.2f}%, "
                f"avg hold {bi.avg_hold_days or 0:.1f} days\n"
            )

        if not backtest_context:
            backtest_context = "No backtest data available for this ticker."

        # Format current holdings
        holdings_text = ""
        portfolio_values = {}
        for h in all_holdings:
            cp = price_service.get_price(h.ticker) or h.entry_price
            pos_val = cp * h.qty
            pid = h.portfolio_id
            if pid not in portfolio_values:
                portfolio_values[pid] = {"total": 0, "by_ticker": {}}
            portfolio_values[pid]["total"] += pos_val
            portfolio_values[pid]["by_ticker"][h.ticker] = portfolio_values[pid]["by_ticker"].get(h.ticker, 0) + pos_val

            if h.ticker == ticker:
                pnl = ((cp - h.entry_price) / h.entry_price * 100) if h.direction == "long" else ((h.entry_price - cp) / h.entry_price * 100)
                holdings_text += (
                    f"  {h.ticker} {h.direction.upper()} {h.qty} shares @ ${h.entry_price:.2f} "
                    f"(now ${cp:.2f}, {pnl:+.2f}%) source={h.strategy_name or 'manual'}\n"
                )

        if not holdings_text:
            holdings_text = f"  No existing {ticker} holdings."

        # Concentration check
        concentration_text = ""
        for pid, pv in portfolio_values.items():
            if pv["total"] > 0:
                for t, tv in pv["by_ticker"].items():
                    pct = tv / pv["total"] * 100
                    if pct > 15:
                        concentration_text += f"  {t}: {pct:.1f}% of portfolio\n"

        current_price = price_service.get_price(ticker) or trade.entry_price

        prompt = f"""A new trade signal just came in. Evaluate it and recommend a portfolio action.

NEW SIGNAL:
  Strategy: {strategy_slug}
  Action: {trade.direction.upper()} {ticker} @ ${trade.entry_price:.2f}
  Signal strength: {trade.entry_signal_strength or 'N/A'}
  ADX: {trade.entry_adx or 'N/A'}, ATR: {trade.entry_atr or 'N/A'}
  Stop: ${trade.stop_price:.2f if trade.stop_price else 'N/A'}
  Timeframe: {trade.timeframe or 'N/A'}

EXISTING {ticker} HOLDINGS:
{holdings_text}

BACKTEST HISTORY:
{backtest_context}

CONCENTRATION ALERTS:
{concentration_text or '  No concentration issues.'}

Based on the backtest data and current holdings, should the portfolio:
1. TAKE this trade (BUY/ADD)
2. SKIP it
3. TRIM an existing position
4. CLOSE an existing position

Respond in EXACTLY this JSON format (no markdown, no backticks):
{{"action_type": "BUY" or "ADD" or "TRIM" or "CLOSE" or "SKIP", "confidence": 1-10, "reasoning": "2-3 sentences max", "suggested_qty": number_or_null}}"""

        # Check cache — skip Claude if we recently evaluated this exact signal
        from app.services.henry_cache import get_cached, set_cached, _make_hash
        cache_key = f"signal_eval:{strategy_slug}:{ticker}:{direction}"
        sig_hash = _make_hash({"price": trade.entry_price, "sig": trade.entry_signal_strength, "adx": trade.entry_adx})

        cached = await get_cached(db, cache_key, max_age_hours=1, data_hash=sig_hash)
        if cached:
            result = cached
        else:
            raw = await _call_claude_async(prompt, max_tokens=500, ticker=ticker, strategy=strategy_slug, scope="signal", function_name="signal_evaluation", enable_web_search=True)

            try:
                clean = raw.strip().replace("```json", "").replace("```", "").strip()
                result = json.loads(clean)
            except json.JSONDecodeError:
                logger.warning(f"Failed to parse signal evaluation: {raw[:200]}")
                return

            # Cache the result
            await set_cached(db, cache_key, "signal_eval", result, ticker=ticker, strategy=strategy_slug, data_hash=sig_hash)

        action_type = result.get("action_type", "SKIP")
        if action_type == "SKIP":
            return

        confidence = result.get("confidence", 5)
        reasoning = result.get("reasoning", "")
        suggested_qty = result.get("suggested_qty")

        # Create action for each linked portfolio
        for link in links:
            if link.direction_filter and link.direction_filter != direction:
                continue

            action = _create_action(
                portfolio_id=link.portfolio_id,
                ticker=ticker,
                direction=direction,
                action_type=action_type,
                confidence=confidence,
                reasoning=reasoning,
                trigger_type="SIGNAL",
                trigger_ref=trade.id,
                suggested_qty=suggested_qty,
                current_price=current_price,
            )
            db.add(action)
            await db.flush()

            # Save recommendation context (non-blocking)
            import asyncio
            from app.services.ai_service import save_context
            asyncio.create_task(save_context(
                content=f"{action_type} {ticker} ({direction}) - conf {confidence}/10: {reasoning[:200]}",
                context_type="recommendation",
                ticker=ticker,
                strategy=strategy_slug,
                confidence=confidence,
                action_id=action.id,
                expires_days=7,
            ))

    except Exception as e:
        logger.warning(f"Signal evaluation failed (non-blocking): {e}")


# ══════════════════════════════════════════════════════════════════════
# 2. THRESHOLD EVALUATION — hourly, pure Python, no Claude call
# ══════════════════════════════════════════════════════════════════════

async def evaluate_thresholds(db: AsyncSession):
    """
    Lightweight threshold checks. Runs every hour during market hours.
    No Claude call — creates actions with pre-templated reasoning.
    """
    try:
        # Get all active portfolios with their holdings
        result = await db.execute(
            select(Portfolio).where(Portfolio.is_active == True)
        )
        portfolios = result.scalars().all()

        for portfolio in portfolios:
            result = await db.execute(
                select(PortfolioHolding)
                .where(
                    PortfolioHolding.portfolio_id == portfolio.id,
                    PortfolioHolding.is_active == True,
                )
            )
            holdings = result.scalars().all()

            if not holdings:
                continue

            # Calculate portfolio value and per-ticker concentration
            total_value = 0.0
            ticker_values: dict[str, float] = {}
            holding_details: dict[str, list] = {}

            for h in holdings:
                cp = price_service.get_price(h.ticker) or h.entry_price
                pos_val = cp * h.qty
                total_value += pos_val
                ticker_values[h.ticker] = ticker_values.get(h.ticker, 0) + pos_val

                if h.ticker not in holding_details:
                    holding_details[h.ticker] = []
                holding_details[h.ticker].append(h)

            if total_value <= 0:
                continue

            # Check for existing pending threshold actions to avoid spam
            # Track by ticker AND action type so higher-priority actions aren't blocked
            result = await db.execute(
                select(PortfolioAction)
                .where(
                    PortfolioAction.portfolio_id == portfolio.id,
                    PortfolioAction.status == "pending",
                    PortfolioAction.trigger_type == "THRESHOLD",
                )
            )
            pending_actions = result.scalars().all()
            # Map ticker -> set of pending action types
            pending_by_ticker: dict[str, set[str]] = {}
            for a in pending_actions:
                if a.ticker not in pending_by_ticker:
                    pending_by_ticker[a.ticker] = set()
                pending_by_ticker[a.ticker].add(a.action_type)
            # Priority: CLOSE > SELL > TRIM > REBALANCE — higher priority always allowed
            ACTION_PRIORITY = {"CLOSE": 10, "SELL": 8, "TRIM": 6, "REBALANCE": 4, "ADD": 2, "BUY": 1}

            # ── CHECK 1: Concentration (>25% in one ticker) ──────────
            max_concentration = portfolio.max_pct_per_trade or 25.0
            for ticker, tv in ticker_values.items():
                pct = tv / total_value * 100
                if pct > max_concentration and "TRIM" not in pending_by_ticker.get(ticker, set()):
                    action = _create_action(
                        portfolio_id=portfolio.id,
                        ticker=ticker,
                        direction=holding_details[ticker][0].direction,
                        action_type="TRIM",
                        confidence=7,
                        reasoning=(
                            f"{ticker} represents {pct:.1f}% of portfolio value, exceeding the "
                            f"{max_concentration:.0f}% concentration limit. Consider trimming to reduce "
                            f"single-stock risk exposure."
                        ),
                        trigger_type="THRESHOLD",
                        current_price=price_service.get_price(ticker),
                    )
                    db.add(action)

            # ── CHECK 2: Drawdown approaching max ────────────────────
            if portfolio.max_drawdown_pct:
                equity = portfolio.cash + total_value
                drawdown = ((portfolio.initial_capital - equity) / portfolio.initial_capital * 100)
                if drawdown > 0 and drawdown >= portfolio.max_drawdown_pct * 0.8:
                    # Only create if no existing drawdown alert
                    if "__DRAWDOWN__" not in pending_threshold_tickers:
                        action = _create_action(
                            portfolio_id=portfolio.id,
                            ticker="PORTFOLIO",
                            direction="long",
                            action_type="REBALANCE",
                            confidence=8,
                            reasoning=(
                                f"Portfolio drawdown is {drawdown:.1f}%, approaching the "
                                f"{portfolio.max_drawdown_pct:.0f}% max limit. Consider reducing "
                                f"position sizes or closing underperforming positions."
                            ),
                            trigger_type="THRESHOLD",
                        )
                        db.add(action)

            # ── CHECK 3: Stop proximity (<1% from stop) ──────────────
            for ticker, hlist in holding_details.items():
                for h in hlist:
                    if not h.trade_id:
                        continue  # Manual holdings may not have stops
                    # Get stop from linked trade
                    trade_result = await db.execute(
                        select(Trade).where(Trade.id == h.trade_id)
                    )
                    linked_trade = trade_result.scalar_one_or_none()
                    if not linked_trade or not linked_trade.stop_price:
                        continue

                    cp = price_service.get_price(h.ticker)
                    if cp is None:
                        continue

                    stop = linked_trade.stop_price
                    if h.direction == "long":
                        distance_pct = (cp - stop) / cp * 100
                    else:
                        distance_pct = (stop - cp) / cp * 100

                    if 0 < distance_pct < 1.0 and "CLOSE" not in pending_by_ticker.get(ticker, set()):
                        action = _create_action(
                            portfolio_id=portfolio.id,
                            ticker=ticker,
                            direction=h.direction,
                            action_type="CLOSE",
                            confidence=6,
                            reasoning=(
                                f"{ticker} is only {distance_pct:.2f}% from its stop level "
                                f"(${stop:.2f}). Current price ${cp:.2f}. Consider closing "
                                f"before stop is hit to preserve capital."
                            ),
                            trigger_type="THRESHOLD",
                            current_price=cp,
                        )
                        db.add(action)

            # ── CHECK 4: Unrealized P&L extremes ─────────────────────
            for ticker, hlist in holding_details.items():
                for h in hlist:
                    cp = price_service.get_price(h.ticker)
                    if cp is None:
                        continue

                    if h.direction == "long":
                        pnl_pct = (cp - h.entry_price) / h.entry_price * 100
                    else:
                        pnl_pct = (h.entry_price - cp) / h.entry_price * 100

                    # Check against backtest avg gain — if 2x avg, suggest taking profits
                    bt_result = await db.execute(
                        select(BacktestImport).where(BacktestImport.ticker == ticker)
                    )
                    bt = bt_result.scalars().first()

                    if bt and bt.avg_gain_pct and pnl_pct > bt.avg_gain_pct * 2:
                        if "TRIM" not in pending_by_ticker.get(ticker, set()) and "CLOSE" not in pending_by_ticker.get(ticker, set()):
                            action = _create_action(
                                portfolio_id=portfolio.id,
                                ticker=ticker,
                                direction=h.direction,
                                action_type="TRIM",
                                confidence=6,
                                reasoning=(
                                    f"{ticker} is up {pnl_pct:.1f}%, which is {pnl_pct / bt.avg_gain_pct:.1f}x "
                                    f"the backtest average gain of {bt.avg_gain_pct:.1f}%. Consider "
                                    f"taking partial profits."
                                ),
                                trigger_type="THRESHOLD",
                                current_price=cp,
                            )
                            db.add(action)

        await db.commit()
        logger.info("Threshold evaluation complete")

    except Exception as e:
        logger.error(f"Threshold evaluation failed: {e}")


# ══════════════════════════════════════════════════════════════════════
# 3. SCHEDULED REVIEW — daily, full Claude analysis
# ══════════════════════════════════════════════════════════════════════

async def scheduled_review(db: AsyncSession):
    """
    Full portfolio review by Henry. Runs once daily.
    Analyzes all holdings against backtest benchmarks, hold times,
    strategy performance, and market context.
    """
    try:
        from app.services.ai_service import _call_claude_async, extract_and_save_memories

        # Get all active portfolios with holdings
        result = await db.execute(
            select(Portfolio).where(Portfolio.is_active == True)
        )
        portfolios = result.scalars().all()

        # Get all backtest imports
        result = await db.execute(select(BacktestImport))
        all_backtests = result.scalars().all()

        backtest_summary = ""
        for bi in all_backtests:
            backtest_summary += (
                f"  {bi.strategy_name} {bi.strategy_version or ''} / {bi.ticker}: "
                f"{bi.trade_count} trades, {bi.win_rate or 0:.1f}% WR, "
                f"PF {bi.profit_factor or 0:.2f}, avg hold {bi.avg_hold_days or 0:.1f}d, "
                f"total {bi.total_pnl_pct or 0:.1f}%\n"
            )

        if not backtest_summary:
            backtest_summary = "No backtest data imported yet."

        # Get recent action history
        cutoff_7d = utcnow() - timedelta(days=7)
        result = await db.execute(
            select(PortfolioAction)
            .where(PortfolioAction.created_at >= cutoff_7d)
            .order_by(PortfolioAction.created_at.desc())
            .limit(20)
        )
        recent_actions = result.scalars().all()

        action_history = ""
        for a in recent_actions:
            action_history += (
                f"  {a.created_at.strftime('%m/%d')} | {a.action_type} {a.ticker} {a.direction} "
                f"| {a.status} | conf={a.confidence} | {a.trigger_type}\n"
            )
        if not action_history:
            action_history = "No recent actions."

        # Market context
        spy = price_service.get_price("SPY")
        vix = price_service.get_price("VIX")
        market_ctx = f"SPY: ${spy:.2f}" if spy else "SPY: unavailable"
        if vix:
            market_ctx += f" | VIX: {vix:.1f}"

        raw = None  # Set before cache check so it's always defined

        for portfolio in portfolios:
            result = await db.execute(
                select(PortfolioHolding)
                .where(
                    PortfolioHolding.portfolio_id == portfolio.id,
                    PortfolioHolding.is_active == True,
                )
            )
            holdings = result.scalars().all()

            if not holdings:
                continue

            holdings_text = ""
            total_value = 0.0
            for h in holdings:
                cp = price_service.get_price(h.ticker) or h.entry_price
                pos_val = cp * h.qty
                total_value += pos_val

                if h.direction == "long":
                    pnl_pct = (cp - h.entry_price) / h.entry_price * 100
                else:
                    pnl_pct = (h.entry_price - cp) / h.entry_price * 100

                hold_days = (utcnow() - h.entry_date).days

                holdings_text += (
                    f"  {h.ticker} {h.direction.upper()} {h.qty} @ ${h.entry_price:.2f} "
                    f"→ ${cp:.2f} ({pnl_pct:+.2f}%) | held {hold_days}d | "
                    f"source={h.strategy_name or 'manual'}\n"
                )

            equity = portfolio.cash + total_value
            total_return = ((equity - portfolio.initial_capital) / portfolio.initial_capital * 100)

            prompt = f"""Perform a daily portfolio review. Identify actions to take.

PORTFOLIO: {portfolio.name}
  Capital: ${portfolio.initial_capital:,.2f} | Cash: ${portfolio.cash:,.2f}
  Equity: ${equity:,.2f} | Return: {total_return:+.2f}%
  Risk limits: max {portfolio.max_pct_per_trade or 25}% per trade, max {portfolio.max_drawdown_pct or 20}% drawdown

CURRENT HOLDINGS:
{holdings_text}

BACKTEST BENCHMARKS:
{backtest_summary}

RECENT ACTION HISTORY:
{action_history}

MARKET:
  {market_ctx}

Analyze:
1. Are any positions overstaying their backtest average hold time?
2. Are any strategies underperforming their backtest benchmarks?
3. Portfolio concentration and risk balance
4. Any rebalancing opportunities?

Respond with a JSON array of recommended actions (or empty array if none needed).
Each action: {{"action_type": "BUY|SELL|TRIM|ADD|CLOSE|REBALANCE", "ticker": "X", "direction": "long|short", "confidence": 1-10, "reasoning": "2-3 sentences", "suggested_qty": number_or_null}}
No markdown, no backticks. Just the JSON array."""

            # Check cache for recent review
            from app.services.henry_cache import get_cached, set_cached, _make_hash
            review_cache_key = f"scheduled_review:{portfolio.id}"
            review_hash = _make_hash({"holdings": holdings_text[:200], "equity": round(equity, 2)})

            cached_review = await get_cached(db, review_cache_key, max_age_hours=12, data_hash=review_hash)
            if cached_review:
                actions = cached_review
            else:
                raw = await _call_claude_async(prompt, max_tokens=1500, scope="review", function_name="scheduled_review", enable_web_search=True)

                try:
                    clean = raw.strip().replace("```json", "").replace("```", "").strip()
                    actions = json.loads(clean)
                except json.JSONDecodeError:
                    logger.warning(f"Failed to parse scheduled review: {raw[:200]}")
                    continue

                if not isinstance(actions, list):
                    actions = [actions]

                # Cache the review result
                await set_cached(db, review_cache_key, "scheduled_review", actions, data_hash=review_hash)

            for a in actions:
                action_type = a.get("action_type", "SKIP")
                if action_type == "SKIP":
                    continue

                action = _create_action(
                    portfolio_id=portfolio.id,
                    ticker=a.get("ticker", "UNKNOWN"),
                    direction=a.get("direction", "long"),
                    action_type=action_type,
                    confidence=a.get("confidence", 5),
                    reasoning=a.get("reasoning", ""),
                    trigger_type="SCHEDULED_REVIEW",
                    suggested_qty=a.get("suggested_qty"),
                    current_price=price_service.get_price(a.get("ticker", "")),
                )
                db.add(action)

        await db.commit()
        logger.info("Scheduled portfolio review complete")

        # Extract and save memories from the review (non-blocking)
        if raw:
            import asyncio
            asyncio.create_task(extract_and_save_memories(raw, source="scheduled_review"))

            # Extract and save context notes from the review (non-blocking)
            from app.services.ai_service import _extract_and_save_context
            asyncio.create_task(_extract_and_save_context(raw, context_type="pattern", expires_days=30))

    except Exception as e:
        logger.error(f"Scheduled review failed: {e}")


# ══════════════════════════════════════════════════════════════════════
# OUTCOME TRACKING — called when a trade closes
# ══════════════════════════════════════════════════════════════════════

async def track_action_outcome(trade: Trade, db: AsyncSession):
    """
    When a trade closes, find any approved actions that referenced it
    and record the outcome.
    """
    try:
        result = await db.execute(
            select(PortfolioAction)
            .where(
                PortfolioAction.trigger_ref == trade.id,
                PortfolioAction.status == "approved",
                PortfolioAction.outcome_correct.is_(None),
            )
        )
        actions = result.scalars().all()

        for action in actions:
            action.outcome_pnl = trade.pnl_percent
            action.outcome_correct = (trade.pnl_dollars or 0) > 0
            action.outcome_resolved_at = utcnow()

        # Best-effort: validate related HenryMemory rows based on trade outcome
        try:
            from app.models import HenryMemory
            was_profitable = (trade.pnl_dollars or 0) > 0
            mem_cutoff = utcnow() - timedelta(days=90)
            mem_result = await db.execute(
                select(HenryMemory).where(
                    HenryMemory.source.in_(("signal_eval", "scheduled_review", "outcome_tracking")),
                    HenryMemory.ticker == trade.ticker,
                    HenryMemory.validated.is_(None),
                    HenryMemory.created_at >= mem_cutoff,
                )
            )
            related_memories = mem_result.scalars().all()
            for mem in related_memories:
                mem.validated = was_profitable
        except Exception:
            pass  # Non-blocking — memory validation is best-effort

    except Exception as e:
        logger.warning(f"Outcome tracking failed (non-blocking): {e}")


# ══════════════════════════════════════════════════════════════════════
# SOURCE OF TRUTH — link webhook trades to existing holdings
# ══════════════════════════════════════════════════════════════════════

async def link_trade_to_holding(trade: Trade, db: AsyncSession):
    """
    When a webhook creates a trade, check if there's a matching manual
    holding and link them instead of creating a duplicate.
    """
    try:
        trader_result = await db.execute(
            select(Trade).where(Trade.id == trade.id).options(selectinload(Trade.trader))
        )
        trade_with_trader = trader_result.scalar_one()
        strategy_slug = trade_with_trader.trader.trader_id

        # Find matching manual holding (same ticker, direction, no trade_id yet)
        result = await db.execute(
            select(PortfolioHolding)
            .where(
                PortfolioHolding.ticker == trade.ticker,
                PortfolioHolding.direction == trade.direction,
                PortfolioHolding.is_active == True,
                PortfolioHolding.trade_id.is_(None),
            )
        )
        matching = result.scalars().first()

        if matching:
            # Link the existing holding to this trade
            matching.trade_id = trade.id
            matching.strategy_name = matching.strategy_name or strategy_slug
            logger.info(f"Linked trade {trade.id} to existing holding {matching.id} for {trade.ticker}")
        else:
            # Create new holdings for linked portfolios
            ps_result = await db.execute(
                select(PortfolioStrategy)
                .where(PortfolioStrategy.trader_id == trade.trader_id)
            )
            links = ps_result.scalars().all()

            for link in links:
                if link.direction_filter and link.direction_filter != trade.direction:
                    continue

                holding = PortfolioHolding(
                    portfolio_id=link.portfolio_id,
                    trade_id=trade.id,
                    ticker=trade.ticker,
                    direction=trade.direction,
                    entry_price=trade.entry_price,
                    qty=trade.qty,
                    entry_date=trade.entry_time,
                    strategy_name=strategy_slug,
                )
                db.add(holding)

    except Exception as e:
        logger.warning(f"Holding linking failed (non-blocking): {e}")
