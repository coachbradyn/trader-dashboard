"""Seed the database with Henry v3.6 trader + default portfolios.

Usage:
    python -m app.cli.seed_demo
"""
import asyncio

from sqlalchemy import select

from app.database import async_session
from app.models import Trader, Portfolio, PortfolioStrategy
from app.utils.auth import generate_api_key, hash_api_key


PORTFOLIOS = [
    {
        "name": "Buy Only",
        "description": "Long-only trades from Henry v3.6 — captures upside momentum plays.",
        "capital": 10000,
        "direction_filter": "long",
    },
    {
        "name": "Full Henry",
        "description": "All trades (long + short) from Henry v3.6 — full strategy exposure.",
        "capital": 10000,
        "direction_filter": None,
    },
    {
        "name": "Aggressive",
        "description": "Henry v3.6 aggressive profile — higher capital, full exposure.",
        "capital": 25000,
        "direction_filter": None,
    },
]


async def main():
    async with async_session() as db:
        # Check if already seeded
        result = await db.execute(select(Trader).where(Trader.trader_id == "henry-v36"))
        if result.scalar_one_or_none():
            print("Database already seeded. Trader 'henry-v36' exists.")
            return

        # Create trader
        raw_key = generate_api_key()
        hashed = hash_api_key(raw_key)

        trader = Trader(
            trader_id="henry-v36",
            display_name="Henry v3.6",
            strategy_name="Henry v3.6 (Momentum Exit Edition)",
            description="Kalman filter + LMA crossover momentum strategy with ADX filter, multi-timeframe analysis, and adaptive exit logic.",
            api_key_hash=hashed,
        )
        db.add(trader)
        await db.flush()

        print(f"Trader created: Henry v3.6 (ID: {trader.id})")

        # Create portfolios
        for pdef in PORTFOLIOS:
            portfolio = Portfolio(
                name=pdef["name"],
                description=pdef["description"],
                initial_capital=pdef["capital"],
                cash=pdef["capital"],
            )
            db.add(portfolio)
            await db.flush()

            link = PortfolioStrategy(
                portfolio_id=portfolio.id,
                trader_id=trader.id,
                direction_filter=pdef["direction_filter"],
            )
            db.add(link)

            filter_label = pdef["direction_filter"] or "all"
            print(f"Portfolio created: {pdef['name']} (${pdef['capital']:,}, {filter_label} directions)")

        await db.commit()

        print()
        print("=== SAVE THIS API KEY — it cannot be recovered ===")
        print(f"API Key: {raw_key}")
        print("===================================================")
        print()
        print("Use this key in your TradingView alert JSON as the 'key' field.")


if __name__ == "__main__":
    asyncio.run(main())
