"""Create a portfolio and link it to one or more trader strategies.

Usage:
    python -m app.cli.create_portfolio \
        --name "Buy Only" \
        --description "Long-only trades from Henry v3.6" \
        --capital 10000 \
        --strategy henry-v36:long

    python -m app.cli.create_portfolio \
        --name "Full Henry" \
        --description "All trades from Henry v3.6" \
        --capital 10000 \
        --strategy henry-v36

    python -m app.cli.create_portfolio \
        --name "Two Strategy" \
        --description "Henry v3.6 + Future Strategy B" \
        --capital 15000 \
        --strategy henry-v36 \
        --strategy strategy-b
"""
import argparse
import asyncio

from sqlalchemy import select

from app.database import async_session
from app.models import Portfolio, Trader, PortfolioStrategy


async def main(args):
    async with async_session() as db:
        # Check if portfolio name exists
        result = await db.execute(select(Portfolio).where(Portfolio.name == args.name))
        if result.scalar_one_or_none():
            print(f"ERROR: Portfolio '{args.name}' already exists.")
            return

        portfolio = Portfolio(
            name=args.name,
            description=args.description or "",
            initial_capital=args.capital,
            cash=args.capital,
        )
        db.add(portfolio)
        await db.flush()

        # Link strategies
        for spec in args.strategy:
            parts = spec.split(":")
            trader_slug = parts[0]
            direction_filter = parts[1] if len(parts) > 1 else None

            if direction_filter and direction_filter not in ("long", "short"):
                print(f"ERROR: Invalid direction filter '{direction_filter}'. Use 'long' or 'short'.")
                return

            result = await db.execute(select(Trader).where(Trader.trader_id == trader_slug))
            trader = result.scalar_one_or_none()
            if not trader:
                print(f"ERROR: Trader '{trader_slug}' not found. Register it first.")
                return

            link = PortfolioStrategy(
                portfolio_id=portfolio.id,
                trader_id=trader.id,
                direction_filter=direction_filter,
            )
            db.add(link)
            filter_label = direction_filter or "all"
            print(f"  Linked: {trader.display_name} ({filter_label} directions)")

        await db.commit()

        print()
        print(f"Portfolio created: {args.name}")
        print(f"  ID: {portfolio.id}")
        print(f"  Capital: ${args.capital:,.2f}")
        print(f"  Strategies: {len(args.strategy)}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Create a portfolio")
    parser.add_argument("--name", required=True, help="Portfolio name")
    parser.add_argument("--description", default="", help="Description")
    parser.add_argument("--capital", type=float, default=10000, help="Initial capital (default: 10000)")
    parser.add_argument(
        "--strategy",
        action="append",
        required=True,
        help="trader-slug or trader-slug:long or trader-slug:short (repeatable)",
    )
    asyncio.run(main(parser.parse_args()))
