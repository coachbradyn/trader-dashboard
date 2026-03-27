"""
AI Portfolio API
=================
Endpoints for managing Henry's paper portfolio, viewing performance comparison,
and browsing decision logs.
"""

import logging
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select, func, desc
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models import (
    Portfolio, Trade, PortfolioTrade, PortfolioSnapshot, PortfolioAction, Trader,
)
from app.services.price_service import price_service
from app.services.performance_calc import calculate_performance

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/ai-portfolio", tags=["ai-portfolio"])


# ── Schemas ──────────────────────────────────────────────────────────────

class CreateAIPortfolioRequest(BaseModel):
    name: str = "Henry AI Portfolio"
    initial_capital: float = 10000.0
    max_pct_per_trade: float = 10.0
    max_open_positions: int = 15
    max_drawdown_pct: float = 20.0


# ── Create / Reset ──────────────────────────────────────────────────────

@router.post("/create")
async def create_portfolio(req: CreateAIPortfolioRequest, db: AsyncSession = Depends(get_db)):
    """Create the AI-managed paper portfolio."""
    from app.services.ai_portfolio import create_ai_portfolio
    try:
        result = await create_ai_portfolio(
            name=req.name,
            initial_capital=req.initial_capital,
            max_pct_per_trade=req.max_pct_per_trade,
            max_open_positions=req.max_open_positions,
            max_drawdown_pct=req.max_drawdown_pct,
            db=db,
        )
        return result
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/reset")
async def reset_portfolio(db: AsyncSession = Depends(get_db)):
    """Reset the AI portfolio — delete all trades, reset equity."""
    from app.services.ai_portfolio import reset_ai_portfolio
    try:
        return await reset_ai_portfolio(db)
    except ValueError as e:
        raise HTTPException(400, str(e))


# ── Status ───────────────────────────────────────────────────────────────

@router.get("/status")
async def get_status(db: AsyncSession = Depends(get_db)):
    """Get AI portfolio status — equity, positions, basic metrics."""
    from app.services.ai_portfolio import get_ai_portfolio, _get_ai_portfolio_equity

    portfolio = await get_ai_portfolio(db)
    if not portfolio:
        return {"exists": False}

    equity = await _get_ai_portfolio_equity(portfolio, db)

    # Open positions count
    pos_result = await db.execute(
        select(func.count(Trade.id))
        .join(PortfolioTrade)
        .where(
            PortfolioTrade.portfolio_id == portfolio.id,
            Trade.status == "open",
            Trade.is_simulated == True,
        )
    )
    open_count = pos_result.scalar() or 0

    # Total trades
    total_result = await db.execute(
        select(func.count(Trade.id))
        .join(PortfolioTrade)
        .where(
            PortfolioTrade.portfolio_id == portfolio.id,
            Trade.is_simulated == True,
        )
    )
    total_trades = total_result.scalar() or 0

    return_pct = ((equity / portfolio.initial_capital) - 1) * 100 if portfolio.initial_capital > 0 else 0

    return {
        "exists": True,
        "id": portfolio.id,
        "name": portfolio.name,
        "equity": round(equity, 2),
        "cash": round(portfolio.cash, 2),
        "initial_capital": portfolio.initial_capital,
        "return_pct": round(return_pct, 2),
        "open_positions": open_count,
        "total_trades": total_trades,
        "created_at": portfolio.created_at.isoformat(),
    }


# ── Performance Comparison ───────────────────────────────────────────────

@router.get("/compare")
async def compare_performance(db: AsyncSession = Depends(get_db)):
    """Side-by-side comparison: AI portfolio vs real portfolios vs SPY."""
    from app.services.ai_portfolio import get_ai_portfolio, _get_ai_portfolio_equity

    portfolio = await get_ai_portfolio(db)
    if not portfolio:
        raise HTTPException(404, "No AI portfolio exists")

    # AI portfolio metrics
    ai_equity = await _get_ai_portfolio_equity(portfolio, db)
    ai_return = ((ai_equity / portfolio.initial_capital) - 1) * 100

    # AI portfolio trades for win rate / profit factor
    ai_trades_result = await db.execute(
        select(Trade)
        .join(PortfolioTrade)
        .where(
            PortfolioTrade.portfolio_id == portfolio.id,
            Trade.status == "closed",
            Trade.is_simulated == True,
        )
    )
    ai_closed = ai_trades_result.scalars().all()

    ai_wins = [t for t in ai_closed if (t.pnl_dollars or 0) > 0]
    ai_losses = [t for t in ai_closed if (t.pnl_dollars or 0) <= 0]
    ai_win_rate = (len(ai_wins) / len(ai_closed) * 100) if ai_closed else 0
    avg_win = sum(t.pnl_percent or 0 for t in ai_wins) / len(ai_wins) if ai_wins else 0
    avg_loss = abs(sum(t.pnl_percent or 0 for t in ai_losses) / len(ai_losses)) if ai_losses else 0
    ai_profit_factor = (avg_win * len(ai_wins)) / (avg_loss * len(ai_losses)) if avg_loss * len(ai_losses) > 0 else 0

    # AI portfolio max drawdown
    ai_snap_result = await db.execute(
        select(func.max(PortfolioSnapshot.drawdown_pct))
        .where(PortfolioSnapshot.portfolio_id == portfolio.id)
    )
    ai_max_dd = ai_snap_result.scalar() or 0

    ai_total_trades = len(ai_closed) + (await db.execute(
        select(func.count(Trade.id)).join(PortfolioTrade).where(
            PortfolioTrade.portfolio_id == portfolio.id,
            Trade.status == "open", Trade.is_simulated == True,
        )
    )).scalar()

    ai_metrics = {
        "name": portfolio.name,
        "equity": round(ai_equity, 2),
        "total_return_pct": round(ai_return, 2),
        "win_rate": round(ai_win_rate, 1),
        "profit_factor": round(ai_profit_factor, 2),
        "max_drawdown_pct": round(ai_max_dd, 1),
        "total_trades": ai_total_trades,
    }

    # Real portfolios metrics
    real_portfolios = []
    rp_result = await db.execute(
        select(Portfolio).where(
            Portfolio.is_active == True,
            Portfolio.is_ai_managed == False,
        )
    )
    for rp in rp_result.scalars().all():
        try:
            perf = await calculate_performance(rp.id, db)
            real_portfolios.append({
                "id": rp.id,
                "name": rp.name,
                "total_return_pct": perf.total_return_pct,
                "win_rate": perf.win_rate,
                "profit_factor": perf.profit_factor,
                "max_drawdown_pct": perf.max_drawdown_pct,
                "total_trades": perf.total_trades,
                "total_pnl": perf.total_pnl,
            })
        except Exception:
            continue

    # Decision stats
    action_result = await db.execute(
        select(PortfolioAction).where(PortfolioAction.portfolio_id == portfolio.id)
    )
    all_actions = action_result.scalars().all()
    total_signals = len(all_actions)
    acted_on = len([a for a in all_actions if a.status == "approved" and a.action_type != "SKIP"])
    skipped = total_signals - acted_on
    avg_conf_taken = (
        sum(a.confidence for a in all_actions if a.status == "approved" and a.action_type != "SKIP") / acted_on
        if acted_on > 0 else 0
    )
    avg_conf_skipped = (
        sum(a.confidence for a in all_actions if a.status != "approved" or a.action_type == "SKIP") / skipped
        if skipped > 0 else 0
    )

    decision_stats = {
        "total_signals": total_signals,
        "acted_on": acted_on,
        "acted_on_pct": round(acted_on / total_signals * 100, 1) if total_signals > 0 else 0,
        "skipped": skipped,
        "avg_confidence_taken": round(avg_conf_taken, 1),
        "avg_confidence_skipped": round(avg_conf_skipped, 1),
    }

    return {
        "ai_portfolio": ai_metrics,
        "real_portfolios": real_portfolios,
        "decision_stats": decision_stats,
    }


# ── Equity History (for chart) ───────────────────────────────────────────

@router.get("/equity-history")
async def get_equity_history(
    days: int = Query(90, le=365),
    db: AsyncSession = Depends(get_db),
):
    """Get AI portfolio equity snapshots for charting."""
    from app.services.ai_portfolio import get_ai_portfolio
    portfolio = await get_ai_portfolio(db)
    if not portfolio:
        raise HTTPException(404, "No AI portfolio exists")

    cutoff = datetime.utcnow() - timedelta(days=days)
    result = await db.execute(
        select(PortfolioSnapshot)
        .where(
            PortfolioSnapshot.portfolio_id == portfolio.id,
            PortfolioSnapshot.snapshot_time >= cutoff,
        )
        .order_by(PortfolioSnapshot.snapshot_time)
    )
    snapshots = result.scalars().all()

    return [
        {
            "time": s.snapshot_time.isoformat(),
            "equity": round(s.equity, 2),
            "drawdown_pct": round(s.drawdown_pct, 2),
        }
        for s in snapshots
    ]


# ── Decision Log ─────────────────────────────────────────────────────────

@router.get("/decisions")
async def get_decisions(
    filter: str = Query("all", enum=["all", "taken", "skipped"]),
    limit: int = Query(50, le=200),
    db: AsyncSession = Depends(get_db),
):
    """Get Henry's decision log for the AI portfolio."""
    from app.services.ai_portfolio import get_ai_portfolio
    portfolio = await get_ai_portfolio(db)
    if not portfolio:
        raise HTTPException(404, "No AI portfolio exists")

    query = select(PortfolioAction).where(PortfolioAction.portfolio_id == portfolio.id)

    if filter == "taken":
        query = query.where(PortfolioAction.status == "approved", PortfolioAction.action_type != "SKIP")
    elif filter == "skipped":
        query = query.where(
            (PortfolioAction.status == "rejected") | (PortfolioAction.action_type == "SKIP")
        )

    query = query.order_by(desc(PortfolioAction.created_at)).limit(limit)
    result = await db.execute(query)
    actions = result.scalars().all()

    decisions = []
    for a in actions:
        # Get trade outcome if this was an executed BUY
        outcome = None
        if a.status == "approved" and a.action_type != "SKIP" and a.trigger_ref:
            # Find the simulated trade created from this action
            trade_result = await db.execute(
                select(Trade)
                .join(PortfolioTrade)
                .where(
                    PortfolioTrade.portfolio_id == portfolio.id,
                    Trade.ticker == a.ticker,
                    Trade.is_simulated == True,
                    Trade.status == "closed",
                    Trade.entry_time >= a.created_at - timedelta(seconds=60),
                    Trade.entry_time <= a.created_at + timedelta(seconds=60),
                )
                .limit(1)
            )
            closed_trade = trade_result.scalar_one_or_none()
            if closed_trade:
                outcome = {
                    "pnl_pct": round(closed_trade.pnl_percent or 0, 2),
                    "pnl_dollars": round(closed_trade.pnl_dollars or 0, 2),
                    "correct": (closed_trade.pnl_dollars or 0) > 0,
                }

        decisions.append({
            "id": a.id,
            "ticker": a.ticker,
            "direction": a.direction,
            "action_type": a.action_type,
            "confidence": a.confidence,
            "reasoning": a.reasoning,
            "status": a.status,
            "outcome": outcome,
            "created_at": a.created_at.isoformat(),
        })

    return decisions


# ── Holdings ─────────────────────────────────────────────────────────────

@router.get("/holdings")
async def get_holdings(db: AsyncSession = Depends(get_db)):
    """Get AI portfolio open positions."""
    from app.services.ai_portfolio import get_ai_portfolio

    portfolio = await get_ai_portfolio(db)
    if not portfolio:
        raise HTTPException(404, "No AI portfolio exists")

    result = await db.execute(
        select(Trade)
        .join(PortfolioTrade)
        .where(
            PortfolioTrade.portfolio_id == portfolio.id,
            Trade.status == "open",
            Trade.is_simulated == True,
        )
        .options(selectinload(Trade.trader))
        .order_by(desc(Trade.entry_time))
    )
    positions = result.scalars().all()

    holdings = []
    for t in positions:
        cp = price_service.get_price(t.ticker) or t.entry_price
        if t.direction == "long":
            pnl_pct = ((cp - t.entry_price) / t.entry_price * 100)
            pnl_dollars = (cp - t.entry_price) * t.qty
        else:
            pnl_pct = ((t.entry_price - cp) / t.entry_price * 100)
            pnl_dollars = (t.entry_price - cp) * t.qty

        hold_hours = (datetime.utcnow() - t.entry_time).total_seconds() / 3600

        # Get Henry's reasoning from the action
        reason_result = await db.execute(
            select(PortfolioAction)
            .where(
                PortfolioAction.portfolio_id == portfolio.id,
                PortfolioAction.ticker == t.ticker,
                PortfolioAction.status == "approved",
                PortfolioAction.action_type != "SKIP",
            )
            .order_by(desc(PortfolioAction.created_at))
            .limit(1)
        )
        action = reason_result.scalar_one_or_none()

        holdings.append({
            "trade_id": t.id,
            "ticker": t.ticker,
            "direction": t.direction,
            "strategy": t.trader.display_name,
            "strategy_id": t.trader.trader_id,
            "entry_price": t.entry_price,
            "current_price": round(cp, 2),
            "qty": t.qty,
            "pnl_pct": round(pnl_pct, 2),
            "pnl_dollars": round(pnl_dollars, 2),
            "hold_hours": round(hold_hours, 1),
            "entry_time": t.entry_time.isoformat(),
            "reasoning": action.reasoning if action else None,
            "confidence": action.confidence if action else None,
        })

    return holdings


# ── Chat with Henry about AI Portfolio ───────────────────────────────────

class AIChatRequest(BaseModel):
    question: str

@router.post("/chat")
async def chat_about_portfolio(req: AIChatRequest, db: AsyncSession = Depends(get_db)):
    """Ask Henry about his AI portfolio decisions, reasoning, or strategy."""
    from app.services.ai_portfolio import get_ai_portfolio, _get_ai_portfolio_equity
    from app.services.ai_service import _call_claude_async

    portfolio = await get_ai_portfolio(db)
    if not portfolio:
        raise HTTPException(404, "No AI portfolio exists")

    equity = await _get_ai_portfolio_equity(portfolio, db)

    # Get recent decisions for context
    action_result = await db.execute(
        select(PortfolioAction)
        .where(PortfolioAction.portfolio_id == portfolio.id)
        .order_by(desc(PortfolioAction.created_at))
        .limit(15)
    )
    recent_actions = action_result.scalars().all()

    decisions_text = ""
    for a in recent_actions:
        decisions_text += (
            f"  {a.created_at.strftime('%m/%d %H:%M')} | {a.ticker} {a.direction} | "
            f"{a.action_type} conf {a.confidence}/10 | {a.status} | {a.reasoning[:100]}\n"
        )
    if not decisions_text:
        decisions_text = "  No decisions yet."

    # Get open positions
    pos_result = await db.execute(
        select(Trade)
        .join(PortfolioTrade)
        .where(
            PortfolioTrade.portfolio_id == portfolio.id,
            Trade.status == "open",
            Trade.is_simulated == True,
        )
        .options(selectinload(Trade.trader))
    )
    open_positions = pos_result.scalars().all()

    holdings_text = ""
    for t in open_positions:
        cp = price_service.get_price(t.ticker) or t.entry_price
        pnl = ((cp - t.entry_price) / t.entry_price * 100) if t.direction == "long" else ((t.entry_price - cp) / t.entry_price * 100)
        holdings_text += f"  {t.trader.trader_id}: {t.direction.upper()} {t.ticker} @ ${t.entry_price:.2f} → ${cp:.2f} ({pnl:+.2f}%)\n"
    if not holdings_text:
        holdings_text = "  No open positions."

    # Closed trades stats
    closed_result = await db.execute(
        select(Trade)
        .join(PortfolioTrade)
        .where(
            PortfolioTrade.portfolio_id == portfolio.id,
            Trade.status == "closed",
            Trade.is_simulated == True,
        )
    )
    closed = closed_result.scalars().all()
    wins = len([t for t in closed if (t.pnl_dollars or 0) > 0])
    total_closed = len(closed)
    win_rate = (wins / total_closed * 100) if total_closed > 0 else 0

    return_pct = ((equity / portfolio.initial_capital) - 1) * 100

    prompt = f"""The user is asking about your AI paper portfolio management. Answer their question directly and specifically, referencing actual data from your portfolio.

AI PORTFOLIO STATE:
  Equity: ${equity:.2f} (return: {return_pct:+.2f}%)
  Cash: ${portfolio.cash:.2f}
  Open positions: {len(open_positions)}
  Closed trades: {total_closed} ({wins}W/{total_closed - wins}L, {win_rate:.1f}% WR)

CURRENT HOLDINGS:
{holdings_text}

RECENT DECISIONS (newest first):
{decisions_text}

USER QUESTION: {req.question}

Answer concisely. Reference specific trades, tickers, and numbers. If the user is questioning a decision, explain your reasoning and what data informed it. If they ask about strategy, explain your decision framework."""

    answer = await _call_claude_async(prompt, max_tokens=800, scope="general")
    return {"answer": answer}


# ── AI Trading Config ────────────────────────────────────────────────────

class AITradingConfig(BaseModel):
    min_confidence: int = 5
    high_alloc_pct: float = 5.0
    mid_alloc_pct: float = 3.0
    min_adx: int = 20
    require_stop: bool = True
    reward_risk_ratio: float = 2.0

@router.get("/config")
async def get_config():
    """Get Henry's AI trading decision framework config."""
    from app.services.ai_portfolio import get_ai_config
    return get_ai_config()

@router.put("/config")
async def update_config(cfg: AITradingConfig):
    """Update Henry's AI trading decision framework config."""
    from app.services.ai_portfolio import save_ai_config
    config = cfg.model_dump()
    await save_ai_config(config)
    return config
