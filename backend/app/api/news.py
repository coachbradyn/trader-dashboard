"""
News API Endpoints
==================
Serves cached news, sentiment, and company info.
"""

from fastapi import APIRouter, Query
from app.services.news_service import news_service, get_company_description

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
    """Get news, sentiment, and company info for a specific ticker."""
    ticker = ticker.upper().strip()

    # Fetch all three in parallel
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

    return {
        "ticker": ticker,
        "company": company,
        "sentiment": sentiment,
        "headlines": headlines,
    }
