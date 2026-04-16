"""
Financial Modeling Prep (FMP) Service
=====================================
Fetches and caches structured financial data for watchlist tickers.
Runs as background jobs -- never blocks AI calls.

Features:
  - DB-backed caching with tiered TTLs (realtime/intraday/daily)
  - In-memory daily rate-limit tracking (warn at 250, stop at 275)
  - 19+ endpoint methods covering quotes, financials, technicals, screener
  - Technical snapshot helper (RSI, ADX, SMA200, EMA50 + quote)
  - Full fundamental fetch with extended columns
"""

import hashlib
from app.utils.utc import utcnow
import json
import logging
from datetime import datetime, date, timedelta, timezone

import httpx

from app.config import get_settings
from app.database import async_session
from app.models.ticker_fundamentals import TickerFundamentals
from app.models.fmp_cache import FmpCache
from sqlalchemy import select

logger = logging.getLogger(__name__)

FMP_BASE = "https://financialmodelingprep.com"

# ══════════════════════════════════════════════════════════════════════
# RATE-LIMIT TRACKING (in-memory, resets daily)
# ══════════════════════════════════════════════════════════════════════

_rate_limit_date: str = ""
_rate_limit_count: int = 0
_RATE_WARN = 700
_RATE_HARD = 900
_RATE_DAILY_LIMIT = 1000


def _increment_rate_counter() -> None:
    global _rate_limit_date, _rate_limit_count
    today = date.today().isoformat()
    if _rate_limit_date != today:
        _rate_limit_date = today
        _rate_limit_count = 0
    _rate_limit_count += 1
    if _rate_limit_count == _RATE_WARN:
        logger.warning(f"FMP API usage warning: {_rate_limit_count} calls today (limit {_RATE_DAILY_LIMIT})")


def _is_throttled(essential: bool = False) -> bool:
    """Return True if we should skip this call. Essential calls are allowed up to hard limit."""
    global _rate_limit_date, _rate_limit_count
    today = date.today().isoformat()
    if _rate_limit_date != today:
        _rate_limit_date = today
        _rate_limit_count = 0
        return False
    if essential:
        return _rate_limit_count >= _RATE_DAILY_LIMIT
    return _rate_limit_count >= _RATE_HARD


def get_api_usage() -> dict:
    """Return current FMP API usage stats."""
    global _rate_limit_date, _rate_limit_count
    today = date.today().isoformat()
    if _rate_limit_date != today:
        return {"calls_today": 0, "limit": _RATE_DAILY_LIMIT, "remaining": _RATE_DAILY_LIMIT, "throttled": False}
    remaining = max(0, _RATE_DAILY_LIMIT - _rate_limit_count)
    return {
        "calls_today": _rate_limit_count,
        "limit": _RATE_DAILY_LIMIT,
        "remaining": remaining,
        "throttled": _rate_limit_count >= _RATE_HARD,
    }


# ══════════════════════════════════════════════════════════════════════
# CACHE LAYER
# ══════════════════════════════════════════════════════════════════════

CACHE_TTL = {
    "realtime": 60,
    "intraday": 3600,
    "daily": 86400,
}


def _params_hash(params: dict | None) -> str:
    """MD5 hash of sorted params for cache key."""
    if not params:
        return hashlib.md5(b"").hexdigest()
    raw = json.dumps(params, sort_keys=True, default=str)
    return hashlib.md5(raw.encode()).hexdigest()


async def _get_from_cache(endpoint: str, params: dict | None, tier: str) -> dict | list | None:
    """Check DB cache. Returns cached data or None."""
    ph = _params_hash(params)
    ttl = CACHE_TTL.get(tier, 86400)
    try:
        async with async_session() as db:
            result = await db.execute(
                select(FmpCache).where(
                    FmpCache.endpoint == endpoint,
                    FmpCache.params_hash == ph,
                )
            )
            entry = result.scalar_one_or_none()
            if entry:
                age = (utcnow() - entry.cached_at).total_seconds()
                if age < ttl:
                    return entry.response_data
    except Exception as e:
        logger.debug(f"FMP cache read error: {e}")
    return None


async def _set_cache(endpoint: str, params: dict | None, tier: str, data: dict | list) -> None:
    """Store response in DB cache."""
    ph = _params_hash(params)
    try:
        async with async_session() as db:
            result = await db.execute(
                select(FmpCache).where(
                    FmpCache.endpoint == endpoint,
                    FmpCache.params_hash == ph,
                )
            )
            existing = result.scalar_one_or_none()
            if existing:
                existing.response_data = data
                existing.cached_at = utcnow()
                existing.cache_tier = tier
            else:
                entry = FmpCache(
                    endpoint=endpoint,
                    params_hash=ph,
                    response_data=data,
                    cache_tier=tier,
                )
                db.add(entry)
            await db.commit()
    except Exception as e:
        logger.debug(f"FMP cache write error: {e}")


# ══════════════════════════════════════════════════════════════════════
# CORE HTTP LAYER
# ══════════════════════════════════════════════════════════════════════

async def _fmp_get(
    endpoint: str,
    params: dict | None = None,
    cache_tier: str = "daily",
    essential: bool = False,
    fallbacks: list[tuple[str, dict]] | None = None,
) -> dict | list | None:
    """
    Make a GET request to FMP with caching and rate limiting.
    Returns parsed JSON or None on failure.

    ``fallbacks`` is an ordered list of (endpoint, params_override) tuples
    to retry with when the primary call returns a 4xx. Used for endpoints
    where FMP has renamed paths or changed param names on the /stable/
    migration (e.g. ``symbol`` → ``symbols`` for batch endpoints,
    ``historical-price-eod-full`` → ``historical-price-eod/full``). The
    override dict is merged over the primary params; pass ``{"_drop": [key,...]}``
    to remove a param instead of overriding it.
    """
    settings = get_settings()
    if not settings.fmp_api_key:
        logger.debug("FMP: no API key set, skipping request")
        return None

    # Check cache first
    cached = await _get_from_cache(endpoint, params, cache_tier)
    if cached is not None:
        return cached

    # Rate-limit check
    if _is_throttled(essential=essential):
        logger.warning(f"FMP rate limit reached, skipping {endpoint}")
        return None

    # Build attempt list: primary + any fallbacks
    attempts: list[tuple[str, dict | None]] = [(endpoint, params)]
    for fb_endpoint, fb_override in (fallbacks or []):
        merged = dict(params or {})
        drop = fb_override.pop("_drop", []) if isinstance(fb_override, dict) else []
        merged.update(fb_override or {})
        for k in drop:
            merged.pop(k, None)
        attempts.append((fb_endpoint, merged or None))

    last_status = None
    last_body = None
    last_url = None
    for attempt_endpoint, attempt_params in attempts:
        url = f"{FMP_BASE}{attempt_endpoint}"
        query = {"apikey": settings.fmp_api_key}
        if attempt_params:
            query.update(attempt_params)

        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                resp = await client.get(url, params=query)
                _increment_rate_counter()
                if resp.status_code == 200:
                    data = resp.json()
                    # Don't cache error responses that FMP returns as 200
                    if isinstance(data, dict) and ("Error" in data or "error" in data):
                        logger.warning(f"FMP API returned error in 200: {str(data)[:200]}")
                        _record_fmp_error(attempt_endpoint, attempt_params, 200, str(data)[:300])
                        return None
                    # Cache against the ORIGINAL endpoint key so subsequent
                    # calls via the same public function hit the cache
                    # regardless of which fallback served the response.
                    await _set_cache(endpoint, params, cache_tier, data)
                    return data
                last_status = resp.status_code
                last_body = resp.text[:300]
                last_url = str(resp.url).replace(settings.fmp_api_key, "***")
                logger.warning(
                    f"FMP API {resp.status_code} for {attempt_endpoint}: {last_body}"
                )
                _record_fmp_error(attempt_endpoint, attempt_params, resp.status_code, last_body)
                # Only retry on 4xx — 5xx is a server issue, no point in
                # spamming fallbacks.
                if not (400 <= resp.status_code < 500):
                    return None
        except Exception as e:
            logger.warning(f"FMP API error for {attempt_endpoint}: {e}")
            _record_fmp_error(attempt_endpoint, attempt_params, 0, str(e)[:300])
            return None

    # All attempts exhausted.
    return None


# Recent FMP 4xx responses, capped to the last 50 per-endpoint. Surfaced
# via the admin diagnostic endpoint so operators can see exactly what
# FMP is rejecting without tailing server logs.
_FMP_ERROR_LOG: dict[str, list[dict]] = {}
_FMP_ERROR_CAP = 50


def _record_fmp_error(endpoint: str, params: dict | None, status: int, body: str) -> None:
    try:
        entry = {
            "ts": utcnow().isoformat(),
            "endpoint": endpoint,
            "params": {k: v for k, v in (params or {}).items() if k != "apikey"},
            "status": status,
            "body": body,
        }
        bucket = _FMP_ERROR_LOG.setdefault(endpoint, [])
        bucket.append(entry)
        if len(bucket) > _FMP_ERROR_CAP:
            del bucket[: len(bucket) - _FMP_ERROR_CAP]
    except Exception:
        pass


def get_recent_errors() -> dict[str, list[dict]]:
    """Return the in-memory ring buffer of recent FMP 4xx/error responses."""
    return {k: list(v) for k, v in _FMP_ERROR_LOG.items()}


# ══════════════════════════════════════════════════════════════════════
# ENDPOINT METHODS (using FMP /stable/ API)
# ══════════════════════════════════════════════════════════════════════

async def get_quote(ticker: str) -> dict | list | None:
    """Real-time quote for a single ticker."""
    return await _fmp_get("/stable/quote", params={"symbol": ticker}, cache_tier="realtime")


async def get_batch_quotes(tickers: list[str]) -> dict | list | None:
    """Real-time quotes for multiple tickers.

    FMP's /stable/batch-quote (and the aliases `batch-quote-short` /
    `quote/batch` on some plans) moved to the plural ``symbols`` param.
    The previous ``symbol`` form returned 100% Bad Request on the
    dashboard's usage panel, so we lead with ``symbols`` and keep the
    legacy forms as fallbacks.
    """
    joined = ",".join(tickers)
    return await _fmp_get(
        "/stable/batch-quote",
        params={"symbols": joined},
        cache_tier="realtime",
        fallbacks=[
            # Some plans expose the endpoint as `batch-quote-short` — same shape.
            ("/stable/batch-quote-short", {"symbols": joined}),
            # Legacy v3-style param as a last resort.
            ("/stable/batch-quote", {"_drop": ["symbols"], "symbol": joined}),
        ],
    )


async def get_historical_daily(ticker: str, days: int = 90) -> dict | list | None:
    """Historical daily OHLCV.

    FMP renamed the full endpoint from ``historical-price-eod-full``
    (hyphen) to ``historical-price-eod/full`` (slash) and deprecated the
    ``timeseries`` param in favour of a ``from``/``to`` date window. Lead
    with the current form; fall back to the legacy hyphenated path so we
    work on older plans too.
    """
    from datetime import timedelta as _td
    today = utcnow().date()
    _from = (today - _td(days=days)).isoformat()
    _to = today.isoformat()
    return await _fmp_get(
        "/stable/historical-price-eod/full",
        params={"symbol": ticker, "from": _from, "to": _to},
        cache_tier="intraday",
        fallbacks=[
            # Legacy hyphen path with date range.
            ("/stable/historical-price-eod-full",
             {"_drop": [], "symbol": ticker, "from": _from, "to": _to}),
            # Legacy hyphen path with the older timeseries param.
            ("/stable/historical-price-eod-full",
             {"_drop": ["from", "to"], "timeseries": str(days)}),
        ],
    )


async def get_intraday(ticker: str, interval: str = "5min") -> dict | list | None:
    """Intraday chart data."""
    return await _fmp_get(
        "/stable/historical-chart",
        params={"symbol": ticker, "timeframe": interval},
        cache_tier="realtime",
    )


# Mapping of indicator names to their stable API path segments
_INDICATOR_PATHS = {
    "rsi": "rsi",
    "sma": "sma",
    "ema": "ema",
    "wma": "wma",
    "dema": "dema",
    "tema": "tema",
    "adx": "adx",
    "williams": "williams",
    "standardDeviation": "standarddeviation",
    "standarddeviation": "standarddeviation",
}

# Mapping of interval aliases to FMP timeframe format
_TIMEFRAME_MAP = {
    "daily": "1day",
    "1day": "1day",
    "1hour": "1hour",
    "1h": "1hour",
    "4hour": "4hour",
    "4h": "4hour",
    "30min": "30min",
    "15min": "15min",
    "5min": "5min",
    "1min": "1min",
}


async def get_technical_indicator(
    ticker: str,
    indicator: str,
    period: int = 14,
    interval: str = "daily",
) -> dict | list | None:
    """Technical indicator via /stable/technical-indicators/{type}."""
    ind_path = _INDICATOR_PATHS.get(indicator.lower(), indicator.lower())
    timeframe = _TIMEFRAME_MAP.get(interval, interval)
    return await _fmp_get(
        f"/stable/technical-indicators/{ind_path}",
        params={"symbol": ticker, "periodLength": str(period), "timeframe": timeframe},
        cache_tier="intraday",
    )


async def get_income_statement(ticker: str, period: str = "quarter") -> dict | list | None:
    """Income statement."""
    return await _fmp_get(
        "/stable/income-statement",
        params={"symbol": ticker, "period": period},
        cache_tier="daily",
    )


async def get_balance_sheet(ticker: str, period: str = "quarter") -> dict | list | None:
    """Balance sheet statement."""
    return await _fmp_get(
        "/stable/balance-sheet-statement",
        params={"symbol": ticker, "period": period},
        cache_tier="daily",
    )


async def get_cash_flow(ticker: str, period: str = "quarter") -> dict | list | None:
    """Cash flow statement."""
    return await _fmp_get(
        "/stable/cashflow-statement",
        params={"symbol": ticker, "period": period},
        cache_tier="daily",
    )


async def get_financial_ratios(ticker: str) -> dict | list | None:
    """Financial ratios."""
    return await _fmp_get("/stable/ratios", params={"symbol": ticker}, cache_tier="daily")


async def get_dcf(ticker: str) -> dict | list | None:
    """Discounted cash flow valuation."""
    return await _fmp_get("/stable/dcf-advanced", params={"symbol": ticker}, cache_tier="daily")


async def get_insider_trading(ticker: str) -> dict | list | None:
    """Insider trading transactions."""
    return await _fmp_get(
        "/stable/insider-trading",
        params={"symbol": ticker},
        cache_tier="daily",
    )


async def get_institutional_holders(ticker: str) -> dict | list | None:
    """Institutional holders."""
    return await _fmp_get("/stable/institutional-holder", params={"symbol": ticker}, cache_tier="daily")


async def get_stock_news(ticker: str, limit: int = 10) -> dict | list | None:
    """Get stock news for a ticker from FMP."""
    return await _fmp_get(
        "/stable/news/stock",
        params={"symbol": ticker, "limit": str(limit)},
        cache_tier="intraday",
    )


async def get_press_releases(ticker: str, limit: int = 5) -> dict | list | None:
    """Get press releases for a ticker from FMP.

    /stable/news/press-releases consistently returned 400 on the usage
    panel. That endpoint under /stable/ requires the plural ``symbols``
    param (same convention as /stable/news/stock on recent plans). Lead
    with plural, fall back to the latest-only variant so Henry still
    gets *some* press-release context when a per-ticker filter fails.
    """
    return await _fmp_get(
        "/stable/news/press-releases",
        params={"symbols": ticker, "limit": str(limit)},
        cache_tier="daily",
        fallbacks=[
            # Some plans expose releases without a symbol filter — filter client-side.
            ("/stable/news/press-releases-latest",
             {"_drop": ["symbols"], "limit": str(limit)}),
            # Legacy singular param.
            ("/stable/news/press-releases",
             {"_drop": ["symbols"], "symbol": ticker, "limit": str(limit)}),
        ],
    )


async def get_earnings_calendar(from_date: str, to_date: str) -> dict | list | None:
    """Earnings calendar for a date range.

    FMP's stable API renamed ``earning-calendar`` → ``earnings-calendar``
    (plural). Kept both as fallbacks so the dashboard works on plans
    that haven't been migrated.
    """
    return await _fmp_get(
        "/stable/earnings-calendar",
        params={"from": from_date, "to": to_date},
        cache_tier="daily",
        fallbacks=[
            ("/stable/earning-calendar", {}),
        ],
    )


async def get_economic_calendar() -> dict | list | None:
    """Economic calendar."""
    return await _fmp_get("/stable/economic-calendar", cache_tier="daily")


async def get_gainers() -> dict | list | None:
    """Market gainers."""
    return await _fmp_get("/stable/biggest-gainers", cache_tier="intraday")


async def get_losers() -> dict | list | None:
    """Market losers."""
    return await _fmp_get("/stable/biggest-losers", cache_tier="intraday")


async def get_most_active() -> dict | list | None:
    """Most active stocks. FMP renamed to the plural path."""
    return await _fmp_get(
        "/stable/most-actives",
        cache_tier="intraday",
        fallbacks=[("/stable/most-active", {})],
    )


async def get_sector_performance() -> dict | list | None:
    """Sector performance snapshot.

    ``historical-sector-performance`` doesn't exist on /stable/; the
    snapshot endpoint takes a single date. We pass today's date first,
    falling back to yesterday when the market is closed and today has
    no snapshot yet, and finally to the legacy path in case a plan
    still exposes it.
    """
    from datetime import timedelta as _td
    today = utcnow().date().isoformat()
    yesterday = (utcnow().date() - _td(days=1)).isoformat()
    return await _fmp_get(
        "/stable/historical-sector-performance-snapshot",
        params={"date": today},
        cache_tier="intraday",
        fallbacks=[
            ("/stable/historical-sector-performance-snapshot", {"date": yesterday}),
            ("/stable/sector-performance-snapshot", {"date": today}),
            ("/stable/historical-sector-performance", {"_drop": ["date"]}),
        ],
    )


async def run_screener(params: dict) -> dict | list | None:
    """Stock screener with arbitrary filter params."""
    return await _fmp_get(
        "/stable/company-screener",
        params=params,
        cache_tier="intraday",
    )


# ══════════════════════════════════════════════════════════════════════
# HELPER: TECHNICAL SNAPSHOT
# ══════════════════════════════════════════════════════════════════════

async def get_technical_snapshot(ticker: str) -> dict:
    """
    Fetch RSI(14), ADX(14), SMA(200), EMA(50), and current quote.
    Returns a dict with all values (None for any that failed).
    """
    snapshot: dict = {
        "ticker": ticker,
        "rsi": None,
        "adx": None,
        "sma200": None,
        "ema50": None,
        "price": None,
        "volume": None,
        "change_pct": None,
    }

    # Fetch all in parallel-ish (each is cached independently)
    quote_data = await get_quote(ticker)
    rsi_data = await get_technical_indicator(ticker, "rsi", period=14)
    adx_data = await get_technical_indicator(ticker, "adx", period=14)
    sma_data = await get_technical_indicator(ticker, "sma", period=200)
    ema_data = await get_technical_indicator(ticker, "ema", period=50)

    # Parse quote
    if quote_data and isinstance(quote_data, list) and len(quote_data) > 0:
        q = quote_data[0]
        snapshot["price"] = q.get("price")
        snapshot["volume"] = q.get("volume")
        snapshot["change_pct"] = q.get("changesPercentage")

    # Parse indicators (FMP returns list, most recent first)
    if rsi_data and isinstance(rsi_data, list) and len(rsi_data) > 0:
        snapshot["rsi"] = rsi_data[0].get("rsi") or rsi_data[0].get("value")
    if adx_data and isinstance(adx_data, list) and len(adx_data) > 0:
        snapshot["adx"] = adx_data[0].get("adx") or adx_data[0].get("value")
    if sma_data and isinstance(sma_data, list) and len(sma_data) > 0:
        snapshot["sma200"] = sma_data[0].get("sma") or sma_data[0].get("value")
    if ema_data and isinstance(ema_data, list) and len(ema_data) > 0:
        snapshot["ema50"] = ema_data[0].get("ema") or ema_data[0].get("value")

    return snapshot


# ══════════════════════════════════════════════════════════════════════
# DERIVED INDICATORS (computed from FMP primitive data)
# ══════════════════════════════════════════════════════════════════════

async def compute_macd(ticker: str, timeframe: str = "daily") -> dict:
    """Compute MACD from EMA 12 and EMA 26. Returns macd, signal, histogram, and previous values."""
    ema12_data = await get_technical_indicator(ticker, "ema", period=12, interval=timeframe)
    ema26_data = await get_technical_indicator(ticker, "ema", period=26, interval=timeframe)

    result = {"macd": None, "signal": None, "histogram": None, "prev_macd": None, "prev_signal": None}

    if not ema12_data or not ema26_data:
        return result
    if not isinstance(ema12_data, list) or not isinstance(ema26_data, list):
        return result
    if len(ema12_data) < 10 or len(ema26_data) < 10:
        return result

    # FMP returns newest first — compute MACD line for recent bars
    macd_values = []
    for i in range(min(len(ema12_data), len(ema26_data), 30)):
        e12 = ema12_data[i].get("ema") or ema12_data[i].get("value")
        e26 = ema26_data[i].get("ema") or ema26_data[i].get("value")
        if e12 is not None and e26 is not None:
            macd_values.append(e12 - e26)
        else:
            macd_values.append(None)

    if len(macd_values) < 10 or macd_values[0] is None:
        return result

    # Signal line = 9-period EMA of MACD values (simple average approximation)
    valid_macd = [v for v in macd_values[:9] if v is not None]
    signal = sum(valid_macd) / len(valid_macd) if valid_macd else None

    # Previous bar signal
    prev_valid = [v for v in macd_values[1:10] if v is not None]
    prev_signal = sum(prev_valid) / len(prev_valid) if prev_valid else None

    histogram = (macd_values[0] - signal) if macd_values[0] is not None and signal is not None else None

    return {
        "macd": macd_values[0],
        "signal": signal,
        "histogram": histogram,
        "prev_macd": macd_values[1] if len(macd_values) > 1 else None,
        "prev_signal": prev_signal,
    }


async def compute_bollinger(ticker: str, period: int = 20, timeframe: str = "daily") -> dict:
    """Compute Bollinger Bands from SMA and Standard Deviation."""
    sma_data = await get_technical_indicator(ticker, "sma", period=period, interval=timeframe)
    std_data = await get_technical_indicator(ticker, "standardDeviation", period=period, interval=timeframe)
    quote = await get_quote(ticker)

    result = {"upper": None, "lower": None, "middle": None, "bandwidth": None, "price_position": None, "price": None}

    sma_val = None
    std_val = None
    if sma_data and isinstance(sma_data, list) and len(sma_data) > 0:
        sma_val = sma_data[0].get("sma") or sma_data[0].get("value")
    if std_data and isinstance(std_data, list) and len(std_data) > 0:
        std_val = std_data[0].get("standardDeviation") or std_data[0].get("value")

    price = None
    if quote and isinstance(quote, list) and len(quote) > 0:
        price = quote[0].get("price")

    if sma_val is None or std_val is None:
        return result

    upper = sma_val + 2 * std_val
    lower = sma_val - 2 * std_val
    bandwidth = (upper - lower) / sma_val if sma_val != 0 else None

    price_position = None
    if price is not None and upper != lower:
        price_position = (price - lower) / (upper - lower)  # 0 = at lower band, 1 = at upper band

    return {
        "upper": upper,
        "lower": lower,
        "middle": sma_val,
        "bandwidth": bandwidth,
        "price_position": price_position,
        "price": price,
    }


async def get_volume_surge(ticker: str, avg_period: int = 20) -> dict:
    """Compare current volume to N-day average volume."""
    quote = await get_quote(ticker)
    hist = await get_historical_daily(ticker, days=avg_period + 5)

    current_vol = None
    if quote and isinstance(quote, list) and len(quote) > 0:
        current_vol = quote[0].get("volume")

    avg_vol = None
    if hist and isinstance(hist, dict) and "historical" in hist:
        volumes = [d.get("volume", 0) for d in hist["historical"][:avg_period] if d.get("volume")]
        if volumes:
            avg_vol = sum(volumes) / len(volumes)
    elif hist and isinstance(hist, list) and len(hist) > 0:
        volumes = [d.get("volume", 0) for d in hist[:avg_period] if d.get("volume")]
        if volumes:
            avg_vol = sum(volumes) / len(volumes)

    surge_ratio = None
    if current_vol and avg_vol and avg_vol > 0:
        surge_ratio = current_vol / avg_vol

    return {
        "current_volume": current_vol,
        "avg_volume": avg_vol,
        "surge_ratio": surge_ratio,
    }


# ══════════════════════════════════════════════════════════════════════
# FUNDAMENTAL DATA FETCHING (existing + expanded)
# ══════════════════════════════════════════════════════════════════════

def _parse_date(date_str: str) -> date | None:
    """Parse a date string from FMP (YYYY-MM-DD format)."""
    try:
        return datetime.strptime(date_str[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


async def fetch_fundamentals_for_ticker(ticker: str) -> dict | None:
    """
    Fetch all fundamental data for a single ticker from FMP.
    Returns a dict of fields to upsert into ticker_fundamentals, or None on failure.
    """
    settings = get_settings()
    if not settings.fmp_api_key:
        return None

    data: dict = {"ticker": ticker, "updated_at": utcnow()}

    # 1. Company profile
    profile = await _fmp_get("/stable/profile", {"symbol": ticker}, cache_tier="daily")
    if profile and isinstance(profile, list) and len(profile) > 0:
        p = profile[0]
        data["company_name"] = p.get("companyName")
        data["sector"] = p.get("sector")
        data["industry"] = p.get("industry")
        data["market_cap"] = p.get("mktCap")
        data["beta"] = p.get("beta")
        data["dividend_yield"] = p.get("lastDiv")

        # Truncate description to one sentence for token efficiency
        desc = p.get("description", "")
        if desc:
            first_sentence = desc.split(". ")[0] + "." if ". " in desc else desc[:300]
            data["description"] = first_sentence[:500]
            # Full description in company_description
            data["company_description"] = desc[:5000] if len(desc) > 5000 else desc

    # 2. Earnings calendar -- next upcoming date
    earnings = await _fmp_get("/stable/earning-calendar", {"symbol": ticker}, cache_tier="daily")
    if earnings and isinstance(earnings, list):
        today = date.today()
        future_earnings = [
            e for e in earnings
            if e.get("date") and _parse_date(e["date"]) and _parse_date(e["date"]) >= today
        ]
        if future_earnings:
            future_earnings.sort(key=lambda e: e["date"])
            nearest = future_earnings[0]
            data["earnings_date"] = _parse_date(nearest["date"])
            time_str = nearest.get("time", "")
            if time_str:
                data["earnings_time"] = "bmo" if "bmo" in time_str.lower() or "before" in time_str.lower() else "amc"

    # 3. Analyst estimates -- current quarter EPS/revenue
    estimates = await _fmp_get("/stable/analyst-estimates", {"symbol": ticker}, cache_tier="daily")
    if estimates and isinstance(estimates, list) and len(estimates) > 0:
        est = estimates[0]
        data["eps_estimate_current"] = est.get("estimatedEpsAvg")
        data["revenue_estimate_current"] = est.get("estimatedRevenueAvg")

    # 4. Price target consensus
    targets = await _fmp_get("/stable/price-target-summary", {"symbol": ticker}, cache_tier="daily")
    if targets and isinstance(targets, list) and len(targets) > 0:
        t = targets[0]
        data["analyst_target_low"] = t.get("targetLow")
        data["analyst_target_high"] = t.get("targetHigh")
        data["analyst_target_consensus"] = t.get("targetConsensus")

    # 5. Analyst grades -- consensus rating
    grades = await _fmp_get("/stable/upgrades-downgrades", {"symbol": ticker}, cache_tier="daily")
    if grades and isinstance(grades, list) and len(grades) > 0:
        recent_grades = grades[:20]
        grade_counts: dict = {}
        for g in recent_grades:
            new_grade = g.get("newGrade", "")
            if new_grade:
                grade_counts[new_grade] = grade_counts.get(new_grade, 0) + 1
        if grade_counts:
            consensus_grade = max(grade_counts, key=grade_counts.get)
            data["analyst_rating"] = consensus_grade
            data["analyst_count"] = sum(grade_counts.values())

    # 6. Earnings surprises -- last quarter
    surprises = await _fmp_get("/stable/earnings-surprises", {"symbol": ticker}, cache_tier="daily")
    if surprises and isinstance(surprises, list) and len(surprises) > 0:
        last = surprises[0]
        data["eps_actual_last"] = last.get("actualEarningResult")
        estimated = last.get("estimatedEarning")
        actual = last.get("actualEarningResult")
        if estimated and actual and estimated != 0:
            data["eps_surprise_last"] = round((actual - estimated) / abs(estimated) * 100, 2)

    # 7. Key metrics -- PE ratio, short interest
    metrics = await _fmp_get("/stable/key-metrics", {"symbol": ticker, "period": "quarter"}, cache_tier="daily")
    if metrics and isinstance(metrics, list) and len(metrics) > 0:
        m = metrics[0]
        data["pe_ratio"] = m.get("peRatio")
        short_pct = m.get("shortInterestPercentOfFloat")
        if short_pct is not None:
            data["short_interest_pct"] = short_pct

    # 8. Financial ratios -- extended fundamentals
    ratios = await get_financial_ratios(ticker)
    if ratios and isinstance(ratios, list) and len(ratios) > 0:
        r = ratios[0]
        data["forward_pe"] = r.get("priceEarningsToGrowthRatio")
        data["profit_margin"] = r.get("netProfitMargin")
        data["roe"] = r.get("returnOnEquity")
        data["debt_to_equity"] = r.get("debtEquityRatio")

    # 9. DCF valuation
    dcf_data = await get_dcf(ticker)
    if dcf_data and isinstance(dcf_data, list) and len(dcf_data) > 0:
        d = dcf_data[0]
        dcf_val = d.get("dcf")
        stock_price = d.get("Stock Price")
        data["dcf_value"] = dcf_val
        if dcf_val and stock_price and stock_price != 0:
            data["dcf_diff_pct"] = round((dcf_val - stock_price) / stock_price * 100, 2)

    # 10. Insider trading -- net buys/sells in last 90 days
    insider = await get_insider_trading(ticker)
    if insider and isinstance(insider, list):
        cutoff_date = date.today() - timedelta(days=90)
        net_shares = 0
        transactions = []
        for txn in insider[:50]:  # Limit to recent 50
            txn_date = _parse_date(txn.get("transactionDate", ""))
            if txn_date and txn_date >= cutoff_date:
                txn_type = txn.get("transactionType", "").lower()
                shares = txn.get("securitiesTransacted", 0) or 0
                if "purchase" in txn_type or "buy" in txn_type:
                    net_shares += shares
                elif "sale" in txn_type or "sell" in txn_type:
                    net_shares -= shares
                transactions.append(f"{txn.get('reportingName', 'Unknown')}: {txn_type} {shares}")
        data["insider_net_90d"] = float(net_shares)
        if transactions:
            data["insider_transactions_90d"] = "; ".join(transactions[:10])

    # 11. Institutional holders -- ownership percentage
    holders = await get_institutional_holders(ticker)
    if holders and isinstance(holders, list):
        # Sum top holders' percentages
        total_pct = sum(h.get("weight", 0) or 0 for h in holders[:20])
        if total_pct > 0:
            data["institutional_ownership_pct"] = round(total_pct * 100, 2) if total_pct <= 1 else round(total_pct, 2)

    # 12. Income statement -- revenue growth YoY
    income = await get_income_statement(ticker, period="annual")
    if income and isinstance(income, list) and len(income) >= 2:
        current_rev = income[0].get("revenue", 0) or 0
        prior_rev = income[1].get("revenue", 0) or 0
        if prior_rev and prior_rev != 0:
            data["revenue_growth_yoy"] = round((current_rev - prior_rev) / abs(prior_rev) * 100, 2)

    return data


# ══════════════════════════════════════════════════════════════════════
# UPSERT / REFRESH / QUERY  (existing functions, preserved)
# ══════════════════════════════════════════════════════════════════════

async def upsert_fundamentals(ticker: str, data: dict) -> None:
    """Insert or update fundamentals for a ticker."""
    async with async_session() as db:
        result = await db.execute(
            select(TickerFundamentals).where(TickerFundamentals.ticker == ticker)
        )
        existing = result.scalar_one_or_none()

        if existing:
            for key, value in data.items():
                if key != "ticker" and value is not None:
                    setattr(existing, key, value)
            existing.updated_at = utcnow()
        else:
            fundamentals = TickerFundamentals(**data)
            db.add(fundamentals)

        await db.commit()


async def refresh_ticker(ticker: str) -> bool:
    """Fetch and upsert fundamentals for a single ticker. Returns True on success."""
    try:
        data = await fetch_fundamentals_for_ticker(ticker)
        if data:
            await upsert_fundamentals(ticker, data)
            logger.info(f"Fundamentals refreshed for {ticker}")
            return True
        return False
    except Exception as e:
        logger.warning(f"Failed to refresh fundamentals for {ticker}: {e}")
        return False


async def refresh_all_watchlist_tickers() -> int:
    """Fetch fundamentals for all active watchlist tickers. Returns count refreshed."""
    from app.models.watchlist_ticker import WatchlistTicker

    try:
        async with async_session() as db:
            result = await db.execute(
                select(WatchlistTicker.ticker).where(WatchlistTicker.is_active == True)
            )
            tickers = [row[0] for row in result.all()]

        if not tickers:
            return 0

        settings = get_settings()
        if not settings.fmp_api_key:
            logger.info("FMP_API_KEY not set -- skipping fundamentals refresh")
            return 0

        refreshed = 0
        for ticker in tickers:
            if _is_throttled():
                logger.warning("FMP rate limit reached during watchlist refresh, stopping")
                break
            success = await refresh_ticker(ticker)
            if success:
                refreshed += 1

        logger.info(f"Fundamentals refresh complete: {refreshed}/{len(tickers)} tickers")
        return refreshed

    except Exception as e:
        logger.error(f"Watchlist fundamentals refresh failed: {e}")
        return 0


async def get_fundamentals(ticker: str) -> TickerFundamentals | None:
    """Get cached fundamentals for a ticker. Returns None if not cached."""
    try:
        async with async_session() as db:
            result = await db.execute(
                select(TickerFundamentals).where(TickerFundamentals.ticker == ticker)
            )
            return result.scalar_one_or_none()
    except Exception:
        return None


async def is_stale(ticker: str, max_hours: int = 168) -> bool:
    """Check if fundamentals for a ticker are stale (older than max_hours, default 7 days)."""
    fund = await get_fundamentals(ticker)
    if not fund:
        return True
    age = utcnow() - fund.updated_at
    return age > timedelta(hours=max_hours)


def format_fundamentals_for_prompt(fund: TickerFundamentals) -> str:
    """Format fundamentals into a concise text block for injection into AI prompts (~200-300 tokens)."""
    if not fund:
        return ""

    lines = []

    # Company identity (one line)
    name = fund.company_name or fund.ticker
    desc = fund.description or ""
    if desc:
        lines.append(f"{name}: {desc}")
    else:
        lines.append(name)

    # Sector / market cap
    parts = []
    if fund.sector:
        parts.append(f"Sector: {fund.sector}")
    if fund.industry:
        parts.append(f"Industry: {fund.industry}")
    if fund.market_cap:
        if fund.market_cap >= 1e12:
            parts.append(f"Mkt cap: ${fund.market_cap / 1e12:.1f}T")
        elif fund.market_cap >= 1e9:
            parts.append(f"Mkt cap: ${fund.market_cap / 1e9:.1f}B")
        elif fund.market_cap >= 1e6:
            parts.append(f"Mkt cap: ${fund.market_cap / 1e6:.0f}M")
    if parts:
        lines.append(" | ".join(parts))

    # Earnings
    if fund.earnings_date:
        days_until = (fund.earnings_date - date.today()).days
        time_label = f" ({fund.earnings_time.upper()})" if fund.earnings_time else ""
        if days_until >= 0:
            lines.append(f"Next earnings: {fund.earnings_date}{time_label} (in {days_until} days)")
        else:
            lines.append(f"Last earnings: {fund.earnings_date}{time_label}")

    # Analyst consensus
    analyst_parts = []
    if fund.analyst_rating:
        analyst_parts.append(f"Rating: {fund.analyst_rating}")
    if fund.analyst_target_consensus:
        target_str = f"Target: ${fund.analyst_target_consensus:.2f}"
        if fund.analyst_target_low and fund.analyst_target_high:
            target_str += f" (${fund.analyst_target_low:.2f}-${fund.analyst_target_high:.2f})"
        analyst_parts.append(target_str)
    if fund.analyst_count:
        analyst_parts.append(f"{fund.analyst_count} analysts")
    if analyst_parts:
        lines.append(" | ".join(analyst_parts))

    # EPS
    eps_parts = []
    if fund.eps_estimate_current is not None:
        eps_parts.append(f"EPS est: ${fund.eps_estimate_current:.2f}")
    if fund.eps_actual_last is not None:
        eps_parts.append(f"Last EPS: ${fund.eps_actual_last:.2f}")
    if fund.eps_surprise_last is not None:
        eps_parts.append(f"Surprise: {fund.eps_surprise_last:+.1f}%")
    if eps_parts:
        lines.append(" | ".join(eps_parts))

    # Valuation / short interest
    val_parts = []
    if fund.pe_ratio is not None:
        val_parts.append(f"P/E: {fund.pe_ratio:.1f}")
    if fund.short_interest_pct is not None and fund.short_interest_pct > 5:
        val_parts.append(f"Short interest: {fund.short_interest_pct:.1f}%")
    if val_parts:
        lines.append(" | ".join(val_parts))

    # Extended fundamentals
    ext_parts = []
    if fund.beta is not None:
        ext_parts.append(f"Beta: {fund.beta:.2f}")
    if fund.roe is not None:
        ext_parts.append(f"ROE: {fund.roe:.1f}%")
    if fund.profit_margin is not None:
        ext_parts.append(f"Margin: {fund.profit_margin:.1f}%")
    if fund.debt_to_equity is not None:
        ext_parts.append(f"D/E: {fund.debt_to_equity:.2f}")
    if ext_parts:
        lines.append(" | ".join(ext_parts))

    # DCF
    if fund.dcf_value is not None:
        dcf_line = f"DCF: ${fund.dcf_value:.2f}"
        if fund.dcf_diff_pct is not None:
            dcf_line += f" ({fund.dcf_diff_pct:+.1f}% vs price)"
        lines.append(dcf_line)

    # Insider / institutional
    insider_parts = []
    if fund.insider_net_90d is not None and fund.insider_net_90d != 0:
        direction = "net buying" if fund.insider_net_90d > 0 else "net selling"
        insider_parts.append(f"Insider: {direction} ({abs(fund.insider_net_90d):.0f} shares 90d)")
    if fund.institutional_ownership_pct is not None:
        insider_parts.append(f"Inst. ownership: {fund.institutional_ownership_pct:.1f}%")
    if insider_parts:
        lines.append(" | ".join(insider_parts))

    # Revenue growth
    if fund.revenue_growth_yoy is not None:
        lines.append(f"Revenue growth YoY: {fund.revenue_growth_yoy:+.1f}%")

    # Staleness warning
    if fund.updated_at:
        age_hours = (utcnow() - fund.updated_at).total_seconds() / 3600
        if age_hours > 168:  # 7 days
            lines.append(f"[Data is {age_hours / 24:.0f} days old -- may be stale]")

    return "\n  ".join(lines)
