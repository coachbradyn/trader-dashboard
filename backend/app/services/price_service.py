import asyncio
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
        now = datetime.now(timezone.utc)
        # US Eastern: UTC-5 (EST) or UTC-4 (EDT)
        # Approximate: market open ~14:30 UTC, close ~21:00 UTC
        hour = now.hour
        weekday = now.weekday()
        if weekday >= 5:  # Saturday/Sunday
            return False
        return 14 <= hour < 21

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
                    now = datetime.now(timezone.utc).isoformat()
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
