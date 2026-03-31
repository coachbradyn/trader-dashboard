"""
Financial Modeling Prep (FMP) Service
=====================================
Fetches and caches structured financial data for watchlist tickers.
Runs as background jobs — never blocks AI calls.

Endpoints used:
  - /api/v3/profile/{ticker}              — company profile
  - /api/v3/earning_calendar              — next earnings date
  - /api/v3/analyst-estimates/{ticker}    — EPS/revenue estimates
  - /api/v4/price-target-consensus        — analyst price targets
  - /api/v3/grade/{ticker}                — analyst ratings
  - /api/v3/earnings-surprises/{ticker}   — last quarter surprise
  - /api/v3/key-metrics/{ticker}          — PE, short interest
"""

import logging
from datetime import datetime, date, timedelta

import httpx

from app.config import get_settings
from app.database import async_session
from app.models.ticker_fundamentals import TickerFundamentals
from sqlalchemy import select

logger = logging.getLogger(__name__)

FMP_BASE = "https://financialmodelingprep.com"


async def _fmp_get(endpoint: str, params: dict | None = None) -> dict | list | None:
    """Make a GET request to the FMP API. Returns parsed JSON or None on failure."""
    settings = get_settings()
    if not settings.fmp_api_key:
        return None

    url = f"{FMP_BASE}{endpoint}"
    query = {"apikey": settings.fmp_api_key}
    if params:
        query.update(params)

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(url, params=query)
            if resp.status_code == 200:
                return resp.json()
            logger.warning(f"FMP API {resp.status_code} for {endpoint}: {resp.text[:200]}")
            return None
    except Exception as e:
        logger.warning(f"FMP API error for {endpoint}: {e}")
        return None


async def fetch_fundamentals_for_ticker(ticker: str) -> dict | None:
    """
    Fetch all fundamental data for a single ticker from FMP.
    Returns a dict of fields to upsert into ticker_fundamentals, or None on failure.
    """
    settings = get_settings()
    if not settings.fmp_api_key:
        return None

    data = {"ticker": ticker, "updated_at": datetime.utcnow()}

    # 1. Company profile
    profile = await _fmp_get(f"/api/v3/profile/{ticker}")
    if profile and isinstance(profile, list) and len(profile) > 0:
        p = profile[0]
        data["company_name"] = p.get("companyName")
        data["sector"] = p.get("sector")
        data["industry"] = p.get("industry")
        data["market_cap"] = p.get("mktCap")
        # Truncate description to one sentence for token efficiency
        desc = p.get("description", "")
        if desc:
            first_sentence = desc.split(". ")[0] + "." if ". " in desc else desc[:300]
            data["description"] = first_sentence[:500]

    # 2. Earnings calendar — next upcoming date
    earnings = await _fmp_get(f"/api/v3/earning_calendar", {"symbol": ticker})
    if earnings and isinstance(earnings, list):
        today = date.today()
        future_earnings = [
            e for e in earnings
            if e.get("date") and _parse_date(e["date"]) and _parse_date(e["date"]) >= today
        ]
        if future_earnings:
            # Sort by date ascending, take the nearest
            future_earnings.sort(key=lambda e: e["date"])
            nearest = future_earnings[0]
            data["earnings_date"] = _parse_date(nearest["date"])
            time_str = nearest.get("time", "")
            if time_str:
                data["earnings_time"] = "bmo" if "bmo" in time_str.lower() or "before" in time_str.lower() else "amc"

    # 3. Analyst estimates — current quarter EPS/revenue
    estimates = await _fmp_get(f"/api/v3/analyst-estimates/{ticker}")
    if estimates and isinstance(estimates, list) and len(estimates) > 0:
        # First entry is typically current/next quarter
        est = estimates[0]
        data["eps_estimate_current"] = est.get("estimatedEpsAvg")
        data["revenue_estimate_current"] = est.get("estimatedRevenueAvg")

    # 4. Price target consensus
    targets = await _fmp_get(f"/api/v4/price-target-consensus", {"symbol": ticker})
    if targets and isinstance(targets, list) and len(targets) > 0:
        t = targets[0]
        data["analyst_target_low"] = t.get("targetLow")
        data["analyst_target_high"] = t.get("targetHigh")
        data["analyst_target_consensus"] = t.get("targetConsensus")

    # 5. Analyst grades — consensus rating
    grades = await _fmp_get(f"/api/v3/grade/{ticker}")
    if grades and isinstance(grades, list) and len(grades) > 0:
        # Count recent grades (last 90 days) to determine consensus
        recent_grades = grades[:20]  # Most recent 20
        grade_counts = {}
        for g in recent_grades:
            new_grade = g.get("newGrade", "")
            if new_grade:
                grade_counts[new_grade] = grade_counts.get(new_grade, 0) + 1
        if grade_counts:
            consensus_grade = max(grade_counts, key=grade_counts.get)
            data["analyst_rating"] = consensus_grade
            data["analyst_count"] = sum(grade_counts.values())

    # 6. Earnings surprises — last quarter
    surprises = await _fmp_get(f"/api/v3/earnings-surprises/{ticker}")
    if surprises and isinstance(surprises, list) and len(surprises) > 0:
        last = surprises[0]
        data["eps_actual_last"] = last.get("actualEarningResult")
        estimated = last.get("estimatedEarning")
        actual = last.get("actualEarningResult")
        if estimated and actual and estimated != 0:
            data["eps_surprise_last"] = round((actual - estimated) / abs(estimated) * 100, 2)

    # 7. Key metrics — PE ratio, short interest
    metrics = await _fmp_get(f"/api/v3/key-metrics/{ticker}", {"period": "quarter"})
    if metrics and isinstance(metrics, list) and len(metrics) > 0:
        m = metrics[0]
        data["pe_ratio"] = m.get("peRatio")
        # Short interest as percentage (some FMP plans include this)
        short_pct = m.get("shortInterestPercentOfFloat")
        if short_pct is not None:
            data["short_interest_pct"] = short_pct

    return data


def _parse_date(date_str: str) -> date | None:
    """Parse a date string from FMP (YYYY-MM-DD format)."""
    try:
        return datetime.strptime(date_str[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


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
            logger.info("FMP_API_KEY not set — skipping fundamentals refresh")
            return 0

        refreshed = 0
        for ticker in tickers:
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

    # Staleness warning
    if fund.updated_at:
        age_hours = (datetime.utcnow() - fund.updated_at).total_seconds() / 3600
        if age_hours > 48:
            lines.append(f"[Data is {age_hours / 24:.0f} days old — may be stale]")

    return "\n  ".join(lines)
