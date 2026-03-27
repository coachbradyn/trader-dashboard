import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.schemas.analytics import MonteCarloRequest, MonteCarloResponse
from app.services.monte_carlo import fetch_pnl_pool, run_monte_carlo, compute_input_stats

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/analytics", tags=["analytics"])


@router.post("/monte-carlo", response_model=MonteCarloResponse)
async def monte_carlo_simulation(
    req: MonteCarloRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Run a Monte Carlo simulation using historical trade data.

    Resamples P&L percentages from live trades and/or backtest imports
    to project probability distributions of future equity outcomes.
    """
    # 1. Gather historical P&L pool from database
    pnl_pool, live_count, bt_count, strategies, tickers = await fetch_pnl_pool(
        db,
        source=req.source,
        strategy=req.strategy,
        ticker=req.ticker,
    )

    if len(pnl_pool) < 5:
        raise HTTPException(
            400,
            f"Not enough trade data for Monte Carlo simulation. "
            f"Found {len(pnl_pool)} trades (need at least 5). "
            f"Source: {req.source}, strategy: {req.strategy or 'all'}, ticker: {req.ticker or 'all'}.",
        )

    # 2. Run simulation in a thread pool to avoid blocking the event loop
    #    (10,000 sims × 500 trades can take ~200ms with NumPy)
    result = await asyncio.to_thread(
        run_monte_carlo,
        pnl_pool,
        req.num_simulations,
        req.forward_trades,
        req.initial_capital,
        req.position_size_pct,
    )

    # 3. Compute input data statistics
    result["input_stats"] = compute_input_stats(
        pnl_pool, live_count, bt_count, strategies, tickers
    )

    return result
