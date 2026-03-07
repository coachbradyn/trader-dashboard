import logging
from datetime import datetime, timedelta
from functools import lru_cache
import asyncio

logger = logging.getLogger(__name__)

# In-memory cache with timestamps
_chart_cache: dict[str, tuple[datetime, list[dict]]] = {}
CACHE_TTL = 900  # 15 minutes

async def get_daily_chart(ticker: str, days: int = 60) -> list[dict]:
    """Fetch daily OHLCV data via yfinance, cached for 15 min."""
    cache_key = f"{ticker}:{days}"

    # Check cache
    if cache_key in _chart_cache:
        cached_time, cached_data = _chart_cache[cache_key]
        if (datetime.utcnow() - cached_time).total_seconds() < CACHE_TTL:
            return cached_data

    # Fetch in thread pool (yfinance is sync)
    data = await asyncio.to_thread(_fetch_yfinance, ticker, days)
    _chart_cache[cache_key] = (datetime.utcnow(), data)
    return data


def _fetch_yfinance(ticker: str, days: int) -> list[dict]:
    """Synchronous yfinance fetch."""
    try:
        import yfinance as yf

        end = datetime.now()
        start = end - timedelta(days=days + 5)  # extra buffer for weekends

        tk = yf.Ticker(ticker)
        hist = tk.history(start=start.strftime("%Y-%m-%d"), end=end.strftime("%Y-%m-%d"), interval="1d")

        if hist.empty:
            return []

        result = []
        for date, row in hist.iterrows():
            result.append({
                "date": date.strftime("%Y-%m-%d"),
                "open": round(float(row["Open"]), 2),
                "high": round(float(row["High"]), 2),
                "low": round(float(row["Low"]), 2),
                "close": round(float(row["Close"]), 2),
                "volume": int(row["Volume"]),
            })

        return result[-days:]  # trim to exact count

    except Exception as e:
        logger.error(f"yfinance fetch failed for {ticker}: {e}")
        return []
