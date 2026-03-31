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
from app.api import analytics as analytics_router
from app.api import watchlist as watchlist_router
from app.api import ai_portfolio as ai_portfolio_router
from app.api import news as news_router
from app.api import execution as execution_router
from app.api import fmp_scanner as fmp_scanner_router
from app.services.price_service import price_service
from app.database import async_session
from app.models import Trade, Trader, ConflictResolution
from app.models.market_summary import MarketSummary


async def _ensure_schema():
    """Ensure critical columns/tables exist even if Alembic migration failed."""
    import logging
    logger = logging.getLogger(__name__)
    try:
        from sqlalchemy import text, inspect
        from app.database import engine
        async with engine.begin() as conn:
            # Check if strategy_description column exists on traders
            def _check_and_fix(connection):
                insp = inspect(connection)
                # Add strategy_description to traders if missing
                cols = [c["name"] for c in insp.get_columns("traders")]
                if "strategy_description" not in cols:
                    connection.execute(text("ALTER TABLE traders ADD COLUMN strategy_description TEXT"))
                    logger.info("Added missing column: traders.strategy_description")

                # Add is_ai_managed to portfolios if missing
                portfolio_cols = [c["name"] for c in insp.get_columns("portfolios")]
                if "is_ai_managed" not in portfolio_cols:
                    connection.execute(text("ALTER TABLE portfolios ADD COLUMN is_ai_managed BOOLEAN DEFAULT FALSE"))
                    logger.info("Added missing column: portfolios.is_ai_managed")

                # Add is_simulated to trades if missing
                trade_cols = [c["name"] for c in insp.get_columns("trades")]
                if "is_simulated" not in trade_cols:
                    connection.execute(text("ALTER TABLE trades ADD COLUMN is_simulated BOOLEAN DEFAULT FALSE"))
                    logger.info("Added missing column: trades.is_simulated")

                # Create henry_memory table if missing
                tables = insp.get_table_names()
                if "henry_memory" not in tables:
                    connection.execute(text("""
                        CREATE TABLE henry_memory (
                            id VARCHAR(36) PRIMARY KEY,
                            memory_type VARCHAR(30) NOT NULL,
                            strategy_id VARCHAR(50),
                            ticker VARCHAR(10),
                            content TEXT NOT NULL,
                            importance INTEGER DEFAULT 5,
                            reference_count INTEGER DEFAULT 0,
                            validated BOOLEAN,
                            source VARCHAR(30) DEFAULT 'system',
                            created_at TIMESTAMP DEFAULT NOW(),
                            updated_at TIMESTAMP DEFAULT NOW()
                        )
                    """))
                    logger.info("Created missing table: henry_memory")

                if "henry_context" not in tables:
                    connection.execute(text("""
                        CREATE TABLE henry_context (
                            id VARCHAR(36) PRIMARY KEY,
                            ticker VARCHAR(20),
                            strategy VARCHAR(50),
                            portfolio_id VARCHAR(36) REFERENCES portfolios(id),
                            context_type VARCHAR(30) NOT NULL,
                            content TEXT NOT NULL,
                            confidence INTEGER,
                            action_id VARCHAR(36),
                            trade_id VARCHAR(36),
                            created_at TIMESTAMP DEFAULT NOW(),
                            expires_at TIMESTAMP
                        )
                    """))
                    logger.info("Created missing table: henry_context")

                if "henry_stats" not in tables:
                    connection.execute(text("""
                        CREATE TABLE henry_stats (
                            id VARCHAR(36) PRIMARY KEY,
                            stat_type VARCHAR(50) NOT NULL,
                            ticker VARCHAR(20),
                            strategy VARCHAR(50),
                            portfolio_id VARCHAR(36) REFERENCES portfolios(id),
                            data JSON NOT NULL,
                            period_days INTEGER DEFAULT 30,
                            computed_at TIMESTAMP DEFAULT NOW()
                        )
                    """))
                    logger.info("Created missing table: henry_stats")

                if "watchlist_tickers" not in tables:
                    connection.execute(text("""
                        CREATE TABLE watchlist_tickers (
                            id VARCHAR(36) PRIMARY KEY,
                            ticker VARCHAR(20) NOT NULL UNIQUE,
                            notes TEXT,
                            is_active BOOLEAN DEFAULT TRUE,
                            created_at TIMESTAMP DEFAULT NOW(),
                            removed_at TIMESTAMP
                        )
                    """))
                    connection.execute(text("CREATE INDEX IF NOT EXISTS ix_watchlist_tickers_ticker ON watchlist_tickers (ticker)"))
                    connection.execute(text("CREATE INDEX IF NOT EXISTS ix_watchlist_tickers_is_active ON watchlist_tickers (is_active)"))
                    logger.info("Created missing table: watchlist_tickers")

                if "watchlist_summaries" not in tables:
                    connection.execute(text("""
                        CREATE TABLE watchlist_summaries (
                            id VARCHAR(36) PRIMARY KEY,
                            ticker VARCHAR(20) NOT NULL UNIQUE,
                            summary TEXT NOT NULL,
                            alert_count_at_generation INTEGER DEFAULT 0,
                            generated_at TIMESTAMP DEFAULT NOW()
                        )
                    """))
                    connection.execute(text("CREATE INDEX IF NOT EXISTS ix_watchlist_summaries_ticker ON watchlist_summaries (ticker)"))
                    logger.info("Created missing table: watchlist_summaries")

                if "henry_cache" not in tables:
                    connection.execute(text("""
                        CREATE TABLE henry_cache (
                            id VARCHAR(36) PRIMARY KEY,
                            cache_key VARCHAR(200) NOT NULL UNIQUE,
                            cache_type VARCHAR(50) NOT NULL,
                            content JSON NOT NULL,
                            ticker VARCHAR(20),
                            strategy VARCHAR(50),
                            is_stale BOOLEAN DEFAULT FALSE,
                            generated_at TIMESTAMP DEFAULT NOW(),
                            data_hash VARCHAR(64)
                        )
                    """))
                    connection.execute(text("CREATE INDEX IF NOT EXISTS ix_henry_cache_cache_key ON henry_cache (cache_key)"))
                    connection.execute(text("CREATE INDEX IF NOT EXISTS ix_henry_cache_cache_type ON henry_cache (cache_type)"))
                    connection.execute(text("CREATE INDEX IF NOT EXISTS ix_henry_cache_ticker ON henry_cache (ticker)"))
                    logger.info("Created missing table: henry_cache")

                if "news_cache" not in tables:
                    connection.execute(text("""
                        CREATE TABLE news_cache (
                            id VARCHAR(36) PRIMARY KEY,
                            alpaca_id VARCHAR(50) NOT NULL UNIQUE,
                            headline TEXT NOT NULL,
                            summary TEXT,
                            source VARCHAR(100),
                            tickers JSON,
                            published_at TIMESTAMP,
                            url VARCHAR(500),
                            sentiment_score FLOAT,
                            fetched_at TIMESTAMP DEFAULT NOW()
                        )
                    """))
                    connection.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_news_cache_alpaca_id ON news_cache (alpaca_id)"))
                    connection.execute(text("CREATE INDEX IF NOT EXISTS ix_news_cache_published_at ON news_cache (published_at)"))
                    connection.execute(text("CREATE INDEX IF NOT EXISTS ix_news_cache_fetched_at ON news_cache (fetched_at)"))
                    logger.info("Created missing table: news_cache")

                # Add execution columns to portfolios if missing
                portfolio_cols = [c["name"] for c in insp.get_columns("portfolios")]
                new_portfolio_cols = {
                    "execution_mode": "ALTER TABLE portfolios ADD COLUMN execution_mode VARCHAR(10) DEFAULT 'local'",
                    "alpaca_api_key": "ALTER TABLE portfolios ADD COLUMN alpaca_api_key VARCHAR(255)",
                    "alpaca_secret_key": "ALTER TABLE portfolios ADD COLUMN alpaca_secret_key VARCHAR(255)",
                    "max_order_amount": "ALTER TABLE portfolios ADD COLUMN max_order_amount FLOAT DEFAULT 1000.0",
                }
                for col_name, sql in new_portfolio_cols.items():
                    if col_name not in portfolio_cols:
                        connection.execute(text(sql))
                        logger.info(f"Added missing column: portfolios.{col_name}")

                # Add position archetype columns to portfolio_holdings if missing
                holding_cols = [c["name"] for c in insp.get_columns("portfolio_holdings")]
                archetype_cols = {
                    "position_type": "ALTER TABLE portfolio_holdings ADD COLUMN position_type VARCHAR(20) DEFAULT 'momentum'",
                    "thesis": "ALTER TABLE portfolio_holdings ADD COLUMN thesis TEXT",
                    "catalyst_date": "ALTER TABLE portfolio_holdings ADD COLUMN catalyst_date DATE",
                    "catalyst_description": "ALTER TABLE portfolio_holdings ADD COLUMN catalyst_description VARCHAR(200)",
                    "max_allocation_pct": "ALTER TABLE portfolio_holdings ADD COLUMN max_allocation_pct FLOAT",
                    "dca_enabled": "ALTER TABLE portfolio_holdings ADD COLUMN dca_enabled BOOLEAN DEFAULT FALSE",
                    "dca_threshold_pct": "ALTER TABLE portfolio_holdings ADD COLUMN dca_threshold_pct FLOAT",
                    "avg_cost": "ALTER TABLE portfolio_holdings ADD COLUMN avg_cost FLOAT",
                    "total_shares": "ALTER TABLE portfolio_holdings ADD COLUMN total_shares FLOAT",
                }
                for col_name, sql in archetype_cols.items():
                    if col_name not in holding_cols:
                        connection.execute(text(sql))
                        logger.info(f"Added missing column: portfolio_holdings.{col_name}")

                if "ticker_fundamentals" not in tables:
                    connection.execute(text("""
                        CREATE TABLE ticker_fundamentals (
                            id VARCHAR(36) PRIMARY KEY,
                            ticker VARCHAR(20) NOT NULL UNIQUE,
                            company_name VARCHAR(200),
                            sector VARCHAR(100),
                            industry VARCHAR(200),
                            market_cap FLOAT,
                            description TEXT,
                            earnings_date DATE,
                            earnings_time VARCHAR(10),
                            analyst_target_low FLOAT,
                            analyst_target_high FLOAT,
                            analyst_target_consensus FLOAT,
                            analyst_rating VARCHAR(30),
                            analyst_count INTEGER,
                            eps_estimate_current FLOAT,
                            eps_actual_last FLOAT,
                            eps_surprise_last FLOAT,
                            revenue_estimate_current FLOAT,
                            revenue_actual_last FLOAT,
                            pe_ratio FLOAT,
                            short_interest_pct FLOAT,
                            insider_transactions_90d TEXT,
                            updated_at TIMESTAMP DEFAULT NOW()
                        )
                    """))
                    connection.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_ticker_fundamentals_ticker ON ticker_fundamentals (ticker)"))
                    connection.execute(text("CREATE INDEX IF NOT EXISTS ix_ticker_fundamentals_updated_at ON ticker_fundamentals (updated_at)"))
                    logger.info("Created missing table: ticker_fundamentals")

                # Add expanded fundamentals columns if missing
                tf_cols = [c["name"] for c in insp.get_columns("ticker_fundamentals")] if "ticker_fundamentals" in tables else []
                new_tf_cols = {
                    "beta": "ALTER TABLE ticker_fundamentals ADD COLUMN beta FLOAT",
                    "forward_pe": "ALTER TABLE ticker_fundamentals ADD COLUMN forward_pe FLOAT",
                    "profit_margin": "ALTER TABLE ticker_fundamentals ADD COLUMN profit_margin FLOAT",
                    "roe": "ALTER TABLE ticker_fundamentals ADD COLUMN roe FLOAT",
                    "debt_to_equity": "ALTER TABLE ticker_fundamentals ADD COLUMN debt_to_equity FLOAT",
                    "revenue_growth_yoy": "ALTER TABLE ticker_fundamentals ADD COLUMN revenue_growth_yoy FLOAT",
                    "dcf_value": "ALTER TABLE ticker_fundamentals ADD COLUMN dcf_value FLOAT",
                    "dcf_diff_pct": "ALTER TABLE ticker_fundamentals ADD COLUMN dcf_diff_pct FLOAT",
                    "dividend_yield": "ALTER TABLE ticker_fundamentals ADD COLUMN dividend_yield FLOAT",
                    "insider_net_90d": "ALTER TABLE ticker_fundamentals ADD COLUMN insider_net_90d FLOAT",
                    "institutional_ownership_pct": "ALTER TABLE ticker_fundamentals ADD COLUMN institutional_ownership_pct FLOAT",
                    "company_description": "ALTER TABLE ticker_fundamentals ADD COLUMN company_description TEXT",
                }
                for col_name, sql in new_tf_cols.items():
                    if col_name not in tf_cols:
                        connection.execute(text(sql))
                        logger.info(f"Added missing column: ticker_fundamentals.{col_name}")

                # Create fmp_cache table if missing
                if "fmp_cache" not in tables:
                    connection.execute(text("""
                        CREATE TABLE fmp_cache (
                            id VARCHAR(36) PRIMARY KEY,
                            endpoint VARCHAR(200) NOT NULL,
                            params_hash VARCHAR(64) NOT NULL,
                            response_data JSON,
                            cached_at TIMESTAMP DEFAULT NOW(),
                            cache_tier VARCHAR(20) NOT NULL DEFAULT 'daily'
                        )
                    """))
                    connection.execute(text("CREATE INDEX IF NOT EXISTS ix_fmp_cache_endpoint ON fmp_cache (endpoint)"))
                    connection.execute(text("CREATE INDEX IF NOT EXISTS ix_fmp_cache_params_hash ON fmp_cache (params_hash)"))
                    connection.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_fmp_cache_endpoint_params ON fmp_cache (endpoint, params_hash)"))
                    logger.info("Created missing table: fmp_cache")

                if "ai_usage" not in tables:
                    connection.execute(text("""
                        CREATE TABLE ai_usage (
                            id VARCHAR(36) PRIMARY KEY,
                            provider VARCHAR(20) NOT NULL,
                            function_name VARCHAR(50) NOT NULL,
                            model VARCHAR(100),
                            input_tokens INTEGER,
                            output_tokens INTEGER,
                            latency_ms INTEGER,
                            was_fallback BOOLEAN DEFAULT FALSE,
                            created_at TIMESTAMP DEFAULT NOW()
                        )
                    """))
                    connection.execute(text("CREATE INDEX IF NOT EXISTS ix_ai_usage_created_at ON ai_usage (created_at)"))
                    connection.execute(text("CREATE INDEX IF NOT EXISTS ix_ai_usage_provider ON ai_usage (provider)"))
                    logger.info("Created missing table: ai_usage")

            await conn.run_sync(_check_and_fix)
    except Exception as e:
        logger.warning(f"Schema check failed (non-blocking): {e}")


async def _sync_holdings_to_watchlist():
    """On startup, ensure all tickers from holdings and open trades are on the watchlist."""
    import logging
    _logger = logging.getLogger(__name__)
    try:
        from app.models.portfolio_holding import PortfolioHolding
        from app.models.watchlist_ticker import WatchlistTicker

        async with async_session() as db:
            # Get all unique tickers from active holdings
            holding_result = await db.execute(
                select(PortfolioHolding.ticker)
                .where(PortfolioHolding.is_active == True)
                .distinct()
            )
            holding_tickers = {row[0] for row in holding_result.all()}

            # Get all unique tickers from open trades
            trade_result = await db.execute(
                select(Trade.ticker)
                .where(Trade.status == "open", Trade.is_simulated == False)
                .distinct()
            )
            trade_tickers = {row[0] for row in trade_result.all()}

            all_tickers = holding_tickers | trade_tickers
            if not all_tickers:
                return

            # Get existing watchlist tickers
            wl_result = await db.execute(select(WatchlistTicker))
            existing = {wt.ticker: wt for wt in wl_result.scalars().all()}

            added = 0
            reactivated = 0
            for ticker in all_tickers:
                if ticker in existing:
                    if not existing[ticker].is_active:
                        existing[ticker].is_active = True
                        existing[ticker].removed_at = None
                        reactivated += 1
                else:
                    db.add(WatchlistTicker(ticker=ticker))
                    added += 1

            if added or reactivated:
                await db.commit()
                _logger.info(f"Watchlist sync: added {added}, reactivated {reactivated} tickers from holdings/trades")
    except Exception as e:
        _logger.warning(f"Watchlist sync failed (non-blocking): {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Ensure DB schema is up to date (fallback if Alembic failed)
    await _ensure_schema()
    # Start background price poller
    task = asyncio.create_task(price_service.run())
    # Start scheduler for summaries
    from app.services.scheduler import start_scheduler, stop_scheduler
    start_scheduler()
    # Refresh strategy cache for AI prompts
    try:
        from app.services.screener_ai import refresh_strategies_cache
        await refresh_strategies_cache()
    except Exception:
        pass
    # Load AI trading config from DB
    try:
        from app.services.ai_portfolio import load_ai_config_from_db
        await load_ai_config_from_db()
    except Exception:
        pass
    # Sync existing holding tickers and traded tickers to watchlist
    try:
        await _sync_holdings_to_watchlist()
    except Exception:
        pass
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
app.include_router(analytics_router.router, prefix="/api", tags=["analytics"])
app.include_router(watchlist_router.router, prefix="/api", tags=["watchlist"])
app.include_router(ai_portfolio_router.router, prefix="/api", tags=["ai-portfolio"])
app.include_router(news_router.router, prefix="/api", tags=["news"])
app.include_router(execution_router.router, prefix="/api", tags=["execution"])
app.include_router(fmp_scanner_router.router, prefix="/api", tags=["fmp-scanner"])


# ─── AI DATA-FETCHING FUNCTIONS ──────────────────────────────────────────────

async def get_trades_for_ai(days_back: int = 1) -> list[dict]:
    """Fetch trades from the last N days, formatted as webhook-style dicts for ai_service."""
    cutoff = datetime.utcnow() - timedelta(days=days_back)
    async with async_session() as db:
        result = await db.execute(
            select(Trade)
            .options(selectinload(Trade.trader))
            .where(Trade.created_at >= cutoff, Trade.is_simulated == False)
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
            .where(Trade.status == "open", Trade.is_simulated == False)
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
            "created_at": (c.created_at.isoformat() + "Z") if c.created_at else None,
        }
        for c in conflicts
    ]


# ─── AI USAGE ANALYTICS ──────────────────────────────────────────────────

@app.get("/api/ai/usage")
async def get_ai_usage(days: int = 7, provider: str = None):
    """Return AI usage analytics: total calls, tokens, costs, and breakdown by function."""
    from app.models.ai_usage import AIUsage
    from sqlalchemy import func as sa_func

    cutoff = datetime.utcnow() - timedelta(days=days)
    try:
        async with async_session() as db:
            query = select(AIUsage).where(AIUsage.created_at >= cutoff)
            if provider:
                query = query.where(AIUsage.provider == provider)
            result = await db.execute(query.order_by(AIUsage.created_at.desc()))
            rows = result.scalars().all()

        # Aggregate
        total_calls = len(rows)
        total_input_tokens = sum(r.input_tokens or 0 for r in rows)
        total_output_tokens = sum(r.output_tokens or 0 for r in rows)
        fallback_count = sum(1 for r in rows if r.was_fallback)
        avg_latency = (sum(r.latency_ms or 0 for r in rows) / total_calls) if total_calls else 0

        # By provider
        by_provider = {}
        for r in rows:
            p = r.provider
            if p not in by_provider:
                by_provider[p] = {"calls": 0, "input_tokens": 0, "output_tokens": 0}
            by_provider[p]["calls"] += 1
            by_provider[p]["input_tokens"] += r.input_tokens or 0
            by_provider[p]["output_tokens"] += r.output_tokens or 0

        # By function
        by_function = {}
        for r in rows:
            fn = r.function_name
            if fn not in by_function:
                by_function[fn] = {"calls": 0, "provider_breakdown": {}}
            by_function[fn]["calls"] += 1
            p = r.provider
            if p not in by_function[fn]["provider_breakdown"]:
                by_function[fn]["provider_breakdown"][p] = 0
            by_function[fn]["provider_breakdown"][p] += 1

        # Approximate costs: Claude ~$3/M input, $15/M output; Gemini ~$0.10/M input, $0.40/M output
        estimated_cost = 0.0
        for p, stats in by_provider.items():
            if p == "claude":
                estimated_cost += (stats["input_tokens"] / 1_000_000 * 3.0) + (stats["output_tokens"] / 1_000_000 * 15.0)
            elif p == "gemini":
                estimated_cost += (stats["input_tokens"] / 1_000_000 * 0.10) + (stats["output_tokens"] / 1_000_000 * 0.40)

        return {
            "period_days": days,
            "total_calls": total_calls,
            "total_input_tokens": total_input_tokens,
            "total_output_tokens": total_output_tokens,
            "fallback_count": fallback_count,
            "avg_latency_ms": round(avg_latency),
            "estimated_cost_usd": round(estimated_cost, 4),
            "by_provider": by_provider,
            "by_function": by_function,
        }
    except Exception as e:
        return {
            "period_days": days,
            "total_calls": 0,
            "error": f"Usage tracking not available yet: {type(e).__name__}",
        }


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
            "generated_at": (s.generated_at.isoformat() + "Z") if s.generated_at else None,
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
        "claude-sonnet-4-5-20250929",
        "claude-sonnet-4-6",
        "claude-haiku-4-5-20251001",
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
