"""
AI Portfolio API
=================
Endpoints for managing Henry's paper portfolio, viewing performance comparison,
and browsing decision logs.
"""

import logging
from app.utils.utc import utcnow
from datetime import datetime, timedelta, timezone

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
            # Include both simulated and real trades linked to this portfolio
        )
    )
    open_count = pos_result.scalar() or 0

    # Total trades
    total_result = await db.execute(
        select(func.count(Trade.id))
        .join(PortfolioTrade)
        .where(
            PortfolioTrade.portfolio_id == portfolio.id,
            # Include both simulated and real trades linked to this portfolio
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
            # Include both simulated and real trades linked to this portfolio
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

    cutoff = utcnow() - timedelta(days=days)
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
    portfolio_id: str | None = Query(None, description="Filter to a single portfolio. Default: all AI-enabled."),
    db: AsyncSession = Depends(get_db),
):
    """Get Henry's decision log across all AI-enabled portfolios.

    Previously scoped to the single `is_ai_managed=True` portfolio and
    raised 404 when none existed — the frontend's `.catch` swallowed
    that, which is why the Decisions tab showed "No decisions yet" even
    when Henry had been logging actions against other AI-enabled
    portfolios (paper/live Alpaca accounts with
    `ai_evaluation_enabled=True`).

    Now returns actions from every portfolio with either flag set, with
    an optional `portfolio_id` filter for per-portfolio drill-down.
    """
    from sqlalchemy import or_

    # Collect every portfolio Henry makes decisions on. If
    # portfolio_id is provided, scope to that one portfolio regardless
    # of its flags (user may be asking about a specific book).
    if portfolio_id:
        port_q = select(Portfolio).where(Portfolio.id == portfolio_id)
    else:
        port_q = select(Portfolio).where(
            Portfolio.is_active == True,
            or_(
                Portfolio.is_ai_managed == True,
                Portfolio.ai_evaluation_enabled == True,
            ),
        )
    portfolio_rows = (await db.execute(port_q)).scalars().all()
    if not portfolio_rows:
        # Empty list — not a 404. The frontend renders "No decisions
        # yet" from an empty array; raising 404 was getting swallowed
        # by the client's catch and producing the same UX either way,
        # but without a debuggable signal.
        return []
    portfolio_ids = [p.id for p in portfolio_rows]
    portfolio_names = {p.id: p.name for p in portfolio_rows}

    query = select(PortfolioAction).where(PortfolioAction.portfolio_id.in_(portfolio_ids))

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
                    PortfolioTrade.portfolio_id == a.portfolio_id,
                    Trade.ticker == a.ticker,
                    # Include both simulated and real trades linked to this portfolio
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
            # Per-portfolio attribution so the UI can show which book
            # each decision targeted. Henry may be running on three
            # portfolios at once; "BUY NOK" means different things
            # depending on which account executed it.
            "portfolio_id": a.portfolio_id,
            "portfolio_name": portfolio_names.get(a.portfolio_id, ""),
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
        )
        .options(selectinload(Trade.trader))
        .order_by(desc(Trade.entry_time))
    )
    positions = result.scalars().all()

    holdings = []
    for t in positions:
        cp = price_service.get_price(t.ticker) or t.entry_price
        if t.entry_price and t.entry_price > 0:
            if t.direction == "long":
                pnl_pct = ((cp - t.entry_price) / t.entry_price * 100)
                pnl_dollars = (cp - t.entry_price) * t.qty
            else:
                pnl_pct = ((t.entry_price - cp) / t.entry_price * 100)
        else:
            pnl_pct = 0.0
            pnl_dollars = (t.entry_price - cp) * t.qty

        hold_hours = (utcnow() - t.entry_time).total_seconds() / 3600

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
            # Include both simulated and real trades linked to this portfolio
        )
        .options(selectinload(Trade.trader))
    )
    open_positions = pos_result.scalars().all()

    holdings_text = ""
    for t in open_positions:
        cp = price_service.get_price(t.ticker) or t.entry_price
        pnl = ((cp - t.entry_price) / t.entry_price * 100) if t.direction == "long" else ((t.entry_price - cp) / t.entry_price * 100) if t.entry_price and t.entry_price > 0 else 0.0
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
            # Include both simulated and real trades linked to this portfolio
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

    answer = await _call_claude_async(prompt, max_tokens=800, scope="general", function_name="ask_henry")
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


@router.post("/add-trade")
async def add_trade_to_ai_portfolio(body: dict, db: AsyncSession = Depends(get_db)):
    """Manually add an existing trade to a portfolio by ticker.
    Sizes the position to fit available cash (max 10% of equity).
    Pass portfolio_id to target a specific portfolio, or defaults to AI portfolio."""
    from app.services.ai_portfolio import get_ai_portfolio, _get_ai_portfolio_equity

    portfolio_id = body.get("portfolio_id")
    if portfolio_id:
        result = await db.execute(select(Portfolio).where(Portfolio.id == portfolio_id))
        portfolio = result.scalar_one_or_none()
    else:
        portfolio = await get_ai_portfolio(db)
    if not portfolio:
        raise HTTPException(404, "Portfolio not found")

    ticker = body.get("ticker", "").upper()
    trade_id = body.get("trade_id")

    if trade_id:
        result = await db.execute(select(Trade).where(Trade.id == trade_id))
        trade = result.scalar_one_or_none()
    elif ticker:
        result = await db.execute(
            select(Trade)
            .where(Trade.ticker == ticker, Trade.status == "open")
            .order_by(desc(Trade.entry_time))
            .limit(1)
        )
        trade = result.scalar_one_or_none()
    else:
        raise HTTPException(400, "Provide ticker or trade_id")

    if not trade:
        raise HTTPException(404, f"No open trade found for {ticker or trade_id}")

    # Check if already linked
    existing = await db.execute(
        select(PortfolioTrade).where(
            PortfolioTrade.portfolio_id == portfolio.id,
            PortfolioTrade.trade_id == trade.id,
        )
    )
    if existing.scalar_one_or_none():
        return {"status": "already_linked", "ticker": trade.ticker, "trade_id": trade.id}

    # Size to available cash — don't just use the original qty
    # Use portfolio cash directly (works for any portfolio type)
    cash = portfolio.cash or 0
    max_alloc = min(cash * 0.50, cash)  # Use up to 50% of available cash
    max_alloc = max(max_alloc, 0)

    if max_alloc < 10 and portfolio.cash < 10:
        raise HTTPException(400, f"Insufficient cash (${portfolio.cash:.2f}) to add trade")

    # Resize qty to fit
    original_qty = trade.qty
    original_cost = trade.entry_price * trade.qty
    if original_cost > max_alloc and trade.entry_price > 0:
        new_qty = round(max_alloc / trade.entry_price, 4)
        trade.qty = new_qty
        actual_cost = new_qty * trade.entry_price
    else:
        actual_cost = original_cost

    # Link and deduct
    pt = PortfolioTrade(portfolio_id=portfolio.id, trade_id=trade.id)
    db.add(pt)
    portfolio.cash -= actual_cost

    await db.commit()

    return {
        "status": "linked",
        "ticker": trade.ticker,
        "direction": trade.direction,
        "original_qty": original_qty,
        "qty": trade.qty,
        "entry_price": trade.entry_price,
        "cost": round(actual_cost, 2),
        "portfolio_cash": round(portfolio.cash, 2),
        "resized": trade.qty != original_qty,
    }


@router.post("/fix-all")
async def fix_all_trades(body: dict | None = None, db: AsyncSession = Depends(get_db)):
    """Fix a portfolio: resize all open positions to fit available cash.
    Pass portfolio_id to target a specific portfolio, or defaults to AI portfolio."""
    from app.services.ai_portfolio import get_ai_portfolio

    portfolio_id = (body or {}).get("portfolio_id") if body else None
    if portfolio_id:
        result = await db.execute(select(Portfolio).where(Portfolio.id == portfolio_id))
        portfolio = result.scalar_one_or_none()
    else:
        portfolio = await get_ai_portfolio(db)
    if not portfolio:
        raise HTTPException(404, "Portfolio not found")

    # Get ALL trades linked to this portfolio (both simulated and non-simulated)
    result = await db.execute(
        select(Trade)
        .join(PortfolioTrade)
        .where(PortfolioTrade.portfolio_id == portfolio.id)
        .options(selectinload(Trade.trader))
        .order_by(Trade.entry_time)
    )
    all_trades = result.scalars().all()

    # Recalculate from scratch: start with initial capital
    starting_cash = portfolio.initial_capital
    running_cash = starting_cash
    fixes = []

    for t in all_trades:
        if t.status == "closed":
            # Closed trade: net effect is the P&L
            running_cash += (t.pnl_dollars or 0)
            continue

        # Open trade — check if it fits
        cost = t.entry_price * t.qty
        max_per_trade = starting_cash * 0.10  # 10% of initial capital

        if cost > running_cash or cost > max_per_trade:
            # Oversized — resize to fit
            old_qty = t.qty
            affordable = min(running_cash * 0.90, max_per_trade)  # Leave 10% buffer

            if affordable < 5 or (t.entry_price > 0 and affordable / t.entry_price < 0.001):
                # Can't afford — close at current price
                cp = price_service.get_price(t.ticker) or t.entry_price
                t.status = "closed"
                t.exit_price = cp
                t.exit_reason = "fix_oversized"
                t.exit_time = utcnow()
                if t.direction == "long":
                    t.pnl_dollars = (cp - t.entry_price) * t.qty
                else:
                    t.pnl_dollars = (t.entry_price - cp) * t.qty
                pos_val = t.entry_price * t.qty
                t.pnl_percent = (t.pnl_dollars / pos_val * 100) if pos_val > 0 else 0
                # Return whatever was allocated
                running_cash += (t.pnl_dollars or 0)
                fixes.append({
                    "ticker": t.ticker,
                    "action": "closed",
                    "old_qty": old_qty,
                    "reason": f"insufficient cash (${running_cash:.2f})",
                    "pnl": round(t.pnl_dollars or 0, 2),
                })
            else:
                # Resize to fit
                new_qty = round(affordable / t.entry_price, 4) if t.entry_price > 0 else 0
                t.qty = new_qty
                new_cost = new_qty * t.entry_price
                running_cash -= new_cost
                fixes.append({
                    "ticker": t.ticker,
                    "action": "resized",
                    "old_qty": round(old_qty, 4),
                    "new_qty": new_qty,
                    "old_cost": round(cost, 2),
                    "new_cost": round(new_cost, 2),
                })
        else:
            running_cash -= cost

    # Set corrected cash
    old_cash = portfolio.cash
    portfolio.cash = round(max(running_cash, 0), 2)

    await db.commit()

    return {
        "status": "fixed",
        "initial_capital": portfolio.initial_capital,
        "old_cash": round(old_cash, 2),
        "new_cash": round(running_cash, 2),
        "fixes_applied": fixes,
        "total_trades": len(all_trades),
    }


@router.post("/set-capital")
async def set_portfolio_capital(body: dict, db: AsyncSession = Depends(get_db)):
    """Reset a portfolio's initial capital and recalculate cash from scratch.
    Example: {"initial_capital": 25, "portfolio_id": "xxx"}
    This replays all closed trade P&L on top of the new starting capital,
    and resizes all open positions to fit."""
    from app.services.ai_portfolio import get_ai_portfolio

    portfolio_id = body.get("portfolio_id")
    new_capital = body.get("initial_capital")
    if new_capital is None:
        raise HTTPException(400, "Provide initial_capital")

    if portfolio_id:
        result = await db.execute(select(Portfolio).where(Portfolio.id == portfolio_id))
        portfolio = result.scalar_one_or_none()
    else:
        portfolio = await get_ai_portfolio(db)
    if not portfolio:
        raise HTTPException(404, "Portfolio not found")

    old_capital = portfolio.initial_capital
    portfolio.initial_capital = new_capital

    # Replay: start with new capital, apply closed trade P&L, resize open trades
    running_cash = new_capital

    all_result = await db.execute(
        select(Trade)
        .join(PortfolioTrade)
        .where(PortfolioTrade.portfolio_id == portfolio.id)
        .order_by(Trade.entry_time)
    )
    all_trades = all_result.scalars().all()

    fixes = []
    for t in all_trades:
        if t.status == "closed":
            running_cash += (t.pnl_dollars or 0)
        elif t.status == "open":
            cost = t.entry_price * t.qty
            max_per_trade = new_capital * 0.10
            affordable = min(max_per_trade, running_cash * 0.90)

            if cost > affordable and t.entry_price > 0:
                old_qty = t.qty
                if affordable < 1:
                    # Close it
                    cp = price_service.get_price(t.ticker) or t.entry_price
                    t.status = "closed"
                    t.exit_price = cp
                    t.exit_reason = "capital_reset"
                    t.exit_time = utcnow()
                    t.pnl_dollars = (cp - t.entry_price) * t.qty if t.direction == "long" else (t.entry_price - cp) * t.qty
                    t.pnl_percent = (t.pnl_dollars / cost * 100) if cost > 0 else 0
                    running_cash += (t.pnl_dollars or 0)
                    fixes.append({"ticker": t.ticker, "action": "closed", "old_qty": round(old_qty, 4), "reason": "insufficient capital"})
                else:
                    new_qty = round(affordable / t.entry_price, 4)
                    t.qty = new_qty
                    running_cash -= new_qty * t.entry_price
                    fixes.append({"ticker": t.ticker, "action": "resized", "old_qty": round(old_qty, 4), "new_qty": new_qty})
            else:
                running_cash -= cost

    portfolio.cash = round(max(running_cash, 0), 2)
    await db.commit()

    return {
        "status": "capital_reset",
        "old_capital": old_capital,
        "new_capital": new_capital,
        "cash": portfolio.cash,
        "fixes": fixes,
    }


@router.post("/resize-ticker")
async def resize_ticker_position(body: dict, db: AsyncSession = Depends(get_db)):
    """Resize a specific ticker's position to a dollar amount.
    Example: {"ticker": "SNDK", "target_dollars": 10, "portfolio_id": "xxx"}"""
    portfolio_id = body.get("portfolio_id")
    ticker = body.get("ticker", "").upper()
    target_dollars = body.get("target_dollars", 10)

    if not ticker:
        raise HTTPException(400, "Provide ticker")
    if not portfolio_id:
        from app.services.ai_portfolio import get_ai_portfolio
        portfolio = await get_ai_portfolio(db)
    else:
        result = await db.execute(select(Portfolio).where(Portfolio.id == portfolio_id))
        portfolio = result.scalar_one_or_none()

    if not portfolio:
        raise HTTPException(404, "Portfolio not found")

    # Find the open trade for this ticker in this portfolio
    result = await db.execute(
        select(Trade)
        .join(PortfolioTrade)
        .where(
            PortfolioTrade.portfolio_id == portfolio.id,
            Trade.ticker == ticker,
            Trade.status == "open",
        )
        .order_by(desc(Trade.entry_time))
        .limit(1)
    )
    trade = result.scalar_one_or_none()
    if not trade:
        raise HTTPException(404, f"No open {ticker} position in this portfolio")

    old_qty = trade.qty
    old_cost = old_qty * trade.entry_price

    # Calculate new qty for target dollar amount
    new_qty = round(target_dollars / trade.entry_price, 4) if trade.entry_price > 0 else 0
    new_cost = new_qty * trade.entry_price

    # Update trade
    trade.qty = new_qty

    # Adjust cash: return old cost, deduct new cost
    portfolio.cash = portfolio.cash + old_cost - new_cost

    await db.commit()

    return {
        "status": "resized",
        "ticker": ticker,
        "old_qty": round(old_qty, 4),
        "new_qty": new_qty,
        "old_cost": round(old_cost, 2),
        "new_cost": round(new_cost, 2),
        "target_dollars": target_dollars,
        "portfolio_cash": round(portfolio.cash, 2),
    }


@router.post("/fix-trade/{trade_id}")
async def fix_trade_sizing(trade_id: str, db: AsyncSession = Depends(get_db)):
    """Fix a trade's qty to respect cash available at time of trade.
    Recalculates qty based on portfolio cash and adjusts cash accordingly."""
    from app.services.ai_portfolio import get_ai_portfolio

    portfolio = await get_ai_portfolio(db)
    if not portfolio:
        raise HTTPException(404, "No AI portfolio exists")

    # Find the trade
    result = await db.execute(
        select(Trade).where(Trade.id == trade_id)
    )
    trade = result.scalar_one_or_none()
    if not trade:
        raise HTTPException(404, f"Trade {trade_id} not found")

    # Verify it's linked to AI portfolio
    pt_result = await db.execute(
        select(PortfolioTrade).where(
            PortfolioTrade.trade_id == trade_id,
            PortfolioTrade.portfolio_id == portfolio.id,
        )
    )
    if not pt_result.scalar_one_or_none():
        raise HTTPException(400, "Trade not linked to AI portfolio")

    old_qty = trade.qty
    old_cost = old_qty * trade.entry_price

    # Calculate proper sizing: use current cash + what this trade cost
    # (add back the old cost since it was already deducted)
    available_cash = portfolio.cash + old_cost
    max_alloc = available_cash * 0.10  # 10% max per trade
    proper_alloc = min(max_alloc, available_cash)
    new_qty = round(proper_alloc / trade.entry_price, 4) if trade.entry_price > 0 else 0

    if new_qty <= 0:
        raise HTTPException(400, f"Cannot resize — cash too low (${available_cash:.2f})")

    new_cost = new_qty * trade.entry_price

    # Update trade qty
    trade.qty = new_qty

    # Adjust portfolio cash: give back old cost, deduct new cost
    portfolio.cash = available_cash - new_cost

    await db.commit()

    return {
        "trade_id": trade_id,
        "ticker": trade.ticker,
        "old_qty": old_qty,
        "new_qty": new_qty,
        "old_cost": round(old_cost, 2),
        "new_cost": round(new_cost, 2),
        "portfolio_cash": round(portfolio.cash, 2),
        "message": f"Resized {trade.ticker} from {old_qty} to {new_qty} shares",
    }


@router.get("/debug")
async def debug_ai_portfolio(db: AsyncSession = Depends(get_db)):
    """Debug view of AI portfolio state — cash, equity, all open trades."""
    from app.services.ai_portfolio import get_ai_portfolio, _get_ai_portfolio_equity

    portfolio = await get_ai_portfolio(db)
    if not portfolio:
        return {"error": "No AI portfolio exists"}

    equity = await _get_ai_portfolio_equity(portfolio, db)

    result = await db.execute(
        select(Trade)
        .join(PortfolioTrade)
        .where(
            PortfolioTrade.portfolio_id == portfolio.id,
            # Include both simulated and real trades linked to this portfolio
        )
        .options(selectinload(Trade.trader))
        .order_by(desc(Trade.entry_time))
    )
    all_trades = result.scalars().all()

    trades = []
    for t in all_trades:
        cp = price_service.get_price(t.ticker) or t.entry_price
        cost = t.entry_price * t.qty
        market_val = cp * t.qty if t.status == "open" else 0
        trades.append({
            "id": t.id,
            "ticker": t.ticker,
            "direction": t.direction,
            "qty": t.qty,
            "entry_price": t.entry_price,
            "current_price": round(cp, 2),
            "cost": round(cost, 2),
            "market_value": round(market_val, 2),
            "status": t.status,
            "pnl_dollars": round(t.pnl_dollars, 2) if t.pnl_dollars else None,
            "entry_time": (t.entry_time.isoformat() + "Z") if t.entry_time else None,
            "strategy": t.trader.trader_id if t.trader else None,
        })

    return {
        "portfolio_id": portfolio.id,
        "name": portfolio.name,
        "initial_capital": portfolio.initial_capital,
        "cash": round(portfolio.cash, 2),
        "equity": round(equity, 2),
        "open_trades": [t for t in trades if t["status"] == "open"],
        "closed_trades": [t for t in trades if t["status"] == "closed"],
        "total_trades": len(trades),
    }
