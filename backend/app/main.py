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
from app.api import settings as settings_router, screener as screener_router
from app.api import portfolio_manager as pm_router
from app.services.price_service import price_service
from app.database import async_session
from app.models import Trade, Trader, ConflictResolution
from app.models.market_summary import MarketSummary


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Start background price poller
    task = asyncio.create_task(price_service.run())
    # Start scheduler for summaries
    from app.services.scheduler import start_scheduler, stop_scheduler
    start_scheduler()
    yield
    stop_scheduler()
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


app = FastAPI(
    title="Henry AI Trader API",
    version="1.0.0",
    lifespan=lifespan,
)

settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.origins_list,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["*"],
)

# Include routers
app.include_router(webhooks.router, prefix="/api", tags=["webhooks"])
app.include_router(trades.router, prefix="/api", tags=["trades"])
app.include_router(traders.router, prefix="/api", tags=["traders"])
app.include_router(portfolios.router, prefix="/api", tags=["portfolios"])
app.include_router(leaderboard.router, prefix="/api", tags=["leaderboard"])
app.include_router(settings_router.router, prefix="/api", tags=["settings"])
app.include_router(screener_router.router, prefix="/api", tags=["screener"])
app.include_router(pm_router.router, prefix="/api", tags=["portfolio-manager"])


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


# ─── MARKET SUMMARIES ───────────────────────────────────────────────────

@app.get("/api/ai/summaries")
async def get_summaries(limit: int = 10):
    """Return recent market summaries."""
    async with async_session() as db:
        result = await db.execute(
            select(MarketSummary)
            .order_by(MarketSummary.generated_at.desc())
            .limit(limit)
        )
        summaries = result.scalars().all()

    return [
        {
            "id": s.id,
            "summary_type": s.summary_type,
            "scope": s.scope,
            "content": s.content,
            "tickers_analyzed": s.tickers_analyzed,
            "generated_at": s.generated_at.isoformat(),
        }
        for s in summaries
    ]


@app.post("/api/ai/summaries/generate")
async def force_generate_summary():
    """Manually trigger summary generation."""
    from app.services.scheduler import _generate_morning_summary
    import asyncio
    asyncio.create_task(_generate_morning_summary())
    return {"status": "generating", "message": "Summary generation started in background"}


# ─── EXISTING ENDPOINTS ──────────────────────────────────────────────────────

@app.get("/api/health")
async def health():
    return {"status": "ok"}


@app.get("/api/debug/ai")
async def debug_ai():
    """Test Claude API with multiple models and show full diagnostics."""
    import anthropic as anth
    import os
    import httpx

    key = os.environ.get("ANTHROPIC_API_KEY", "")
    key_preview = f"{key[:12]}...{key[-4:]}" if len(key) > 16 else ("SET but short" if key else "NOT SET")
    key_len = len(key)

    results = {"key_preview": key_preview, "key_length": key_len, "models": {}}

    # Test listing models via REST (to see what's available)
    try:
        async with httpx.AsyncClient() as http:
            resp = await http.get(
                "https://api.anthropic.com/v1/models",
                headers={
                    "x-api-key": key,
                    "anthropic-version": "2023-06-01",
                },
                timeout=10,
            )
            results["models_api_status"] = resp.status_code
            if resp.status_code == 200:
                data = resp.json()
                model_ids = [m.get("id", "?") for m in data.get("data", [])]
                results["available_models"] = model_ids[:20]
            else:
                results["models_api_error"] = resp.text[:300]
    except Exception as e:
        results["models_api_error"] = f"{type(e).__name__}: {str(e)[:200]}"

    # Try each model
    test_models = [
        "claude-sonnet-4-5-20250514",
        "claude-3-5-sonnet-latest",
        "claude-3-5-sonnet-20241022",
        "claude-3-5-haiku-latest",
        "claude-3-haiku-20240307",
    ]
    for model in test_models:
        try:
            client = anth.Anthropic()
            resp = client.messages.create(
                model=model,
                max_tokens=10,
                messages=[{"role": "user", "content": "Hi"}],
                timeout=10.0,
            )
            results["models"][model] = {"status": "ok", "response": resp.content[0].text}
            break  # Found a working model, no need to test more
        except Exception as e:
            results["models"][model] = {"status": "error", "error": f"{type(e).__name__}: {str(e)[:150]}"}

    return results


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
