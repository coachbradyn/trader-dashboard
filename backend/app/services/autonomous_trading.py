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
from app.utils.utc import utcnow
import logging
from datetime import datetime, timedelta, date, timezone

from sqlalchemy import select, func, desc
from sqlalchemy.orm import selectinload

from app.database import async_session
from app.models import (
    Portfolio, Trade, Trader, PortfolioTrade, PortfolioSnapshot,
    PortfolioAction,
)
from app.models.portfolio_holding import PortfolioHolding
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

    # Pre-init so the "no opportunities" memory path below can reference
    # it safely even if the scanner-phase try block bails before assigning.
    profiles: list = []

    # Try scanner first
    try:
        from app.services.scanner_service import select_profiles_for_now, run_scanner
        from app.services.fmp_service import get_api_usage

        profiles = await select_profiles_for_now()
        for profile in profiles:
            usage = get_api_usage()
            if usage.get("rpm", 0) >= usage.get("rpm_limit", 300):
                logger.info("Autonomous: pausing 10s between profiles for rate limit")
                import asyncio as _aio_at
                await _aio_at.sleep(10)
                usage = get_api_usage()
                if usage.get("rpm", 0) >= usage.get("rpm_limit", 300):
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
        # Document the inactive-scan pass as a memory so the
        # retrieval pipeline can learn "Henry looked and passed on
        # $DATE with VIX=X, regime=Y" instead of leaving a silent
        # gap. Matches the spec: if Henry doesn't trade for any
        # reason, he documents why.
        try:
            from app.services.ai_service import save_memory
            from app.services.market_regime import current_regime_classification
            regime = await current_regime_classification()
            regime_note = regime.get("label") if regime else "unknown regime"
            vix = price_service.get_price("VIX")
            vix_note = f"VIX={vix:.1f}" if vix else "VIX=?"
            profile_names = ",".join(p.get("name") or p.get("id") or "?" for p in (profiles or []))
            asyncio.create_task(save_memory(
                content=(
                    f"SCAN: ran {profile_names or 'no profiles'} — 0 opportunities surfaced. "
                    f"{vix_note}, regime={regime_note}. Henry held cash across "
                    f"{len(portfolios)} AI-enabled portfolios."
                ),
                memory_type="decision",
                importance=5,
                source="scan_empty",
            ))
        except Exception:
            pass
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
                signal_weights=opp.get("signal_weights"),
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

        # Pace between profiles if nearing per-minute rate limit
        usage = get_api_usage()
        if usage.get("rpm", 0) >= usage.get("rpm_limit", 300):
            logger.info("Autonomous: pausing 10s between profiles for rate limit")
            await asyncio.sleep(10)
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
{{"ticker": "AAPL", "direction": "long", "price": 150.00, "confidence": 7, "reasoning": "why", "pattern": "inside_day_breakout", "stop": 145.00, "signal_weights": {{"technical_strength": 0.0-1.0, "fundamental_value": 0.0-1.0, "thesis_quality": 0.0-1.0, "catalyst_proximity": 0.0-1.0, "risk_reward_ratio": 0.0-1.0, "memory_alignment": 0.0-1.0, "regime_fit": 0.0-1.0, "entry_timing": 0.0-1.0}}}}

Score each signal_weights dimension 0.0-1.0 based on how strongly it supports the trade.
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

# ══════════════════════════════════════════════════════════════════════
# CAPITAL REALLOCATION — free cash by closing weak positions
# ══════════════════════════════════════════════════════════════════════

async def _liquidate_for_capital(
    portfolio: Portfolio,
    target_amount: float,
    incoming_confidence: int,
    incoming_ticker: str,
    db,  # AsyncSession — caller provides; we do NOT commit
) -> float:
    """
    Attempt to free capital by closing existing positions to fund a higher-
    conviction incoming trade. Returns the amount of cash freed (may be 0).
    Only closes positions where the case for doing so is clear-cut.

    Priority tiers (sell weakest first):
      1. Losers beyond stop — unmanaged risk, close unconditionally
      2. Underperformers — pnl < -3%, held > 24h, incoming conf >= 8
      3. Low-conviction winners — autonomous positions where incoming conf
         beats the position's logged confidence, held > 4h

    Max ONE non-stop position closed per call to prevent cascade liquidation.
    """
    import asyncio
    from app.services.henry_activity import log_activity

    cash_before = portfolio.cash

    # Already have enough cash — nothing to do
    if portfolio.cash >= target_amount:
        return 0.0

    # Get all open positions for this portfolio
    result = await db.execute(
        select(Trade)
        .join(PortfolioTrade)
        .where(
            PortfolioTrade.portfolio_id == portfolio.id,
            Trade.status == "open",
        )
    )
    open_positions = result.scalars().all()

    if not open_positions:
        return 0.0

    def _close_position(pos, cp, exit_reason):
        """Apply the standard close pattern to a position (no commit)."""
        pos.exit_price = cp
        pos.exit_reason = exit_reason
        pos.exit_time = utcnow()
        pos.status = "closed"

        if pos.direction == "long":
            pos.pnl_dollars = (cp - pos.entry_price) * pos.qty
        else:
            pos.pnl_dollars = (pos.entry_price - cp) * pos.qty

        position_value = pos.entry_price * pos.qty
        pos.pnl_percent = (pos.pnl_dollars / position_value * 100) if position_value > 0 else 0

        portfolio.cash += position_value + pos.pnl_dollars

        db.add(PortfolioAction(
            portfolio_id=portfolio.id,
            ticker=pos.ticker,
            direction=pos.direction,
            action_type="CLOSE",
            confidence=7,
            reasoning=f"[Reallocation] Closed to fund higher-conviction trade in {incoming_ticker} (conf {incoming_confidence}). Reason: {exit_reason}",
            trigger_type="REALLOCATION",
            current_price=cp,
            priority_score=10.5,
            status="approved",
            resolved_at=utcnow(),
        ))

        from app.services.ai_service import save_context
        asyncio.create_task(save_context(
            content=(
                f"REALLOCATION EXIT: {pos.ticker} {pos.direction.upper()} | "
                f"PnL: {pos.pnl_percent:+.2f}% (${pos.pnl_dollars:+.2f}) | "
                f"Reason: {exit_reason} | Freed cash for {incoming_ticker}"
            ),
            context_type="outcome",
            ticker=pos.ticker,
            trade_id=pos.id,
        ))

        asyncio.create_task(log_activity(
            f"REALLOCATION CLOSE: {pos.ticker} | PnL: {pos.pnl_percent:+.2f}% (${pos.pnl_dollars:+.2f}) | "
            f"Reason: {exit_reason} → funding {incoming_ticker} (conf {incoming_confidence})",
            "trade_exit", ticker=pos.ticker,
        ))

        # Execute sell on Alpaca if portfolio is wired to paper/live
        if portfolio.execution_mode in ("paper", "live") and portfolio.alpaca_api_key:
            from app.services.trade_processor import _execute_on_alpaca
            asyncio.create_task(_execute_on_alpaca(
                portfolio, pos.ticker, pos.qty, "sell", cp,
                trade_id=pos.id,
            ))

    # Enrich positions with current price and P&L
    enriched = []
    for pos in open_positions:
        # Skip same ticker — don't churn
        if pos.ticker == incoming_ticker:
            continue

        cp = price_service.get_price(pos.ticker) or pos.entry_price
        if pos.entry_price and pos.entry_price > 0:
            if pos.direction == "long":
                pnl_pct = (cp - pos.entry_price) / pos.entry_price * 100
            else:
                pnl_pct = (pos.entry_price - cp) / pos.entry_price * 100
        else:
            pnl_pct = 0.0

        hold_hours = (utcnow() - pos.entry_time).total_seconds() / 3600 if pos.entry_time else 0
        enriched.append({
            "pos": pos,
            "cp": cp,
            "pnl_pct": pnl_pct,
            "hold_hours": hold_hours,
        })

    # ── TIER 1: Stop breaches — close unconditionally ──────────────────
    for e in enriched:
        pos = e["pos"]
        if not pos.stop_price:
            continue
        breached = False
        if pos.direction == "long" and e["cp"] <= pos.stop_price:
            breached = True
        elif pos.direction == "short" and e["cp"] >= pos.stop_price:
            breached = True

        if breached:
            _close_position(pos, e["cp"], f"stop_breach (${pos.stop_price:.2f})")
            logger.info(f"Reallocation: closed {pos.ticker} — stop breach")

        if portfolio.cash >= target_amount:
            return portfolio.cash - cash_before

    # Don't liquidate for medium-conviction trades (except stop breaches above)
    if incoming_confidence < 8:
        return portfolio.cash - cash_before

    # Safety: don't leave portfolio completely empty
    remaining_open = [e for e in enriched if e["pos"].status == "open"]
    if len(remaining_open) <= 1 and portfolio.cash <= 0:
        return portfolio.cash - cash_before

    non_stop_closed = False  # Max one non-stop close per call

    # ── TIER 2: Underperformers (pnl < -3%, held > 24h) ───────────────
    if not non_stop_closed and portfolio.cash < target_amount:
        underperformers = [
            e for e in remaining_open
            if e["pnl_pct"] < -3 and e["hold_hours"] > 24
        ]
        # Sell worst performer first
        underperformers.sort(key=lambda e: e["pnl_pct"])
        if underperformers:
            worst = underperformers[0]
            _close_position(worst["pos"], worst["cp"], f"underperformer ({worst['pnl_pct']:+.1f}%, {worst['hold_hours']:.0f}h)")
            logger.info(f"Reallocation: closed underperformer {worst['pos'].ticker} ({worst['pnl_pct']:+.1f}%)")
            non_stop_closed = True

    if portfolio.cash >= target_amount:
        return portfolio.cash - cash_before

    # ── TIER 3: Low-conviction winners (autonomous, incoming conf > position conf) ──
    if not non_stop_closed and portfolio.cash < target_amount:
        candidates = []
        for e in remaining_open:
            pos = e["pos"]
            if pos.status != "open":
                continue
            if e["pnl_pct"] <= 0:
                continue
            if e["hold_hours"] <= 4:
                continue
            # Must be an autonomous position
            payload = pos.raw_entry_payload
            if not isinstance(payload, dict) or not payload.get("autonomous"):
                continue
            # Get the position's logged confidence from its PortfolioAction
            pos_confidence = payload.get("confidence", 10)
            if incoming_confidence <= pos_confidence:
                continue
            candidates.append({**e, "pos_confidence": pos_confidence})

        # Sell lowest-confidence winner first
        candidates.sort(key=lambda c: c["pos_confidence"])
        if candidates:
            weakest = candidates[0]
            _close_position(
                weakest["pos"], weakest["cp"],
                f"low_conviction_winner (conf {weakest['pos_confidence']}, pnl {weakest['pnl_pct']:+.1f}%)"
            )
            logger.info(f"Reallocation: closed low-conviction winner {weakest['pos'].ticker}")

    return portfolio.cash - cash_before


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
    signal_weights: dict | None = None,
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

            # ── Options-first attempt ────────────────────────────────
            # If the portfolio has options enabled and the strategy
            # selector returns a recommendation, try to place that order
            # first. Any failure (score below threshold, chain fetch fail,
            # Alpaca rejection, insufficient buying power) falls through
            # to the equity path below — options are always an enhancement,
            # never a requirement.
            options_rec: dict | None = None
            if int(getattr(port_obj, "options_level", 0) or 0) > 0:
                try:
                    from app.services.options_strategy import select_options_strategy
                    options_rec = await select_options_strategy(
                        ticker=ticker,
                        direction=direction,
                        confidence=float(confidence),
                        portfolio_id=port_obj.id,
                        session=db,
                    )
                except Exception as _e:
                    logger.debug(
                        f"Autonomous options selector failed for {ticker}: {_e}"
                    )
                    options_rec = None

            if options_rec:
                options_ok = await _try_execute_autonomous_options(
                    db, port_obj, ticker, direction, confidence,
                    reasoning, source, options_rec,
                )
                if options_ok:
                    return True
                logger.info(
                    f"Autonomous options submission failed for {ticker}; "
                    f"falling back to equity"
                )
                # Fall through to equity path. Don't commit partial state —
                # the options helper rolls back its own writes on failure.

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
                if confidence >= 8:
                    freed = await _liquidate_for_capital(
                        port_obj, target_amount, confidence, ticker, db
                    )
                    if freed > 0:
                        alloc_amount = min(target_amount, max_amount, port_obj.cash)
                        qty = alloc_amount / price if price > 0 and alloc_amount >= min_trade else 0
                if qty <= 0:
                    logger.info(f"Autonomous: skipped {ticker} — insufficient cash after reallocation attempt (have ${port_obj.cash:.2f})")
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
                entry_time=utcnow(),
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

            from app.services.decision_signals import validate_signal_weights
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
                resolved_at=utcnow(),
                instrument_type="equity",
                signal_weights=validate_signal_weights(signal_weights),
            )
            db.add(action)
            await db.flush()
            # Phase 4.5 — autonomous trades go through the same Kelly +
            # injected_memory_ids capture as user-approved actions. The
            # sizing here is informational only (autonomous already
            # picked alloc_amount above) — surfaces what Kelly *would*
            # have recommended for outcome calibration.
            try:
                from app.services.position_sizing import apply_sizing_to_action
                await apply_sizing_to_action(db, action, strategy_id=source)
            except Exception:
                pass

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

            # Execute on Alpaca if portfolio is wired to paper/live
            if port_obj.execution_mode in ("paper", "live") and port_obj.alpaca_api_key:
                from app.services.trade_processor import _execute_on_alpaca
                asyncio.create_task(_execute_on_alpaca(
                    port_obj, ticker, round(qty, 4), "buy", price,
                    trade_id=sim_trade.id,
                ))

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
                if pos.entry_price and pos.entry_price > 0:
                    if pos.direction == "long":
                        pnl_pct = (current_price - pos.entry_price) / pos.entry_price * 100
                    else:
                        pnl_pct = (pos.entry_price - current_price) / pos.entry_price * 100
                else:
                    pnl_pct = 0.0

                hold_days = (utcnow() - pos.entry_time).days if pos.entry_time else 0

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
                    pos.exit_time = utcnow()
                    pos.status = "closed"

                    if pos.direction == "long":
                        pos.pnl_dollars = (current_price - pos.entry_price) * pos.qty
                    else:
                        pos.pnl_dollars = (pos.entry_price - current_price) * pos.qty

                    position_value = pos.entry_price * pos.qty
                    pos.pnl_percent = (pos.pnl_dollars / position_value * 100) if position_value > 0 else 0

                    portfolio.cash += position_value + pos.pnl_dollars

                    # Also mark the matching PortfolioHolding inactive so
                    # the Holdings list updates immediately. Without this
                    # the row lingered until Alpaca filled the async sell
                    # (or forever, if the fill failed), which is why the
                    # user saw "Henry closed X" in activity but no change
                    # on the portfolio page. If Alpaca later reports the
                    # position still open, the reconciler will reactivate.
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
                            h.notes = (h.notes or "") + f" | autonomous_exit:{exit_reason}"

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
                        resolved_at=utcnow(),
                    ))

                    from app.services.ai_service import save_context
                    asyncio.create_task(save_context(
                        content=f"AUTONOMOUS EXIT: {pos.ticker} {pos.direction.upper()} | PnL: {pos.pnl_percent:+.2f}% (${pos.pnl_dollars:+.2f}) | Reason: {exit_reason}",
                        context_type="outcome",
                        ticker=pos.ticker,
                        trade_id=pos.id,
                    ))

                    # Execute sell on Alpaca if portfolio is wired to paper/live
                    if portfolio.execution_mode in ("paper", "live") and portfolio.alpaca_api_key:
                        from app.services.trade_processor import _execute_on_alpaca
                        asyncio.create_task(_execute_on_alpaca(
                            portfolio, pos.ticker, pos.qty, "sell", current_price,
                            trade_id=pos.id,
                        ))

                    closed += 1
                    logger.info(f"Autonomous exit: {pos.ticker} | {exit_reason} | PnL: {pos.pnl_percent:+.2f}%")
                    from app.services.henry_activity import log_activity as _log_exit
                    await _log_exit(
                        f"CLOSED {pos.ticker} | PnL: {pos.pnl_percent:+.2f}% (${pos.pnl_dollars:+.2f}) | Reason: {exit_reason}",
                        "trade_exit", ticker=pos.ticker,
                    )

                # ── Manual holdings (PortfolioHolding rows) ────────────
                # Trade rows cover webhook/strategy positions; PortfolioHolding
                # rows cover manual entries + Alpaca sync. Without this
                # block Henry never considered selling manual holdings in
                # an AI-managed account (e.g. the ASTS position on the
                # Alpaca portfolio), so they drifted outside his risk
                # discipline. Same rule set as Trade above, minus stop
                # loss (holdings don't carry a stop_price).
                manual_closed = await _review_manual_holdings(
                    db, portfolio, get_quote, get_technical_indicator,
                )
                closed += manual_closed

                if closed > 0:
                    await _take_ai_snapshot(portfolio, db)

                await db.commit()
                total_closed += closed

        except Exception as e:
            logger.error(f"Autonomous exit check failed for {portfolio.name}: {e}")

    return total_closed


async def _review_manual_holdings(
    db, portfolio, get_quote, get_technical_indicator,
) -> int:
    """Apply the same exit rules to manual PortfolioHolding rows.

    Closes mark the holding inactive, credit cash for AI-managed local
    portfolios, log a PortfolioAction, and — when the portfolio is wired
    to paper/live — submit a SELL to Alpaca. Returns the number closed.
    """
    result = await db.execute(
        select(PortfolioHolding).where(
            PortfolioHolding.portfolio_id == portfolio.id,
            PortfolioHolding.is_active == True,
        )
    )
    holdings = list(result.scalars().all())
    if not holdings:
        return 0

    closed = 0
    for h in holdings:
        if not h.qty or h.qty <= 0:
            continue

        # Current price — prefer FMP quote when the USAGE cap allows,
        # fall back to the process-cached price_service, then entry_price.
        current_price = None
        try:
            quote = await get_quote(h.ticker)
            if quote and isinstance(quote, list) and len(quote) > 0:
                current_price = quote[0].get("price")
        except Exception:
            current_price = None
        if not current_price:
            current_price = price_service.get_price(h.ticker) or h.entry_price
        if not current_price:
            continue

        if h.direction == "long":
            pnl_pct = (current_price - h.entry_price) / h.entry_price * 100 if h.entry_price else 0.0
        else:
            pnl_pct = (h.entry_price - current_price) / h.entry_price * 100 if h.entry_price else 0.0

        hold_days = (utcnow().date() - h.entry_date.date()).days if h.entry_date else 0

        should_close = False
        exit_reason = ""

        # Profit target — match Trade rules.
        if pnl_pct >= 15:
            should_close = True
            exit_reason = f"profit_target ({pnl_pct:+.1f}%)"

        # Overbought exit on profitable longs.
        if not should_close and h.direction == "long" and pnl_pct > 0:
            try:
                rsi_data = await get_technical_indicator(h.ticker, "rsi", period=14, interval="daily")
                if rsi_data and isinstance(rsi_data, list) and len(rsi_data) > 0:
                    rsi = rsi_data[0].get("rsi") or rsi_data[0].get("value")
                    if rsi and rsi > 80:
                        should_close = True
                        exit_reason = f"overbought_exit (RSI {rsi:.0f}, P&L {pnl_pct:+.1f}%)"
            except Exception:
                pass

        # Loss limit.
        if not should_close and pnl_pct <= -8:
            should_close = True
            exit_reason = f"loss_limit ({pnl_pct:+.1f}%)"

        # Dead money.
        if not should_close and hold_days > 10 and pnl_pct < 2:
            should_close = True
            exit_reason = f"dead_money ({hold_days}d, {pnl_pct:+.1f}%)"

        if not should_close:
            continue

        # Close the holding locally.
        h.is_active = False
        h.notes = (h.notes or "") + f" | autonomous_exit:{exit_reason}"
        pnl_dollars = (
            (current_price - h.entry_price) if h.direction == "long"
            else (h.entry_price - current_price)
        ) * h.qty
        position_value = h.entry_price * h.qty
        portfolio.cash = (portfolio.cash or 0.0) + position_value + pnl_dollars

        db.add(PortfolioAction(
            portfolio_id=portfolio.id,
            ticker=h.ticker,
            direction=h.direction,
            action_type="CLOSE",
            confidence=7,
            reasoning=f"[Autonomous Exit — Manual Holding] {exit_reason}",
            trigger_type="SCANNER",
            current_price=current_price,
            priority_score=10.5,
            status="approved",
            resolved_at=utcnow(),
        ))

        from app.services.ai_service import save_context
        import asyncio as _asyncio
        _asyncio.create_task(save_context(
            content=(
                f"AUTONOMOUS EXIT (manual): {h.ticker} {h.direction.upper()} | "
                f"PnL: {pnl_pct:+.2f}% (${pnl_dollars:+.2f}) | Reason: {exit_reason}"
            ),
            context_type="outcome",
            ticker=h.ticker,
        ))

        # Send the sell to Alpaca when the portfolio is wired to it.
        if (portfolio.execution_mode or "local").lower() in ("paper", "live") and portfolio.alpaca_api_key:
            from app.services.trade_processor import _execute_on_alpaca
            _asyncio.create_task(_execute_on_alpaca(
                portfolio, h.ticker, h.qty, "sell", current_price,
            ))

        from app.services.henry_activity import log_activity as _log_exit
        try:
            await _log_exit(
                f"CLOSED {h.ticker} (manual) | PnL: {pnl_pct:+.2f}% (${pnl_dollars:+.2f}) | Reason: {exit_reason}",
                "trade_exit", ticker=h.ticker,
            )
        except Exception:
            pass

        closed += 1
        logger.info(f"Autonomous exit (manual): {h.ticker} | {exit_reason} | PnL: {pnl_pct:+.2f}%")

    return closed


# ══════════════════════════════════════════════════════════════════════
# Options execution helper (Step 2C)
# ══════════════════════════════════════════════════════════════════════

async def _try_execute_autonomous_options(
    db,
    portfolio: Portfolio,
    ticker: str,
    direction: str,
    confidence: int,
    reasoning: str,
    source: str,
    options_rec: dict,
) -> bool:
    """Submit the options recommendation via the execution API. Returns
    True on success (order submitted or recorded locally) and records a
    PortfolioAction with instrument_type='options' + the full recommendation
    stored in the options_strategy JSON column.

    Returns False on any failure — Alpaca rejection, insufficient buying
    power, broker errors — so the caller can fall back to equity. This
    helper never raises; it swallows and logs.

    The caller's db session is used for the PortfolioAction write. We
    commit only on success to avoid leaving a half-populated action row
    when Alpaca fails.
    """
    try:
        from app.services.alpaca_service import alpaca_service
        import uuid as _uuid
        from datetime import date as _date
        from app.models.options_trade import OptionsTrade

        legs = options_rec.get("legs") or []
        if not legs:
            return False

        # Compute net limit price from legs (debit positive, credit negative).
        net_debit = options_rec.get("net_debit")
        net_credit = options_rec.get("net_credit")
        if net_debit is not None:
            limit_price = float(net_debit)
        elif net_credit is not None:
            limit_price = -float(net_credit)
        else:
            # Sum mid prices across legs as fallback
            lp = 0.0
            for leg in legs:
                sign = 1 if leg.get("action", "").lower() == "buy" else -1
                lp += sign * float(leg.get("premium") or 0.0) * int(leg.get("quantity", 1))
            limit_price = lp

        # Route to Alpaca (paper/live) or record locally
        exec_mode = (portfolio.execution_mode or "local").lower()
        api_key = portfolio.alpaca_api_key_decrypted
        secret_key = portfolio.alpaca_secret_key_decrypted
        paper = exec_mode == "paper"

        submit_ok = True
        submit_payload: dict = {"status": "local-only"}

        if exec_mode in ("paper", "live") and api_key and secret_key:
            if len(legs) == 1:
                leg = legs[0]
                submit_payload = await alpaca_service.submit_options_order(
                    api_key=api_key, secret_key=secret_key, paper=paper,
                    option_symbol=leg.get("option_symbol"),
                    qty=int(leg.get("quantity", 1)),
                    side=leg.get("action", "buy"),
                    limit_price=abs(limit_price) or float(leg.get("premium") or 0.0),
                )
            else:
                submit_payload = await alpaca_service.submit_multi_leg_order(
                    api_key=api_key, secret_key=secret_key, paper=paper,
                    legs=[
                        {
                            "option_symbol": l.get("option_symbol"),
                            "qty": int(l.get("quantity", 1)),
                            "side": l.get("action", "buy"),
                        }
                        for l in legs
                    ],
                    limit_price=limit_price,
                )
            if submit_payload.get("status") == "error":
                logger.warning(
                    f"Autonomous options: Alpaca rejected {ticker} "
                    f"{options_rec.get('strategy_type')}: "
                    f"{submit_payload.get('message')}"
                )
                return False

        # Success — record the action + per-leg OptionsTrade rows.
        alpaca_order_id = submit_payload.get("order_id")
        spread_group_id = str(_uuid.uuid4()) if len(legs) > 1 else None

        action = PortfolioAction(
            portfolio_id=portfolio.id,
            ticker=ticker,
            direction=direction,
            action_type="BUY",
            suggested_price=float(limit_price) if limit_price else None,
            current_price=float(limit_price) if limit_price else None,
            confidence=confidence,
            reasoning=(
                f"[Autonomous options - {source}] "
                f"{options_rec.get('strategy_type')} "
                f"{options_rec.get('expiration')}: "
                f"{reasoning[:400]}"
            ),
            trigger_type="SCANNER",
            trigger_ref=alpaca_order_id,
            priority_score=confidence * 1.5,
            status="approved",
            resolved_at=utcnow(),
            instrument_type="options",
            options_strategy=options_rec,
        )
        db.add(action)

        # Parse expiration once
        try:
            y, m, d = (options_rec.get("expiration") or "").split("-")
            exp_date = _date(int(y), int(m), int(d))
        except Exception:
            exp_date = _date.today()

        for leg in legs:
            db.add(OptionsTrade(
                portfolio_id=portfolio.id,
                ticker=ticker,
                option_symbol=leg.get("option_symbol") or "",
                option_type=leg.get("type", "call"),
                strike=float(leg.get("strike") or 0.0),
                expiration=exp_date,
                direction="long" if leg.get("action") == "buy" else "short",
                quantity=int(leg.get("quantity", 1)),
                entry_premium=float(leg.get("premium") or 0.0),
                strategy_type=options_rec.get("strategy_type") or "unknown",
                spread_group_id=spread_group_id,
                alpaca_order_id=alpaca_order_id,
                notes=f"autonomous:{source}",
            ))

        await db.commit()
        logger.info(
            f"Autonomous options: submitted {options_rec.get('strategy_type')} "
            f"for {ticker} (conf {confidence}, source={source}, "
            f"score={options_rec.get('score'):.2f})"
        )
        return True
    except Exception as e:
        logger.warning(f"Autonomous options submission failed for {ticker}: {e}")
        try:
            await db.rollback()
        except Exception:
            pass
        return False
