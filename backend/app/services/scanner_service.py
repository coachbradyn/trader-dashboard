"""
Scanner Service
===============
Automated stock scanning pipeline:
1. Screen stocks via FMP screener API
2. Fetch technical snapshots for top candidates
3. Filter to actionable setups (oversold or trending)
4. Enrich with cached fundamentals
5. Send shortlist to Claude for AI-powered opportunity ranking
6. Create PortfolioAction entries with action_type=OPPORTUNITY

Scanner criteria stored in henry_cache with cache_type="scanner_config".
"""

import json
import logging
from datetime import datetime, timedelta

from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import async_session
from app.models.portfolio_action import PortfolioAction
from app.models.portfolio import Portfolio

logger = logging.getLogger(__name__)

# ══════════════════════════════════════════════════════════════════════
# DEFAULT SCANNER CRITERIA
# ══════════════════════════════════════════════════════════════════════

DEFAULT_SCANNER_CRITERIA = {
    "min_price": 5.0,
    "min_volume": 500000,
    "min_market_cap": 500000000,
    "max_market_cap": None,
    "sectors": [],  # empty = all
    "technical_filters": {
        "oversold_rsi": 35,
        "trending_rsi_min": 50,
        "trending_adx_min": 20,
    },
    "fundamental_filters": {
        "prefer_analyst_buy": True,
        "prefer_insider_buying": True,
        "flag_earnings_within_days": 5,
    },
}

SCANNER_CACHE_KEY = "scanner:criteria"


# ══════════════════════════════════════════════════════════════════════
# CRITERIA MANAGEMENT
# ══════════════════════════════════════════════════════════════════════

async def get_scanner_criteria() -> dict:
    """Get scanner criteria from henry_cache or return defaults."""
    try:
        from app.models.henry_cache import HenryCache

        async with async_session() as db:
            result = await db.execute(
                select(HenryCache).where(HenryCache.cache_key == SCANNER_CACHE_KEY)
            )
            entry = result.scalar_one_or_none()
            if entry and entry.content:
                return entry.content
    except Exception as e:
        logger.debug(f"Error reading scanner criteria: {e}")
    return DEFAULT_SCANNER_CRITERIA.copy()


async def update_scanner_criteria(criteria: dict) -> dict:
    """Update scanner criteria in henry_cache. Merges with defaults."""
    from app.models.henry_cache import HenryCache

    merged = DEFAULT_SCANNER_CRITERIA.copy()
    # Deep merge top-level and nested dicts
    for key, value in criteria.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
            merged[key].update(value)
        else:
            merged[key] = value

    try:
        async with async_session() as db:
            result = await db.execute(
                select(HenryCache).where(HenryCache.cache_key == SCANNER_CACHE_KEY)
            )
            existing = result.scalar_one_or_none()
            if existing:
                existing.content = merged
                existing.cache_type = "scanner_config"
                existing.generated_at = datetime.utcnow()
            else:
                entry = HenryCache(
                    cache_key=SCANNER_CACHE_KEY,
                    cache_type="scanner_config",
                    content=merged,
                )
                db.add(entry)
            await db.commit()
    except Exception as e:
        logger.error(f"Error saving scanner criteria: {e}")

    return merged


# ══════════════════════════════════════════════════════════════════════
# MAIN SCANNER PIPELINE
# ══════════════════════════════════════════════════════════════════════

async def run_scanner() -> list[dict]:
    """
    Full scanner pipeline:
    1. Get criteria
    2. Run FMP screener
    3. Fetch technicals for top 20
    4. Filter actionable setups
    5. Check fundamentals
    6. AI analysis
    7. Create OPPORTUNITY actions
    """
    from app.services.fmp_service import (
        run_screener, get_technical_snapshot, get_fundamentals,
        format_fundamentals_for_prompt, get_api_usage,
    )

    # Check if FMP API is available
    usage = get_api_usage()
    if usage["throttled"]:
        logger.warning("Scanner skipped: FMP API throttled")
        return []

    # 1. Get criteria
    criteria = await get_scanner_criteria()
    logger.info(f"Running scanner with criteria: min_price={criteria.get('min_price')}, min_vol={criteria.get('min_volume')}")

    # 2. Build screener params and call FMP
    screener_params: dict = {
        "priceMoreThan": str(criteria.get("min_price", 5)),
        "volumeMoreThan": str(criteria.get("min_volume", 500000)),
        "marketCapMoreThan": str(criteria.get("min_market_cap", 500000000)),
        "limit": "50",
        "exchange": "NYSE,NASDAQ",
    }
    if criteria.get("max_market_cap"):
        screener_params["marketCapLowerThan"] = str(criteria["max_market_cap"])
    if criteria.get("sectors"):
        screener_params["sector"] = ",".join(criteria["sectors"])

    screener_results = await run_screener(screener_params)
    if not screener_results or not isinstance(screener_results, list):
        logger.info("Scanner: no screener results")
        return []

    logger.info(f"Scanner: {len(screener_results)} stocks from screener")

    # 3. Fetch technical snapshots for top 20
    top_candidates = screener_results[:20]
    snapshots = []
    for stock in top_candidates:
        ticker = stock.get("symbol")
        if not ticker:
            continue
        try:
            snap = await get_technical_snapshot(ticker)
            snap["screener_data"] = stock
            snapshots.append(snap)
        except Exception as e:
            logger.debug(f"Scanner: failed to get snapshot for {ticker}: {e}")

    if not snapshots:
        logger.info("Scanner: no technical snapshots obtained")
        return []

    # 4. Filter to actionable setups
    tech_filters = criteria.get("technical_filters", {})
    oversold_rsi = tech_filters.get("oversold_rsi", 35)
    trending_rsi_min = tech_filters.get("trending_rsi_min", 50)
    trending_adx_min = tech_filters.get("trending_adx_min", 20)

    actionable = []
    for snap in snapshots:
        rsi = snap.get("rsi")
        adx = snap.get("adx")
        if rsi is None:
            continue

        is_oversold = rsi < oversold_rsi
        is_trending = rsi > trending_rsi_min and adx is not None and adx > trending_adx_min

        if is_oversold or is_trending:
            snap["setup_type"] = "oversold" if is_oversold else "trending"
            actionable.append(snap)

    actionable = actionable[:15]
    logger.info(f"Scanner: {len(actionable)} actionable setups after technical filter")

    if not actionable:
        return []

    # 5. Enrich with cached fundamentals
    for snap in actionable:
        ticker = snap.get("ticker")
        if ticker:
            fund = await get_fundamentals(ticker)
            if fund:
                snap["fundamentals_summary"] = format_fundamentals_for_prompt(fund)
            else:
                snap["fundamentals_summary"] = "No cached fundamentals."

    # 6. Build prompt and call AI
    opportunities = await _ai_analyze_candidates(actionable)

    # 7. Create PortfolioAction entries
    created_actions = await _create_opportunity_actions(opportunities)

    logger.info(f"Scanner complete: {len(created_actions)} opportunities created")
    return created_actions


async def _ai_analyze_candidates(candidates: list[dict]) -> list[dict]:
    """Send candidate list to Claude for AI-powered ranking and analysis."""
    try:
        from app.services.ai_service import _call_claude_async
    except ImportError:
        logger.warning("AI service not available, returning candidates as-is")
        return [
            {
                "ticker": c.get("ticker", ""),
                "direction": "long",
                "confidence": 5,
                "reasoning": f"Technical setup: {c.get('setup_type', 'unknown')}. RSI={c.get('rsi')}, ADX={c.get('adx')}",
                "suggested_price": c.get("price"),
                "setup_type": c.get("setup_type", "unknown"),
            }
            for c in candidates[:5]
        ]

    # Build concise candidate summaries
    candidate_text = ""
    for i, c in enumerate(candidates, 1):
        sd = c.get("screener_data", {})
        rsi_val = c.get("rsi")
        rsi_str = f"{rsi_val:.1f}" if isinstance(rsi_val, (int, float)) else "N/A"
        line = f"\n{i}. {c.get('ticker')} - ${c.get('price', 'N/A')} | RSI: {rsi_str}"
        if c.get("adx") and isinstance(c["adx"], (int, float)):
            line += f" | ADX: {c['adx']:.1f}"
        if c.get("sma200") and c.get("price"):
            try:
                vs_sma = (c["price"] - c["sma200"]) / c["sma200"] * 100
                line += f" | vs SMA200: {vs_sma:+.1f}%"
            except (TypeError, ZeroDivisionError):
                pass
        line += f" | Setup: {c.get('setup_type', 'unknown')}"
        if sd.get("marketCap"):
            line += f" | Mkt Cap: ${sd['marketCap'] / 1e9:.1f}B"
        line += f"\n   {c.get('fundamentals_summary', 'No fundamentals.')}\n"
        candidate_text += line

    prompt = f"""You are a stock scanner AI. Analyze these candidates and select the best trading opportunities.

CANDIDATES:
{candidate_text}

For each opportunity worth pursuing, provide:
- Why this is a good setup (technical + fundamental reasoning)
- Suggested entry approach (limit price, or market)
- Confidence level (1-10)
- Direction (long/short)

Respond with a JSON array of opportunities. Each object:
{{"ticker": "AAPL", "direction": "long", "confidence": 7, "reasoning": "2-3 sentences", "suggested_price": 150.00, "setup_type": "oversold"}}

Return only the top 5-8 best opportunities. Empty array if nothing compelling.
No markdown, no backticks. Just the JSON array."""

    try:
        raw = await _call_claude_async(
            prompt,
            max_tokens=2000,
            scope="scanner",
            function_name="scanner",
            enable_web_search=True,
        )

        clean = raw.strip().replace("```json", "").replace("```", "").strip()
        result = json.loads(clean)
        if not isinstance(result, list):
            result = [result]
        return result

    except json.JSONDecodeError:
        logger.warning(f"Scanner AI: failed to parse JSON response")
        return []
    except Exception as e:
        logger.error(f"Scanner AI analysis failed: {e}")
        return []


async def _create_opportunity_actions(opportunities: list[dict]) -> list[dict]:
    """Create PortfolioAction entries with action_type=OPPORTUNITY for each opportunity."""
    if not opportunities:
        return []

    created = []
    try:
        async with async_session() as db:
            # Get a default portfolio to attach opportunities to
            result = await db.execute(
                select(Portfolio).where(Portfolio.is_active == True).limit(1)
            )
            portfolio = result.scalar_one_or_none()
            if not portfolio:
                logger.warning("Scanner: no active portfolio found for opportunities")
                return []

            for opp in opportunities:
                ticker = opp.get("ticker", "")
                if not ticker:
                    continue

                confidence = min(max(int(opp.get("confidence", 5)), 1), 10)
                expiry = datetime.utcnow() + timedelta(hours=24)

                action = PortfolioAction(
                    portfolio_id=portfolio.id,
                    ticker=ticker,
                    direction=opp.get("direction", "long"),
                    action_type="OPPORTUNITY",
                    suggested_price=opp.get("suggested_price"),
                    current_price=opp.get("suggested_price"),
                    confidence=confidence,
                    reasoning=opp.get("reasoning", "Scanner opportunity"),
                    trigger_type="SCANNER",
                    priority_score=round(1.5 * confidence, 1),  # Scanner weight = 1.5
                    expires_at=expiry,
                )
                db.add(action)
                created.append({
                    "ticker": ticker,
                    "direction": opp.get("direction", "long"),
                    "confidence": confidence,
                    "reasoning": opp.get("reasoning", ""),
                    "suggested_price": opp.get("suggested_price"),
                    "setup_type": opp.get("setup_type", "unknown"),
                    "expires_at": expiry.isoformat(),
                })

            await db.commit()

    except Exception as e:
        logger.error(f"Failed to create opportunity actions: {e}")

    return created


# ══════════════════════════════════════════════════════════════════════
# RESULTS & STATS
# ══════════════════════════════════════════════════════════════════════

async def get_scanner_results(limit: int = 20) -> list[dict]:
    """Return recent OPPORTUNITY actions (pending)."""
    try:
        async with async_session() as db:
            result = await db.execute(
                select(PortfolioAction)
                .where(
                    PortfolioAction.action_type == "OPPORTUNITY",
                    PortfolioAction.trigger_type == "SCANNER",
                    PortfolioAction.status == "pending",
                )
                .order_by(PortfolioAction.created_at.desc())
                .limit(limit)
            )
            actions = result.scalars().all()

            return [
                {
                    "id": a.id,
                    "ticker": a.ticker,
                    "direction": a.direction,
                    "confidence": a.confidence,
                    "reasoning": a.reasoning,
                    "suggested_price": a.suggested_price,
                    "current_price": a.current_price,
                    "priority_score": a.priority_score,
                    "status": a.status,
                    "expires_at": a.expires_at.isoformat() if a.expires_at else None,
                    "created_at": a.created_at.isoformat(),
                }
                for a in actions
            ]
    except Exception as e:
        logger.error(f"Error fetching scanner results: {e}")
        return []


async def get_scanner_history(limit: int = 50) -> list[dict]:
    """Return past scanner results (all statuses) with outcomes."""
    try:
        async with async_session() as db:
            result = await db.execute(
                select(PortfolioAction)
                .where(
                    PortfolioAction.action_type == "OPPORTUNITY",
                    PortfolioAction.trigger_type == "SCANNER",
                )
                .order_by(PortfolioAction.created_at.desc())
                .limit(limit)
            )
            actions = result.scalars().all()

            return [
                {
                    "id": a.id,
                    "ticker": a.ticker,
                    "direction": a.direction,
                    "confidence": a.confidence,
                    "reasoning": a.reasoning,
                    "suggested_price": a.suggested_price,
                    "current_price": a.current_price,
                    "priority_score": a.priority_score,
                    "status": a.status,
                    "outcome_pnl": a.outcome_pnl,
                    "outcome_correct": a.outcome_correct,
                    "expires_at": a.expires_at.isoformat() if a.expires_at else None,
                    "created_at": a.created_at.isoformat(),
                    "resolved_at": a.resolved_at.isoformat() if a.resolved_at else None,
                }
                for a in actions
            ]
    except Exception as e:
        logger.error(f"Error fetching scanner history: {e}")
        return []


async def get_scanner_stats() -> dict:
    """Compute scanner accuracy stats from outcome tracking."""
    try:
        async with async_session() as db:
            # Total opportunities
            total_result = await db.execute(
                select(func.count(PortfolioAction.id)).where(
                    PortfolioAction.action_type == "OPPORTUNITY",
                    PortfolioAction.trigger_type == "SCANNER",
                )
            )
            total = total_result.scalar() or 0

            # Approved / acted upon
            approved_result = await db.execute(
                select(func.count(PortfolioAction.id)).where(
                    PortfolioAction.action_type == "OPPORTUNITY",
                    PortfolioAction.trigger_type == "SCANNER",
                    PortfolioAction.status == "approved",
                )
            )
            approved = approved_result.scalar() or 0

            # With outcomes
            outcome_result = await db.execute(
                select(PortfolioAction).where(
                    PortfolioAction.action_type == "OPPORTUNITY",
                    PortfolioAction.trigger_type == "SCANNER",
                    PortfolioAction.outcome_correct.isnot(None),
                )
            )
            outcomes = outcome_result.scalars().all()

            correct = sum(1 for o in outcomes if o.outcome_correct)
            total_outcomes = len(outcomes)
            avg_pnl = (
                sum(o.outcome_pnl or 0 for o in outcomes) / total_outcomes
                if total_outcomes > 0 else 0
            )
            accuracy = (correct / total_outcomes * 100) if total_outcomes > 0 else 0

            # Pending (not expired)
            pending_result = await db.execute(
                select(func.count(PortfolioAction.id)).where(
                    PortfolioAction.action_type == "OPPORTUNITY",
                    PortfolioAction.trigger_type == "SCANNER",
                    PortfolioAction.status == "pending",
                    PortfolioAction.expires_at > datetime.utcnow(),
                )
            )
            pending = pending_result.scalar() or 0

            # Expired
            expired_result = await db.execute(
                select(func.count(PortfolioAction.id)).where(
                    PortfolioAction.action_type == "OPPORTUNITY",
                    PortfolioAction.trigger_type == "SCANNER",
                    PortfolioAction.status == "expired",
                )
            )
            expired = expired_result.scalar() or 0

            # By confidence level
            confidence_breakdown = {}
            if outcomes:
                for o in outcomes:
                    conf = o.confidence
                    if conf not in confidence_breakdown:
                        confidence_breakdown[conf] = {"total": 0, "correct": 0}
                    confidence_breakdown[conf]["total"] += 1
                    if o.outcome_correct:
                        confidence_breakdown[conf]["correct"] += 1

            return {
                "total_opportunities": total,
                "approved": approved,
                "pending_active": pending,
                "expired": expired,
                "outcomes_tracked": total_outcomes,
                "correct": correct,
                "accuracy_pct": round(accuracy, 1),
                "avg_pnl_pct": round(avg_pnl, 2),
                "by_confidence": confidence_breakdown,
            }

    except Exception as e:
        logger.error(f"Error computing scanner stats: {e}")
        return {
            "total_opportunities": 0,
            "error": str(e),
        }
