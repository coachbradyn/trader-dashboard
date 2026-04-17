from datetime import timezone
"""
News API Endpoints
==================
Serves cached news, sentiment, company info, and bull/bear thesis.
"""

import logging
from app.utils.utc import utcnow
from fastapi import APIRouter, Query
from app.services.news_service import news_service, get_company_description

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/news")
async def get_news(
    ticker: str | None = Query(None, description="Filter by ticker symbol"),
    limit: int = Query(20, ge=1, le=100),
    hours: int = Query(24, ge=1, le=168),
):
    """Get cached news articles, optionally filtered by ticker."""
    articles = await news_service.get_cached_news(
        ticker=ticker, limit=limit, hours=hours
    )
    return {"articles": articles, "count": len(articles)}


@router.get("/news/ticker/{ticker}")
async def get_ticker_news(ticker: str):
    """Get news, sentiment, and company info for a specific ticker.
    Falls back to FMP news when Alpaca has nothing, and generates
    synthetic sector context as a last resort."""
    # Accept both "NVDA" and "NASDAQ:NVDA" — strip any exchange prefix.
    ticker = ticker.upper().strip().split(":")[-1]

    # Fetch all sources in parallel
    import asyncio

    headlines_task = news_service.get_ticker_headlines(ticker, limit=10)
    sentiment_task = news_service.get_news_sentiment(ticker)
    company_task = get_company_description(ticker)

    headlines, sentiment, company = await asyncio.gather(
        headlines_task, sentiment_task, company_task,
        return_exceptions=True,
    )

    # Handle any individual failures gracefully
    if isinstance(headlines, Exception):
        headlines = []
    if isinstance(sentiment, Exception):
        sentiment = {"score": 0.0, "label": "Neutral", "article_count": 0}
    if isinstance(company, Exception):
        company = {
            "name": ticker,
            "sector": None,
            "industry": None,
            "market_cap": None,
            "description": None,
            "high_52w": None,
            "low_52w": None,
        }

    # ── Fallback 1: FMP news if Alpaca returned nothing ──
    if not headlines:
        try:
            from app.services.fmp_service import get_stock_news, get_press_releases
            fmp_news = await get_stock_news(ticker, limit=10)
            if fmp_news and isinstance(fmp_news, list) and len(fmp_news) > 0:
                for article in fmp_news[:10]:
                    headlines.append({
                        "id": article.get("url", ""),
                        "headline": article.get("title", ""),
                        "summary": article.get("text", "")[:200] if article.get("text") else None,
                        "source": article.get("site", "FMP"),
                        "tickers": [ticker],
                        "published_at": article.get("publishedDate", ""),
                        "url": article.get("url"),
                        "sentiment_score": None,
                    })
                logger.info(f"News: used FMP fallback for {ticker}, got {len(headlines)} articles")
        except Exception as e:
            logger.debug(f"FMP news fallback failed for {ticker}: {e}")

    # ── Fallback 2: FMP press releases if still nothing ──
    if not headlines:
        try:
            from app.services.fmp_service import get_press_releases
            releases = await get_press_releases(ticker, limit=5)
            if releases and isinstance(releases, list):
                for pr in releases[:5]:
                    headlines.append({
                        "id": pr.get("url", ""),
                        "headline": pr.get("title", f"{ticker} Press Release"),
                        "summary": pr.get("text", "")[:200] if pr.get("text") else None,
                        "source": "Press Release",
                        "tickers": [ticker],
                        "published_at": pr.get("date", ""),
                        "url": pr.get("url"),
                        "sentiment_score": None,
                    })
        except Exception:
            pass

    # ── Fallback 3: Generate synthetic context from fundamentals ──
    if not headlines:
        try:
            from app.services.fmp_service import get_fundamentals
            fund = await get_fundamentals(ticker)
            synthetic_notes = []

            if fund:
                # Sector context
                if fund.sector and fund.industry:
                    synthetic_notes.append({
                        "id": f"synthetic-sector-{ticker}",
                        "headline": f"{fund.company_name or ticker} operates in {fund.sector} ({fund.industry})",
                        "summary": fund.description or f"{fund.company_name or ticker} is a {fund.industry} company in the {fund.sector} sector.",
                        "source": "Market Data",
                        "tickers": [ticker],
                        "published_at": (fund.updated_at.isoformat() + "Z") if fund.updated_at else None,
                        "url": None,
                        "sentiment_score": None,
                    })

                # Earnings context
                if fund.earnings_date:
                    from datetime import date as date_type
                    days_until = (fund.earnings_date - date_type.today()).days
                    if days_until >= 0:
                        synthetic_notes.append({
                            "id": f"synthetic-earnings-{ticker}",
                            "headline": f"{ticker} reports earnings in {days_until} days ({fund.earnings_date})",
                            "summary": f"EPS estimate: ${fund.eps_estimate_current:.2f}" if fund.eps_estimate_current else None,
                            "source": "Earnings Calendar",
                            "tickers": [ticker],
                            "published_at": (fund.updated_at.isoformat() + "Z") if fund.updated_at else None,
                            "url": None,
                            "sentiment_score": None,
                        })

                # Analyst context
                if fund.analyst_rating and fund.analyst_target_consensus:
                    synthetic_notes.append({
                        "id": f"synthetic-analyst-{ticker}",
                        "headline": f"Analyst consensus: {fund.analyst_rating} — target ${fund.analyst_target_consensus:.2f}",
                        "summary": f"{fund.analyst_count or '?'} analysts covering. Target range ${fund.analyst_target_low:.2f}-${fund.analyst_target_high:.2f}" if fund.analyst_target_low and fund.analyst_target_high else None,
                        "source": "Analyst Data",
                        "tickers": [ticker],
                        "published_at": (fund.updated_at.isoformat() + "Z") if fund.updated_at else None,
                        "url": None,
                        "sentiment_score": None,
                    })

                # DCF context
                if getattr(fund, "dcf_value", None) and getattr(fund, "dcf_diff_pct", None):
                    status = "undervalued" if fund.dcf_diff_pct > 0 else "overvalued"
                    synthetic_notes.append({
                        "id": f"synthetic-dcf-{ticker}",
                        "headline": f"DCF model suggests {ticker} is {abs(fund.dcf_diff_pct):.0f}% {status} (fair value ${fund.dcf_value:.2f})",
                        "summary": None,
                        "source": "Valuation Model",
                        "tickers": [ticker],
                        "published_at": (fund.updated_at.isoformat() + "Z") if fund.updated_at else None,
                        "url": None,
                        "sentiment_score": 0.1 if fund.dcf_diff_pct > 0 else -0.1,
                    })

                # Insider context
                if getattr(fund, "insider_net_90d", None) and fund.insider_net_90d != 0:
                    action = "buying" if fund.insider_net_90d > 0 else "selling"
                    synthetic_notes.append({
                        "id": f"synthetic-insider-{ticker}",
                        "headline": f"Insider net {action}: ${abs(fund.insider_net_90d / 1e6):.1f}M in last 90 days",
                        "summary": None,
                        "source": "Insider Activity",
                        "tickers": [ticker],
                        "published_at": (fund.updated_at.isoformat() + "Z") if fund.updated_at else None,
                        "url": None,
                        "sentiment_score": 0.15 if fund.insider_net_90d > 0 else -0.15,
                    })

                headlines = synthetic_notes
                if synthetic_notes:
                    # Update sentiment from synthetic data
                    scores = [n["sentiment_score"] for n in synthetic_notes if n.get("sentiment_score")]
                    if scores:
                        avg = sum(scores) / len(scores)
                        sentiment = {"score": round(avg, 3), "label": _synthetic_sentiment_label(avg), "article_count": len(synthetic_notes)}
            else:
                # No fundamentals either — create a minimal note
                headlines = [{
                    "id": f"synthetic-minimal-{ticker}",
                    "headline": f"No recent news available for {ticker}",
                    "summary": f"Market data for {ticker} will populate as fundamentals are loaded.",
                    "source": "System",
                    "tickers": [ticker],
                    "published_at": None,
                    "url": None,
                    "sentiment_score": None,
                }]
        except Exception as e:
            logger.debug(f"Synthetic context generation failed for {ticker}: {e}")

    return {
        "ticker": ticker,
        "company": company,
        "sentiment": sentiment,
        "headlines": headlines,
    }


def _synthetic_sentiment_label(score: float) -> str:
    if score >= 0.1: return "Slightly Bullish"
    if score <= -0.1: return "Slightly Bearish"
    return "Neutral"


@router.get("/news/ticker/{ticker}/thesis")
async def get_ticker_thesis(ticker: str):
    """
    Get a cached bull/bear thesis for a ticker.
    Generated by Gemini, cached until explicitly refreshed.
    """
    ticker = ticker.upper().strip()

    # Check cache first
    try:
        from app.database import async_session
        from app.models.henry_cache import HenryCache
        from sqlalchemy import select

        async with async_session() as db:
            result = await db.execute(
                select(HenryCache).where(
                    HenryCache.cache_key == f"thesis:{ticker}",
                    HenryCache.cache_type == "bull_bear_thesis",
                    HenryCache.is_stale == False,
                )
            )
            cached = result.scalar_one_or_none()
            if cached:
                import json
                return {
                    "ticker": ticker,
                    "thesis": json.loads(cached.content) if isinstance(cached.content, str) else cached.content,
                    "generated_at": cached.generated_at.isoformat() if cached.generated_at else None,
                    "cached": True,
                }
    except Exception:
        pass

    return {"ticker": ticker, "thesis": None, "cached": False}


@router.post("/news/ticker/{ticker}/thesis")
async def generate_ticker_thesis(ticker: str):
    """
    Generate a bull/bear thesis for a ticker using Gemini.
    Caches the result for future lookups.
    """
    ticker = ticker.upper().strip()

    try:
        # Get context: company info + recent headlines
        import asyncio
        company = await get_company_description(ticker)
        headlines = await news_service.get_ticker_headlines(ticker, limit=5)

        company_ctx = ""
        if company and company.get("name"):
            company_ctx = f"Company: {company['name']}"
            if company.get("sector"):
                company_ctx += f" | Sector: {company['sector']}"
            if company.get("industry"):
                company_ctx += f" | Industry: {company['industry']}"
            if company.get("market_cap"):
                company_ctx += f" | Market Cap: ${company['market_cap']:,.0f}"
            if company.get("description"):
                company_ctx += f"\nBusiness: {company['description']}"
            if company.get("high_52w") and company.get("low_52w"):
                company_ctx += f"\n52-week range: ${company['low_52w']:.2f} - ${company['high_52w']:.2f}"

        news_ctx = ""
        if headlines:
            news_lines = [f"  - {h.get('headline', '')}" for h in headlines[:5]]
            news_ctx = "Recent headlines:\n" + "\n".join(news_lines)

        prompt = f"""Generate a concise bull/bear thesis for {ticker}.

{company_ctx}

{news_ctx}

Return a JSON object with this exact structure:
{{
  "bull_case": "2-3 sentence bull thesis — why this stock could go up significantly",
  "bear_case": "2-3 sentence bear thesis — the key risks and why it could go down",
  "key_catalysts": ["catalyst 1", "catalyst 2", "catalyst 3"],
  "risk_factors": ["risk 1", "risk 2", "risk 3"],
  "sentiment_summary": "One sentence: what's the overall market sentiment right now?"
}}

Be specific to {ticker} — reference actual business drivers, not generic statements. Keep each thesis under 80 words."""

        from app.services.ai_provider import call_ai
        raw = await call_ai(
            system="You are a financial analyst. Return ONLY valid JSON, no markdown, no code blocks.",
            prompt=prompt,
            function_name="watchlist_summary",
            max_tokens=600,
        )

        # Parse JSON — strip fences and use regex to extract the JSON
        # object even when the model wraps it in prose or markdown.
        import json
        import re
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        # Regex extract first JSON object if still not pure JSON.
        json_match = re.search(r"\{[\s\S]*\}", cleaned)
        if json_match:
            cleaned = json_match.group(0)
        thesis_data = json.loads(cleaned)

        # Cache it
        try:
            from app.database import async_session
            from app.models.henry_cache import HenryCache
            from sqlalchemy import select
            import uuid
            from datetime import datetime

            async with async_session() as db:
                # Delete old cache
                result = await db.execute(
                    select(HenryCache).where(
                        HenryCache.cache_key == f"thesis:{ticker}",
                        HenryCache.cache_type == "bull_bear_thesis",
                    )
                )
                old = result.scalar_one_or_none()
                if old:
                    await db.delete(old)
                    await db.flush()

                cache_entry = HenryCache(
                    id=str(uuid.uuid4()),
                    cache_key=f"thesis:{ticker}",
                    cache_type="bull_bear_thesis",
                    content=json.dumps(thesis_data),
                    ticker=ticker,
                    is_stale=False,
                    generated_at=utcnow(),
                )
                db.add(cache_entry)
                await db.commit()
        except Exception as e:
            logger.warning(f"Failed to cache thesis for {ticker}: {e}")

        # Also save to Henry's memory for reference
        try:
            from app.services.ai_service import save_memory
            await save_memory(
                content=f"Bull thesis on {ticker}: {thesis_data.get('bull_case', '')}. Bear thesis: {thesis_data.get('bear_case', '')}",
                memory_type="observation",
                ticker=ticker,
                importance=6,
                source="thesis_generator",
            )
        except Exception:
            pass

        return {
            "ticker": ticker,
            "thesis": thesis_data,
            "generated_at": utcnow().isoformat() if 'datetime' in dir() else None,
            "cached": False,
        }

    except json.JSONDecodeError:
        return {"ticker": ticker, "thesis": None, "error": "Failed to parse AI response"}
    except Exception as e:
        logger.error(f"Failed to generate thesis for {ticker}: {e}")
        return {"ticker": ticker, "thesis": None, "error": str(e)}
