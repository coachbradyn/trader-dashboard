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

async def _get_ai_enabled_portfolios() -> list[Portfolio]:
    """Get all portfolios where Henry should trade autonomously:
    AI-managed portfolios + any portfolio with ai_evaluation_enabled."""
    try:
        async with async_session() as db:
            from sqlalchemy import or_
            result = await db.execute(
                select(Portfolio).where(
                    Portfolio.is_active == True,
                    or_(
                        Portfolio.is_ai_managed == True,
                        Portfolio.ai_evaluation_enabled == True,
                    ),
                )
            )
            return list(result.scalars().all())
    except Exception as e:
        logger.error(f"Failed to get AI-enabled portfolios: {e}")
        return []


async def run_autonomous_trading() -> dict:
    """
    Henry's autonomous trading loop. Called on schedule during market hours.

    Pipeline:
    1. Scan for opportunities ONCE (scanner profiles + pattern detection)
    2. Execute high-conviction picks across ALL AI-enabled portfolios
    3. Each portfolio gets independently sized positions based on its own cash
    """
    from app.services.ai_portfolio import get_ai_config
    from app.services.henry_activity import log_activity

    summary = {"scanner_trades": 0, "pattern_trades": 0, "portfolios_traded": 0, "errors": []}
    await log_activity("Autonomous trading loop started", "scan_start")

    # Get all portfolios Henry should trade on
    portfolios = await _get_ai_enabled_portfolios()
    if not portfolios:
        logger.info("Autonomous trading: no AI-enabled portfolios")
        await log_activity("No AI-enabled portfolios found", "status")
        return summary

    cfg = get_ai_config()
    portfolio_names = [p.name for p in portfolios]
    await log_activity(f"Trading across {len(portfolios)} portfolios: {', '.join(portfolio_names)}", "scan_start")

    # ── Phase 1: Find opportunities (scan ONCE, execute across all portfolios) ──
    opportunities = []

    # Try scanner first
    try:
        from app.services.scanner_service import select_profiles_for_now, run_scanner
        from app.services.fmp_service import get_api_usage

        profiles = await select_profiles_for_now()
        for profile in profiles:
            usage = get_api_usage()
            if usage.get("remaining", 0) < 20:
                break
            criteria = profile.get("criteria")
            if not criteria:
                continue
            profile_name = profile.get("name", profile.get("id"))
            await log_activity(f"Running profile: {profile_name}", "scan_profile")
            try:
                opps = await run_scanner(profile_criteria=criteria, profile_name=profile_name, skip_actions=True)
                if opps:
                    for o in opps:
                        o["source"] = f"profile:{profile_name}"
                    opportunities.extend(opps)
            except Exception as e:
                logger.warning(f"Profile '{profile_name}' failed: {e}")
    except Exception as e:
        logger.error(f"Scanner phase failed: {e}")
        summary["errors"].append(f"scanner: {e}")

    # Phase 2: Pattern detection if scanner found nothing
    if not opportunities:
        try:
            patterns = await _detect_patterns()
            if patterns:
                # Send to Henry for evaluation
                approved = await _henry_evaluate_patterns(patterns, portfolios[0], portfolios[0].cash, cfg)
                for a in approved:
                    a["source"] = f"pattern:{a.get('pattern', 'unknown')}"
                opportunities.extend(approved)
        except Exception as e:
            logger.error(f"Pattern detection failed: {e}")
            summary["errors"].append(f"patterns: {e}")

    if not opportunities:
        await log_activity("No opportunities found across all profiles and patterns", "status")
        return summary

    await log_activity(f"Found {len(opportunities)} opportunities — executing across {len(portfolios)} portfolios", "scan_result")

    # ── Phase 3: Execute across ALL AI-enabled portfolios ──
    min_confidence = max(cfg.get("min_confidence", 5), 6)

    for portfolio in portfolios:
        portfolio_trades = 0
        max_positions = portfolio.max_open_positions or 15

        # Get this portfolio's open positions
        try:
            async with async_session() as db:
                open_result = await db.execute(
                    select(func.count(Trade.id))
                    .join(PortfolioTrade)
                    .where(
                        PortfolioTrade.portfolio_id == portfolio.id,
                        Trade.status == "open",
                    )
                )
                open_count = open_result.scalar() or 0

                existing_result = await db.execute(
                    select(Trade.ticker)
                    .join(PortfolioTrade)
                    .where(
                        PortfolioTrade.portfolio_id == portfolio.id,
                        Trade.status == "open",
                    )
                )
                existing_tickers = {row[0] for row in existing_result.all()}
        except Exception:
            continue

        slots = max_positions - open_count
        if slots <= 0:
            continue

        for opp in opportunities:
            if portfolio_trades >= slots:
                break

            ticker = opp.get("ticker", "")
            confidence = opp.get("confidence", 0)
            direction = opp.get("direction", "long")

            if not ticker or ticker in existing_tickers or confidence < min_confidence:
                continue

            price = opp.get("suggested_price") or opp.get("price")
            if not price:
                from app.services.fmp_service import get_quote
                quote = await get_quote(ticker)
                if quote and isinstance(quote, list) and len(quote) > 0:
                    price = quote[0].get("price")
                if not price:
                    continue

            success = await _execute_autonomous_trade(
                portfolio, ticker, direction, price, confidence,
                f"{opp.get('source', 'scanner')}: {opp.get('reasoning', '')[:300]}",
                portfolio.cash,  # Use this portfolio's cash for equity
                cfg,
                source=opp.get("source", "scanner"),
                stop_price=opp.get("stop"),
            )
            if success:
                portfolio_trades += 1
                existing_tickers.add(ticker)
                summary["scanner_trades"] += 1

        if portfolio_trades > 0:
            summary["portfolios_traded"] += 1
            await log_activity(
                f"[{portfolio.name}] Executed {portfolio_trades} trades",
                "trade_execute",
            )

    total = summary["scanner_trades"]
    msg = f"Trading complete: {total} trades across {summary['portfolios_traded']} portfolios"
    if total == 0:
        msg = "No trades executed — opportunities didn't meet criteria or cash limits"
    await log_activity(msg, "status")
    logger.info(f"Autonomous trading complete: {summary}")
    return summary


async def _detect_patterns() -> list[dict]:
    """Detect patterns without executing — returns candidate list."""
    from app.services.fmp_service import (
        get_api_usage, get_gainers, get_most_active,
        get_historical_daily, get_technical_indicator,
    )

    usage = get_api_usage()
    if usage.get("remaining", 0) < 30:
        return []

    candidates = []

    # Inside day breakouts
    try:
        actives = await get_most_active()
        if actives and isinstance(actives, list):
            for stock in actives[:15]:
                ticker = stock.get("symbol", "")
                price = stock.get("price")
                if not ticker or not price or price < 5:
                    continue
                hist = await get_historical_daily(ticker, days=5)
                candles = None
                if hist and isinstance(hist, dict) and "historical" in hist:
                    candles = hist["historical"][:5]
                elif hist and isinstance(hist, list):
                    candles = hist[:5]
                if candles and len(candles) >= 3:
                    today_h, today_l = candles[0].get("high", 0), candles[0].get("low", 0)
                    yest_h, yest_l = candles[1].get("high", 0), candles[1].get("low", 0)
                    if today_h <= yest_h and today_l >= yest_l and price > yest_h:
                        candidates.append({
                            "ticker": ticker, "pattern": "inside_day_breakout", "direction": "long",
                            "price": price, "stop": yest_l, "target": price + (yest_h - yest_l) * 2,
                            "entry_reason": f"Inside day breakout above ${yest_h:.2f}",
                        })
    except Exception:
        pass

    # Volume accumulation
    try:
        gainers = await get_gainers()
        if gainers and isinstance(gainers, list):
            for stock in gainers[:10]:
                ticker = stock.get("symbol", "")
                price = stock.get("price")
                change_pct = stock.get("changesPercentage", 0)
                if not ticker or not price or price < 5 or abs(change_pct) > 15:
                    continue
                if any(c["ticker"] == ticker for c in candidates):
                    continue
                current_vol = stock.get("volume", 0)
                hist = await get_historical_daily(ticker, days=25)
                volumes = []
                if hist and isinstance(hist, dict) and "historical" in hist:
                    volumes = [d.get("volume", 0) for d in hist["historical"][:20] if d.get("volume")]
                elif hist and isinstance(hist, list):
                    volumes = [d.get("volume", 0) for d in hist[:20] if d.get("volume")]
                avg_vol = sum(volumes) / len(volumes) if volumes else 0
                if avg_vol > 0 and current_vol > avg_vol * 2 and change_pct > 2:
                    candidates.append({
                        "ticker": ticker, "pattern": "volume_accumulation", "direction": "long",
                        "price": price, "stop": price * 0.95, "target": price * 1.10,
                        "entry_reason": f"Volume surge {current_vol/avg_vol:.1f}x with +{change_pct:.1f}%",
                    })
    except Exception:
        pass

    return candidates


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

            # Position sizing — cap at available cash
            high_alloc = cfg.get("high_alloc_pct", 5.0) / 100.0
            mid_alloc = cfg.get("mid_alloc_pct", 3.0) / 100.0
            alloc_pct = high_alloc if confidence >= 8 else mid_alloc

            target_amount = equity * alloc_pct
            max_per_trade_pct = 10.0  # Max 10% of equity per trade
            max_amount = equity * (max_per_trade_pct / 100.0)
            alloc_amount = min(target_amount, max_amount, port_obj.cash)

            # Need at least enough for 1 share or $10 minimum
            min_trade = min(10.0, price) if price > 0 else 10.0
            qty = alloc_amount / price if price > 0 and alloc_amount >= min_trade else 0

            if qty <= 0:
                logger.info(f"Autonomous: skipped {ticker} — insufficient cash (have ${port_obj.cash:.2f}, need ${min_trade:.2f} min, target was ${target_amount:.2f})")
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
    Check ALL AI-enabled portfolio positions for exit signals.
    Returns total count of positions closed across all portfolios.
    """
    from app.services.ai_portfolio import _take_ai_snapshot
    from app.services.fmp_service import get_quote, get_technical_indicator, get_api_usage
    import asyncio

    usage = get_api_usage()
    if usage.get("remaining", 0) < 10:
        return 0

    portfolios = await _get_ai_enabled_portfolios()
    if not portfolios:
        return 0

    total_closed = 0

    for portfolio in portfolios:
        closed = 0
        try:
            async with async_session() as db:
                # Re-fetch for fresh state
                port_result = await db.execute(select(Portfolio).where(Portfolio.id == portfolio.id))
                portfolio = port_result.scalar_one_or_none()
                if not portfolio:
                    continue

                # Get open positions for THIS portfolio
                result = await db.execute(
                    select(Trade)
                    .join(PortfolioTrade)
                    .where(
                        PortfolioTrade.portfolio_id == portfolio.id,
                        Trade.status == "open",
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

                # Calculate P&L
                if pos.direction == "long":
                    pnl_pct = (current_price - pos.entry_price) / pos.entry_price * 100
                else:
                    pnl_pct = (pos.entry_price - current_price) / pos.entry_price * 100

                hold_days = (datetime.utcnow() - pos.entry_time).days if pos.entry_time else 0

                # ── Rule 1: Stop loss hit ──
                if pos.stop_price:
                    if pos.direction == "long" and current_price <= pos.stop_price:
                        should_close = True
                        exit_reason = f"stop_hit (${pos.stop_price:.2f})"
                    elif pos.direction == "short" and current_price >= pos.stop_price:
                        should_close = True
                        exit_reason = f"stop_hit (${pos.stop_price:.2f})"

                # ── Rule 2: Profit target (>15% gain — take profits) ──
                if not should_close and pnl_pct >= 15:
                    should_close = True
                    exit_reason = f"profit_target ({pnl_pct:+.1f}%)"

                # ── Rule 3: Trailing stop (gave back >40% of max gain) ──
                if not should_close and pnl_pct > 5:
                    # We don't track max gain per position, so use a simpler rule:
                    # if RSI was high and is now dropping, momentum fading
                    pass  # Covered by RSI check below

                # ── Rule 4: Overbought exit (RSI > 80 on profitable longs) ──
                if not should_close:
                    try:
                        rsi_data = await get_technical_indicator(pos.ticker, "rsi", period=14, interval="daily")
                        if rsi_data and isinstance(rsi_data, list) and len(rsi_data) > 0:
                            rsi = rsi_data[0].get("rsi") or rsi_data[0].get("value")
                            if rsi and rsi > 80 and pos.direction == "long" and pnl_pct > 0:
                                should_close = True
                                exit_reason = f"overbought_exit (RSI {rsi:.0f}, P&L {pnl_pct:+.1f}%)"
                    except Exception:
                        pass

                # ── Rule 5: Loss limit (-8% — cut losers) ──
                if not should_close and pnl_pct <= -8:
                    should_close = True
                    exit_reason = f"loss_limit ({pnl_pct:+.1f}%)"

                # ── Rule 6: Dead money (>10 days, <2% gain) ──
                if not should_close and hold_days > 10 and pnl_pct < 2:
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
                total_closed += closed

        except Exception as e:
            logger.error(f"Autonomous exit check failed for {portfolio.name}: {e}")

    return total_closed
