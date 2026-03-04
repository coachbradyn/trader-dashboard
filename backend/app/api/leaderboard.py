from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Portfolio
from app.services.performance_calc import calculate_performance

router = APIRouter()


@router.get("/leaderboard")
async def get_leaderboard(
    sort_by: str = Query("total_return_pct", enum=["total_return_pct", "win_rate", "profit_factor", "sharpe_ratio", "total_trades"]),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Portfolio).where(Portfolio.is_active == True))
    portfolios = result.scalars().all()

    entries = []
    for p in portfolios:
        try:
            perf = await calculate_performance(p.id, db)
            entries.append({
                "portfolio_id": p.id,
                "portfolio_name": p.name,
                "description": p.description,
                "total_return_pct": perf.total_return_pct,
                "win_rate": perf.win_rate,
                "profit_factor": perf.profit_factor,
                "sharpe_ratio": perf.sharpe_ratio,
                "total_trades": perf.total_trades,
                "max_drawdown_pct": perf.max_drawdown_pct,
                "total_pnl": perf.total_pnl,
                "current_streak": perf.current_streak,
            })
        except Exception:
            continue

    # Sort descending by chosen metric
    entries.sort(key=lambda x: x.get(sort_by, 0), reverse=True)

    # Add rank
    for i, entry in enumerate(entries):
        entry["rank"] = i + 1

    return entries
