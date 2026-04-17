"""
AI Portfolio Service
=====================
Manages Henry's paper portfolio. Every incoming signal gets evaluated,
and BUY decisions auto-execute as simulated trades. SKIP decisions are
logged with reasoning. Exit signals close matching simulated trades.
"""

import asyncio
from app.utils.utc import utcnow
import json
import logging
from datetime import datetime, timedelta, timezone

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
                entry.generated_at = utcnow()
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
                if pos.entry_price and pos.entry_price > 0:
                    if pos.direction == "long":
                        pnl = ((cp - pos.entry_price) / pos.entry_price * 100)
                    else:
                        pnl = ((pos.entry_price - cp) / pos.entry_price * 100)
                else:
                    pnl = 0.0
                holdings_lines.append(
                    f"  {pos.trader.trader_id}: {pos.direction.upper()} {pos.ticker} "
                    f"x{pos.qty:.2f} @ ${pos.entry_price:.2f} (now ${cp:.2f}, {pnl:+.2f}%)"
                )

            # Include manual PortfolioHolding rows. Without them Henry
            # evaluated new signals against only the Trade-tracked
            # positions, so e.g. a manual ASTS holding didn't count
            # toward concentration or duplicate-ticker checks.
            manual_result = await db.execute(
                select(PortfolioHolding).where(
                    PortfolioHolding.portfolio_id == portfolio.id,
                    PortfolioHolding.is_active == True,
                )
            )
            for h in manual_result.scalars().all():
                cp = price_service.get_price(h.ticker) or h.entry_price
                pos_val = cp * h.qty
                total_exposure += pos_val
                ticker_exposure[h.ticker] = ticker_exposure.get(h.ticker, 0) + pos_val
                if h.entry_price and h.entry_price > 0:
                    if h.direction == "long":
                        pnl = ((cp - h.entry_price) / h.entry_price * 100)
                    else:
                        pnl = ((h.entry_price - cp) / h.entry_price * 100)
                else:
                    pnl = 0.0
                holdings_lines.append(
                    f"  manual: {h.direction.upper()} {h.ticker} "
                    f"x{h.qty:.4f} @ ${h.entry_price:.2f} (now ${cp:.2f}, {pnl:+.2f}%)"
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
                    (HenryContext.expires_at.is_(None)) | (HenryContext.expires_at > utcnow()),
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

            # Current drawdown — use 30-day peak to avoid stale/corrupted historical peaks
            dd_cutoff = utcnow() - timedelta(days=30)
            snap_result = await db.execute(
                select(func.max(PortfolioSnapshot.peak_equity))
                .where(
                    PortfolioSnapshot.portfolio_id == portfolio.id,
                    PortfolioSnapshot.timestamp >= dd_cutoff,
                )
            )
            peak = snap_result.scalar() or equity or portfolio.initial_capital
            # If peak is less than current equity, use current equity (no drawdown)
            if peak < equity:
                peak = equity
            current_dd = ((peak - equity) / peak * 100) if peak > 0 else 0

            # Pre-compute hard SKIPs — no AI needed
            has_stop = trade.stop_price is not None and trade.stop_price > 0
            adx_val = trade.entry_adx or 0
            sig_val = trade.entry_signal_strength or 0
            at_max_positions = len(open_positions) >= max_positions
            near_max_dd = current_dd >= (max_dd * 0.85)

            # Hard SKIP conditions — save the Claude call entirely
            if require_stop and not has_stop:
                result = {"action": "SKIP", "confidence": 0, "reasoning": f"No stop loss provided (required by config)"}
                logger.info(f"AI portfolio SKIP {trade.ticker}: no stop (hard rule)")
            elif at_max_positions:
                result = {"action": "SKIP", "confidence": 0, "reasoning": f"Portfolio full ({len(open_positions)}/{max_positions} positions)"}
                logger.info(f"AI portfolio SKIP {trade.ticker}: max positions reached")
            elif near_max_dd:
                result = {"action": "SKIP", "confidence": 0, "reasoning": f"Drawdown {current_dd:.1f}% near max {max_dd:.1f}% — preserving capital"}
                logger.info(f"AI portfolio SKIP {trade.ticker}: near max drawdown")
            elif adx_val > 0 and adx_val < min_adx and sig_val < 80:
                result = {"action": "SKIP", "confidence": 0, "reasoning": f"ADX {adx_val:.0f} < {min_adx} minimum (weak trend)"}
                logger.info(f"AI portfolio SKIP {trade.ticker}: ADX too low")
            else:
                # Need AI judgment — build compact prompt
                from app.services.decision_signals import SIGNAL_WEIGHTS_PROMPT_FRAGMENT
                prompt = f"""Henry's autonomous portfolio. BUY or SKIP? JSON only.

SIGNAL: {trader.trader_id} | {trade.direction.upper()} {trade.ticker} @ ${trade.entry_price:.2f} | sig={sig_val:.0f} ADX={adx_val:.0f} | stop=${trade.stop_price:.2f if has_stop else 'NONE'} | tf={trade.timeframe or '?'}
PORTFOLIO: ${equity:.0f} equity | ${portfolio.cash:.0f} cash ({portfolio.cash / equity * 100:.0f}%) | {len(open_positions)}/{max_positions} positions | DD={current_dd:.1f}%/{max_dd:.0f}%
HOLDINGS: {holdings_text[:300]}
BACKTEST: {bt_text[:200]}
TRACK RECORD: {hit_rate_text[:100]}
{f'NOTES: {ctx_text[:150]}' if ctx_text.strip() else ''}

Rules: R/R>{rr_ratio}:1, no pyramiding, concentration<{max_pct_per_trade}%.
{{"action": "BUY" or "SKIP", "confidence": 1-10, "reasoning": "1-2 sentences", "signal_weights": {{"technical_strength": 0.0-1.0, "fundamental_value": 0.0-1.0, "thesis_quality": 0.0-1.0, "catalyst_proximity": 0.0-1.0, "risk_reward_ratio": 0.0-1.0, "memory_alignment": 0.0-1.0, "regime_fit": 0.0-1.0, "entry_timing": 0.0-1.0}}}}
{SIGNAL_WEIGHTS_PROMPT_FRAGMENT}"""

                # Check cache — skip Claude if we recently evaluated same signal
                from app.services.henry_cache import get_cached, set_cached, _make_hash
                ai_cache_key = f"ai_signal:{trader.trader_id}:{trade.ticker}:{trade.direction}"
                ai_sig_hash = _make_hash({"price": trade.entry_price, "sig": trade.entry_signal_strength, "adx": trade.entry_adx, "cash": int(portfolio.cash)})

                cached_result = await get_cached(db, ai_cache_key, max_age_hours=1, data_hash=ai_sig_hash)
                if cached_result:
                    result = cached_result
                else:
                    from app.services.ai_service import _call_claude_async
                    raw = await _call_claude_async(
                        prompt, max_tokens=300,
                        ticker=trade.ticker, strategy=trader.trader_id, scope="signal",
                        function_name="ai_portfolio_decision"
                    )

                    try:
                        from app.utils.json_extract import extract_json_object
                        result = extract_json_object(raw) or {"action": "SKIP", "confidence": 0, "reasoning": "No JSON"}
                    except Exception:
                        logger.warning(f"AI portfolio: failed to parse response for {trade.ticker}")
                        result = {"action": "SKIP", "confidence": 0, "reasoning": "Parse error"}

                    # Cache it
                    await set_cached(db, ai_cache_key, "signal_eval", result, ticker=trade.ticker, strategy=trader.trader_id, data_hash=ai_sig_hash)

            action = result.get("action", "SKIP").upper()
            confidence = result.get("confidence", 0)
            reasoning = result.get("reasoning", "")

            from app.services.decision_signals import validate_signal_weights
            sig_weights = validate_signal_weights(result.get("signal_weights"))

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
                resolved_at=utcnow(),
                reject_reason="Low confidence or SKIP" if action == "SKIP" or confidence < min_conf else None,
                signal_weights=sig_weights,
            )
            db.add(action_record)
            await db.flush()

            # Persist a memory when Henry skips (or is blocked by a hard
            # rule). Without this, the reasoning lived only on the
            # PortfolioAction row and never fed the retrieval pipeline —
            # so Henry couldn't learn from "why I passed on NOK last
            # time" when a similar setup arrived. Keep the content
            # quantitative (ticker, strategy, ADX/sig, regime, reason)
            # so memory extraction + cosine retrieval can match it
            # against future signals.
            is_skip = (action == "SKIP") or (confidence < min_conf)
            if is_skip:
                try:
                    from app.services.ai_service import save_memory
                    skip_content = (
                        f"SKIP {trader.trader_id} {trade.direction.upper()} "
                        f"{trade.ticker} @ ${trade.entry_price:.2f} "
                        f"(sig={sig_val:.0f}, ADX={adx_val:.0f}, "
                        f"conf={confidence}, DD={current_dd:.1f}%). "
                        f"Reason: {reasoning[:300] if reasoning else 'low conviction'}."
                    )
                    # Skips are generally lower importance than trade
                    # entries, but clamp to a 4 floor so they're not
                    # immediately aged out — SKIP reasons are exactly
                    # what the calibration system needs.
                    skip_importance = max(4, min(7, int(confidence or 4)))
                    asyncio.create_task(save_memory(
                        content=skip_content,
                        memory_type="decision",
                        strategy_id=trader.trader_id,
                        ticker=trade.ticker,
                        importance=skip_importance,
                        source="signal_skip",
                    ))
                except Exception:
                    pass
            # Phase 4 — populate position sizing fields (Kelly + cond prob).
            # Skipped for non-add action types inside the helper.
            try:
                from app.services.position_sizing import apply_sizing_to_action
                await apply_sizing_to_action(
                    db, action_record, strategy_id=trader.trader_id
                )
            except Exception:
                pass

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

                    # Track ticker price for P&L display
                    price_service.add_ticker(trade.ticker)

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
                    # Log to activity feed
                    try:
                        from app.services.henry_activity import log_activity
                        asyncio.create_task(log_activity(
                            f"SIGNAL BUY: {trade.ticker} {trade.direction.upper()} x{qty:.2f} @ ${trade.entry_price:.2f} (conf {confidence}/10) from {trader.trader_id}",
                            "trade_execute", ticker=trade.ticker,
                        ))
                    except Exception:
                        pass
                else:
                    # Attempt reallocation for high-conviction trades
                    if confidence >= 8:
                        from app.services.autonomous_trading import _liquidate_for_capital
                        freed = await _liquidate_for_capital(
                            portfolio, target_amount, confidence, trade.ticker, db
                        )
                        if freed > 0:
                            alloc_amount = min(target_amount, max_amount, portfolio.cash)
                            qty = alloc_amount / trade.entry_price if trade.entry_price > 0 and alloc_amount >= min_trade else 0

                    if qty > 0:
                        # Retry execution after successful reallocation
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
                        pt = PortfolioTrade(portfolio_id=portfolio.id, trade_id=sim_trade.id)
                        db.add(pt)
                        portfolio.cash -= alloc_amount
                        action_record.status = "approved"
                        action_record.reject_reason = None
                        action_record.trigger_ref = sim_trade.id

                        from app.services.ai_service import save_context
                        asyncio.create_task(save_context(
                            content=f"AI PORTFOLIO (realloc): BOUGHT {trade.ticker} {trade.direction.upper()} x{qty:.2f} @ ${trade.entry_price:.2f} | conf {confidence}/10 | {reasoning}",
                            context_type="recommendation",
                            ticker=trade.ticker,
                            strategy=trader.trader_id,
                            confidence=confidence,
                            trade_id=sim_trade.id,
                            expires_days=30,
                        ))
                        logger.info(f"AI portfolio: BUY {trade.ticker} x{qty:.2f} @ ${trade.entry_price:.2f} (conf {confidence}, after reallocation)")
                        try:
                            from app.services.henry_activity import log_activity
                            asyncio.create_task(log_activity(
                                f"SIGNAL BUY (realloc): {trade.ticker} {trade.direction.upper()} x{qty:.2f} @ ${trade.entry_price:.2f} (conf {confidence}/10) from {trader.trader_id}",
                                "trade_execute", ticker=trade.ticker,
                            ))
                        except Exception:
                            pass
                    else:
                        action_record.status = "rejected"
                        action_record.reject_reason = f"Insufficient cash (${portfolio.cash:.2f}), reallocation failed"
                        logger.info(f"AI portfolio: BUY rejected for {trade.ticker} — insufficient cash (${portfolio.cash:.2f})")
                        try:
                            from app.services.henry_activity import log_activity
                            asyncio.create_task(log_activity(
                                f"SIGNAL REJECTED: {trade.ticker} — BUY approved (conf {confidence}) but insufficient cash (${portfolio.cash:.2f})",
                                "trade_skip", ticker=trade.ticker,
                            ))
                        except Exception:
                            pass
            else:
                logger.info(f"AI portfolio: SKIP {trade.ticker} (conf {confidence}, action={action})")
                try:
                    from app.services.henry_activity import log_activity
                    asyncio.create_task(log_activity(
                        f"SIGNAL SKIP: {trade.ticker} from {trader.trader_id} — {reasoning[:150]}",
                        "trade_skip", ticker=trade.ticker,
                    ))
                except Exception:
                    pass

            await db.commit()

        # After the AI-managed portfolio commits its decision, mirror an
        # approved BUY out to every OTHER AI-enabled portfolio (paper/
        # live Alpaca books, etc.). Each mirror is sized independently
        # against its own cash + risk limits, routes through Alpaca when
        # the book is in paper/live mode, and is skipped when the book
        # already holds the ticker. Runs outside the primary session
        # because _execute_autonomous_trade opens its own. Failures on
        # one book don't affect the others or the primary AI portfolio.
        if action == "BUY" and confidence >= min_conf:
            try:
                await _mirror_buy_to_live_portfolios(
                    origin_portfolio_id=portfolio.id,
                    trade=trade,
                    trader=trader,
                    confidence=confidence,
                    reasoning=reasoning,
                )
            except Exception as e:
                logger.error(f"Mirror fan-out failed for {trade.ticker}: {e}")

    except Exception as e:
        logger.error(f"AI portfolio signal evaluation failed: {e}", exc_info=True)


async def _mirror_buy_to_live_portfolios(
    origin_portfolio_id: str,
    trade: Trade,
    trader: Trader,
    confidence: int,
    reasoning: str,
) -> None:
    """Replicate an approved signal-driven BUY from Henry's AI paper
    portfolio to every other active AI-enabled portfolio.

    Each mirror book sizes independently from its own cash balance and
    max_pct_per_trade setting, skips when the ticker is already open on
    that book (no pyramiding off a webhook), and for paper/live books
    routes the order through `_execute_autonomous_trade` which in turn
    submits to Alpaca via _execute_on_alpaca. Failures are swallowed
    per-portfolio so one broker rejection doesn't cascade.

    Spec: "Any trades on the Henry AI portfolio should be considered
    for the lives, with available cash balance or stock amount being
    the qualifiers."
    """
    from sqlalchemy import or_
    from app.services.autonomous_trading import _execute_autonomous_trade

    async with async_session() as db:
        result = await db.execute(
            select(Portfolio).where(
                Portfolio.is_active == True,
                Portfolio.id != origin_portfolio_id,
                or_(
                    Portfolio.is_ai_managed == True,
                    Portfolio.ai_evaluation_enabled == True,
                ),
            )
        )
        mirrors = list(result.scalars().all())

    if not mirrors:
        return

    cfg = get_ai_config()
    logger.info(
        f"Mirror fan-out: replicating {trade.ticker} BUY (conf {confidence}) "
        f"to {len(mirrors)} AI-enabled portfolio(s)"
    )

    for mp in mirrors:
        try:
            # Dedup: skip books that already have an open Trade or an
            # active PortfolioHolding on this ticker+direction. The
            # "available stock amount" qualifier from the spec — we
            # don't stack on top of an existing position via a webhook.
            async with async_session() as db:
                existing_trade = await db.execute(
                    select(Trade.id)
                    .join(PortfolioTrade, PortfolioTrade.trade_id == Trade.id)
                    .where(
                        PortfolioTrade.portfolio_id == mp.id,
                        Trade.ticker == trade.ticker,
                        Trade.direction == trade.direction,
                        Trade.status == "open",
                    )
                    .limit(1)
                )
                if existing_trade.scalar_one_or_none():
                    logger.debug(f"Mirror: {mp.name} already holds {trade.ticker} (trade) — skipping")
                    continue
                existing_hld = await db.execute(
                    select(PortfolioHolding.id)
                    .where(
                        PortfolioHolding.portfolio_id == mp.id,
                        PortfolioHolding.ticker == trade.ticker,
                        PortfolioHolding.direction == trade.direction,
                        PortfolioHolding.is_active == True,
                    )
                    .limit(1)
                )
                if existing_hld.scalar_one_or_none():
                    logger.debug(f"Mirror: {mp.name} already holds {trade.ticker} (holding) — skipping")
                    continue

                # Equity for sizing — use cash for non-AI-managed books,
                # full equity (cash + realised + unrealised) for AI-managed.
                if getattr(mp, "is_ai_managed", False):
                    mp_equity = await _get_ai_portfolio_equity(mp, db)
                else:
                    mp_equity = float(mp.cash or 0)

            success = await _execute_autonomous_trade(
                portfolio=mp,
                ticker=trade.ticker,
                direction=trade.direction,
                price=trade.entry_price,
                confidence=confidence,
                reasoning=f"[Mirror from signal] {reasoning}",
                equity=mp_equity,
                cfg=cfg,
                source=f"signal_mirror:{trader.trader_id}",
                stop_price=trade.stop_price,
            )
            if success:
                logger.info(
                    f"Mirror: executed {trade.ticker} on {mp.name} "
                    f"(mode={mp.execution_mode})"
                )
                try:
                    from app.services.henry_activity import log_activity
                    await log_activity(
                        f"MIRROR BUY: {trade.ticker} on {mp.name} "
                        f"(mode={mp.execution_mode}, conf {confidence}/10)",
                        "trade_execute", ticker=trade.ticker,
                    )
                except Exception:
                    pass
            else:
                logger.info(
                    f"Mirror: {trade.ticker} not executed on {mp.name} "
                    f"(rejected by sizing / cash / safety check)"
                )
        except Exception as e:
            logger.error(f"Mirror to {mp.name} failed: {e}")


async def evaluate_signal_for_portfolio(
    trade: Trade,
    trader: Trader,
    payload_dict: dict,
    portfolio: Portfolio,
) -> None:
    """
    Background task: evaluate an entry signal for a specific portfolio with AI enabled.
    Henry decides BUY or SKIP. If BUY, creates a trade sized to available cash.
    Works for ANY portfolio with ai_evaluation_enabled=True.
    """
    try:
        async with async_session() as db:
            # Re-fetch portfolio for fresh state
            result = await db.execute(select(Portfolio).where(Portfolio.id == portfolio.id))
            port = result.scalar_one_or_none()
            if not port:
                return

            cfg = get_ai_config()
            min_conf = cfg.get("min_confidence", DEFAULT_MIN_CONFIDENCE)

            # Quick prompt — shorter than the full AI portfolio evaluation
            from app.services.ai_service import _call_claude_async
            prompt = f"""You are Henry, evaluating a trade signal for the "{port.name}" portfolio.

SIGNAL: {trader.trader_id} → {trade.direction.upper()} {trade.ticker} @ ${trade.entry_price:.2f}
Signal strength: {trade.entry_signal_strength or 'N/A'} | ADX: {trade.entry_adx or 'N/A'} | Stop: ${trade.stop_price:.2f if trade.stop_price else 'NONE'}

PORTFOLIO: ${port.cash:.2f} cash | Execution: {port.execution_mode}

Should this trade be taken? Consider the signal quality and portfolio cash.
Respond in JSON: {{"action": "BUY" or "SKIP", "confidence": 1-10, "reasoning": "1-2 sentences"}}"""

            raw = await _call_claude_async(
                prompt, max_tokens=300,
                ticker=trade.ticker, strategy=trader.trader_id,
                scope="signal", function_name="ai_portfolio_decision",
            )

            try:
                import json
                clean = raw.strip().replace("```json", "").replace("```", "").strip()
                result_data = json.loads(clean)
            except Exception:
                result_data = {"action": "SKIP", "confidence": 0, "reasoning": "Parse error"}

            action = result_data.get("action", "SKIP").upper()
            confidence = result_data.get("confidence", 0)
            reasoning = result_data.get("reasoning", "")

            # Log the decision
            action_record = PortfolioAction(
                portfolio_id=port.id,
                ticker=trade.ticker,
                direction=trade.direction,
                action_type=action if action != "SKIP" else "SKIP",
                confidence=confidence,
                reasoning=f"[Henry AI] {reasoning}",
                trigger_type="SIGNAL",
                trigger_ref=trade.id,
                current_price=trade.entry_price,
                priority_score=confidence * 2.0,
                status="approved" if action == "BUY" and confidence >= min_conf else "rejected",
                resolved_at=utcnow(),
                reject_reason=None if action == "BUY" and confidence >= min_conf else "SKIP or low confidence",
            )
            db.add(action_record)
            await db.flush()
            try:
                from app.services.position_sizing import apply_sizing_to_action
                await apply_sizing_to_action(
                    db, action_record, strategy_id=getattr(trade.trader, "trader_id", None) if hasattr(trade, "trader") else None
                )
            except Exception:
                pass

            if action == "BUY" and confidence >= min_conf:
                # Size to available cash
                alloc_pct = 0.05 if confidence >= 8 else 0.03
                alloc_amount = min(port.cash * alloc_pct * 10, port.cash * 0.50, port.cash)
                # At least enough for minimum trade
                if alloc_amount >= min(10, trade.entry_price) and trade.entry_price > 0:
                    qty = round(alloc_amount / trade.entry_price, 4)

                    # Link the original trade to this portfolio
                    pt = PortfolioTrade(portfolio_id=port.id, trade_id=trade.id)
                    db.add(pt)

                    # Portfolio-specific sizing is tracked via port.cash deduction
                    # and PortfolioTrade link — do NOT mutate shared trade.qty

                    port.cash -= qty * trade.entry_price
                    logger.info(f"AI eval ({port.name}): BUY {trade.ticker} x{qty:.4f} @ ${trade.entry_price:.2f} (conf {confidence})")

                    # Execute on Alpaca if portfolio is wired to paper/live
                    if port.execution_mode in ("paper", "live") and port.alpaca_api_key:
                        from app.services.trade_processor import _execute_on_alpaca
                        asyncio.create_task(_execute_on_alpaca(
                            port, trade.ticker, qty, "buy", trade.entry_price,
                            trade_id=trade.id,
                        ))

                    try:
                        from app.services.henry_activity import log_activity
                        asyncio.create_task(log_activity(
                            f"[{port.name}] BUY {trade.ticker} x{qty:.4f} @ ${trade.entry_price:.2f} (conf {confidence})",
                            "trade_execute", ticker=trade.ticker,
                        ))
                    except Exception:
                        pass
                else:
                    realloc_success = False
                    # Attempt reallocation for high-conviction trades
                    if confidence >= 8:
                        from app.services.autonomous_trading import _liquidate_for_capital
                        freed = await _liquidate_for_capital(
                            port, alloc_amount, confidence, trade.ticker, db
                        )
                        if freed > 0:
                            alloc_amount = min(port.cash * alloc_pct * 10, port.cash * 0.50, port.cash)
                            if alloc_amount >= min(10, trade.entry_price) and trade.entry_price > 0:
                                qty = round(alloc_amount / trade.entry_price, 4)
                                pt = PortfolioTrade(portfolio_id=port.id, trade_id=trade.id)
                                db.add(pt)
                                port.cash -= qty * trade.entry_price
                                action_record.status = "approved"
                                action_record.reject_reason = None
                                realloc_success = True
                                logger.info(f"AI eval ({port.name}): BUY {trade.ticker} x{qty:.4f} @ ${trade.entry_price:.2f} (conf {confidence}, after reallocation)")

                                # Execute on Alpaca if portfolio is wired to paper/live
                                if port.execution_mode in ("paper", "live") and port.alpaca_api_key:
                                    from app.services.trade_processor import _execute_on_alpaca
                                    asyncio.create_task(_execute_on_alpaca(
                                        port, trade.ticker, qty, "buy", trade.entry_price,
                                        trade_id=trade.id,
                                    ))

                                try:
                                    from app.services.henry_activity import log_activity
                                    asyncio.create_task(log_activity(
                                        f"[{port.name}] BUY (realloc) {trade.ticker} x{qty:.4f} @ ${trade.entry_price:.2f} (conf {confidence})",
                                        "trade_execute", ticker=trade.ticker,
                                    ))
                                except Exception:
                                    pass

                    if not realloc_success:
                        action_record.status = "rejected"
                        action_record.reject_reason = f"Insufficient cash (${port.cash:.2f}), reallocation failed"
                        logger.info(f"AI eval ({port.name}): BUY rejected {trade.ticker} — cash ${port.cash:.2f}")
            else:
                logger.info(f"AI eval ({port.name}): SKIP {trade.ticker} (conf {confidence})")

            await db.commit()

    except Exception as e:
        logger.error(f"AI evaluation for {portfolio.name} failed: {e}", exc_info=True)


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
            sim_trade.exit_time = trade.exit_time or utcnow()
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
    """Daily review of every AI-managed portfolio.

    Fetches all portfolios where is_ai_managed or ai_evaluation_enabled is
    True (via the same helper the autonomous trader uses) and runs the
    per-portfolio review in isolation — one portfolio's failure doesn't
    block the rest.

    Historically this only ran against the canonical paper portfolio
    returned by get_ai_portfolio(); live/real AI-managed portfolios
    received zero autonomous risk management. Now every qualifying
    portfolio gets the 2:30 PM review.
    """
    try:
        from app.services.autonomous_trading import _get_ai_enabled_portfolios
        portfolios = await _get_ai_enabled_portfolios()
    except Exception as e:
        logger.error(f"AI portfolio review: failed to load portfolios: {e}")
        portfolios = []

    logger.info(
        f"AI portfolio review: running for {len(portfolios)} portfolio"
        f"{'s' if len(portfolios) != 1 else ''}"
    )
    for portfolio in portfolios:
        try:
            await _review_single_portfolio(portfolio)
        except Exception as e:
            logger.error(
                f"AI portfolio review failed for {portfolio.name} "
                f"({portfolio.id}): {e}",
                exc_info=True,
            )


async def _review_single_portfolio(portfolio: Portfolio) -> None:
    """Run the review loop for one portfolio. Separated so
    scheduled_ai_portfolio_review can call it once per AI-enabled
    portfolio and keep failures isolated."""
    try:
        async with async_session() as db:
            # Re-fetch inside this session so relationship loads use the
            # right session context. The helper hands us a detached object.
            from sqlalchemy import select as _select
            fresh = await db.execute(
                _select(Portfolio).where(Portfolio.id == portfolio.id)
            )
            portfolio = fresh.scalar_one_or_none()
            if portfolio is None:
                return

            equity = await _get_ai_portfolio_equity(portfolio, db)

            # Get open positions (webhook/strategy Trades)
            result = await db.execute(
                select(Trade)
                .join(PortfolioTrade)
                .where(
                    PortfolioTrade.portfolio_id == portfolio.id,
                    Trade.status == "open",
                )
                .options(selectinload(Trade.trader))
            )
            open_positions = result.scalars().all()

            # Get manual holdings. These used to be invisible to this
            # review, which meant Henry couldn't recommend closing e.g.
            # an ASTS position that was manually added or Alpaca-synced
            # into an AI-managed portfolio. Treat them as first-class
            # positions alongside Trade rows.
            manual_result = await db.execute(
                select(PortfolioHolding).where(
                    PortfolioHolding.portfolio_id == portfolio.id,
                    PortfolioHolding.is_active == True,
                )
            )
            manual_holdings = list(manual_result.scalars().all())

            if not open_positions and not manual_holdings:
                logger.info(
                    f"AI portfolio review ({portfolio.name}): no open positions"
                )
                return

            # Format positions (Trades + manual holdings together).
            pos_lines = []
            for pos in open_positions:
                cp = price_service.get_price(pos.ticker) or pos.entry_price
                if pos.entry_price and pos.entry_price > 0:
                    if pos.direction == "long":
                        pnl = ((cp - pos.entry_price) / pos.entry_price * 100)
                    else:
                        pnl = ((pos.entry_price - cp) / pos.entry_price * 100)
                else:
                    pnl = 0.0
                hold_hours = (utcnow() - pos.entry_time).total_seconds() / 3600
                pos_lines.append(
                    f"  {pos.trader.trader_id}: {pos.direction.upper()} {pos.ticker} "
                    f"x{pos.qty:.2f} @ ${pos.entry_price:.2f} → ${cp:.2f} ({pnl:+.2f}%) "
                    f"held {hold_hours:.0f}h"
                )
            for h in manual_holdings:
                cp = price_service.get_price(h.ticker) or h.entry_price
                if h.entry_price and h.entry_price > 0:
                    if h.direction == "long":
                        pnl = ((cp - h.entry_price) / h.entry_price * 100)
                    else:
                        pnl = ((h.entry_price - cp) / h.entry_price * 100)
                else:
                    pnl = 0.0
                hold_days = (utcnow().date() - h.entry_date.date()).days if h.entry_date else 0
                pos_lines.append(
                    f"  manual: {h.direction.upper()} {h.ticker} "
                    f"x{h.qty:.4f} @ ${h.entry_price:.2f} → ${cp:.2f} ({pnl:+.2f}%) "
                    f"held {hold_days}d"
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

            prompt = f"""You are Henry, reviewing the AI-managed portfolio "{portfolio.name}" (mode: {portfolio.execution_mode or 'local'}). You trade AUTONOMOUSLY — you do not need user approval. Your CLOSE and TRIM decisions execute immediately. Your objective: MAXIMIZE RISK-ADJUSTED RETURNS.

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
            exec_mode = (portfolio.execution_mode or "local").lower()
            for rec in review.get("positions", []):
                action = rec.get("action", "HOLD").upper()
                ticker = rec.get("ticker", "")
                reasoning = rec.get("reasoning", "")

                if action != "CLOSE":
                    continue

                # 1. Try to close a matching Trade first.
                trade_closed = False
                for pos in open_positions:
                    if pos.ticker != ticker or pos.status != "open":
                        continue
                    cp = price_service.get_price(pos.ticker) or pos.entry_price
                    pos.exit_price = cp
                    pos.exit_reason = "ai_review_close"
                    pos.exit_time = utcnow()
                    pos.status = "closed"

                    if pos.direction == "long":
                        pos.pnl_dollars = (cp - pos.entry_price) * pos.qty
                    else:
                        pos.pnl_dollars = (pos.entry_price - cp) * pos.qty
                    position_value = pos.entry_price * pos.qty
                    pos.pnl_percent = (pos.pnl_dollars / position_value * 100) if position_value > 0 else 0.0

                    portfolio.cash += position_value + pos.pnl_dollars

                    # Close any PortfolioHolding that mirrors this Trade so
                    # the Holdings list reflects the close immediately —
                    # previously the row lingered until Alpaca confirmed
                    # the sell fill (or forever, on a rejection).
                    hld_rows = await db.execute(
                        select(PortfolioHolding).where(
                            PortfolioHolding.portfolio_id == portfolio.id,
                            PortfolioHolding.ticker == pos.ticker,
                            PortfolioHolding.direction == pos.direction,
                            PortfolioHolding.is_active == True,
                        )
                    )
                    for h in hld_rows.scalars().all():
                        if h.qty and h.qty > pos.qty:
                            h.qty -= pos.qty
                        else:
                            h.is_active = False
                            h.notes = (h.notes or "") + " | ai_review_close"

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
                        resolved_at=utcnow(),
                    ))

                    # PARITY FIX: For live/paper portfolios, the DB close
                    # above must be mirrored by an actual Alpaca sell.
                    if exec_mode in ("paper", "live") and portfolio.alpaca_api_key:
                        from app.services.trade_processor import _execute_on_alpaca
                        asyncio.create_task(_execute_on_alpaca(
                            portfolio, pos.ticker, pos.qty, "sell", cp,
                            trade_id=getattr(pos, "id", None),
                        ))

                    logger.info(
                        f"AI portfolio review ({portfolio.name}): "
                        f"CLOSED trade {ticker} | PnL: {pos.pnl_percent:+.2f}% "
                        f"| mode={portfolio.execution_mode}"
                    )
                    trade_closed = True
                    break

                if trade_closed:
                    continue

                # 2. Otherwise try to close a matching manual holding.
                #    This is the ASTS-style case: the holding isn't tied
                #    to a Trade row, so the Trade loop above can't resolve
                #    it. Mark inactive, credit cash, log the action, and
                #    fire the sell to Alpaca for paper/live.
                for h in manual_holdings:
                    if h.ticker != ticker or not h.is_active:
                        continue
                    cp = price_service.get_price(h.ticker) or h.entry_price
                    if h.direction == "long":
                        pnl_dollars = (cp - h.entry_price) * h.qty
                    else:
                        pnl_dollars = (h.entry_price - cp) * h.qty
                    position_value = h.entry_price * h.qty
                    pnl_percent = (pnl_dollars / position_value * 100) if position_value > 0 else 0.0

                    h.is_active = False
                    h.notes = (h.notes or "") + " | ai_review_close"
                    portfolio.cash = (portfolio.cash or 0.0) + position_value + pnl_dollars

                    db.add(PortfolioAction(
                        portfolio_id=portfolio.id,
                        ticker=ticker,
                        direction=h.direction,
                        action_type="CLOSE",
                        confidence=7,
                        reasoning=f"[Scheduled Review — Manual Holding] {reasoning}",
                        trigger_type="SCHEDULED_REVIEW",
                        current_price=cp,
                        priority_score=7.0,
                        status="approved",
                        resolved_at=utcnow(),
                    ))

                    if exec_mode in ("paper", "live") and portfolio.alpaca_api_key:
                        from app.services.trade_processor import _execute_on_alpaca
                        asyncio.create_task(_execute_on_alpaca(
                            portfolio, h.ticker, h.qty, "sell", cp,
                        ))

                    logger.info(
                        f"AI portfolio review ({portfolio.name}): "
                        f"CLOSED manual holding {ticker} | PnL: {pnl_percent:+.2f}% "
                        f"| mode={portfolio.execution_mode}"
                    )
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
        logger.error(
            f"AI portfolio scheduled review failed for "
            f"{getattr(portfolio, 'name', '?')}: {e}",
            exc_info=True,
        )


# ── Helpers ──────────────────────────────────────────────────────────────

async def _get_ai_portfolio_equity(portfolio: Portfolio, db: AsyncSession) -> float:
    """Calculate current AI portfolio equity = initial_capital + realized_pnl + unrealized_pnl.

    Uses the same formula as non-AI portfolios in trade_processor._take_snapshot()
    to prevent cash-tracking drift from inflating equity.
    """
    # Realized P&L from closed trades
    closed_result = await db.execute(
        select(Trade)
        .join(PortfolioTrade)
        .where(
            PortfolioTrade.portfolio_id == portfolio.id,
            Trade.status == "closed",
        )
    )
    closed_pnl = sum(t.pnl_dollars or 0.0 for t in closed_result.scalars().all())

    # Unrealized P&L from open positions
    open_result = await db.execute(
        select(Trade)
        .join(PortfolioTrade)
        .where(
            PortfolioTrade.portfolio_id == portfolio.id,
            Trade.status == "open",
        )
    )
    unrealized_pnl = 0.0
    for t in open_result.scalars().all():
        cp = price_service.get_price(t.ticker) or t.entry_price
        if t.direction == "long":
            unrealized_pnl += (cp - t.entry_price) * t.qty
        else:
            unrealized_pnl += (t.entry_price - cp) * t.qty

    return portfolio.initial_capital + closed_pnl + unrealized_pnl


async def _take_ai_snapshot(portfolio: Portfolio, db: AsyncSession):
    """Take an equity snapshot for the AI portfolio."""
    equity = await _get_ai_portfolio_equity(portfolio, db)

    # Count open positions and compute unrealized P&L
    result = await db.execute(
        select(Trade)
        .join(PortfolioTrade)
        .where(
            PortfolioTrade.portfolio_id == portfolio.id,
            Trade.status == "open",
        )
    )
    open_trades = result.scalars().all()
    open_count = len(open_trades)

    unrealized = 0.0
    for t in open_trades:
        cp = price_service.get_price(t.ticker) or t.entry_price
        if t.direction == "long":
            unrealized += (cp - t.entry_price) * t.qty
        else:
            unrealized += (t.entry_price - cp) * t.qty

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
