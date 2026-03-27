import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.schemas.analytics import MonteCarloRequest, MonteCarloResponse
from app.services.monte_carlo import fetch_pnl_pool, run_monte_carlo, run_buyhold_monte_carlo, compute_input_stats

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

    # 4. If a ticker is specified, run buy-and-hold MC overlay
    if req.ticker:
        try:
            from app.services.chart_service import get_daily_chart
            chart_data = await get_daily_chart(req.ticker.upper(), 365)
            if chart_data and len(chart_data) >= 20:
                # Compute daily returns from historical prices
                closes = [d["close"] for d in chart_data]
                daily_returns = [
                    ((closes[i] - closes[i - 1]) / closes[i - 1]) * 100.0
                    for i in range(1, len(closes))
                ]

                buyhold_result = await asyncio.to_thread(
                    run_buyhold_monte_carlo,
                    daily_returns,
                    req.num_simulations,
                    req.forward_trades,
                    req.initial_capital,
                )
                result["buyhold"] = buyhold_result
        except Exception as e:
            logger.warning(f"Buy-and-hold MC failed for {req.ticker}: {e}")

    return result


@router.post("/monte-carlo/buyhold")
async def buyhold_monte_carlo(
    ticker: str = Query(..., description="Ticker symbol"),
    num_simulations: int = Query(1000, ge=100, le=10000),
    forward_steps: int = Query(100, ge=10, le=500),
    initial_capital: float = Query(10000.0, gt=0),
    history_days: int = Query(365, ge=30, le=730),
):
    """
    Standalone buy-and-hold Monte Carlo simulation for a specific stock.
    Uses historical daily returns to project price probability distributions.
    """
    from app.services.chart_service import get_daily_chart

    chart_data = await get_daily_chart(ticker.upper(), history_days)
    if not chart_data or len(chart_data) < 20:
        raise HTTPException(400, f"Not enough historical data for {ticker} (need 20+ trading days)")

    closes = [d["close"] for d in chart_data]
    daily_returns = [
        ((closes[i] - closes[i - 1]) / closes[i - 1]) * 100.0
        for i in range(1, len(closes))
    ]

    result = await asyncio.to_thread(
        run_buyhold_monte_carlo,
        daily_returns,
        num_simulations,
        forward_steps,
        initial_capital,
    )

    result["ticker"] = ticker.upper()
    result["current_price"] = closes[-1]
    result["history_days"] = len(chart_data)

    return result
