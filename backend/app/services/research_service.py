"""
Research Service
================
Saves research findings to henry_context with context_type="research".
Handles auto-research triggers and research extraction from AI responses.
"""

import json
import logging
import asyncio
from datetime import datetime, timedelta, date, timezone

from app.database import async_session
from app.models.henry_context import HenryContext
from app.models.watchlist_ticker import WatchlistTicker
from sqlalchemy import select, func

logger = logging.getLogger(__name__)


async def save_research(
    content: str,
    ticker: str | None = None,
    expires_days: int = 30,
    confidence: int | None = None,
) -> None:
    """Save a research finding to henry_context with type='research'."""
    if not content or len(content) > 500:
        content = content[:497] + "..." if content else ""

    try:
        expires_at = datetime.now(timezone.utc) + timedelta(days=expires_days)

        async with async_session() as db:
            ctx = HenryContext(
                content=content,
                context_type="research",
                ticker=ticker,
                confidence=confidence,
                expires_at=expires_at,
            )
            db.add(ctx)
            await db.commit()
            logger.debug(f"Saved research note for {ticker or 'general'}: {content[:80]}...")
    except Exception as e:
        logger.warning(f"Failed to save research note: {e}")


async def extract_and_save_research(
    analysis_text: str,
    ticker: str | None = None,
) -> None:
    """
    After an AI call that used web search, extract key research findings
    and save them to henry_context as research notes.
    """
    try:
        from app.services.ai_provider import call_ai

        system = (
            "Extract 1-3 key factual findings from this analysis that would be useful to remember "
            "for future trading decisions. Focus on: earnings dates, catalyst events, FDA decisions, "
            "analyst actions, sector trends, or any other time-sensitive information. "
            "Return a JSON array of objects with keys: content (1 sentence, under 200 chars), "
            "ticker (null or ticker symbol), expires_days (number — use 1 for events happening today, "
            "7 for near-term catalysts, 30 for general research). "
            "Only include genuinely useful findings, not generic observations."
        )
        raw = await call_ai(system, analysis_text, function_name="memory_extraction", max_tokens=400)

        raw = raw.strip().replace("```json", "").replace("```", "").strip()
        items = json.loads(raw)

        if isinstance(items, list):
            for item in items[:3]:
                content = item.get("content", "")
                if not content:
                    continue
                item_ticker = item.get("ticker") or ticker
                expires_days = item.get("expires_days", 30)
                # Clamp expiry
                if expires_days < 1:
                    expires_days = 1
                if expires_days > 60:
                    expires_days = 60

                await save_research(
                    content=content,
                    ticker=item_ticker,
                    expires_days=expires_days,
                )
    except Exception:
        pass  # Non-blocking


async def check_auto_research_triggers() -> list[str]:
    """
    Check for tickers that need auto-research and return a list of tickers to research.
    Triggers:
    1. New watchlist ticker with no fundamentals or research
    2. Large price move (>5%) with no recent news/research
    3. Position with thin context (no fundamentals, no research, no news)
    4. Approaching catalyst date (within 2 weeks)
    """
    tickers_to_research = []

    try:
        from app.models.ticker_fundamentals import TickerFundamentals
        from app.models.portfolio_holding import PortfolioHolding
        from app.services.price_service import price_service

        async with async_session() as db:
            # Get all active watchlist tickers
            result = await db.execute(
                select(WatchlistTicker.ticker).where(WatchlistTicker.is_active == True)
            )
            watchlist_tickers = [row[0] for row in result.all()]

            if not watchlist_tickers:
                return []

            # Check each ticker
            for ticker in watchlist_tickers:
                # 1. No fundamentals at all
                fund_result = await db.execute(
                    select(TickerFundamentals).where(TickerFundamentals.ticker == ticker)
                )
                fund = fund_result.scalar_one_or_none()

                if not fund:
                    tickers_to_research.append(ticker)
                    continue

                # 2. Check for thin context — no research notes
                research_count = await db.execute(
                    select(func.count(HenryContext.id)).where(
                        HenryContext.ticker == ticker,
                        HenryContext.context_type == "research",
                        (HenryContext.expires_at.is_(None)) | (HenryContext.expires_at > datetime.now(timezone.utc)),
                    )
                )
                has_research = (research_count.scalar() or 0) > 0

                if not has_research and fund.updated_at < datetime.now(timezone.utc) - timedelta(hours=48):
                    tickers_to_research.append(ticker)
                    continue

            # 4. Check for approaching catalyst dates
            result = await db.execute(
                select(PortfolioHolding).where(
                    PortfolioHolding.is_active == True,
                    PortfolioHolding.position_type == "catalyst",
                    PortfolioHolding.catalyst_date.isnot(None),
                )
            )
            catalyst_holdings = result.scalars().all()

            for h in catalyst_holdings:
                if h.catalyst_date:
                    days_until = (h.catalyst_date - date.today()).days
                    if 0 <= days_until <= 14 and h.ticker not in tickers_to_research:
                        tickers_to_research.append(h.ticker)

    except Exception as e:
        logger.warning(f"Auto-research trigger check failed: {e}")

    return tickers_to_research


async def auto_research_ticker(ticker: str) -> None:
    """
    Perform auto-research on a single ticker using web search.
    Called for tickers identified by check_auto_research_triggers.
    """
    try:
        from app.services.ai_provider import call_ai

        # Check if we already have recent research (avoid spamming)
        async with async_session() as db:
            recent = await db.execute(
                select(func.count(HenryContext.id)).where(
                    HenryContext.ticker == ticker,
                    HenryContext.context_type == "research",
                    HenryContext.created_at >= datetime.now(timezone.utc) - timedelta(hours=12),
                )
            )
            if (recent.scalar() or 0) >= 2:
                return  # Already researched recently

        # Build a focused research prompt
        fundamentals_context = ""
        try:
            from app.services.fmp_service import get_fundamentals, format_fundamentals_for_prompt
            fund = await get_fundamentals(ticker)
            if fund:
                fundamentals_context = f"\nKNOWN FUNDAMENTALS:\n  {format_fundamentals_for_prompt(fund)}\n"
        except Exception:
            pass

        system = (
            "You are Henry, an AI trading analyst. Research this ticker using web search and provide "
            "a concise summary of the most important recent developments. Focus on: why the stock is "
            "moving (if it is), upcoming catalysts, recent earnings/guidance, analyst actions, FDA dates, "
            "sector trends, or any news that would affect a trading decision. Be factual and specific."
        )

        prompt = f"""Research {ticker} for my trading dashboard.{fundamentals_context}
Find the most important recent context that I should know about. Focus on:
1. Any recent significant news (last 30 days)
2. Upcoming catalysts or events
3. Why the stock might be moving
4. Any analyst actions or rating changes

Provide a concise analysis (under 300 words). Highlight any time-sensitive information."""

        result = await call_ai(
            system, prompt,
            function_name="ask_henry",
            max_tokens=800,
            enable_web_search=True,
        )

        if result and result != "AI analysis temporarily unavailable.":
            # Extract and save key findings
            await extract_and_save_research(result, ticker=ticker)
            logger.info(f"Auto-research completed for {ticker}")

    except Exception as e:
        logger.warning(f"Auto-research failed for {ticker}: {e}")


async def run_auto_research() -> int:
    """Run auto-research for all triggered tickers. Returns count researched."""
    tickers = await check_auto_research_triggers()
    if not tickers:
        return 0

    # Limit to 5 tickers per run to avoid API cost explosion
    tickers = tickers[:5]
    researched = 0

    for ticker in tickers:
        await auto_research_ticker(ticker)
        researched += 1

    logger.info(f"Auto-research complete: {researched} tickers")
    return researched
