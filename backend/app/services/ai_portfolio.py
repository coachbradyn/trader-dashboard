"""
AI Portfolio Service
=====================
Manages Henry's paper portfolio. Every incoming signal gets evaluated,
and BUY decisions auto-execute as simulated trades. SKIP decisions are
logged with reasoning. Exit signals close matching simulated trades.
"""

import asyncio
import json
import logging
from datetime import datetime, timedelta

import anthropic

from sqlalchemy import select, func, desc
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import async_session
from app.models import (
    Portfolio, Trade, Trader, PortfolioTrade, PortfolioSnapshot,
    PortfolioStrategy, PortfolioAction, BacktestImport, PortfolioHolding,
)
from app.models.henry_context import HenryContext
from app.models.henry_stats import HenryStats
from app.services.price_service import price_service

logger = logging.getLogger(__name__)

# Default confidence → allocation mapping (% of AI portfolio equity)
DEFAULT_CONFIDENCE_ALLOCATION = {
    10: 0.05, 9: 0.05, 8: 0.05,
    7: 0.03, 6: 0.03, 5: 0.03,
}
DEFAULT_MIN_CONFIDENCE = 5
DEFAULT_MIN_ADX = 20
DEFAULT_REQUIRE_STOP = True
DEFAULT_REWARD_RISK_RATIO = 2.0

# In-memory config cache — refreshed from DB on startup / settings change
_ai_config: dict | None = None


def get_ai_config() -> dict:
    """Return the current AI trading config (cached in memory)."""
    if _ai_config:
        return _ai_config
    return {
        "min_confidence": DEFAULT_MIN_CONFIDENCE,
        "high_alloc_pct": 5.0,
        "mid_alloc_pct": 3.0,
        "min_adx": DEFAULT_MIN_ADX,
        "require_stop": DEFAULT_REQUIRE_STOP,
        "reward_risk_ratio": DEFAULT_REWARD_RISK_RATIO,
    }


async def load_ai_config_from_db() -> dict:
    """Load AI trading config from henry_cache table (or return defaults)."""
    global _ai_config
    try:
        from app.models.henry_cache import HenryCache
        async with async_session() as db:
            result = await db.execute(
                select(HenryCache).where(HenryCache.cache_key == "ai_trading_config")
            )
            entry = result.scalar_one_or_none()
            if entry and entry.content:
                _ai_config = entry.content
                return _ai_config
    except Exception:
        pass
    _ai_config = get_ai_config()
    return _ai_config


async def save_ai_config(config: dict) -> None:
    """Save AI trading config to DB and update in-memory cache."""
    global _ai_config
    _ai_config = config
    try:
        from app.models.henry_cache import HenryCache
        async with async_session() as db:
            result = await db.execute(
                select(HenryCache).where(HenryCache.cache_key == "ai_trading_config")
            )
            entry = result.scalar_one_or_none()
            if entry:
                entry.content = config
                entry.generated_at = datetime.utcnow()
            else:
                db.add(HenryCache(
                    cache_key="ai_trading_config",
                    cache_type="config",
                    content=config,
                ))
            await db.commit()
    except Exception as e:
        logger.warning(f"Failed to save AI config: {e}")


async def get_ai_portfolio(db: AsyncSession) -> Portfolio | None:
    """Get the active AI-managed portfolio, if one exists."""
    result = await db.execute(
        select(Portfolio).where(
            Portfolio.is_ai_managed == True,
            Portfolio.is_active == True,
        )
    )
    return result.scalar_one_or_none()


async def create_ai_portfolio(
    name: str = "Henry AI Portfolio",
    initial_capital: float = 10000.0,
    max_pct_per_trade: float = 10.0,
    max_open_positions: int = 15,
    max_drawdown_pct: float = 20.0,
    db: AsyncSession | None = None,
) -> dict:
    """Create the AI-managed portfolio. Only one can exist at a time."""
    close_session = db is None
    if db is None:
        session = async_session()
        db = await session.__aenter__()
    try:
        # Check if one already exists
        existing = await get_ai_portfolio(db)
        if existing:
            raise ValueError("An AI-managed portfolio already exists")

        portfolio = Portfolio(
            name=name,
            description="Paper portfolio managed entirely by Henry AI. Trades are simulated — no real money at risk.",
            initial_capital=initial_capital,
            cash=initial_capital,
            is_ai_managed=True,
            max_pct_per_trade=max_pct_per_trade,
            max_open_positions=max_open_positions,
            max_drawdown_pct=max_drawdown_pct,
        )
        db.add(portfolio)
        await db.flush()

        # Assign all active strategies
        trader_result = await db.execute(
            select(Trader).where(Trader.is_active == True)
        )
        traders = trader_result.scalars().all()
        for t in traders:
            ps = PortfolioStrategy(portfolio_id=portfolio.id, trader_id=t.id)
            db.add(ps)

        # Take initial snapshot
        snapshot = PortfolioSnapshot(
            portfolio_id=portfolio.id,
            equity=initial_capital,
            cash=initial_capital,
            unrealized_pnl=0.0,
            open_positions=0,
            drawdown_pct=0.0,
            peak_equity=initial_capital,
        )
        db.add(snapshot)

        await db.commit()
        return {
            "id": portfolio.id,
            "name": portfolio.name,
            "initial_capital": initial_capital,
            "status": "created",
        }
    finally:
        if close_session:
            await db.close()


async def reset_ai_portfolio(db: AsyncSession) -> dict:
    """Reset the AI portfolio: close all trades, reset equity, clear context."""
    portfolio = await get_ai_portfolio(db)
    if not portfolio:
        raise ValueError("No AI-managed portfolio exists")

    # Delete simulated trades linked to this portfolio
    pt_result = await db.execute(
        select(PortfolioTrade).where(PortfolioTrade.portfolio_id == portfolio.id)
    )
    portfolio_trades = pt_result.scalars().all()
    trade_ids = [pt.trade_id for pt in portfolio_trades]

    # Delete portfolio_trades
    for pt in portfolio_trades:
        await db.delete(pt)

    # Delete the simulated trades
    if trade_ids:
        trade_result = await db.execute(
            select(Trade).where(Trade.id.in_(trade_ids), Trade.is_simulated == True)
        )
        for t in trade_result.scalars().all():
            await db.delete(t)

    # Delete snapshots
    snap_result = await db.execute(
        select(PortfolioSnapshot).where(PortfolioSnapshot.portfolio_id == portfolio.id)
    )
    for s in snap_result.scalars().all():
        await db.delete(s)

    # Delete portfolio actions
    action_result = await db.execute(
        select(PortfolioAction).where(PortfolioAction.portfolio_id == portfolio.id)
    )
    for a in action_result.scalars().all():
        await db.delete(a)

    # Reset cash
    portfolio.cash = portfolio.initial_capital

    # Take fresh snapshot
    db.add(PortfolioSnapshot(
        portfolio_id=portfolio.id,
        equity=portfolio.initial_capital,
        cash=portfolio.initial_capital,
        unrealized_pnl=0.0,
        open_positions=0,
        drawdown_pct=0.0,
        peak_equity=portfolio.initial_capital,
    ))

    await db.commit()
    return {"status": "reset", "equity": portfolio.initial_capital}


# ── Signal Evaluation ────────────────────────────────────────────────────

async def evaluate_signal_for_ai_portfolio(
    trade: Trade,
    trader: Trader,
    payload_dict: dict,
) -> None:
    """
    Background task: evaluate an incoming entry signal for the AI portfolio.
    If Henry says BUY, auto-execute as a simulated trade.
    If SKIP, log the decision.
    """
    try:
        async with async_session() as db:
            portfolio = await get_ai_portfolio(db)
            if not portfolio:
                return

            # Get AI portfolio equity
            equity = await _get_ai_portfolio_equity(portfolio, db)

            # Get AI portfolio holdings
            holdings_result = await db.execute(
                select(Trade)
                .join(PortfolioTrade)
                .where(
                    PortfolioTrade.portfolio_id == portfolio.id,
                    Trade.status == "open",
                    Trade.is_simulated == True,
                )
                .options(selectinload(Trade.trader))
            )
            open_positions = holdings_result.scalars().all()

            # Format holdings for prompt
            holdings_lines = []
            total_exposure = 0.0
            ticker_exposure = {}
            for pos in open_positions:
                cp = price_service.get_price(pos.ticker) or pos.entry_price
                pos_val = cp * pos.qty
                total_exposure += pos_val
                ticker_exposure[pos.ticker] = ticker_exposure.get(pos.ticker, 0) + pos_val
                if pos.direction == "long":
                    pnl = ((cp - pos.entry_price) / pos.entry_price * 100)
                else:
                    pnl = ((pos.entry_price - cp) / pos.entry_price * 100)
                holdings_lines.append(
                    f"  {pos.trader.trader_id}: {pos.direction.upper()} {pos.ticker} "
                    f"x{pos.qty:.2f} @ ${pos.entry_price:.2f} (now ${cp:.2f}, {pnl:+.2f}%)"
                )
            holdings_text = "\n".join(holdings_lines) if holdings_lines else "  No open positions."

            # Concentration
            conc_lines = []
            for t, val in ticker_exposure.items():
                pct = (val / equity * 100) if equity > 0 else 0
                conc_lines.append(f"  {t}: {pct:.1f}%")
            conc_text = "\n".join(conc_lines) if conc_lines else "  No concentration issues."

            # Backtest stats
            bt_result = await db.execute(
                select(BacktestImport).where(BacktestImport.ticker == trade.ticker)
            )
            backtests = bt_result.scalars().all()
            bt_lines = []
            for b in backtests:
                bt_lines.append(
                    f"  {b.strategy_name}: {b.trade_count} trades, "
                    f"WR {b.win_rate or 0:.1f}%, PF {b.profit_factor or 0:.2f}, "
                    f"avg gain {b.avg_gain_pct or 0:.2f}%"
                )
            bt_text = "\n".join(bt_lines) if bt_lines else "  No backtest data."

            # Henry's prior notes on this ticker
            ctx_result = await db.execute(
                select(HenryContext)
                .where(
                    HenryContext.ticker == trade.ticker,
                    (HenryContext.expires_at.is_(None)) | (HenryContext.expires_at > datetime.utcnow()),
                )
                .order_by(desc(HenryContext.created_at))
                .limit(5)
            )
            contexts = ctx_result.scalars().all()
            ctx_text = "\n".join(f"  [{c.context_type}] {c.content}" for c in contexts) or "  No prior notes."

            # Henry's hit rate
            hit_rate_text = "Unknown"
            try:
                hr_result = await db.execute(
                    select(HenryStats)
                    .where(HenryStats.stat_type == "henry_hit_rate")
                    .order_by(desc(HenryStats.computed_at))
                    .limit(1)
                )
                hr_stat = hr_result.scalar_one_or_none()
                if hr_stat and hr_stat.data:
                    hit_rate_text = f"{hr_stat.data.get('overall_pct', '?')}% overall"
            except Exception:
                pass

            # Max positions / concentration limits from portfolio settings + AI config
            cfg = get_ai_config()
            max_positions = portfolio.max_open_positions or 15
            max_pct_per_trade = portfolio.max_pct_per_trade or 10.0
            max_dd = portfolio.max_drawdown_pct or 20.0
            min_adx = cfg.get("min_adx", DEFAULT_MIN_ADX)
            require_stop = cfg.get("require_stop", DEFAULT_REQUIRE_STOP)
            rr_ratio = cfg.get("reward_risk_ratio", DEFAULT_REWARD_RISK_RATIO)

            # Current drawdown
            snap_result = await db.execute(
                select(func.max(PortfolioSnapshot.peak_equity))
                .where(PortfolioSnapshot.portfolio_id == portfolio.id)
            )
            peak = snap_result.scalar() or portfolio.initial_capital
            current_dd = ((peak - equity) / peak * 100) if peak > 0 else 0

            prompt = f"""You are Henry, managing an AI paper portfolio. Your goal is to MAXIMIZE RISK-ADJUSTED RETURNS — grow equity while protecting against drawdowns.

DECISION FRAMEWORK:
1. ONLY take trades where the expected reward clearly exceeds the risk (aim for {rr_ratio}:1+ reward/risk)
2. {"Use the stop loss from the signal — if none provided, SKIP (no stop = unmanageable risk)" if require_stop else "Stop loss preferred but not required — size down if no stop provided"}
3. SKIP if ADX < {min_adx} (no trend) unless signal strength is exceptionally high (>80)
4. SKIP if this ticker already has an open position in the same direction (no pyramiding by default)
5. SKIP if portfolio would exceed {max_positions} open positions
6. SKIP if portfolio concentration in this ticker would exceed {max_pct_per_trade}%
7. SKIP if current drawdown ({current_dd:.1f}%) is near max allowed ({max_dd:.1f}%) — preserve capital
8. Prefer strategies with proven backtest data (higher WR, higher PF = higher confidence)
9. If backtest data shows this strategy loses money on this ticker, SKIP regardless of signal
10. Factor in your own hit rate — if you've been wrong lately, size down (lower confidence)

INCOMING SIGNAL:
  Strategy: {trader.trader_id} ({trader.display_name})
  Direction: {trade.direction.upper()} {trade.ticker} @ ${trade.entry_price:.2f}
  Signal strength: {trade.entry_signal_strength or 'N/A'}
  ADX: {trade.entry_adx or 'N/A'}, ATR: ${trade.entry_atr or 0:.2f}
  Stop: ${trade.stop_price:.2f if trade.stop_price else 'NONE'}
  Timeframe: {trade.timeframe or 'N/A'}

AI PORTFOLIO STATE:
  Equity: ${equity:.2f} (initial: ${portfolio.initial_capital:.2f}, return: {((equity / portfolio.initial_capital) - 1) * 100:+.2f}%)
  Cash: ${portfolio.cash:.2f} ({portfolio.cash / equity * 100:.0f}% cash)
  Open positions: {len(open_positions)} / {max_positions} max
  Drawdown from peak: {current_dd:.1f}% (max allowed: {max_dd:.1f}%)

CURRENT HOLDINGS:
{holdings_text}

CONCENTRATION:
{conc_text}

BACKTEST DATA ({trade.ticker}):
{bt_text}

PRIOR NOTES ({trade.ticker}):
{ctx_text}

YOUR TRACK RECORD: {hit_rate_text}

Respond in EXACTLY this JSON format (no markdown, no backticks):
{{"action": "BUY" or "SKIP", "confidence": 1-10, "reasoning": "2-3 sentences explaining your decision with specific numbers"}}"""

            # Check cache — skip Claude if we recently evaluated same signal for AI portfolio
            from app.services.henry_cache import get_cached, set_cached, _make_hash
            ai_cache_key = f"ai_signal:{trader.trader_id}:{trade.ticker}:{trade.direction}"
            ai_sig_hash = _make_hash({"price": trade.entry_price, "sig": trade.entry_signal_strength, "adx": trade.entry_adx})

            cached_result = await get_cached(db, ai_cache_key, max_age_hours=1, data_hash=ai_sig_hash)
            if cached_result:
                result = cached_result
            else:
                from app.services.ai_service import _call_claude_async
                raw = await _call_claude_async(
                    prompt, max_tokens=400,
                    ticker=trade.ticker, strategy=trader.trader_id, scope="signal",
                    function_name="ai_portfolio_decision"
                )

                try:
                    clean = raw.strip().replace("```json", "").replace("```", "").strip()
                    result = json.loads(clean)
                except json.JSONDecodeError:
                    logger.warning(f"AI portfolio: failed to parse response for {trade.ticker}")
                    result = {"action": "SKIP", "confidence": 0, "reasoning": "Parse error"}

                # Cache it
                await set_cached(db, ai_cache_key, "signal_eval", result, ticker=trade.ticker, strategy=trader.trader_id, data_hash=ai_sig_hash)

            action = result.get("action", "SKIP").upper()
            confidence = result.get("confidence", 0)
            reasoning = result.get("reasoning", "")

            # Log the decision as a portfolio action
            cfg = get_ai_config()
            min_conf = cfg.get("min_confidence", DEFAULT_MIN_CONFIDENCE)

            action_record = PortfolioAction(
                portfolio_id=portfolio.id,
                ticker=trade.ticker,
                direction=trade.direction,
                action_type=action if action != "SKIP" else "SKIP",
                confidence=confidence,
                reasoning=reasoning,
                trigger_type="SIGNAL",
                trigger_ref=trade.id,
                current_price=trade.entry_price,
                priority_score=confidence * 2.0,
                status="approved" if action == "BUY" and confidence >= min_conf else "rejected",
                resolved_at=datetime.utcnow(),
                reject_reason="Low confidence or SKIP" if action == "SKIP" or confidence < min_conf else None,
            )
            db.add(action_record)

            # Auto-execute if BUY and sufficient confidence
            if action == "BUY" and confidence >= min_conf:
                high_alloc = cfg.get("high_alloc_pct", 5.0) / 100.0
                mid_alloc = cfg.get("mid_alloc_pct", 3.0) / 100.0
                alloc_pct = high_alloc if confidence >= 8 else mid_alloc

                # Size based on equity target but CAP at available cash
                target_amount = equity * alloc_pct
                max_per_trade_pct = portfolio.max_pct_per_trade or 10.0
                max_amount = equity * (max_per_trade_pct / 100.0)
                alloc_amount = min(target_amount, max_amount, portfolio.cash)

                # Need at least enough for 1 share or $10 minimum
                min_trade = min(10.0, trade.entry_price) if trade.entry_price > 0 else 10.0
                qty = alloc_amount / trade.entry_price if trade.entry_price > 0 and alloc_amount >= min_trade else 0

                if qty > 0:
                    # Create simulated trade
                    sim_trade = Trade(
                        trader_id=trade.trader_id,
                        ticker=trade.ticker,
                        direction=trade.direction,
                        entry_price=trade.entry_price,
                        qty=round(qty, 4),
                        entry_signal_strength=trade.entry_signal_strength,
                        entry_adx=trade.entry_adx,
                        entry_atr=trade.entry_atr,
                        stop_price=trade.stop_price,
                        timeframe=trade.timeframe,
                        entry_time=trade.entry_time,
                        status="open",
                        is_simulated=True,
                        raw_entry_payload={"source": "ai_portfolio", "confidence": confidence},
                    )
                    db.add(sim_trade)
                    await db.flush()

                    # Link to AI portfolio
                    pt = PortfolioTrade(portfolio_id=portfolio.id, trade_id=sim_trade.id)
                    db.add(pt)

                    # Deduct from cash
                    portfolio.cash -= alloc_amount

                    # Save context
                    from app.services.ai_service import save_context
                    asyncio.create_task(save_context(
                        content=f"AI PORTFOLIO: BOUGHT {trade.ticker} {trade.direction.upper()} x{qty:.2f} @ ${trade.entry_price:.2f} | conf {confidence}/10 | {reasoning}",
                        context_type="recommendation",
                        ticker=trade.ticker,
                        strategy=trader.trader_id,
                        confidence=confidence,
                        trade_id=sim_trade.id,
                        expires_days=30,
                    ))

                    logger.info(f"AI portfolio: BUY {trade.ticker} x{qty:.2f} @ ${trade.entry_price:.2f} (conf {confidence})")
                else:
                    action_record.status = "rejected"
                    action_record.reject_reason = "Insufficient cash or zero quantity"
                    logger.info(f"AI portfolio: BUY rejected for {trade.ticker} — insufficient cash")
            else:
                logger.info(f"AI portfolio: SKIP {trade.ticker} (conf {confidence}, action={action})")

            await db.commit()

    except Exception as e:
        logger.error(f"AI portfolio signal evaluation failed: {e}", exc_info=True)


async def process_exit_for_ai_portfolio(
    trade: Trade,
    trader: Trader,
) -> None:
    """
    Background task: when an exit signal closes a real trade, check if
    the AI portfolio has a matching open simulated trade and close it.
    """
    try:
        async with async_session() as db:
            portfolio = await get_ai_portfolio(db)
            if not portfolio:
                return

            # Find matching open simulated trade
            result = await db.execute(
                select(Trade)
                .join(PortfolioTrade)
                .where(
                    PortfolioTrade.portfolio_id == portfolio.id,
                    Trade.trader_id == trader.id,
                    Trade.ticker == trade.ticker,
                    Trade.direction == trade.direction,
                    Trade.status == "open",
                    Trade.is_simulated == True,
                )
                .order_by(desc(Trade.entry_time))
                .limit(1)
            )
            sim_trade = result.scalar_one_or_none()
            if not sim_trade:
                return

            # Close simulated trade with exit data from the real trade
            sim_trade.exit_price = trade.exit_price
            sim_trade.exit_reason = trade.exit_reason
            sim_trade.exit_time = trade.exit_time or datetime.utcnow()
            sim_trade.bars_in_trade = trade.bars_in_trade
            sim_trade.status = "closed"
            sim_trade.raw_exit_payload = {"source": "ai_portfolio_exit"}

            # Calculate P&L
            if sim_trade.direction == "long":
                sim_trade.pnl_dollars = (trade.exit_price - sim_trade.entry_price) * sim_trade.qty
            else:
                sim_trade.pnl_dollars = (sim_trade.entry_price - trade.exit_price) * sim_trade.qty

            position_value = sim_trade.entry_price * sim_trade.qty
            sim_trade.pnl_percent = (sim_trade.pnl_dollars / position_value * 100) if position_value > 0 else 0.0

            # Return cash + P&L to AI portfolio
            portfolio.cash += position_value + sim_trade.pnl_dollars

            # Take snapshot
            await _take_ai_snapshot(portfolio, db)

            # Save outcome context
            from app.services.ai_service import save_context
            asyncio.create_task(save_context(
                content=f"AI PORTFOLIO CLOSED: {sim_trade.ticker} {sim_trade.direction.upper()} | PnL: {sim_trade.pnl_percent:+.2f}% (${sim_trade.pnl_dollars:+.2f}) | Bars: {sim_trade.bars_in_trade or '?'} | Exit: {sim_trade.exit_reason or 'unknown'}",
                context_type="outcome",
                ticker=sim_trade.ticker,
                trade_id=sim_trade.id,
            ))

            await db.commit()
            logger.info(f"AI portfolio: closed {sim_trade.ticker} {sim_trade.direction} | PnL: {sim_trade.pnl_percent:+.2f}%")

    except Exception as e:
        logger.error(f"AI portfolio exit processing failed: {e}", exc_info=True)


# ── Scheduled Review ─────────────────────────────────────────────────────

async def scheduled_ai_portfolio_review() -> None:
    """Daily review of AI portfolio positions. May auto-close or adjust."""
    try:
        async with async_session() as db:
            portfolio = await get_ai_portfolio(db)
            if not portfolio:
                return

            equity = await _get_ai_portfolio_equity(portfolio, db)

            # Get open positions
            result = await db.execute(
                select(Trade)
                .join(PortfolioTrade)
                .where(
                    PortfolioTrade.portfolio_id == portfolio.id,
                    Trade.status == "open",
                    Trade.is_simulated == True,
                )
                .options(selectinload(Trade.trader))
            )
            open_positions = result.scalars().all()

            if not open_positions:
                logger.info("AI portfolio review: no open positions")
                return

            # Format positions
            pos_lines = []
            for pos in open_positions:
                cp = price_service.get_price(pos.ticker) or pos.entry_price
                if pos.direction == "long":
                    pnl = ((cp - pos.entry_price) / pos.entry_price * 100)
                else:
                    pnl = ((pos.entry_price - cp) / pos.entry_price * 100)
                hold_hours = (datetime.utcnow() - pos.entry_time).total_seconds() / 3600
                pos_lines.append(
                    f"  {pos.trader.trader_id}: {pos.direction.upper()} {pos.ticker} "
                    f"x{pos.qty:.2f} @ ${pos.entry_price:.2f} → ${cp:.2f} ({pnl:+.2f}%) "
                    f"held {hold_hours:.0f}h"
                )
            positions_text = "\n".join(pos_lines)

            # Peak equity & drawdown
            snap_result = await db.execute(
                select(func.max(PortfolioSnapshot.peak_equity))
                .where(PortfolioSnapshot.portfolio_id == portfolio.id)
            )
            peak = snap_result.scalar() or portfolio.initial_capital
            dd = ((peak - equity) / peak * 100) if peak > 0 else 0

            # SPY comparison
            spy_price = price_service.get_price("SPY")
            spy_text = f"SPY: ${spy_price:.2f}" if spy_price else "SPY: unavailable"

            from app.services.ai_service import _call_claude_async, save_context

            max_dd = portfolio.max_drawdown_pct or 20.0

            prompt = f"""You are Henry, reviewing your AI paper portfolio. Your objective: MAXIMIZE RISK-ADJUSTED RETURNS.

REVIEW RULES:
1. CLOSE any position that has lost more than the original stop distance (risk exceeded)
2. CLOSE any position held longer than 2x the strategy's average hold time from backtests
3. CLOSE if the trade thesis has broken — the setup that triggered entry no longer applies
4. TRIM positions that exceed 10% of portfolio equity (concentration risk)
5. If portfolio drawdown ({dd:.1f}%) is approaching max allowed ({max_dd:.1f}%), aggressively cut losers
6. HOLD positions that are working and have room to run
7. Consider: would you enter this trade today at the current price? If not, CLOSE.

AI PORTFOLIO:
  Equity: ${equity:.2f} (initial: ${portfolio.initial_capital:.2f}, return: {((equity/portfolio.initial_capital)-1)*100:+.2f}%)
  Cash: ${portfolio.cash:.2f} ({portfolio.cash / equity * 100:.0f}% cash)
  Peak: ${peak:.2f} | Drawdown: {dd:.1f}% (max allowed: {max_dd:.1f}%)
  {spy_text}

OPEN POSITIONS ({len(open_positions)}):
{positions_text}

For each position, respond with an action and specific reasoning.

Respond in EXACTLY this JSON format (no markdown, no backticks):
{{"positions": [{{"ticker": "NVDA", "action": "HOLD" or "CLOSE" or "TRIM", "reasoning": "2 sentences with numbers"}}], "portfolio_health": "2-3 sentence assessment of overall portfolio risk and opportunity"}}"""

            raw = await _call_claude_async(prompt, max_tokens=800, scope="general", function_name="scheduled_review")

            try:
                clean = raw.strip().replace("```json", "").replace("```", "").strip()
                review = json.loads(clean)
            except json.JSONDecodeError:
                logger.warning("AI portfolio review: failed to parse response")
                return

            # Execute recommendations
            for rec in review.get("positions", []):
                action = rec.get("action", "HOLD").upper()
                ticker = rec.get("ticker", "")
                reasoning = rec.get("reasoning", "")

                if action == "CLOSE":
                    # Find matching position
                    for pos in open_positions:
                        if pos.ticker == ticker and pos.status == "open":
                            cp = price_service.get_price(pos.ticker) or pos.entry_price
                            pos.exit_price = cp
                            pos.exit_reason = "ai_review_close"
                            pos.exit_time = datetime.utcnow()
                            pos.status = "closed"

                            if pos.direction == "long":
                                pos.pnl_dollars = (cp - pos.entry_price) * pos.qty
                            else:
                                pos.pnl_dollars = (pos.entry_price - cp) * pos.qty
                            position_value = pos.entry_price * pos.qty
                            pos.pnl_percent = (pos.pnl_dollars / position_value * 100) if position_value > 0 else 0.0

                            portfolio.cash += position_value + pos.pnl_dollars

                            # Log action
                            db.add(PortfolioAction(
                                portfolio_id=portfolio.id,
                                ticker=ticker,
                                direction=pos.direction,
                                action_type="CLOSE",
                                confidence=7,
                                reasoning=f"[Scheduled Review] {reasoning}",
                                trigger_type="SCHEDULED_REVIEW",
                                current_price=cp,
                                priority_score=7.0,
                                status="approved",
                                resolved_at=datetime.utcnow(),
                            ))

                            logger.info(f"AI portfolio review: CLOSED {ticker} | PnL: {pos.pnl_percent:+.2f}%")
                            break

            # Save portfolio health observation
            health = review.get("portfolio_health", "")
            if health:
                asyncio.create_task(save_context(
                    content=f"AI PORTFOLIO REVIEW: {health}",
                    context_type="observation",
                    expires_days=7,
                ))

            await _take_ai_snapshot(portfolio, db)
            await db.commit()

    except Exception as e:
        logger.error(f"AI portfolio scheduled review failed: {e}", exc_info=True)


# ── Helpers ──────────────────────────────────────────────────────────────

async def _get_ai_portfolio_equity(portfolio: Portfolio, db: AsyncSession) -> float:
    """Calculate current AI portfolio equity."""
    result = await db.execute(
        select(Trade)
        .join(PortfolioTrade)
        .where(
            PortfolioTrade.portfolio_id == portfolio.id,
            Trade.is_simulated == True,
        )
    )
    all_trades = result.scalars().all()

    closed_pnl = sum(t.pnl_dollars or 0.0 for t in all_trades if t.status == "closed")
    unrealized_pnl = 0.0
    for t in all_trades:
        if t.status == "open":
            cp = price_service.get_price(t.ticker) or t.entry_price
            if t.direction == "long":
                unrealized_pnl += (cp - t.entry_price) * t.qty
            else:
                unrealized_pnl += (t.entry_price - cp) * t.qty

    return portfolio.initial_capital + closed_pnl + unrealized_pnl


async def _take_ai_snapshot(portfolio: Portfolio, db: AsyncSession):
    """Take an equity snapshot for the AI portfolio."""
    equity = await _get_ai_portfolio_equity(portfolio, db)

    # Count open positions
    result = await db.execute(
        select(func.count(Trade.id))
        .join(PortfolioTrade)
        .where(
            PortfolioTrade.portfolio_id == portfolio.id,
            Trade.status == "open",
            Trade.is_simulated == True,
        )
    )
    open_count = result.scalar() or 0

    # Unrealized P&L
    unrealized = equity - portfolio.initial_capital - sum(
        t.pnl_dollars or 0
        for t in (await db.execute(
            select(Trade).join(PortfolioTrade).where(
                PortfolioTrade.portfolio_id == portfolio.id,
                Trade.status == "closed",
                Trade.is_simulated == True,
            )
        )).scalars().all()
    )

    # Peak and drawdown
    last_snap_result = await db.execute(
        select(PortfolioSnapshot)
        .where(PortfolioSnapshot.portfolio_id == portfolio.id)
        .order_by(desc(PortfolioSnapshot.snapshot_time))
        .limit(1)
    )
    last_snap = last_snap_result.scalar_one_or_none()
    peak = max(equity, last_snap.peak_equity if last_snap else portfolio.initial_capital)
    dd = ((peak - equity) / peak * 100) if peak > 0 else 0.0

    snapshot = PortfolioSnapshot(
        portfolio_id=portfolio.id,
        equity=equity,
        cash=portfolio.cash,
        unrealized_pnl=unrealized,
        open_positions=open_count,
        drawdown_pct=dd,
        peak_equity=peak,
    )
    db.add(snapshot)
