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
