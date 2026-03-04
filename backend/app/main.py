import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.api import webhooks, trades, traders, portfolios, leaderboard
from app.services.price_service import price_service


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Start background price poller
    task = asyncio.create_task(price_service.run())
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


app = FastAPI(
    title="Trader Dashboard API",
    version="1.0.0",
    lifespan=lifespan,
)

settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.origins_list,
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

# Include routers
app.include_router(webhooks.router, prefix="/api", tags=["webhooks"])
app.include_router(trades.router, prefix="/api", tags=["trades"])
app.include_router(traders.router, prefix="/api", tags=["traders"])
app.include_router(portfolios.router, prefix="/api", tags=["portfolios"])
app.include_router(leaderboard.router, prefix="/api", tags=["leaderboard"])


@app.get("/api/health")
async def health():
    return {"status": "ok"}


@app.get("/api/prices")
async def get_prices():
    return price_service.cache


@app.post("/api/admin/seed")
async def seed_database(secret: str):
    """One-time seed endpoint protected by ADMIN_SECRET."""
    from fastapi import HTTPException
    if secret != settings.admin_secret:
        raise HTTPException(status_code=403, detail="Forbidden")

    from sqlalchemy import select
    from app.database import async_session
    from app.models import Trader, Portfolio, PortfolioStrategy
    from app.utils.auth import generate_api_key, hash_api_key

    async with async_session() as db:
        result = await db.execute(select(Trader).where(Trader.trader_id == "henry-v36"))
        if result.scalar_one_or_none():
            return {"status": "already_seeded"}

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

        portfolios_data = [
            {"name": "Buy Only", "desc": "Long-only trades from Henry v3.6", "capital": 10000, "filter": "long"},
            {"name": "Full Henry", "desc": "All trades (long + short) from Henry v3.6", "capital": 10000, "filter": None},
            {"name": "Aggressive", "desc": "Henry v3.6 aggressive profile — higher capital", "capital": 25000, "filter": None},
        ]
        for p in portfolios_data:
            portfolio = Portfolio(name=p["name"], description=p["desc"], initial_capital=p["capital"], cash=p["capital"])
            db.add(portfolio)
            await db.flush()
            db.add(PortfolioStrategy(portfolio_id=portfolio.id, trader_id=trader.id, direction_filter=p["filter"]))

        await db.commit()
        return {"status": "seeded", "api_key": raw_key}
