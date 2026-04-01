"""
Henry Autonomous Trading
========================
Makes Henry an independent trader for his AI portfolio.
Instead of only reacting to TradingView signals, Henry:
1. Executes high-confidence scanner opportunities automatically
2. Detects patterns FMP screener misses (inside day breakouts, volume accumulation, momentum)
3. Manages entries with proper position sizing and risk management

Runs as a scheduled job during market hours.
"""

import json
import logging
from datetime import datetime, timedelta, date

from sqlalchemy import select, func, desc
from sqlalchemy.orm import selectinload

from app.database import async_session
from app.models import (
    Portfolio, Trade, Trader, PortfolioTrade, PortfolioSnapshot,
    PortfolioAction,
)
from app.services.price_service import price_service

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════
# MAIN AUTONOMOUS LOOP
# ══════════════════════════════════════════════════════════════════════

async def run_autonomous_trading() -> dict:
    """
    Henry's autonomous trading loop. Called on schedule during market hours.

    Pipeline:
    1. Check if AI portfolio exists and has capacity
    2. Run scanner for new opportunities
    3. Auto-execute high-confidence scanner picks
    4. If scanner found nothing, run pattern detection
    5. Auto-execute high-confidence pattern picks
    6. Return summary of actions taken
    """
    from app.services.ai_portfolio import get_ai_portfolio, get_ai_config, _get_ai_portfolio_equity
    from app.services.henry_activity import log_activity

    summary = {"scanner_trades": 0, "pattern_trades": 0, "skipped": 0, "errors": []}
    await log_activity("Autonomous trading loop started", "scan_start")

    try:
        async with async_session() as db:
            portfolio = await get_ai_portfolio(db)
            if not portfolio:
                logger.info("Autonomous trading: no AI portfolio exists")
                return summary

            equity = await _get_ai_portfolio_equity(portfolio, db)
            cfg = get_ai_config()
            max_positions = portfolio.max_open_positions or 15

            # Count open positions
            open_result = await db.execute(
                select(func.count(Trade.id))
                .join(PortfolioTrade)
                .where(
                    PortfolioTrade.portfolio_id == portfolio.id,
                    Trade.status == "open",
                    Trade.is_simulated == True,
                )
            )
            open_count = open_result.scalar() or 0

            if open_count >= max_positions:
                logger.info(f"Autonomous trading: at max positions ({open_count}/{max_positions})")
                return summary

            # Check drawdown
            snap_result = await db.execute(
                select(func.max(PortfolioSnapshot.peak_equity))
                .where(PortfolioSnapshot.portfolio_id == portfolio.id)
            )
            peak = snap_result.scalar() or portfolio.initial_capital
            current_dd = ((peak - equity) / peak * 100) if peak > 0 else 0
            max_dd = portfolio.max_drawdown_pct or 20.0

            if current_dd >= max_dd * 0.9:
                logger.info(f"Autonomous trading: drawdown too high ({current_dd:.1f}% near {max_dd:.1f}% max)")
                return summary

            # Get existing tickers to avoid duplicates
            existing_result = await db.execute(
                select(Trade.ticker)
                .join(PortfolioTrade)
                .where(
                    PortfolioTrade.portfolio_id == portfolio.id,
                    Trade.status == "open",
                    Trade.is_simulated == True,
                )
            )
            existing_tickers = {row[0] for row in existing_result.all()}

            slots_available = max_positions - open_count

    except Exception as e:
        logger.error(f"Autonomous trading: failed to check portfolio state: {e}")
        summary["errors"].append(str(e))
        return summary

    # ── Phase 1: Run scanner with market-appropriate profiles ──
    try:
        scanner_trades = await _execute_multi_profile_scan(
            portfolio, equity, cfg, existing_tickers, slots_available
        )
        summary["scanner_trades"] = scanner_trades
        slots_available -= scanner_trades
    except Exception as e:
        logger.error(f"Autonomous trading: scanner execution failed: {e}")
        summary["errors"].append(f"scanner: {e}")

    # ── Phase 2: Pattern detection (if no profiles produced trades) ──
    if slots_available > 0 and summary["scanner_trades"] == 0:
        try:
            pattern_trades = await _execute_pattern_opportunities(
                portfolio, equity, cfg, existing_tickers, slots_available
            )
            summary["pattern_trades"] = pattern_trades
        except Exception as e:
            logger.error(f"Autonomous trading: pattern detection failed: {e}")
            summary["errors"].append(f"patterns: {e}")

    total = summary["scanner_trades"] + summary["pattern_trades"]
    logger.info(f"Autonomous trading complete: {total} trades executed ({summary})")
    msg = f"Trading loop complete: {total} trades executed"
    if summary["scanner_trades"]:
        msg += f" ({summary['scanner_trades']} from scanner)"
    if summary["pattern_trades"]:
        msg += f" ({summary['pattern_trades']} from patterns)"
    if total == 0:
        msg += " — no actionable opportunities found"
    await log_activity(msg, "status")
    return summary


# ══════════════════════════════════════════════════════════════════════
# PHASE 1: MULTI-PROFILE SCANNING
# ══════════════════════════════════════════════════════════════════════

async def _execute_multi_profile_scan(
    portfolio: Portfolio,
    equity: float,
    cfg: dict,
    existing_tickers: set,
    max_trades: int,
) -> int:
    """
    Run scanner with each market-appropriate profile.
    Henry selects which profiles to use based on current VIX, trend, and time of day.
    """
    from app.services.scanner_service import select_profiles_for_now, run_scanner
    from app.services.fmp_service import get_api_usage

    # Get profiles appropriate for current market conditions
    profiles = await select_profiles_for_now()
    if not profiles:
        logger.info("Autonomous trading: no scan profiles matched current conditions")
        # Fall back to running with saved criteria (backward compat)
        return await _execute_scanner_opportunities(
            portfolio, equity, cfg, existing_tickers, max_trades
        )

    trades_made = 0
    profiles_run = 0

    for profile in profiles:
        if trades_made >= max_trades:
            break

        # Check API budget before each profile
        usage = get_api_usage()
        if usage.get("remaining", 0) < 20:
            logger.info(f"Autonomous: stopping profile rotation — FMP budget low ({usage.get('remaining')} remaining)")
            break

        profile_name = profile.get("name", profile.get("id", "unknown"))
        criteria = profile.get("criteria")
        if not criteria:
            continue

        logger.info(f"Autonomous: running profile '{profile_name}'")
        await log_activity(f"Running scan profile: {profile_name}", "scan_profile")
        profiles_run += 1

        try:
            opportunities = await run_scanner(
                profile_criteria=criteria,
                profile_name=profile_name,
                skip_actions=True,  # Don't create pending OPPORTUNITY actions — we execute directly
            )
        except Exception as e:
            logger.warning(f"Autonomous: profile '{profile_name}' failed: {e}")
            continue

        if not opportunities:
            logger.info(f"Autonomous: profile '{profile_name}' found no opportunities")
            continue

        # Execute high-confidence picks
        min_confidence = max(cfg.get("min_confidence", 5), 6)
        for opp in opportunities:
            if trades_made >= max_trades:
                break

            ticker = opp.get("ticker", "")
            confidence = opp.get("confidence", 0)
            direction = opp.get("direction", "long")

            if not ticker or ticker in existing_tickers:
                continue
            if confidence < min_confidence:
                continue

            suggested_price = opp.get("suggested_price")
            if not suggested_price:
                from app.services.fmp_service import get_quote
                quote = await get_quote(ticker)
                if quote and isinstance(quote, list) and len(quote) > 0:
                    suggested_price = quote[0].get("price")
                if not suggested_price:
                    continue

            success = await _execute_autonomous_trade(
                portfolio, ticker, direction, suggested_price, confidence,
                f"[{profile_name}] {opp.get('reasoning', 'scanner pick')}",
                equity, cfg, source=f"profile:{profile_name}",
            )
            if success:
                trades_made += 1
                existing_tickers.add(ticker)

    logger.info(f"Autonomous: {profiles_run} profiles run, {trades_made} trades from scanning")
    return trades_made


async def _execute_scanner_opportunities(
    portfolio: Portfolio,
    equity: float,
    cfg: dict,
    existing_tickers: set,
    max_trades: int,
) -> int:
    """Fallback: execute using saved criteria (no profiles). Backward compatible."""
    from app.services.scanner_service import run_scanner

    opportunities = await run_scanner(skip_actions=True)
    if not opportunities:
        return 0

    trades_made = 0
    min_confidence = max(cfg.get("min_confidence", 5), 6)

    for opp in opportunities:
        if trades_made >= max_trades:
            break
        ticker = opp.get("ticker", "")
        confidence = opp.get("confidence", 0)
        direction = opp.get("direction", "long")
        if not ticker or ticker in existing_tickers or confidence < min_confidence:
            continue

        suggested_price = opp.get("suggested_price")
        if not suggested_price:
            from app.services.fmp_service import get_quote
            quote = await get_quote(ticker)
            if quote and isinstance(quote, list) and len(quote) > 0:
                suggested_price = quote[0].get("price")
            if not suggested_price:
                continue

        success = await _execute_autonomous_trade(
            portfolio, ticker, direction, suggested_price, confidence,
            f"Scanner: {opp.get('reasoning', '')}",
            equity, cfg, source="scanner",
        )
        if success:
            trades_made += 1
            existing_tickers.add(ticker)

    return trades_made


# ══════════════════════════════════════════════════════════════════════
# PHASE 2: PATTERN DETECTION
# ══════════════════════════════════════════════════════════════════════

async def _execute_pattern_opportunities(
    portfolio: Portfolio,
    equity: float,
    cfg: dict,
    existing_tickers: set,
    max_trades: int,
) -> int:
    """
    When the scanner finds nothing, look for specific patterns:
    1. Inside day breakouts — stock with narrowing range about to break out
    2. Volume accumulation — unusual volume building at support levels
    3. Momentum continuation — strong stocks pulling back to support
    """
    from app.services.fmp_service import (
        get_api_usage, get_gainers, get_most_active,
        get_historical_daily, get_quote, get_technical_indicator,
    )

    usage = get_api_usage()
    if usage.get("remaining", 0) < 30:
        logger.info("Autonomous patterns: insufficient FMP API budget")
        return 0

    candidates = []

    # ── Pattern 1: Inside Day Breakouts ──
    # Look at most active stocks for inside day setups
    try:
        actives = await get_most_active()
        if actives and isinstance(actives, list):
            for stock in actives[:15]:
                ticker = stock.get("symbol", "")
                if not ticker or ticker in existing_tickers:
                    continue
                price = stock.get("price")
                if not price or price < 5:
                    continue

                # Fetch last 5 daily candles to detect inside day
                hist = await get_historical_daily(ticker, days=5)
                candles = None
                if hist and isinstance(hist, dict) and "historical" in hist:
                    candles = hist["historical"][:5]
                elif hist and isinstance(hist, list):
                    candles = hist[:5]

                if candles and len(candles) >= 3:
                    # Inside day: today's range is within yesterday's range
                    today_h = candles[0].get("high", 0)
                    today_l = candles[0].get("low", 0)
                    yest_h = candles[1].get("high", 0)
                    yest_l = candles[1].get("low", 0)

                    is_inside = today_h <= yest_h and today_l >= yest_l
                    if is_inside and today_h > 0:
                        # Breakout direction: check if price broke above yesterday's high
                        if price > yest_h:
                            candidates.append({
                                "ticker": ticker,
                                "pattern": "inside_day_breakout",
                                "direction": "long",
                                "price": price,
                                "entry_reason": f"Inside day breakout above ${yest_h:.2f}. Range compressed ({today_l:.2f}-{today_h:.2f} inside {yest_l:.2f}-{yest_h:.2f}), now breaking out with volume.",
                                "stop": yest_l,  # Stop below inside day low
                                "target": price + (yest_h - yest_l) * 2,  # 2x range target
                            })
    except Exception as e:
        logger.debug(f"Inside day detection error: {e}")

    # ── Pattern 2: Volume Accumulation ──
    # Look at gainers for stocks with unusual volume at key levels
    try:
        gainers = await get_gainers()
        if gainers and isinstance(gainers, list):
            for stock in gainers[:10]:
                ticker = stock.get("symbol", "")
                if not ticker or ticker in existing_tickers:
                    continue
                if any(c["ticker"] == ticker for c in candidates):
                    continue  # Already found
                price = stock.get("price")
                change_pct = stock.get("changesPercentage", 0)
                if not price or price < 5 or abs(change_pct) > 15:
                    continue  # Skip extreme movers

                # Check if volume is 2x+ average
                hist = await get_historical_daily(ticker, days=25)
                volumes = []
                if hist and isinstance(hist, dict) and "historical" in hist:
                    volumes = [d.get("volume", 0) for d in hist["historical"][:20] if d.get("volume")]
                elif hist and isinstance(hist, list):
                    volumes = [d.get("volume", 0) for d in hist[:20] if d.get("volume")]

                current_vol = stock.get("volume", 0)
                avg_vol = sum(volumes) / len(volumes) if volumes else 0

                if avg_vol > 0 and current_vol > avg_vol * 2 and change_pct > 2:
                    # Volume accumulation with positive price action
                    candidates.append({
                        "ticker": ticker,
                        "pattern": "volume_accumulation",
                        "direction": "long",
                        "price": price,
                        "entry_reason": f"Volume surge {current_vol/avg_vol:.1f}x average with +{change_pct:.1f}% price action. Institutional accumulation signal.",
                        "stop": price * 0.95,  # 5% stop
                        "target": price * 1.10,  # 10% target
                    })
    except Exception as e:
        logger.debug(f"Volume accumulation detection error: {e}")

    # ── Pattern 3: Momentum Pullback ──
    # Find stocks in strong uptrends pulling back to EMA support
    try:
        if len(candidates) < max_trades and usage.get("remaining", 0) > 15:
            # Use gainers from yesterday that might be pulling back today
            actives = await get_most_active()
            if actives and isinstance(actives, list):
                for stock in actives[:8]:
                    ticker = stock.get("symbol", "")
                    if not ticker or ticker in existing_tickers:
                        continue
                    if any(c["ticker"] == ticker for c in candidates):
                        continue
                    price = stock.get("price")
                    if not price or price < 10:
                        continue

                    # Check RSI and EMA alignment
                    rsi_data = await get_technical_indicator(ticker, "rsi", period=14, interval="daily")
                    ema21_data = await get_technical_indicator(ticker, "ema", period=21, interval="daily")

                    rsi = None
                    if rsi_data and isinstance(rsi_data, list) and len(rsi_data) > 0:
                        rsi = rsi_data[0].get("rsi") or rsi_data[0].get("value")
                    ema21 = None
                    if ema21_data and isinstance(ema21_data, list) and len(ema21_data) > 0:
                        ema21 = ema21_data[0].get("ema") or ema21_data[0].get("value")

                    if rsi and ema21 and 40 < rsi < 55 and price > ema21:
                        # RSI pulling back from overbought, price still above EMA — momentum pullback
                        pct_above_ema = (price - ema21) / ema21 * 100
                        if pct_above_ema < 3:  # Within 3% of EMA — near support
                            candidates.append({
                                "ticker": ticker,
                                "pattern": "momentum_pullback",
                                "direction": "long",
                                "price": price,
                                "entry_reason": f"Momentum pullback to EMA 21 support (${ema21:.2f}). RSI at {rsi:.0f} cooling from overbought. Price {pct_above_ema:.1f}% above EMA — buying the dip in an uptrend.",
                                "stop": ema21 * 0.98,  # Below EMA
                                "target": price * 1.08,  # 8% target
                            })
    except Exception as e:
        logger.debug(f"Momentum pullback detection error: {e}")

    if not candidates:
        logger.info("Autonomous patterns: no patterns found")
        return 0

    logger.info(f"Autonomous patterns: found {len(candidates)} candidates")

    # ── Send to Henry for evaluation ──
    approved = await _henry_evaluate_patterns(candidates, portfolio, equity, cfg)

    # ── Execute approved trades ──
    trades_made = 0
    for trade in approved:
        if trades_made >= max_trades:
            break

        ticker = trade["ticker"]
        if ticker in existing_tickers:
            continue

        success = await _execute_autonomous_trade(
            portfolio, ticker, trade["direction"], trade["price"],
            trade.get("confidence", 6),
            trade.get("reasoning", trade.get("entry_reason", "")),
            equity, cfg, source=f"pattern:{trade['pattern']}",
            stop_price=trade.get("stop"),
        )
        if success:
            trades_made += 1
            existing_tickers.add(ticker)

    return trades_made


async def _henry_evaluate_patterns(
    candidates: list[dict],
    portfolio: Portfolio,
    equity: float,
    cfg: dict,
) -> list[dict]:
    """Send pattern candidates to Henry for evaluation. Returns approved trades."""
    if not candidates:
        return []

    try:
        from app.services.ai_service import _call_claude_async

        candidate_text = ""
        for i, c in enumerate(candidates, 1):
            candidate_text += (
                f"\n{i}. {c['ticker']} ({c['pattern']}) — {c['direction'].upper()} @ ${c['price']:.2f}\n"
                f"   Reason: {c['entry_reason']}\n"
                f"   Stop: ${c.get('stop', 0):.2f} | Target: ${c.get('target', 0):.2f}\n"
            )

        prompt = f"""You are Henry, evaluating pattern-based trade candidates for your AI portfolio.

These were found through technical pattern detection (not the regular screener). Evaluate each and decide which ones are worth trading.

CANDIDATES:
{candidate_text}

PORTFOLIO STATE:
  Equity: ${equity:.2f}
  Cash: ${portfolio.cash:.2f}

EVALUATION CRITERIA:
- Does the pattern setup look valid?
- Is the risk/reward favorable (at least 2:1)?
- Is the stop level reasonable?
- Would this stock move enough to justify the trade?

Respond with a JSON array of approved trades. Each object:
{{"ticker": "AAPL", "direction": "long", "price": 150.00, "confidence": 7, "reasoning": "why", "pattern": "inside_day_breakout", "stop": 145.00}}

Return empty array if nothing is compelling. No markdown, no backticks."""

        raw = await _call_claude_async(
            prompt, max_tokens=1000,
            scope="scanner", function_name="ai_portfolio_decision",
            enable_web_search=False,  # Speed over research for trading decisions
        )

        clean = raw.strip().replace("```json", "").replace("```", "").strip()
        result = json.loads(clean)
        if not isinstance(result, list):
            result = [result]

        return result

    except Exception as e:
        logger.error(f"Henry pattern evaluation failed: {e}")
        return []


# ══════════════════════════════════════════════════════════════════════
# TRADE EXECUTION
# ══════════════════════════════════════════════════════════════════════

async def _execute_autonomous_trade(
    portfolio: Portfolio,
    ticker: str,
    direction: str,
    price: float,
    confidence: int,
    reasoning: str,
    equity: float,
    cfg: dict,
    source: str = "autonomous",
    stop_price: float | None = None,
) -> bool:
    """Execute a single autonomous trade in the AI portfolio. Returns True on success."""
    import asyncio

    try:
        async with async_session() as db:
            # Re-fetch portfolio for fresh cash
            port = await db.execute(
                select(Portfolio).where(Portfolio.id == portfolio.id)
            )
            port_obj = port.scalar_one_or_none()
            if not port_obj:
                return False

            # Position sizing
            high_alloc = cfg.get("high_alloc_pct", 5.0) / 100.0
            mid_alloc = cfg.get("mid_alloc_pct", 3.0) / 100.0
            alloc_pct = high_alloc if confidence >= 8 else mid_alloc
            alloc_amount = equity * alloc_pct
            qty = alloc_amount / price if price > 0 else 0

            if qty <= 0 or port_obj.cash < alloc_amount:
                logger.info(f"Autonomous: skipped {ticker} — insufficient cash (need ${alloc_amount:.2f}, have ${port_obj.cash:.2f})")
                return False

            # Get or create a pseudo-trader for autonomous trades
            trader_result = await db.execute(
                select(Trader).where(Trader.trader_id == "henry-autonomous").limit(1)
            )
            trader = trader_result.scalar_one_or_none()
            if not trader:
                # Use the first active trader as a fallback
                trader_result = await db.execute(
                    select(Trader).where(Trader.is_active == True).limit(1)
                )
                trader = trader_result.scalar_one_or_none()
                if not trader:
                    logger.warning("Autonomous: no trader found")
                    return False

            # Create simulated trade
            sim_trade = Trade(
                trader_id=trader.id,
                ticker=ticker,
                direction=direction,
                entry_price=price,
                qty=round(qty, 4),
                stop_price=stop_price,
                entry_time=datetime.utcnow(),
                status="open",
                is_simulated=True,
                raw_entry_payload={"source": source, "confidence": confidence, "autonomous": True},
            )
            db.add(sim_trade)
            await db.flush()

            # Link to AI portfolio
            pt = PortfolioTrade(portfolio_id=port_obj.id, trade_id=sim_trade.id)
            db.add(pt)

            # Deduct cash
            port_obj.cash -= alloc_amount

            # Log action
            action = PortfolioAction(
                portfolio_id=port_obj.id,
                ticker=ticker,
                direction=direction,
                action_type="BUY",
                suggested_price=price,
                current_price=price,
                confidence=confidence,
                reasoning=f"[Autonomous - {source}] {reasoning[:500]}",
                trigger_type="SCANNER",
                trigger_ref=sim_trade.id,
                priority_score=confidence * 1.5,
                status="approved",
                resolved_at=datetime.utcnow(),
            )
            db.add(action)

            # Save context
            from app.services.ai_service import save_context
            asyncio.create_task(save_context(
                content=f"AUTONOMOUS TRADE: {direction.upper()} {ticker} x{qty:.2f} @ ${price:.2f} | conf {confidence}/10 | source: {source} | {reasoning[:200]}",
                context_type="recommendation",
                ticker=ticker,
                confidence=confidence,
                trade_id=sim_trade.id,
                expires_days=30,
            ))

            await db.commit()
            logger.info(f"Autonomous: executed {direction.upper()} {ticker} x{qty:.2f} @ ${price:.2f} (conf {confidence}, source={source})")
            from app.services.henry_activity import log_activity as _log
            await _log(
                f"BOUGHT {ticker} {direction.upper()} x{qty:.2f} @ ${price:.2f} (confidence {confidence}/10)",
                "trade_execute", ticker=ticker,
                details=f"Source: {source} | Allocation: ${alloc_amount:.2f} ({alloc_pct*100:.1f}%)",
            )
            return True

    except Exception as e:
        logger.error(f"Autonomous trade execution failed for {ticker}: {e}")
        return False


# ══════════════════════════════════════════════════════════════════════
# AUTONOMOUS EXIT MONITORING
# ══════════════════════════════════════════════════════════════════════

async def check_autonomous_exits() -> int:
    """
    Check open AI portfolio positions for exit signals:
    - Stop loss hit (if stop_price set)
    - RSI > 75 (overbought — consider trimming)
    - Position held > 10 days with < 2% gain (dead money)
    Returns count of positions closed.
    """
    from app.services.ai_portfolio import get_ai_portfolio, _take_ai_snapshot
    from app.services.fmp_service import get_quote, get_technical_indicator, get_api_usage
    import asyncio

    usage = get_api_usage()
    if usage.get("remaining", 0) < 10:
        return 0

    closed = 0

    try:
        async with async_session() as db:
            portfolio = await get_ai_portfolio(db)
            if not portfolio:
                return 0

            # Get open positions
            result = await db.execute(
                select(Trade)
                .join(PortfolioTrade)
                .where(
                    PortfolioTrade.portfolio_id == portfolio.id,
                    Trade.status == "open",
                    Trade.is_simulated == True,
                )
            )
            open_positions = result.scalars().all()

            for pos in open_positions:
                should_close = False
                exit_reason = ""

                quote = await get_quote(pos.ticker)
                current_price = None
                if quote and isinstance(quote, list) and len(quote) > 0:
                    current_price = quote[0].get("price")
                if not current_price:
                    current_price = price_service.get_price(pos.ticker) or pos.entry_price

                # Check stop loss
                if pos.stop_price:
                    if pos.direction == "long" and current_price <= pos.stop_price:
                        should_close = True
                        exit_reason = f"stop_hit (${pos.stop_price:.2f})"
                    elif pos.direction == "short" and current_price >= pos.stop_price:
                        should_close = True
                        exit_reason = f"stop_hit (${pos.stop_price:.2f})"

                # Check overbought (RSI > 80)
                if not should_close:
                    try:
                        rsi_data = await get_technical_indicator(pos.ticker, "rsi", period=14, interval="daily")
                        if rsi_data and isinstance(rsi_data, list) and len(rsi_data) > 0:
                            rsi = rsi_data[0].get("rsi") or rsi_data[0].get("value")
                            if rsi and rsi > 80 and pos.direction == "long":
                                # Only close if profitable
                                if current_price > pos.entry_price:
                                    should_close = True
                                    exit_reason = f"overbought_exit (RSI {rsi:.0f})"
                    except Exception:
                        pass

                # Check dead money (>10 days, <2% gain)
                if not should_close and pos.entry_time:
                    hold_days = (datetime.utcnow() - pos.entry_time).days
                    if pos.direction == "long":
                        pnl_pct = (current_price - pos.entry_price) / pos.entry_price * 100
                    else:
                        pnl_pct = (pos.entry_price - current_price) / pos.entry_price * 100

                    if hold_days > 10 and pnl_pct < 2:
                        should_close = True
                        exit_reason = f"dead_money ({hold_days}d, {pnl_pct:+.1f}%)"

                if should_close:
                    # Close the position
                    pos.exit_price = current_price
                    pos.exit_reason = exit_reason
                    pos.exit_time = datetime.utcnow()
                    pos.status = "closed"

                    if pos.direction == "long":
                        pos.pnl_dollars = (current_price - pos.entry_price) * pos.qty
                    else:
                        pos.pnl_dollars = (pos.entry_price - current_price) * pos.qty

                    position_value = pos.entry_price * pos.qty
                    pos.pnl_percent = (pos.pnl_dollars / position_value * 100) if position_value > 0 else 0

                    portfolio.cash += position_value + pos.pnl_dollars

                    # Log
                    db.add(PortfolioAction(
                        portfolio_id=portfolio.id,
                        ticker=pos.ticker,
                        direction=pos.direction,
                        action_type="CLOSE",
                        confidence=7,
                        reasoning=f"[Autonomous Exit] {exit_reason}",
                        trigger_type="SCANNER",
                        current_price=current_price,
                        priority_score=10.5,
                        status="approved",
                        resolved_at=datetime.utcnow(),
                    ))

                    from app.services.ai_service import save_context
                    asyncio.create_task(save_context(
                        content=f"AUTONOMOUS EXIT: {pos.ticker} {pos.direction.upper()} | PnL: {pos.pnl_percent:+.2f}% (${pos.pnl_dollars:+.2f}) | Reason: {exit_reason}",
                        context_type="outcome",
                        ticker=pos.ticker,
                        trade_id=pos.id,
                    ))

                    closed += 1
                    logger.info(f"Autonomous exit: {pos.ticker} | {exit_reason} | PnL: {pos.pnl_percent:+.2f}%")
                    from app.services.henry_activity import log_activity as _log_exit
                    await _log_exit(
                        f"CLOSED {pos.ticker} | PnL: {pos.pnl_percent:+.2f}% (${pos.pnl_dollars:+.2f}) | Reason: {exit_reason}",
                        "trade_exit", ticker=pos.ticker,
                    )

            if closed > 0:
                await _take_ai_snapshot(portfolio, db)

            await db.commit()

    except Exception as e:
        logger.error(f"Autonomous exit check failed: {e}")

    return closed
