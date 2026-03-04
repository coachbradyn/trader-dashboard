"""Register a new trader/strategy and get back an API key for webhooks.

Usage:
    python -m app.cli.register_trader \
        --trader-id henry-v36 \
        --display-name "Henry v3.6" \
        --strategy-name "Henry v3.6 (Momentum Exit Edition)" \
        --description "Kalman + LMA momentum strategy with ADX filter"
"""
import argparse
import asyncio

from sqlalchemy import select

from app.database import async_session
from app.models import Trader
from app.utils.auth import generate_api_key, hash_api_key


async def main(args):
    async with async_session() as db:
        # Check if trader_id already exists
        result = await db.execute(select(Trader).where(Trader.trader_id == args.trader_id))
        if result.scalar_one_or_none():
            print(f"ERROR: Trader with id '{args.trader_id}' already exists.")
            return

        raw_key = generate_api_key()
        hashed = hash_api_key(raw_key)

        trader = Trader(
            trader_id=args.trader_id,
            display_name=args.display_name,
            strategy_name=args.strategy_name,
            description=args.description or "",
            api_key_hash=hashed,
        )
        db.add(trader)
        await db.commit()

        print(f"Trader registered: {args.display_name} ({args.trader_id})")
        print(f"Database ID: {trader.id}")
        print()
        print("=== SAVE THIS API KEY — it cannot be recovered ===")
        print(f"API Key: {raw_key}")
        print("===================================================")
        print()
        print("Use this key in TradingView alert JSON as the 'key' field.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Register a new trader")
    parser.add_argument("--trader-id", required=True, help="URL-safe slug, e.g. henry-v36")
    parser.add_argument("--display-name", required=True, help="Display name, e.g. Henry v3.6")
    parser.add_argument("--strategy-name", required=True, help="Strategy name")
    parser.add_argument("--description", default="", help="Strategy description")
    asyncio.run(main(parser.parse_args()))
