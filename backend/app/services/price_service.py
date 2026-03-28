import asyncio
from datetime import datetime

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
                    params={"symbols": symbols, "feed": "iex"},
                    headers={
                        "APCA-API-KEY-ID": settings.alpaca_api_key,
                        "APCA-API-SECRET-KEY": settings.alpaca_secret_key,
                    },
                    timeout=10,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    now = datetime.utcnow().isoformat()
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

    async def run(self):
        settings = get_settings()
        while True:
            await self._fetch_prices()
            interval = (
                settings.price_poll_interval_market
                if self._is_market_hours()
                else settings.price_poll_interval_closed
            )
            await asyncio.sleep(interval)


# Singleton
price_service = PriceService()
