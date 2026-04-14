"""
News Service
=============
Fetches, caches, and serves news from Alpaca News API.
Includes keyword-based sentiment scoring and company description caching.
"""

import asyncio
from app.utils.utc import utcnow
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx
from sqlalchemy import select, delete, func, or_
from sqlalchemy import cast as sa_cast

from app.config import get_settings
from app.database import async_session, engine
from app.models.news_cache import NewsCache

# Use JSONB containment on PostgreSQL, string matching on SQLite
_IS_POSTGRES = "postgresql" in str(engine.url)

if _IS_POSTGRES:
    from sqlalchemy.dialects.postgresql import JSONB as _JSONB


def _ticker_filter(ticker: str):
    """Build a dialect-appropriate filter for the JSON tickers column."""
    ticker_upper = ticker.upper()
    if _IS_POSTGRES:
        return sa_cast(NewsCache.tickers, _JSONB).contains([ticker_upper])
    else:
        # SQLite: tickers stored as JSON text like '["NVDA", "AAPL"]'
        # Match quoted ticker to avoid substring collisions (e.g. "A" vs "AAPL")
        from sqlalchemy import cast, String as SAString
        return cast(NewsCache.tickers, SAString).contains(f'"{ticker_upper}"')

logger = logging.getLogger(__name__)

# ─── SENTIMENT KEYWORDS ─────────────────────────────────────────────────────

POSITIVE_WORDS = {
    "surge", "surges", "surging", "rally", "rallies", "rallying", "gain", "gains",
    "jump", "jumps", "soar", "soars", "soaring", "rise", "rises", "rising",
    "beat", "beats", "exceed", "exceeds", "record", "upgrade", "upgrades",
    "bullish", "outperform", "outperforms", "strong", "strength", "profit",
    "growth", "expand", "expands", "expanding", "boost", "boosts", "boosted",
    "positive", "optimism", "optimistic", "breakthrough", "innovation",
    "revenue", "upside", "recovery", "recover", "rebounds", "rebound",
    "buy", "overweight", "accelerate", "accelerates",
}

NEGATIVE_WORDS = {
    "drop", "drops", "dropping", "fall", "falls", "falling", "decline", "declines",
    "plunge", "plunges", "plunging", "crash", "crashes", "crashing", "sink", "sinks",
    "miss", "misses", "missed", "loss", "losses", "downgrade", "downgrades",
    "bearish", "underperform", "underperforms", "weak", "weakness", "deficit",
    "shrink", "shrinks", "shrinking", "cut", "cuts", "slash", "slashes",
    "negative", "pessimism", "pessimistic", "risk", "risks", "fear", "fears",
    "sell", "underweight", "decelerate", "layoff", "layoffs", "lawsuit",
    "warning", "warns", "warned", "recession", "bankruptcy", "default",
}


def _compute_sentiment(headline: str) -> float:
    """Compute a simple keyword-based sentiment score from -1.0 to 1.0."""
    if not headline:
        return 0.0
    words = set(headline.lower().split())
    pos_count = len(words & POSITIVE_WORDS)
    neg_count = len(words & NEGATIVE_WORDS)
    total = pos_count + neg_count
    if total == 0:
        return 0.0
    # Each positive word contributes +0.1, negative -0.1, clamped to [-1.0, 1.0]
    score = (pos_count - neg_count) * 0.1
    return max(-1.0, min(1.0, score))


def _sentiment_label(score: float) -> str:
    """Convert a sentiment score to a human-readable label."""
    if score >= 0.2:
        return "Bullish"
    elif score >= 0.05:
        return "Slightly Bullish"
    elif score <= -0.2:
        return "Bearish"
    elif score <= -0.05:
        return "Slightly Bearish"
    return "Neutral"


# ─── FMP → Alpaca-upsert shape normalizers ──────────────────────────────────
# The news_cache upsert expects Alpaca's schema (id/headline/summary/source/
# symbols/url/created_at). FMP news and press releases use different keys, so
# map them here before handing to _upsert_articles.

def _normalize_fmp_articles(raw, ticker: str) -> list[dict]:
    """FMP /stable/news/stock returns a list of articles with keys like
    url/title/text/site/publishedDate/symbol. Handles the occasional wrapper
    dict ({"articles": [...]}) and silently drops malformed entries.
    """
    if raw is None:
        return []
    if isinstance(raw, dict):
        for key in ("articles", "news", "data"):
            if isinstance(raw.get(key), list):
                raw = raw[key]
                break
        else:
            return []
    if not isinstance(raw, list):
        return []

    out: list[dict] = []
    for a in raw:
        if not isinstance(a, dict):
            continue
        url = a.get("url") or ""
        title = a.get("title") or ""
        if not url and not title:
            continue
        aid = f"fmp-news-{url or title}"[:255]
        syms = a.get("symbols")
        if not syms:
            sym = a.get("symbol") or ticker
            syms = [sym] if isinstance(sym, str) else []
        syms = [str(s).upper().split(":")[-1] for s in syms if s]
        if ticker.upper() not in syms:
            syms.append(ticker.upper())
        out.append({
            "id": aid,
            "headline": title[:500],
            "summary": (a.get("text") or "")[:500],
            "source": a.get("site") or a.get("publisher") or "FMP",
            "symbols": syms,
            "url": url,
            "created_at": a.get("publishedDate") or a.get("date"),
        })
    return out


def _normalize_fmp_press_releases(raw, ticker: str) -> list[dict]:
    """FMP /stable/news/press-releases has keys date/title/text/symbol."""
    if raw is None:
        return []
    if isinstance(raw, dict):
        for key in ("articles", "data", "releases"):
            if isinstance(raw.get(key), list):
                raw = raw[key]
                break
        else:
            return []
    if not isinstance(raw, list):
        return []

    out: list[dict] = []
    for r in raw:
        if not isinstance(r, dict):
            continue
        title = r.get("title") or f"{ticker} press release"
        date = r.get("date") or r.get("publishedDate")
        if not date:
            continue
        aid = f"fmp-pr-{ticker}-{date}"[:255]
        out.append({
            "id": aid,
            "headline": title[:500],
            "summary": (r.get("text") or "")[:500],
            "source": "Press Release",
            "symbols": [ticker.upper()],
            "url": r.get("url") or "",
            "created_at": date,
        })
    return out


# ─── COMPANY DESCRIPTION CACHE ──────────────────────────────────────────────

_company_cache: dict[str, dict] = {}  # ticker -> {data, fetched_at}
_COMPANY_TTL = 86400  # 24 hours in seconds


async def get_company_description(ticker: str) -> dict:
    """
    Get company info from yfinance. Cached in memory with 24h TTL.
    Returns dict with: name, sector, industry, market_cap, description, high_52w, low_52w
    """
    now = time.time()
    cached = _company_cache.get(ticker)
    if cached and (now - cached["fetched_at"]) < _COMPANY_TTL:
        return cached["data"]

    try:
        data = await asyncio.to_thread(_fetch_company_sync, ticker)
        _company_cache[ticker] = {"data": data, "fetched_at": now}
        return data
    except Exception as e:
        logger.error(f"Company description fetch failed for {ticker}: {e}")
        return {
            "name": ticker,
            "sector": None,
            "industry": None,
            "market_cap": None,
            "description": None,
            "high_52w": None,
            "low_52w": None,
        }


def _fetch_company_sync(ticker: str) -> dict:
    """Sync yfinance call to get company info."""
    import yfinance as yf

    tk = yf.Ticker(ticker)
    info = tk.info or {}

    long_summary = info.get("longBusinessSummary", "") or ""

    return {
        "name": info.get("longName") or info.get("shortName") or ticker,
        "sector": info.get("sector"),
        "industry": info.get("industry"),
        "market_cap": info.get("marketCap"),
        "description": long_summary or None,  # Full description, frontend handles truncation
        "high_52w": info.get("fiftyTwoWeekHigh"),
        "low_52w": info.get("fiftyTwoWeekLow"),
    }


# ─── NEWS SERVICE ────────────────────────────────────────────────────────────

class NewsService:
    """Fetches news from Alpaca, caches in DB, and provides read access."""

    # Alpaca-key-missing warning throttle — log at most once per hour rather
    # than on every ticker page load.
    _alpaca_missing_logged_at: float = 0.0

    async def _fetch_alpaca_news(
        self, tickers: list[str] | None = None, limit: int = 20
    ) -> list[dict]:
        """Call Alpaca News API via httpx (async)."""
        settings = get_settings()
        if not settings.alpaca_api_key:
            now = time.time()
            if now - NewsService._alpaca_missing_logged_at > 3600:
                logger.warning(
                    "Alpaca API key not configured — ticker news will fall back "
                    "to FMP /stable/news/stock"
                )
                NewsService._alpaca_missing_logged_at = now
            return []

        params = {"limit": limit, "sort": "desc"}
        if tickers:
            params["symbols"] = ",".join(tickers[:10])

        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    "https://data.alpaca.markets/v1beta1/news",
                    params=params,
                    headers={
                        "APCA-API-KEY-ID": settings.alpaca_api_key,
                        "APCA-API-SECRET-KEY": settings.alpaca_secret_key,
                    },
                    timeout=10,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    return data.get("news", [])
                else:
                    logger.warning(
                        f"Alpaca news API returned {resp.status_code}: {resp.text[:200]}"
                    )
        except Exception as e:
            logger.error(f"Alpaca news fetch failed: {e}")

        return []

    async def _upsert_articles(self, articles: list[dict]) -> int:
        """Deduplicate and insert articles into news_cache. Returns count inserted."""
        if not articles:
            return 0

        inserted = 0
        async with async_session() as db:
            # Get existing alpaca_ids to deduplicate
            alpaca_ids = [str(a.get("id", "")) for a in articles if a.get("id")]
            if not alpaca_ids:
                return 0

            result = await db.execute(
                select(NewsCache.alpaca_id).where(
                    NewsCache.alpaca_id.in_(alpaca_ids)
                )
            )
            existing_ids = {row[0] for row in result.all()}

            for article in articles:
                aid = str(article.get("id", ""))
                if not aid or aid in existing_ids:
                    continue

                headline = article.get("headline", "")
                sentiment = _compute_sentiment(headline)

                # Parse published_at
                published_at = None
                raw_date = article.get("created_at") or article.get("updated_at")
                if raw_date:
                    try:
                        # Alpaca returns ISO format strings
                        if isinstance(raw_date, str):
                            published_at = datetime.fromisoformat(
                                raw_date.replace("Z", "+00:00")
                            )
                        else:
                            published_at = raw_date
                    except Exception:
                        pass

                news_item = NewsCache(
                    alpaca_id=aid,
                    headline=headline,
                    summary=(article.get("summary", "") or "")[:500],
                    source=article.get("source", ""),
                    tickers=article.get("symbols", []),
                    published_at=published_at,
                    url=article.get("url", ""),
                    sentiment_score=sentiment,
                )
                db.add(news_item)
                inserted += 1

            if inserted:
                await db.commit()

        return inserted

    async def fetch_and_cache_general_news(self, limit: int = 20) -> int:
        """Fetch general market news and cache it. Returns count of new articles."""
        try:
            articles = await self._fetch_alpaca_news(limit=limit)
            return await self._upsert_articles(articles)
        except Exception as e:
            logger.error(f"fetch_and_cache_general_news failed: {e}")
            return 0

    async def fetch_and_cache_ticker_news(self, ticker: str, limit: int = 10) -> int:
        """Fetch news for a specific ticker and cache it. Tries Alpaca first,
        then FMP /stable/news/stock if Alpaca returns nothing (or keys are
        missing). Returns count of new articles cached.
        """
        ticker = ticker.upper().strip()
        alpaca_count = 0
        fmp_count = 0
        try:
            articles = await self._fetch_alpaca_news(tickers=[ticker], limit=limit)
            if articles:
                alpaca_count = await self._upsert_articles(articles)
                logger.info(
                    f"News fetch {ticker}: Alpaca returned {len(articles)} "
                    f"articles, {alpaca_count} new rows cached"
                )
                if alpaca_count > 0:
                    return alpaca_count
        except Exception as e:
            logger.warning(f"Alpaca news fetch failed for {ticker}: {e}")

        # FMP fallback — normalize their response shape into the Alpaca
        # upsert schema so both sources share a single cache path.
        try:
            from app.services.fmp_service import get_stock_news
            fmp_news = await get_stock_news(ticker, limit=limit)
            normalized = _normalize_fmp_articles(fmp_news, ticker)
            if normalized:
                fmp_count = await self._upsert_articles(normalized)
                logger.info(
                    f"News fetch {ticker}: FMP returned {len(normalized)} "
                    f"articles, {fmp_count} new rows cached"
                )
                return fmp_count
        except Exception as e:
            logger.warning(f"FMP news fallback failed for {ticker}: {e}")

        # Last resort: press releases
        try:
            from app.services.fmp_service import get_press_releases
            releases = await get_press_releases(ticker, limit=5)
            normalized = _normalize_fmp_press_releases(releases, ticker)
            if normalized:
                pr_count = await self._upsert_articles(normalized)
                if pr_count:
                    logger.info(
                        f"News fetch {ticker}: FMP press releases → "
                        f"{pr_count} new rows cached"
                    )
                return pr_count
        except Exception as e:
            logger.debug(f"FMP press-release fallback failed for {ticker}: {e}")

        logger.info(
            f"News fetch {ticker}: no articles from Alpaca, FMP news, or FMP "
            f"press releases — ticker page will show synthetic fundamentals"
        )
        return 0

    async def fetch_and_cache_many_tickers(
        self, tickers: list[str], per_ticker_limit: int = 8,
        ttl_seconds: int = 7200,
    ) -> dict:
        """Proactively warm the cache for a list of tickers. Used by the
        scheduler job to keep holdings/watchlist news fresh so ticker pages
        aren't cold on first visit.

        Skips tickers whose cache is newer than ttl_seconds to avoid burning
        API calls. Returns {ticker: inserted_count} for tickers it touched.
        """
        if not tickers:
            return {}

        results: dict[str, int] = {}
        stale_cutoff = utcnow() - timedelta(seconds=ttl_seconds)

        # Find which tickers actually need refreshing
        to_fetch: list[str] = []
        try:
            async with async_session() as db:
                for raw in tickers:
                    t = raw.upper().strip()
                    if not t or t in to_fetch:
                        continue
                    recent = await db.execute(
                        select(func.max(NewsCache.fetched_at))
                        .where(_ticker_filter(t))
                    )
                    last = recent.scalar()
                    if last is None or last < stale_cutoff:
                        to_fetch.append(t)
        except Exception as e:
            logger.warning(f"fetch_and_cache_many_tickers cache check failed: {e}")
            to_fetch = [t.upper().strip() for t in tickers if t]

        if not to_fetch:
            logger.info(f"News warm: all {len(tickers)} tickers fresh, nothing to fetch")
            return {}

        logger.info(f"News warm: fetching {len(to_fetch)} stale tickers ({', '.join(to_fetch[:10])}{'...' if len(to_fetch) > 10 else ''})")
        for t in to_fetch:
            try:
                count = await self.fetch_and_cache_ticker_news(t, limit=per_ticker_limit)
                results[t] = count
            except Exception as e:
                logger.warning(f"News warm failed for {t}: {e}")
                results[t] = 0
            # Tiny pause to avoid hammering upstream
            await asyncio.sleep(0.1)

        fetched = sum(1 for v in results.values() if v > 0)
        logger.info(f"News warm done: {fetched}/{len(to_fetch)} tickers got new articles")
        return results

    async def get_cached_news(
        self,
        ticker: Optional[str] = None,
        limit: int = 20,
        hours: int = 24,
    ) -> list[dict]:
        """Get cached news articles, optionally filtered by ticker.

        Filters by published_at (not fetched_at) so on-demand per-ticker
        articles don't age out prematurely.  Ticker filtering uses native
        PostgreSQL JSONB @> containment for exact matching.
        """
        try:
            cutoff = utcnow() - timedelta(hours=hours)
            async with async_session() as db:
                query = (
                    select(NewsCache)
                    .where(
                        or_(
                            NewsCache.published_at >= cutoff,
                            NewsCache.published_at.is_(None),
                        )
                    )
                    .order_by(NewsCache.published_at.desc())
                    .limit(limit)
                )

                if ticker:
                    query = query.where(_ticker_filter(ticker))

                result = await db.execute(query)
                articles = result.scalars().all()

                return [
                    {
                        # `id` is required by the frontend `NewsArticle` type
                        # (used as React list key). NewsCache.id is the
                        # primary key — fall back to url when absent.
                        "id": getattr(a, "id", None) or a.url or f"{a.source}:{a.headline[:40]}",
                        "headline": a.headline,
                        "summary": a.summary,
                        "source": a.source,
                        "tickers": a.tickers or [],
                        "published_at": a.published_at.isoformat() if a.published_at else None,
                        "url": a.url,
                        "sentiment_score": a.sentiment_score,
                    }
                    for a in articles
                ]
        except Exception as e:
            logger.error(f"get_cached_news failed: {e}")
            return []

    async def get_ticker_headlines(self, ticker: str, limit: int = 5) -> list[dict]:
        """Get recent headlines for a specific ticker.

        Skips the Alpaca/FMP fetch if we already have fresh articles (< 30 min
        old) for this ticker. On a cache miss, fetch_and_cache_ticker_news
        cascades Alpaca → FMP news → FMP press releases so non-AAPL tickers
        aren't dependent on Alpaca being configured.
        """
        # Normalize exchange prefixes (e.g. NASDAQ:NVDA → NVDA)
        ticker_upper = ticker.upper().strip().split(":")[-1]

        should_fetch = True
        existing_count = 0
        try:
            async with async_session() as db:
                recent = await db.execute(
                    select(func.max(NewsCache.fetched_at), func.count(NewsCache.id))
                    .where(_ticker_filter(ticker_upper))
                )
                row = recent.first()
                last_fetch = row[0] if row else None
                existing_count = (row[1] if row else 0) or 0
                if last_fetch and (utcnow() - last_fetch).total_seconds() < 1800:
                    should_fetch = False
        except Exception as e:
            logger.debug(f"get_ticker_headlines cache check failed for {ticker_upper}: {e}")

        if should_fetch:
            logger.debug(
                f"News {ticker_upper}: cache stale ({existing_count} cached) → "
                f"fetching fresh"
            )
            await self.fetch_and_cache_ticker_news(ticker_upper, limit=limit)
        else:
            logger.debug(
                f"News {ticker_upper}: using cache ({existing_count} articles, "
                f"< 30 min old)"
            )

        results = await self.get_cached_news(ticker=ticker_upper, limit=limit, hours=72)
        if not results and should_fetch:
            logger.info(
                f"News {ticker_upper}: 0 articles after fetch — API endpoint "
                f"will fall through to synthetic fundamentals"
            )
        return results

    async def get_news_sentiment(self, ticker: str) -> dict:
        """Get aggregated sentiment for a ticker from cached articles."""
        try:
            articles = await self.get_cached_news(ticker=ticker, limit=50, hours=48)
            if not articles:
                return {"score": 0.0, "label": "Neutral", "article_count": 0}

            scores = [
                a["sentiment_score"]
                for a in articles
                if a.get("sentiment_score") is not None
            ]
            if not scores:
                return {"score": 0.0, "label": "Neutral", "article_count": len(articles)}

            avg_score = round(sum(scores) / len(scores), 3)
            return {
                "score": avg_score,
                "label": _sentiment_label(avg_score),
                "article_count": len(articles),
            }
        except Exception as e:
            logger.error(f"get_news_sentiment failed for {ticker}: {e}")
            return {"score": 0.0, "label": "Neutral", "article_count": 0}

    async def cleanup_old_news(self, days: int = 7) -> int:
        """Delete news older than N days. Returns count deleted."""
        try:
            cutoff = utcnow() - timedelta(days=days)
            async with async_session() as db:
                result = await db.execute(
                    delete(NewsCache).where(NewsCache.fetched_at < cutoff)
                )
                await db.commit()
                deleted = result.rowcount
                if deleted:
                    logger.info(f"Cleaned up {deleted} old news articles")
                return deleted
        except Exception as e:
            logger.error(f"cleanup_old_news failed: {e}")
            return 0

    async def get_recent_headlines_for_prompt(self, limit: int = 5) -> list[dict]:
        """Get the most recent headlines for inclusion in Henry's system prompt."""
        try:
            async with async_session() as db:
                result = await db.execute(
                    select(NewsCache)
                    .order_by(NewsCache.published_at.desc())
                    .limit(limit)
                )
                articles = result.scalars().all()
                return [
                    {
                        "headline": a.headline,
                        "published_at": a.published_at.isoformat() if a.published_at else None,
                    }
                    for a in articles
                ]
        except Exception:
            return []


# Module-level singleton
news_service = NewsService()
