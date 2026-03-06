import asyncio
import json
from contextlib import asynccontextmanager
from datetime import datetime, timedelta

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.config import get_settings
from app.api import webhooks, trades, traders, portfolios, leaderboard
from app.services.price_service import price_service
from app.database import async_session
from app.models import Trade, Trader, ConflictResolution


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


# ─── AI DATA-FETCHING FUNCTIONS ──────────────────────────────────────────────

async def get_trades_for_ai(days_back: int = 1) -> list[dict]:
    """Fetch trades from the last N days, formatted as webhook-style dicts for ai_service."""
    cutoff = datetime.utcnow() - timedelta(days=days_back)
    async with async_session() as db:
        result = await db.execute(
            select(Trade)
            .options(selectinload(Trade.trader))
            .where(Trade.created_at >= cutoff)
            .order_by(Trade.created_at.desc())
        )
        db_trades = result.scalars().all()

    out = []
    for t in db_trades:
        if t.status == "open":
            out.append({
                "signal": "entry",
                "trader": t.trader.trader_id,
                "dir": t.direction,
                "ticker": t.ticker,
                "price": t.entry_price,
                "qty": t.qty,
                "sig": t.entry_signal_strength or 0,
                "adx": t.entry_adx or 0,
                "atr": t.entry_atr or 0,
                "stop": t.stop_price or 0,
                "tf": t.timeframe or "?",
                "time": int(t.entry_time.timestamp() * 1000) if t.entry_time else 0,
            })
        else:
            out.append({
                "signal": "exit",
                "trader": t.trader.trader_id,
                "dir": t.direction,
                "ticker": t.ticker,
                "price": t.exit_price or t.entry_price,
                "pnl_pct": t.pnl_percent or 0,
                "bars_in_trade": t.bars_in_trade or 0,
                "exit_reason": t.exit_reason or "unknown",
                "tf": t.timeframe or "?",
                "time": int(t.exit_time.timestamp() * 1000) if t.exit_time else 0,
            })
            # Also include the entry for this trade
            out.append({
                "signal": "entry",
                "trader": t.trader.trader_id,
                "dir": t.direction,
                "ticker": t.ticker,
                "price": t.entry_price,
                "qty": t.qty,
                "sig": t.entry_signal_strength or 0,
                "adx": t.entry_adx or 0,
                "atr": t.entry_atr or 0,
                "stop": t.stop_price or 0,
                "tf": t.timeframe or "?",
                "time": int(t.entry_time.timestamp() * 1000) if t.entry_time else 0,
            })
    return out


async def get_positions_for_ai() -> list[dict]:
    """Fetch currently open positions, formatted for ai_service."""
    async with async_session() as db:
        result = await db.execute(
            select(Trade)
            .options(selectinload(Trade.trader))
            .where(Trade.status == "open")
            .order_by(Trade.entry_time.desc())
        )
        open_trades = result.scalars().all()

    out = []
    for t in open_trades:
        current_price = price_service.get_price(t.ticker) or t.entry_price
        if t.direction == "long":
            pnl_pct = ((current_price - t.entry_price) / t.entry_price * 100)
        else:
            pnl_pct = ((t.entry_price - current_price) / t.entry_price * 100)

        out.append({
            "trader": t.trader.trader_id,
            "dir": t.direction,
            "ticker": t.ticker,
            "entry_price": t.entry_price,
            "current_price": current_price,
            "pnl_pct": round(pnl_pct, 2),
            "bars_in_trade": 0,
        })
    return out


async def get_market_data_for_ai() -> dict | None:
    """Pull SPY/VIX from the price service cache."""
    spy_price = price_service.get_price("SPY")
    vix_price = price_service.get_price("VIX")
    if not spy_price:
        return None
    return {
        "spy_change": 0.0,  # Would need previous close to calculate
        "vix": vix_price or 0.0,
    }


# ─── REGISTER AI ROUTES ──────────────────────────────────────────────────────

from app.services.ai_service import register_ai_routes
register_ai_routes(app, get_trades_for_ai, get_positions_for_ai, get_market_data_for_ai)


# ─── CONFLICT LOG ENDPOINTS ──────────────────────────────────────────────────

@app.get("/api/ai/conflicts")
async def get_conflicts(days_back: int = 7, limit: int = 50):
    """Return recent conflict resolutions."""
    cutoff = datetime.utcnow() - timedelta(days=days_back)
    async with async_session() as db:
        result = await db.execute(
            select(ConflictResolution)
            .where(ConflictResolution.created_at >= cutoff)
            .order_by(ConflictResolution.created_at.desc())
            .limit(limit)
        )
        conflicts = result.scalars().all()

    return [
        {
            "id": c.id,
            "ticker": c.ticker,
            "strategies": json.loads(c.strategies) if isinstance(c.strategies, str) else c.strategies,
            "recommendation": c.recommendation,
            "confidence": c.confidence,
            "reasoning": c.reasoning,
            "signals": c.signals,
            "created_at": c.created_at.isoformat(),
        }
        for c in conflicts
    ]


# ─── EXISTING ENDPOINTS ──────────────────────────────────────────────────────

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
