"""
Reactive Trading Pipeline
=========================
Event-driven trading: when a watched stock moves significantly, Henry
wakes up immediately — pulls context via FMP MCP tools, evaluates the
setup, and decides whether to act.

Replaces the cron-based "scan 500 stocks twice a day" model with
"react to what the market gives you, in real time."

The pipeline is triggered by price_service's movement detection
(±3% from session baseline). Henry gets FMP tools (quotes, technicals,
fundamentals, news) attached to his Claude call so he can pull whatever
context he needs — no pre-fetching, no fragile indicator cascade.

Registration happens at app startup via wire_reactive_trading().
"""
from __future__ import annotations

import asyncio
import logging

from app.utils.utc import utcnow

logger = logging.getLogger(__name__)


async def _handle_price_move(
    ticker: str,
    pct_change: float,
    price: float,
    direction: str,
) -> None:
    """Called by price_service when a stock moves ±3%+.

    1. Check if we have exposure (open position → exit evaluation)
    2. Check if any AI portfolio has capacity (no position → entry evaluation)
    3. Call Claude with FMP tools to evaluate and decide
    4. Execute if approved
    """
    from app.database import async_session
    from app.models import Portfolio, Trade, PortfolioTrade
    from app.services.henry_activity import log_activity
    from sqlalchemy import select, func

    await log_activity(
        f"ALERT: {ticker} {direction} {abs(pct_change):.1f}% → ${price:.2f}",
        "pattern_detect",
        ticker=ticker,
    )

    try:
        async with async_session() as db:
            # Do we hold this ticker in any AI portfolio?
            held = (
                await db.execute(
                    select(Trade.id, Trade.direction, Trade.entry_price, Trade.qty, PortfolioTrade.portfolio_id)
                    .join(PortfolioTrade)
                    .join(Portfolio)
                    .where(
                        Trade.ticker == ticker,
                        Trade.status == "open",
                        (Portfolio.is_ai_managed == True) | (Portfolio.ai_evaluation_enabled == True),
                    )
                )
            ).all()

            if held:
                # We have exposure — evaluate whether to hold or exit
                for trade_id, trade_dir, entry_price, qty, portfolio_id in held:
                    pnl_pct = ((price - entry_price) / entry_price * 100) if trade_dir == "long" else ((entry_price - price) / entry_price * 100)
                    await _evaluate_reactive(
                        ticker=ticker,
                        price=price,
                        pct_change=pct_change,
                        direction=direction,
                        context="exit_evaluation",
                        portfolio_id=portfolio_id,
                        trade_id=trade_id,
                        position_direction=trade_dir,
                        position_pnl_pct=pnl_pct,
                        position_qty=qty,
                        entry_price=entry_price,
                    )
            else:
                # No exposure — evaluate as potential entry
                portfolios = (
                    await db.execute(
                        select(Portfolio).where(
                            Portfolio.is_active == True,
                            (Portfolio.is_ai_managed == True) | (Portfolio.ai_evaluation_enabled == True),
                        )
                    )
                ).scalars().all()

                for port in portfolios:
                    open_count = (
                        await db.execute(
                            select(func.count(Trade.id))
                            .join(PortfolioTrade)
                            .where(
                                PortfolioTrade.portfolio_id == port.id,
                                Trade.status == "open",
                            )
                        )
                    ).scalar() or 0

                    max_positions = port.max_open_positions or 15
                    if open_count >= max_positions:
                        continue
                    if port.cash < 100:
                        continue

                    await _evaluate_reactive(
                        ticker=ticker,
                        price=price,
                        pct_change=pct_change,
                        direction=direction,
                        context="entry_evaluation",
                        portfolio_id=port.id,
                        portfolio_cash=port.cash,
                        portfolio_equity=port.initial_capital,
                    )

    except Exception as e:
        logger.error(f"Reactive pipeline failed for {ticker}: {e}")
        await log_activity(
            f"Reactive eval failed: {ticker} — {e}",
            "error",
            ticker=ticker,
        )


async def _evaluate_reactive(
    ticker: str,
    price: float,
    pct_change: float,
    direction: str,
    context: str,
    portfolio_id: str,
    **kwargs,
) -> None:
    """Call Claude with FMP tools to evaluate a reactive opportunity."""
    from app.services.ai_service import _build_system_prompt
    from app.services.ai_provider import call_ai
    from app.services.henry_activity import log_activity
    from app.utils.json_extract import extract_json_object

    if context == "exit_evaluation":
        prompt = f"""{ticker} just moved {pct_change:+.1f}% to ${price:.2f}. You hold a {kwargs.get('position_direction', 'long')} position entered at ${kwargs.get('entry_price', 0):.2f} (current P&L: {kwargs.get('position_pnl_pct', 0):+.1f}%).

Use the FMP tools to check: current quote, RSI, ADX, recent news, and any earnings/analyst activity. Then decide:
- HOLD: the move doesn't change your thesis
- EXIT: close the position (stop hit, thesis broken, take profit)

Respond in JSON: {{"action": "HOLD" or "EXIT", "confidence": 1-10, "reasoning": "1-2 sentences", "signal_weights": {{"technical_strength": 0.0-1.0, "fundamental_value": 0.0-1.0, "thesis_quality": 0.0-1.0, "catalyst_proximity": 0.0-1.0, "risk_reward_ratio": 0.0-1.0, "memory_alignment": 0.0-1.0, "regime_fit": 0.0-1.0, "entry_timing": 0.0-1.0}}}}"""
    else:
        dir_suggestion = "long" if direction == "down" and abs(pct_change) >= 5 else ("long" if direction == "up" else "short")
        prompt = f"""{ticker} just moved {pct_change:+.1f}% to ${price:.2f}. Evaluate whether this is a tradeable opportunity.

Use the FMP tools to check: current quote, RSI, ADX, key support/resistance levels, recent news, earnings calendar, and analyst targets. Then decide:
- BUY: enter a position (specify direction)
- SKIP: not compelling enough

Cash available: ${kwargs.get('portfolio_cash', 0):.2f}

Respond in JSON: {{"action": "BUY" or "SKIP", "direction": "long" or "short", "confidence": 1-10, "reasoning": "1-2 sentences", "signal_weights": {{"technical_strength": 0.0-1.0, "fundamental_value": 0.0-1.0, "thesis_quality": 0.0-1.0, "catalyst_proximity": 0.0-1.0, "risk_reward_ratio": 0.0-1.0, "memory_alignment": 0.0-1.0, "regime_fit": 0.0-1.0, "entry_timing": 0.0-1.0}}}}"""

    try:
        system = await _build_system_prompt(
            ticker=ticker,
            scope="signal",
            query_text=f"reactive evaluation {ticker} {direction} {abs(pct_change):.0f}% move",
        )

        raw = await call_ai(
            system=system,
            prompt=prompt,
            function_name="signal_evaluation",
            max_tokens=1500,
            enable_fmp_tools=True,
        )

        result = extract_json_object(raw)
        if not result:
            logger.warning(f"Reactive eval: no JSON from Claude for {ticker}")
            return

        action = result.get("action", "SKIP").upper()
        confidence = result.get("confidence", 0)
        reasoning = result.get("reasoning", "")

        await log_activity(
            f"REACTIVE {context}: {ticker} {pct_change:+.1f}% → {action} (conf {confidence})",
            "analysis",
            ticker=ticker,
            details=reasoning[:200],
        )

        if context == "exit_evaluation" and action == "EXIT" and confidence >= 5:
            await _execute_reactive_exit(
                ticker=ticker,
                trade_id=kwargs["trade_id"],
                portfolio_id=portfolio_id,
                price=price,
                reasoning=reasoning,
                signal_weights=result.get("signal_weights"),
            )

        elif context == "entry_evaluation" and action == "BUY" and confidence >= 6:
            trade_direction = result.get("direction", "long")
            await _execute_reactive_entry(
                ticker=ticker,
                direction=trade_direction,
                price=price,
                confidence=confidence,
                reasoning=reasoning,
                portfolio_id=portfolio_id,
                signal_weights=result.get("signal_weights"),
            )

    except Exception as e:
        logger.error(f"Reactive eval failed for {ticker}: {e}")


async def _execute_reactive_exit(
    ticker: str,
    trade_id: str,
    portfolio_id: str,
    price: float,
    reasoning: str,
    signal_weights: dict | None,
) -> None:
    """Close a position based on reactive evaluation."""
    from app.database import async_session
    from app.models import Trade, Portfolio, PortfolioTrade, PortfolioAction
    from app.services.decision_signals import validate_signal_weights
    from app.services.henry_activity import log_activity
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload
    import asyncio

    try:
        async with async_session() as db:
            trade = (await db.execute(select(Trade).where(Trade.id == trade_id))).scalar_one_or_none()
            if not trade or trade.status != "open":
                return

            trade.exit_price = price
            trade.exit_time = utcnow()
            trade.exit_reason = f"reactive_move: {reasoning[:100]}"
            trade.status = "closed"

            if trade.direction == "long":
                trade.pnl_dollars = (price - trade.entry_price) * trade.qty
            else:
                trade.pnl_dollars = (trade.entry_price - price) * trade.qty

            position_value = trade.entry_price * trade.qty
            trade.pnl_percent = (trade.pnl_dollars / position_value * 100) if position_value > 0 else 0.0

            # Credit cash back
            pt_result = await db.execute(
                select(PortfolioTrade)
                .where(PortfolioTrade.trade_id == trade.id)
                .options(selectinload(PortfolioTrade.portfolio))
            )
            for pt in pt_result.scalars().all():
                pt.portfolio.cash += position_value + trade.pnl_dollars

            db.add(PortfolioAction(
                portfolio_id=portfolio_id,
                ticker=ticker,
                direction=trade.direction,
                action_type="CLOSE",
                confidence=7,
                reasoning=f"[Reactive] {reasoning[:400]}",
                trigger_type="REACTIVE_MOVE",
                trigger_ref=trade.id,
                current_price=price,
                priority_score=14.0,
                status="approved",
                resolved_at=utcnow(),
                signal_weights=validate_signal_weights(signal_weights),
            ))

            await db.commit()

            await log_activity(
                f"REACTIVE EXIT: {ticker} @ ${price:.2f} | PnL: {trade.pnl_percent:+.1f}%",
                "trade_exit",
                ticker=ticker,
            )

            # Execute on Alpaca if wired
            portfolio = (await db.execute(select(Portfolio).where(Portfolio.id == portfolio_id))).scalar_one_or_none()
            if portfolio and portfolio.execution_mode in ("paper", "live") and portfolio.alpaca_api_key:
                from app.services.trade_processor import _execute_on_alpaca
                asyncio.create_task(_execute_on_alpaca(
                    portfolio, ticker, trade.qty, "sell", price,
                    trade_id=trade.id,
                ))

    except Exception as e:
        logger.error(f"Reactive exit failed for {ticker}: {e}")


async def _execute_reactive_entry(
    ticker: str,
    direction: str,
    price: float,
    confidence: int,
    reasoning: str,
    portfolio_id: str,
    signal_weights: dict | None,
) -> None:
    """Enter a position based on reactive evaluation."""
    from app.services.autonomous_trading import _execute_autonomous_trade
    from app.services.ai_portfolio import get_ai_config
    from app.database import async_session
    from app.models import Portfolio
    from sqlalchemy import select

    try:
        async with async_session() as db:
            portfolio = (await db.execute(select(Portfolio).where(Portfolio.id == portfolio_id))).scalar_one_or_none()
            if not portfolio:
                return

        cfg = get_ai_config()
        success = await _execute_autonomous_trade(
            portfolio=portfolio,
            ticker=ticker,
            direction=direction,
            price=price,
            confidence=confidence,
            reasoning=f"[Reactive move] {reasoning[:400]}",
            equity=portfolio.cash,
            cfg=cfg,
            source="reactive_move",
            signal_weights=signal_weights,
        )

        if success:
            from app.services.henry_activity import log_activity
            await log_activity(
                f"REACTIVE ENTRY: {direction.upper()} {ticker} @ ${price:.2f} (conf {confidence})",
                "trade_execute",
                ticker=ticker,
            )

    except Exception as e:
        logger.error(f"Reactive entry failed for {ticker}: {e}")


def wire_reactive_trading():
    """Register the reactive pipeline with price_service. Call at startup."""
    from app.services.price_service import on_price_move
    on_price_move(_handle_price_move)
    logger.info("Reactive trading pipeline wired to price_service")
