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
import json
import logging
from datetime import datetime, date, timedelta

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
_RATE_WARN = 250
_RATE_HARD = 275
_RATE_DAILY_LIMIT = 300


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
                age = (datetime.utcnow() - entry.cached_at).total_seconds()
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
                existing.cached_at = datetime.utcnow()
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
) -> dict | list | None:
    """
    Make a GET request to FMP with caching and rate limiting.
    Returns parsed JSON or None on failure.
    """
    settings = get_settings()
    if not settings.fmp_api_key:
        return None

    # Check cache first
    cached = await _get_from_cache(endpoint, params, cache_tier)
    if cached is not None:
        return cached

    # Rate-limit check
    if _is_throttled(essential=essential):
        logger.warning(f"FMP rate limit reached, skipping {endpoint}")
        return None

    url = f"{FMP_BASE}{endpoint}"
    query = {"apikey": settings.fmp_api_key}
    if params:
        query.update(params)

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(url, params=query)
            _increment_rate_counter()
            if resp.status_code == 200:
                data = resp.json()
                await _set_cache(endpoint, params, cache_tier, data)
                return data
            logger.warning(f"FMP API {resp.status_code} for {endpoint}: {resp.text[:200]}")
            return None
    except Exception as e:
        logger.warning(f"FMP API error for {endpoint}: {e}")
        return None


# ══════════════════════════════════════════════════════════════════════
# ENDPOINT METHODS
# ══════════════════════════════════════════════════════════════════════

async def get_quote(ticker: str) -> dict | list | None:
    """Real-time quote for a single ticker."""
    return await _fmp_get(f"/api/v3/quote/{ticker}", cache_tier="realtime")


async def get_batch_quotes(tickers: list[str]) -> dict | list | None:
    """Real-time quotes for multiple tickers."""
    csv = ",".join(tickers)
    return await _fmp_get(f"/api/v3/quote/{csv}", cache_tier="realtime")


async def get_historical_daily(ticker: str, days: int = 90) -> dict | list | None:
    """Historical daily OHLCV."""
    return await _fmp_get(
        f"/api/v3/historical-price-full/{ticker}",
        params={"timeseries": str(days)},
        cache_tier="intraday",
    )


async def get_intraday(ticker: str, interval: str = "5min") -> dict | list | None:
    """Intraday chart data."""
    return await _fmp_get(f"/api/v3/historical-chart/{interval}/{ticker}", cache_tier="realtime")


async def get_technical_indicator(
    ticker: str,
    indicator: str,
    period: int = 14,
    interval: str = "daily",
) -> dict | list | None:
    """Technical indicator (RSI, SMA, EMA, ADX, etc.)."""
    return await _fmp_get(
        f"/api/v3/technical_indicator/{interval}/{ticker}",
        params={"type": indicator, "period": str(period)},
        cache_tier="intraday",
    )


async def get_income_statement(ticker: str, period: str = "quarter") -> dict | list | None:
    """Income statement."""
    return await _fmp_get(
        f"/api/v3/income-statement/{ticker}",
        params={"period": period},
        cache_tier="daily",
    )


async def get_balance_sheet(ticker: str, period: str = "quarter") -> dict | list | None:
    """Balance sheet statement."""
    return await _fmp_get(
        f"/api/v3/balance-sheet-statement/{ticker}",
        params={"period": period},
        cache_tier="daily",
    )


async def get_cash_flow(ticker: str, period: str = "quarter") -> dict | list | None:
    """Cash flow statement."""
    return await _fmp_get(
        f"/api/v3/cash-flow-statement/{ticker}",
        params={"period": period},
        cache_tier="daily",
    )


async def get_financial_ratios(ticker: str) -> dict | list | None:
    """Financial ratios."""
    return await _fmp_get(f"/api/v3/ratios/{ticker}", cache_tier="daily")


async def get_dcf(ticker: str) -> dict | list | None:
    """Discounted cash flow valuation."""
    return await _fmp_get(f"/api/v3/discounted-cash-flow/{ticker}", cache_tier="daily")


async def get_insider_trading(ticker: str) -> dict | list | None:
    """Insider trading transactions."""
    return await _fmp_get(
        f"/api/v4/insider-trading",
        params={"symbol": ticker},
        cache_tier="daily",
    )


async def get_institutional_holders(ticker: str) -> dict | list | None:
    """Institutional holders."""
    return await _fmp_get(f"/api/v3/institutional-holder/{ticker}", cache_tier="daily")


async def get_earnings_calendar(from_date: str, to_date: str) -> dict | list | None:
    """Earnings calendar for a date range."""
    return await _fmp_get(
        f"/api/v3/earning_calendar",
        params={"from": from_date, "to": to_date},
        cache_tier="daily",
    )


async def get_economic_calendar() -> dict | list | None:
    """Economic calendar."""
    return await _fmp_get(f"/api/v3/economic_calendar", cache_tier="daily")


async def get_gainers() -> dict | list | None:
    """Market gainers."""
    return await _fmp_get(f"/api/v3/stock_market/gainers", cache_tier="intraday")


async def get_losers() -> dict | list | None:
    """Market losers."""
    return await _fmp_get(f"/api/v3/stock_market/losers", cache_tier="intraday")


async def get_most_active() -> dict | list | None:
    """Most active stocks."""
    return await _fmp_get(f"/api/v3/stock_market/actives", cache_tier="intraday")


async def get_sector_performance() -> dict | list | None:
    """Sector performance."""
    return await _fmp_get(f"/api/v3/sector-performance", cache_tier="intraday")


async def run_screener(params: dict) -> dict | list | None:
    """Stock screener with arbitrary filter params."""
    return await _fmp_get(
        f"/api/v3/stock-screener",
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

    data: dict = {"ticker": ticker, "updated_at": datetime.utcnow()}

    # 1. Company profile
    profile = await _fmp_get(f"/api/v3/profile/{ticker}", cache_tier="daily")
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
    earnings = await _fmp_get(f"/api/v3/earning_calendar", {"symbol": ticker}, cache_tier="daily")
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
    estimates = await _fmp_get(f"/api/v3/analyst-estimates/{ticker}", cache_tier="daily")
    if estimates and isinstance(estimates, list) and len(estimates) > 0:
        est = estimates[0]
        data["eps_estimate_current"] = est.get("estimatedEpsAvg")
        data["revenue_estimate_current"] = est.get("estimatedRevenueAvg")

    # 4. Price target consensus
    targets = await _fmp_get(f"/api/v4/price-target-consensus", {"symbol": ticker}, cache_tier="daily")
    if targets and isinstance(targets, list) and len(targets) > 0:
        t = targets[0]
        data["analyst_target_low"] = t.get("targetLow")
        data["analyst_target_high"] = t.get("targetHigh")
        data["analyst_target_consensus"] = t.get("targetConsensus")

    # 5. Analyst grades -- consensus rating
    grades = await _fmp_get(f"/api/v3/grade/{ticker}", cache_tier="daily")
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
    surprises = await _fmp_get(f"/api/v3/earnings-surprises/{ticker}", cache_tier="daily")
    if surprises and isinstance(surprises, list) and len(surprises) > 0:
        last = surprises[0]
        data["eps_actual_last"] = last.get("actualEarningResult")
        estimated = last.get("estimatedEarning")
        actual = last.get("actualEarningResult")
        if estimated and actual and estimated != 0:
            data["eps_surprise_last"] = round((actual - estimated) / abs(estimated) * 100, 2)

    # 7. Key metrics -- PE ratio, short interest
    metrics = await _fmp_get(f"/api/v3/key-metrics/{ticker}", {"period": "quarter"}, cache_tier="daily")
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
            existing.updated_at = datetime.utcnow()
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


async def is_stale(ticker: str, max_hours: int = 48) -> bool:
    """Check if fundamentals for a ticker are stale (older than max_hours)."""
    fund = await get_fundamentals(ticker)
    if not fund:
        return True
    age = datetime.utcnow() - fund.updated_at
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
        age_hours = (datetime.utcnow() - fund.updated_at).total_seconds() / 3600
        if age_hours > 48:
            lines.append(f"[Data is {age_hours / 24:.0f} days old -- may be stale]")

    return "\n  ".join(lines)
