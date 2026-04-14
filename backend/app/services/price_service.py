import asyncio
from app.utils.utc import utcnow
from datetime import datetime, timezone

import httpx

from app.config import get_settings


class PriceService:
    def __init__(self):
        self.cache: dict[str, dict] = {}  # {ticker: {price, timestamp}}
        self._tickers: set[str] = set()

    def add_ticker(self, ticker: str):
        self._tickers.add(ticker.upper())

    def remove_ticker(self, ticker: str):
        self._tickers.discard(ticker.upper())

    def get_price(self, ticker: str) -> float | None:
        entry = self.cache.get(ticker.upper())
        return entry["price"] if entry else None

    def _is_market_hours(self) -> bool:
        from zoneinfo import ZoneInfo
        now_et = datetime.now(ZoneInfo("America/New_York"))
        weekday = now_et.weekday()
        if weekday >= 5:  # Saturday/Sunday
            return False
        hour = now_et.hour
        minute = now_et.minute
        # Market open 9:30 AM ET, close 4:00 PM ET
        if hour < 9 or (hour == 9 and minute < 30):
            return False
        if hour >= 16:
            return False
        return True

    async def _fetch_prices(self):
        settings = get_settings()
        if not settings.alpaca_api_key or not self._tickers:
            return

        tickers = list(self._tickers)
        symbols = ",".join(tickers)

        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"{settings.alpaca_base_url}/v2/stocks/snapshots",
                    # NOTE: Using "iex" feed (free) — only shows IEX exchange data.
                    # For accurate NBBO prices across all exchanges, upgrade to
                    # Alpaca's paid data subscription and change to "sip".
                    params={"symbols": symbols, "feed": "iex"},
                    headers={
                        "APCA-API-KEY-ID": settings.alpaca_api_key,
                        "APCA-API-SECRET-KEY": settings.alpaca_secret_key,
                    },
                    timeout=10,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    now = utcnow().isoformat()
                    for ticker, snapshot in data.items():
                        latest_trade = snapshot.get("latestTrade", {})
                        price = latest_trade.get("p", 0)
                        if price > 0:
                            self.cache[ticker.upper()] = {
                                "price": price,
                                "timestamp": now,
                            }
        except Exception:
            pass  # Silently fail — cache retains last known prices

    async def _update_options_positions(self):
        """Refresh current_premium and greeks_current on all open options
        legs. Shares cadence with equity polling; Alpaca's batch quote
        endpoint makes this cheap. Non-critical — silent failures can't
        break the equity loop.
        """
        try:
            from app.database import async_session
            from app.models.options_trade import OptionsTrade
            from app.services.options_service import update_positions_live_data
            from sqlalchemy import select

            async with async_session() as db:
                result = await db.execute(
                    select(OptionsTrade).where(OptionsTrade.status == "open")
                )
                rows = list(result.scalars().all())
                if not rows:
                    return
                updated = await update_positions_live_data(rows)
                if updated:
                    await db.commit()
        except Exception:
            pass

    async def run(self):
        settings = get_settings()
        while True:
            await self._fetch_prices()
            await self._update_options_positions()
            interval = (
                settings.price_poll_interval_market
                if self._is_market_hours()
                else settings.price_poll_interval_closed
            )
            await asyncio.sleep(interval)


# Singleton
price_service = PriceService()
