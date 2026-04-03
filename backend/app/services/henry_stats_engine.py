"""
Henry Stats Engine
==================
Background computation of pre-computed analytics injected into Henry's prompts.
Runs every 2h during market hours via scheduler.
Each sub-function queries trades/actions, computes stats, upserts to HenryStats.
"""

import logging
from app.utils.utc import utcnow
from datetime import datetime, timedelta, timezone
from collections import defaultdict

from sqlalchemy import select, delete, func, and_

logger = logging.getLogger(__name__)


async def compute_all_stats():
    """Orchestrator: compute all stat types, each wrapped in try/except."""
    from app.database import async_session

    logger.info("Computing Henry stats...")

    async with async_session() as db:
        for fn in [
            _compute_strategy_performance,
            _compute_exit_reason_analysis,
            _compute_henry_hit_rate,
            _compute_hold_time_analysis,
            _compute_portfolio_risk,
            _compute_strategy_correlation,
        ]:
            try:
                await fn(db)
            except Exception as e:
                logger.error(f"Stats computation failed for {fn.__name__}: {e}")

        await db.commit()

    logger.info("Henry stats computation complete")


async def _upsert_stat(db, stat_type: str, data: dict, strategy: str = None,
                       ticker: str = None, portfolio_id: str = None, period_days: int = 30):
    """Delete existing matching rows, then insert fresh."""
    from app.models import HenryStats

    conditions = [HenryStats.stat_type == stat_type]
    if strategy is not None:
        conditions.append(HenryStats.strategy == strategy)
    else:
        conditions.append(HenryStats.strategy.is_(None))
    if ticker is not None:
        conditions.append(HenryStats.ticker == ticker)
    else:
        conditions.append(HenryStats.ticker.is_(None))
    if portfolio_id is not None:
        conditions.append(HenryStats.portfolio_id == portfolio_id)
    else:
        conditions.append(HenryStats.portfolio_id.is_(None))

    await db.execute(delete(HenryStats).where(and_(*conditions)))

    stat = HenryStats(
        stat_type=stat_type,
        strategy=strategy,
        ticker=ticker,
        portfolio_id=portfolio_id,
        data=data,
        period_days=period_days,
        computed_at=utcnow(),
    )
    db.add(stat)


async def _compute_strategy_performance(db):
    """Closed trades (30 days), grouped by trader_id (strategy)."""
    from app.models import Trade, Trader
    from sqlalchemy.orm import selectinload

    cutoff = utcnow() - timedelta(days=30)
    result = await db.execute(
        select(Trade)
        .options(selectinload(Trade.trader))
        .where(Trade.status == "closed", Trade.exit_time >= cutoff)
    )
    trades = result.scalars().all()

    by_strategy = defaultdict(list)
    for t in trades:
        sid = t.trader.trader_id if t.trader else "unknown"
        by_strategy[sid].append(t)

    for strategy_id, strades in by_strategy.items():
        wins = [t for t in strades if (t.pnl_dollars or 0) > 0]
        losses = [t for t in strades if (t.pnl_dollars or 0) <= 0]

        win_rate = round(len(wins) / len(strades) * 100, 1) if strades else 0
        avg_gain = round(sum(t.pnl_percent or 0 for t in wins) / len(wins), 2) if wins else 0
        avg_loss = round(sum(t.pnl_percent or 0 for t in losses) / len(losses), 2) if losses else 0

        gross_profit = sum(t.pnl_dollars or 0 for t in wins)
        gross_loss = abs(sum(t.pnl_dollars or 0 for t in losses))
        profit_factor = round(gross_profit / gross_loss, 2) if gross_loss > 0 else None

        avg_hold = None
        bars = [t.bars_in_trade for t in strades if t.bars_in_trade is not None]
        if bars:
            avg_hold = round(sum(bars) / len(bars), 1)

        # Current streak
        sorted_trades = sorted(strades, key=lambda t: t.exit_time or datetime.min)
        streak = ""
        if sorted_trades:
            last_win = (sorted_trades[-1].pnl_dollars or 0) > 0
            count = 0
            for t in reversed(sorted_trades):
                is_win = (t.pnl_dollars or 0) > 0
                if is_win == last_win:
                    count += 1
                else:
                    break
            streak = f"W{count}" if last_win else f"L{count}"

        await _upsert_stat(db, "strategy_performance", {
            "win_rate": win_rate,
            "avg_gain": avg_gain,
            "avg_loss": avg_loss,
            "profit_factor": profit_factor,
            "trade_count": len(strades),
            "avg_hold_bars": avg_hold,
            "current_streak": streak,
        }, strategy=strategy_id)


async def _compute_exit_reason_analysis(db):
    """Closed trades (30 days), grouped by exit_reason."""
    from app.models import Trade

    cutoff = utcnow() - timedelta(days=30)
    result = await db.execute(
        select(Trade).where(Trade.status == "closed", Trade.exit_time >= cutoff)
    )
    trades = result.scalars().all()

    by_reason = defaultdict(list)
    for t in trades:
        reason = t.exit_reason or "unknown"
        by_reason[reason].append(t)

    reason_data = {}
    for reason, rtrades in by_reason.items():
        wins = [t for t in rtrades if (t.pnl_dollars or 0) > 0]
        avg_pnl = round(sum(t.pnl_percent or 0 for t in rtrades) / len(rtrades), 2) if rtrades else 0
        win_rate = round(len(wins) / len(rtrades) * 100, 1) if rtrades else 0
        reason_data[reason] = {
            "count": len(rtrades),
            "avg_pnl_pct": avg_pnl,
            "win_rate": win_rate,
        }

    await _upsert_stat(db, "exit_reason_analysis", reason_data)


async def _compute_henry_hit_rate(db):
    """Query approved PortfolioActions where outcome_correct IS NOT NULL."""
    from app.models import PortfolioAction

    result = await db.execute(
        select(PortfolioAction).where(
            PortfolioAction.status == "approved",
            PortfolioAction.outcome_correct.isnot(None),
        )
    )
    outcomes = result.scalars().all()

    if not outcomes:
        return

    total = len(outcomes)
    correct = sum(1 for o in outcomes if o.outcome_correct)
    overall_pct = round(correct / total * 100, 1)

    # By confidence bucket
    low = [o for o in outcomes if o.confidence and o.confidence <= 3]
    mid = [o for o in outcomes if o.confidence and 4 <= o.confidence <= 6]
    high = [o for o in outcomes if o.confidence and o.confidence >= 7]

    low_pct = round(sum(1 for o in low if o.outcome_correct) / len(low) * 100, 1) if low else None
    mid_pct = round(sum(1 for o in mid if o.outcome_correct) / len(mid) * 100, 1) if mid else None
    high_pct = round(sum(1 for o in high if o.outcome_correct) / len(high) * 100, 1) if high else None

    await _upsert_stat(db, "henry_hit_rate", {
        "overall_pct": overall_pct,
        "total_outcomes": total,
        "low_conf_pct": low_pct,
        "low_conf_count": len(low),
        "mid_conf_pct": mid_pct,
        "mid_conf_count": len(mid),
        "high_conf_pct": high_pct,
        "high_conf_count": len(high),
    })


async def _compute_hold_time_analysis(db):
    """Closed trades with bars_in_trade, split winners/losers."""
    from app.models import Trade

    cutoff = utcnow() - timedelta(days=30)
    result = await db.execute(
        select(Trade).where(
            Trade.status == "closed",
            Trade.exit_time >= cutoff,
            Trade.bars_in_trade.isnot(None),
        )
    )
    trades = result.scalars().all()

    if not trades:
        return

    winners = [t for t in trades if (t.pnl_dollars or 0) > 0]
    losers = [t for t in trades if (t.pnl_dollars or 0) <= 0]

    def _stats(trade_list):
        bars = sorted([t.bars_in_trade for t in trade_list])
        if not bars:
            return None
        avg = round(sum(bars) / len(bars), 1)
        median = bars[len(bars) // 2]
        p90 = bars[int(len(bars) * 0.9)] if len(bars) >= 5 else bars[-1]
        return {"avg": avg, "median": median, "p90": p90, "count": len(bars)}

    await _upsert_stat(db, "hold_time_analysis", {
        "winners": _stats(winners),
        "losers": _stats(losers),
        "all": _stats(trades),
    })


async def _compute_portfolio_risk(db):
    """Active holdings grouped by portfolio -- concentration, exposure."""
    from app.models import PortfolioHolding, Portfolio
    from app.services.price_service import price_service

    result = await db.execute(
        select(PortfolioHolding).where(PortfolioHolding.is_active == True)
    )
    holdings = result.scalars().all()

    by_portfolio = defaultdict(list)
    for h in holdings:
        by_portfolio[h.portfolio_id].append(h)

    for pid, pholdings in by_portfolio.items():
        ticker_values = {}
        total_value = 0.0

        for h in pholdings:
            cp = price_service.get_price(h.ticker) or h.entry_price
            val = cp * h.qty
            total_value += val
            ticker_values[h.ticker] = ticker_values.get(h.ticker, 0) + val

        if total_value <= 0:
            continue

        concentration = {
            ticker: round(val / total_value * 100, 1)
            for ticker, val in sorted(ticker_values.items(), key=lambda x: -x[1])
        }

        largest_ticker = max(ticker_values, key=ticker_values.get) if ticker_values else None
        largest_pct = concentration.get(largest_ticker, 0) if largest_ticker else 0

        await _upsert_stat(db, "portfolio_risk", {
            "total_exposure": round(total_value, 2),
            "position_count": len(pholdings),
            "ticker_count": len(ticker_values),
            "concentration": concentration,
            "largest_position": largest_ticker,
            "largest_pct": largest_pct,
        }, portfolio_id=pid)


async def _compute_strategy_correlation(db):
    """Entry trades (90 days), find same-ticker entries within 4h between strategy pairs."""
    from app.models import Trade, Trader
    from sqlalchemy.orm import selectinload

    cutoff = utcnow() - timedelta(days=90)
    result = await db.execute(
        select(Trade)
        .options(selectinload(Trade.trader))
        .where(Trade.entry_time >= cutoff)
        .order_by(Trade.entry_time)
    )
    trades = result.scalars().all()

    # Group by ticker
    by_ticker = defaultdict(list)
    for t in trades:
        if t.trader:
            by_ticker[t.ticker].append(t)

    pair_agree = defaultdict(int)
    pair_total = defaultdict(int)

    for ticker, ttrades in by_ticker.items():
        # For each pair of trades on same ticker within 4h
        for i, t1 in enumerate(ttrades):
            for t2 in ttrades[i + 1:]:
                if t1.trader.trader_id == t2.trader.trader_id:
                    continue
                if abs((t1.entry_time - t2.entry_time).total_seconds()) > 4 * 3600:
                    continue

                pair_key = tuple(sorted([t1.trader.trader_id, t2.trader.trader_id]))
                pair_total[pair_key] += 1
                if t1.direction == t2.direction:
                    pair_agree[pair_key] += 1

    if not pair_total:
        return

    correlation_data = {}
    for pair, total in pair_total.items():
        agree = pair_agree.get(pair, 0)
        correlation_data[f"{pair[0]}_{pair[1]}"] = {
            "agreement_pct": round(agree / total * 100, 1),
            "total_overlaps": total,
            "agreements": agree,
        }

    await _upsert_stat(db, "strategy_correlation", correlation_data, period_days=90)
