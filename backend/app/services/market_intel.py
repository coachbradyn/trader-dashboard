"""
Market Intelligence Service
============================
Pulls rich market context data for Henry's morning briefing.
Uses Alpaca (news, movers) + yfinance (gaps, sector ETFs, earnings).

All functions are async-safe (yfinance calls run in thread pool).
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)


# ─── ALPACA NEWS API ──────────────────────────────────────────────────────────

async def fetch_news(tickers: list[str] = None, limit: int = 15) -> list[dict]:
    """Fetch recent news from Alpaca News API.

    If tickers provided, gets news for those symbols.
    Otherwise gets general market news.
    """
    settings = get_settings()
    if not settings.alpaca_api_key:
        return []

    params = {"limit": limit, "sort": "desc"}
    if tickers:
        params["symbols"] = ",".join(tickers[:10])  # Alpaca limits to 10

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
                articles = data.get("news", [])
                return [
                    {
                        "headline": a.get("headline", ""),
                        "summary": (a.get("summary", "") or "")[:200],
                        "source": a.get("source", ""),
                        "symbols": a.get("symbols", []),
                        "created_at": a.get("created_at", ""),
                        "url": a.get("url", ""),
                    }
                    for a in articles
                ]
            else:
                logger.warning(f"Alpaca news API returned {resp.status_code}: {resp.text[:200]}")
    except Exception as e:
        logger.error(f"Alpaca news fetch failed: {e}")

    return []


# ─── ALPACA MOVERS / SNAPSHOTS ────────────────────────────────────────────────

async def fetch_market_snapshot(tickers: list[str]) -> dict[str, dict]:
    """Get current snapshots for tickers (price, prev close, volume, change %)."""
    settings = get_settings()
    if not settings.alpaca_api_key or not tickers:
        return {}

    try:
        symbols = ",".join(tickers[:50])
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{settings.alpaca_base_url}/v2/stocks/snapshots",
                params={"symbols": symbols, "feed": "iex"},
                headers={
                    "APCA-API-KEY-ID": settings.alpaca_api_key,
                    "APCA-API-SECRET-KEY": settings.alpaca_secret_key,
                },
                timeout=10,
            )
            if resp.status_code == 200:
                data = resp.json()
                result = {}
                for ticker, snap in data.items():
                    trade = snap.get("latestTrade", {})
                    daily_bar = snap.get("dailyBar", {})
                    prev_daily = snap.get("prevDailyBar", {})
                    minute_bar = snap.get("minuteBar", {})

                    current_price = trade.get("p", 0)
                    prev_close = prev_daily.get("c", 0)
                    change_pct = ((current_price - prev_close) / prev_close * 100) if prev_close > 0 else 0

                    result[ticker] = {
                        "price": current_price,
                        "prev_close": prev_close,
                        "change_pct": round(change_pct, 2),
                        "volume": daily_bar.get("v", 0),
                        "high": daily_bar.get("h", 0),
                        "low": daily_bar.get("l", 0),
                        "open": daily_bar.get("o", 0),
                    }
                return result
    except Exception as e:
        logger.error(f"Market snapshot failed: {e}")

    return {}


async def fetch_top_movers() -> dict:
    """Fetch top gainers and losers from Alpaca."""
    settings = get_settings()
    if not settings.alpaca_api_key:
        return {"gainers": [], "losers": []}

    result = {"gainers": [], "losers": []}
    try:
        async with httpx.AsyncClient() as client:
            for direction in ["gainers", "losers"]:
                resp = await client.get(
                    f"{settings.alpaca_base_url}/v1beta1/screener/stocks/most-actives",
                    params={"by": "change", "top": 5},
                    headers={
                        "APCA-API-KEY-ID": settings.alpaca_api_key,
                        "APCA-API-SECRET-KEY": settings.alpaca_secret_key,
                    },
                    timeout=10,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    movers = data.get("most_actives", [])
                    result[direction] = [
                        {
                            "symbol": m.get("symbol", ""),
                            "change_pct": m.get("change", 0),
                            "volume": m.get("volume", 0),
                            "trade_count": m.get("trade_count", 0),
                        }
                        for m in movers[:5]
                    ]
    except Exception as e:
        logger.warning(f"Movers fetch failed: {e}")

    return result


# ─── YFINANCE: GAPS, SECTORS, EARNINGS ────────────────────────────────────────

def _fetch_premarket_gaps(tickers: list[str]) -> list[dict]:
    """Get pre-market gap data for held tickers (sync, run in thread)."""
    try:
        import yfinance as yf
        gaps = []
        for ticker in tickers[:15]:
            try:
                tk = yf.Ticker(ticker)
                info = tk.fast_info
                prev_close = getattr(info, "previous_close", None)
                current = getattr(info, "last_price", None)
                if prev_close and current and prev_close > 0:
                    gap_pct = (current - prev_close) / prev_close * 100
                    if abs(gap_pct) > 0.5:  # Only report gaps > 0.5%
                        gaps.append({
                            "ticker": ticker,
                            "prev_close": round(prev_close, 2),
                            "current": round(current, 2),
                            "gap_pct": round(gap_pct, 2),
                        })
            except Exception:
                continue
        return sorted(gaps, key=lambda x: abs(x["gap_pct"]), reverse=True)
    except Exception as e:
        logger.error(f"Pre-market gaps failed: {e}")
        return []


def _fetch_sector_performance() -> list[dict]:
    """Get sector ETF performance (sync, run in thread)."""
    try:
        import yfinance as yf

        sector_etfs = {
            "XLK": "Technology",
            "XLF": "Financials",
            "XLE": "Energy",
            "XLV": "Healthcare",
            "XLI": "Industrials",
            "XLY": "Consumer Disc.",
            "XLP": "Consumer Staples",
            "XLB": "Materials",
            "XLU": "Utilities",
            "XLRE": "Real Estate",
            "XLC": "Communication",
        }

        tickers_str = " ".join(sector_etfs.keys())
        data = yf.download(tickers_str, period="2d", interval="1d", progress=False, threads=True)

        if data.empty:
            return []

        results = []
        close = data["Close"]
        for etf, sector_name in sector_etfs.items():
            try:
                if etf in close.columns and len(close[etf].dropna()) >= 2:
                    vals = close[etf].dropna().values
                    prev = vals[-2]
                    curr = vals[-1]
                    change = (curr - prev) / prev * 100
                    results.append({
                        "etf": etf,
                        "sector": sector_name,
                        "change_pct": round(change, 2),
                    })
            except Exception:
                continue

        return sorted(results, key=lambda x: x["change_pct"], reverse=True)
    except Exception as e:
        logger.error(f"Sector performance failed: {e}")
        return []


def _fetch_earnings_calendar(tickers: list[str]) -> list[dict]:
    """Check if any held tickers have upcoming earnings (sync, run in thread)."""
    try:
        import yfinance as yf
        upcoming = []
        today = datetime.now().date()
        week_ahead = today + timedelta(days=7)

        for ticker in tickers[:15]:
            try:
                tk = yf.Ticker(ticker)
                cal = tk.calendar
                if cal is not None and not cal.empty:
                    # calendar returns a DataFrame with earnings date
                    if "Earnings Date" in cal.index:
                        dates = cal.loc["Earnings Date"]
                        for d in dates:
                            if hasattr(d, 'date'):
                                d = d.date()
                            if today <= d <= week_ahead:
                                upcoming.append({
                                    "ticker": ticker,
                                    "earnings_date": str(d),
                                    "days_away": (d - today).days,
                                })
                                break
            except Exception:
                continue

        return sorted(upcoming, key=lambda x: x["days_away"])
    except Exception as e:
        logger.error(f"Earnings calendar failed: {e}")
        return []


def _fetch_vix_context() -> dict:
    """Get VIX level + recent trend (sync, run in thread)."""
    try:
        import yfinance as yf
        vix = yf.Ticker("^VIX")
        hist = vix.history(period="5d", interval="1d")
        if hist.empty:
            return {}

        closes = hist["Close"].values
        current = round(float(closes[-1]), 2)
        prev = round(float(closes[-2]), 2) if len(closes) >= 2 else current
        week_ago = round(float(closes[0]), 2) if len(closes) >= 5 else current

        # Classify regime
        if current < 15:
            regime = "low volatility"
        elif current < 20:
            regime = "normal"
        elif current < 25:
            regime = "elevated"
        elif current < 30:
            regime = "high"
        else:
            regime = "extreme fear"

        return {
            "current": current,
            "prev_close": prev,
            "change": round(current - prev, 2),
            "week_ago": week_ago,
            "5d_trend": "rising" if current > week_ago + 1 else ("falling" if current < week_ago - 1 else "flat"),
            "regime": regime,
        }
    except Exception as e:
        logger.error(f"VIX context failed: {e}")
        return {}


def _fetch_spy_context() -> dict:
    """Get SPY price, change, and key levels (sync, run in thread)."""
    try:
        import yfinance as yf
        spy = yf.Ticker("SPY")
        hist = spy.history(period="5d", interval="1d")
        if hist.empty:
            return {}

        latest = hist.iloc[-1]
        prev = hist.iloc[-2] if len(hist) >= 2 else latest

        current_close = round(float(latest["Close"]), 2)
        prev_close = round(float(prev["Close"]), 2)
        change_pct = round((current_close - prev_close) / prev_close * 100, 2)

        # 5-day range
        high_5d = round(float(hist["High"].max()), 2)
        low_5d = round(float(hist["Low"].min()), 2)

        return {
            "price": current_close,
            "prev_close": prev_close,
            "change_pct": change_pct,
            "5d_high": high_5d,
            "5d_low": low_5d,
            "volume": int(latest["Volume"]),
        }
    except Exception as e:
        logger.error(f"SPY context failed: {e}")
        return {}


# ─── MAIN AGGREGATOR ──────────────────────────────────────────────────────────

async def gather_market_intel(held_tickers: list[str]) -> dict:
    """
    Gather all market intelligence in parallel.
    Returns a rich dict for Henry's morning briefing prompt.

    Args:
        held_tickers: Tickers the user currently holds
    """
    # Run yfinance calls in thread pool (they're sync)
    # Run Alpaca calls as async
    # All in parallel
    all_tickers = list(set(held_tickers + ["SPY", "QQQ"]))

    (
        news_held,
        news_general,
        snapshots,
        movers,
        gaps,
        sectors,
        earnings,
        vix,
        spy,
    ) = await asyncio.gather(
        fetch_news(held_tickers, limit=10),
        fetch_news(limit=10),
        fetch_market_snapshot(all_tickers),
        fetch_top_movers(),
        asyncio.to_thread(_fetch_premarket_gaps, held_tickers),
        asyncio.to_thread(_fetch_sector_performance),
        asyncio.to_thread(_fetch_earnings_calendar, held_tickers),
        asyncio.to_thread(_fetch_vix_context),
        asyncio.to_thread(_fetch_spy_context),
        return_exceptions=True,
    )

    # Handle any failures gracefully
    def safe(val, default):
        return val if not isinstance(val, Exception) else default

    return {
        "news_portfolio": safe(news_held, []),
        "news_general": safe(news_general, []),
        "snapshots": safe(snapshots, {}),
        "movers": safe(movers, {"gainers": [], "losers": []}),
        "premarket_gaps": safe(gaps, []),
        "sectors": safe(sectors, []),
        "earnings": safe(earnings, []),
        "vix": safe(vix, {}),
        "spy": safe(spy, {}),
        "gathered_at": datetime.now(timezone.utc).isoformat(),
    }
