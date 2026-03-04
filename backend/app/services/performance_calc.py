import math
from datetime import date

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Trade, PortfolioTrade, Portfolio, PortfolioSnapshot, DailyStats
from app.schemas.portfolio import PerformanceResponse, EquityPoint, DailyStatsResponse


async def calculate_performance(portfolio_id: str, db: AsyncSession) -> PerformanceResponse:
    # Get portfolio
    result = await db.execute(select(Portfolio).where(Portfolio.id == portfolio_id))
    portfolio = result.scalar_one_or_none()
    if not portfolio:
        raise ValueError("Portfolio not found")

    # Get all closed trades for this portfolio
    result = await db.execute(
        select(Trade)
        .join(PortfolioTrade)
        .where(PortfolioTrade.portfolio_id == portfolio_id, Trade.status == "closed")
        .order_by(Trade.exit_time.desc())
    )
    closed_trades = result.scalars().all()

    total = len(closed_trades)
    wins = sum(1 for t in closed_trades if (t.pnl_dollars or 0) > 0)
    losses = total - wins

    gross_profit = sum(t.pnl_dollars for t in closed_trades if (t.pnl_dollars or 0) > 0)
    gross_loss = abs(sum(t.pnl_dollars for t in closed_trades if (t.pnl_dollars or 0) <= 0))

    win_rate = (wins / total * 100) if total > 0 else 0.0
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else 0.0
    avg_win = (gross_profit / wins) if wins > 0 else 0.0
    avg_loss = (gross_loss / losses) if losses > 0 else 0.0
    total_pnl = gross_profit - gross_loss
    total_return_pct = (total_pnl / portfolio.initial_capital * 100) if portfolio.initial_capital > 0 else 0.0

    # Max drawdown from snapshots
    result = await db.execute(
        select(func.max(PortfolioSnapshot.drawdown_pct))
        .where(PortfolioSnapshot.portfolio_id == portfolio_id)
    )
    max_dd = result.scalar() or 0.0

    # Current streak
    streak = 0
    for t in closed_trades:  # already ordered desc
        is_win = (t.pnl_dollars or 0) > 0
        if streak == 0:
            streak = 1 if is_win else -1
        elif (streak > 0 and is_win) or (streak < 0 and not is_win):
            streak += 1 if is_win else -1
        else:
            break

    # Sharpe ratio from daily stats
    result = await db.execute(
        select(DailyStats.daily_pnl_pct)
        .where(DailyStats.portfolio_id == portfolio_id)
        .order_by(DailyStats.date)
    )
    daily_returns = [r[0] for r in result.all()]
    sharpe = 0.0
    if len(daily_returns) > 1:
        mean_r = sum(daily_returns) / len(daily_returns)
        std_r = math.sqrt(sum((r - mean_r) ** 2 for r in daily_returns) / (len(daily_returns) - 1))
        if std_r > 0:
            sharpe = (mean_r / std_r) * math.sqrt(252)

    return PerformanceResponse(
        portfolio_id=portfolio_id,
        portfolio_name=portfolio.name,
        total_trades=total,
        wins=wins,
        losses=losses,
        win_rate=round(win_rate, 2),
        profit_factor=round(profit_factor, 3),
        avg_win=round(avg_win, 2),
        avg_loss=round(avg_loss, 2),
        total_pnl=round(total_pnl, 2),
        total_return_pct=round(total_return_pct, 2),
        max_drawdown_pct=round(max_dd, 2),
        sharpe_ratio=round(sharpe, 3),
        current_streak=streak,
    )


async def get_equity_history(portfolio_id: str, db: AsyncSession) -> list[EquityPoint]:
    result = await db.execute(
        select(PortfolioSnapshot)
        .where(PortfolioSnapshot.portfolio_id == portfolio_id)
        .order_by(PortfolioSnapshot.snapshot_time)
    )
    snapshots = result.scalars().all()
    return [
        EquityPoint(time=s.snapshot_time, equity=s.equity, drawdown_pct=s.drawdown_pct)
        for s in snapshots
    ]


async def get_daily_stats(portfolio_id: str, db: AsyncSession) -> list[DailyStatsResponse]:
    result = await db.execute(
        select(DailyStats)
        .where(DailyStats.portfolio_id == portfolio_id)
        .order_by(DailyStats.date)
    )
    stats = result.scalars().all()
    return [
        DailyStatsResponse(
            date=s.date.isoformat(),
            daily_pnl=s.daily_pnl,
            daily_pnl_pct=s.daily_pnl_pct,
            trades_closed=s.trades_closed,
            wins=s.wins,
            losses=s.losses,
            ending_equity=s.ending_equity,
        )
        for s in stats
    ]
