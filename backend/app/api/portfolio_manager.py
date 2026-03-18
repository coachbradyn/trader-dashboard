import csv
import io
import logging
import re
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models import PortfolioAction, BacktestImport, BacktestTrade, PortfolioHolding
from app.schemas.portfolio_manager import (
    HoldingCreate, HoldingUpdate, HoldingResponse,
    ActionResponse, ActionReject, ActionStats,
    BacktestImportResponse, BacktestTradeResponse,
)
from app.services.price_service import price_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/portfolio-manager", tags=["portfolio-manager"])


# ── FILENAME PARSER ──────────────────────────────────────────────────

def parse_backtest_filename(filename: str) -> dict:
    """
    Parse TradingView backtest CSV filename.
    Pattern: {STRATEGY}_{VERSION}_{EXCHANGE}_{TICKER}_{DATE}.csv
    Example: HENRY_v3.8_NASDAQ_NVDA_2026-03-17.csv
    """
    name = filename.rsplit(".", 1)[0]  # strip .csv
    parts = name.split("_")

    if len(parts) < 4:
        raise ValueError(f"Cannot parse filename '{filename}'. Expected pattern: STRATEGY_VERSION_EXCHANGE_TICKER_DATE.csv")

    # Try to find version (starts with 'v')
    version_idx = None
    for i, p in enumerate(parts):
        if re.match(r"^v\d", p, re.IGNORECASE):
            version_idx = i
            break

    if version_idx is not None:
        strategy_name = "_".join(parts[:version_idx])
        strategy_version = parts[version_idx]
        remaining = parts[version_idx + 1:]
    else:
        strategy_name = parts[0]
        strategy_version = None
        remaining = parts[1:]

    # Last part might be a date (YYYY-MM-DD), strip it
    if remaining and re.match(r"^\d{4}-\d{2}-\d{2}$", remaining[-1]):
        remaining = remaining[:-1]

    if len(remaining) >= 2:
        exchange = remaining[0]
        ticker = remaining[1]
    elif len(remaining) == 1:
        exchange = None
        ticker = remaining[0]
    else:
        raise ValueError(f"Cannot extract ticker from filename '{filename}'")

    return {
        "strategy_name": strategy_name,
        "strategy_version": strategy_version,
        "exchange": exchange,
        "ticker": ticker,
    }


# ── CSV PARSER ──────────────────────────────────────────────────────

def parse_float(val: str) -> float | None:
    if not val or val.strip() == "":
        return None
    try:
        return float(val.replace(",", ""))
    except ValueError:
        return None


def parse_backtest_csv(content: str) -> list[dict]:
    """Parse TradingView 'List of Trades' CSV export."""
    reader = csv.DictReader(io.StringIO(content))
    rows = []
    for row in reader:
        # Determine direction from Type column
        type_val = row.get("Type", "").strip()
        if "long" in type_val.lower():
            direction = "long"
        elif "short" in type_val.lower():
            direction = "short"
        else:
            direction = "long"

        # Parse date
        date_str = row.get("Date and time", "").strip()
        try:
            trade_date = datetime.strptime(date_str, "%Y-%m-%d %H:%M")
        except ValueError:
            try:
                trade_date = datetime.strptime(date_str, "%Y-%m-%d")
            except ValueError:
                trade_date = datetime.utcnow()

        rows.append({
            "trade_number": int(row.get("Trade #", "0").strip()),
            "type": type_val,
            "direction": direction,
            "signal": row.get("Signal", "").strip() or None,
            "price": parse_float(row.get("Price USD", "0")) or 0.0,
            "qty": parse_float(row.get("Position size (qty)", "")),
            "position_value": parse_float(row.get("Position size (value)", "")),
            "net_pnl": parse_float(row.get("Net P&L USD", "")),
            "net_pnl_pct": parse_float(row.get("Net P&L %", "")),
            "favorable_excursion": parse_float(row.get("Favorable excursion USD", "")),
            "favorable_excursion_pct": parse_float(row.get("Favorable excursion %", "")),
            "adverse_excursion": parse_float(row.get("Adverse excursion USD", "")),
            "adverse_excursion_pct": parse_float(row.get("Adverse excursion %", "")),
            "cumulative_pnl": parse_float(row.get("Cumulative P&L USD", "")),
            "cumulative_pnl_pct": parse_float(row.get("Cumulative P&L %", "")),
            "trade_date": trade_date,
        })
    return rows


def compute_backtest_stats(trades: list[dict]) -> dict:
    """Compute summary stats from parsed backtest trade rows."""
    # Only look at exit rows for P&L stats
    exits = [t for t in trades if "exit" in t["type"].lower()]

    if not exits:
        return {}

    winners = [t for t in exits if (t["net_pnl"] or 0) > 0]
    losers = [t for t in exits if (t["net_pnl"] or 0) <= 0]

    win_rate = (len(winners) / len(exits) * 100) if exits else None

    avg_gain = None
    if winners:
        gains = [t["net_pnl_pct"] for t in winners if t["net_pnl_pct"] is not None]
        avg_gain = sum(gains) / len(gains) if gains else None

    avg_loss = None
    if losers:
        losses = [t["net_pnl_pct"] for t in losers if t["net_pnl_pct"] is not None]
        avg_loss = sum(losses) / len(losses) if losses else None

    # Profit factor
    gross_profit = sum(t["net_pnl"] for t in winners if t["net_pnl"] is not None)
    gross_loss = abs(sum(t["net_pnl"] for t in losers if t["net_pnl"] is not None))
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else None

    # Max drawdown from cumulative P&L
    cum_pnls = [t["cumulative_pnl_pct"] for t in exits if t["cumulative_pnl_pct"] is not None]
    max_drawdown = None
    if cum_pnls:
        peak = cum_pnls[0]
        max_dd = 0.0
        for cp in cum_pnls:
            if cp > peak:
                peak = cp
            dd = peak - cp
            if dd > max_dd:
                max_dd = dd
        max_drawdown = max_dd

    # Average adverse excursion
    mae_vals = [t["adverse_excursion_pct"] for t in exits if t["adverse_excursion_pct"] is not None]
    avg_mae = (sum(mae_vals) / len(mae_vals)) if mae_vals else None

    # Average hold time (requires pairing entries and exits)
    hold_days = []
    entries_by_num = {t["trade_number"]: t for t in trades if "entry" in t["type"].lower()}
    for ex in exits:
        entry = entries_by_num.get(ex["trade_number"])
        if entry:
            delta = (ex["trade_date"] - entry["trade_date"]).total_seconds() / 86400
            hold_days.append(delta)
    avg_hold = (sum(hold_days) / len(hold_days)) if hold_days else None

    # Total P&L
    total_pnl = exits[-1]["cumulative_pnl_pct"] if exits and exits[-1]["cumulative_pnl_pct"] is not None else None

    return {
        "trade_count": len(exits),
        "win_rate": round(win_rate, 2) if win_rate is not None else None,
        "profit_factor": round(profit_factor, 2) if profit_factor is not None else None,
        "avg_gain_pct": round(avg_gain, 2) if avg_gain is not None else None,
        "avg_loss_pct": round(avg_loss, 2) if avg_loss is not None else None,
        "max_drawdown_pct": round(max_drawdown, 2) if max_drawdown is not None else None,
        "max_adverse_excursion_pct": round(avg_mae, 2) if avg_mae is not None else None,
        "avg_hold_days": round(avg_hold, 2) if avg_hold is not None else None,
        "total_pnl_pct": round(total_pnl, 2) if total_pnl is not None else None,
    }


# ══════════════════════════════════════════════════════════════════════
# BACKTEST IMPORT ENDPOINTS
# ══════════════════════════════════════════════════════════════════════

@router.post("/import", response_model=list[BacktestImportResponse])
async def import_backtests(
    files: list[UploadFile] = File(...),
    db: AsyncSession = Depends(get_db),
):
    """Upload one or more TradingView backtest CSV files. Filename auto-parsed for strategy/ticker."""
    results = []

    for file in files:
        if not file.filename or not file.filename.endswith(".csv"):
            raise HTTPException(400, f"File '{file.filename}' is not a CSV")

        try:
            meta = parse_backtest_filename(file.filename)
        except ValueError as e:
            raise HTTPException(400, str(e))

        content = (await file.read()).decode("utf-8-sig")  # handle BOM
        trade_rows = parse_backtest_csv(content)

        if not trade_rows:
            raise HTTPException(400, f"No trades found in '{file.filename}'")

        stats = compute_backtest_stats(trade_rows)

        # Check for existing import with same strategy+ticker, replace it
        existing = await db.execute(
            select(BacktestImport).where(
                BacktestImport.strategy_name == meta["strategy_name"],
                BacktestImport.ticker == meta["ticker"],
            )
        )
        old_import = existing.scalar_one_or_none()
        if old_import:
            await db.delete(old_import)
            await db.flush()

        bt_import = BacktestImport(
            strategy_name=meta["strategy_name"],
            strategy_version=meta["strategy_version"],
            exchange=meta["exchange"],
            ticker=meta["ticker"],
            filename=file.filename,
            trade_count=stats.get("trade_count", 0),
            win_rate=stats.get("win_rate"),
            profit_factor=stats.get("profit_factor"),
            avg_gain_pct=stats.get("avg_gain_pct"),
            avg_loss_pct=stats.get("avg_loss_pct"),
            max_drawdown_pct=stats.get("max_drawdown_pct"),
            max_adverse_excursion_pct=stats.get("max_adverse_excursion_pct"),
            avg_hold_days=stats.get("avg_hold_days"),
            total_pnl_pct=stats.get("total_pnl_pct"),
        )
        db.add(bt_import)
        await db.flush()

        # Insert individual trade rows
        for row in trade_rows:
            bt_trade = BacktestTrade(
                import_id=bt_import.id,
                **row,
            )
            db.add(bt_trade)

        results.append(bt_import)

    await db.commit()

    return [
        BacktestImportResponse(
            id=imp.id,
            strategy_name=imp.strategy_name,
            strategy_version=imp.strategy_version,
            exchange=imp.exchange,
            ticker=imp.ticker,
            filename=imp.filename,
            trade_count=imp.trade_count,
            win_rate=imp.win_rate,
            profit_factor=imp.profit_factor,
            avg_gain_pct=imp.avg_gain_pct,
            avg_loss_pct=imp.avg_loss_pct,
            max_drawdown_pct=imp.max_drawdown_pct,
            max_adverse_excursion_pct=imp.max_adverse_excursion_pct,
            avg_hold_days=imp.avg_hold_days,
            total_pnl_pct=imp.total_pnl_pct,
            imported_at=imp.imported_at,
        )
        for imp in results
    ]


@router.get("/imports", response_model=list[BacktestImportResponse])
async def list_imports(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(BacktestImport).order_by(BacktestImport.imported_at.desc())
    )
    return result.scalars().all()


@router.get("/imports/{import_id}/trades", response_model=list[BacktestTradeResponse])
async def get_import_trades(import_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(BacktestTrade)
        .where(BacktestTrade.import_id == import_id)
        .order_by(BacktestTrade.trade_number, BacktestTrade.trade_date)
    )
    return result.scalars().all()


@router.delete("/imports/{import_id}")
async def delete_import(import_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(BacktestImport)
        .where(BacktestImport.id == import_id)
        .options(selectinload(BacktestImport.trades))
    )
    bt_import = result.scalar_one_or_none()
    if not bt_import:
        raise HTTPException(404, "Import not found")

    await db.delete(bt_import)
    await db.commit()
    return {"status": "deleted"}


# ══════════════════════════════════════════════════════════════════════
# HOLDINGS ENDPOINTS
# ══════════════════════════════════════════════════════════════════════

@router.get("/holdings", response_model=list[HoldingResponse])
async def list_holdings(
    portfolio_id: str | None = None,
    active_only: bool = True,
    db: AsyncSession = Depends(get_db),
):
    query = select(PortfolioHolding)
    if portfolio_id:
        query = query.where(PortfolioHolding.portfolio_id == portfolio_id)
    if active_only:
        query = query.where(PortfolioHolding.is_active == True)
    query = query.order_by(PortfolioHolding.created_at.desc())

    result = await db.execute(query)
    holdings = result.scalars().all()

    responses = []
    for h in holdings:
        current_price = price_service.get_price(h.ticker)
        unrealized = None
        unrealized_pct = None

        if current_price is not None and h.is_active:
            if h.direction == "long":
                unrealized = (current_price - h.entry_price) * h.qty
            else:
                unrealized = (h.entry_price - current_price) * h.qty
            position_value = h.entry_price * h.qty
            unrealized_pct = (unrealized / position_value * 100) if position_value > 0 else 0.0

        source = "manual" if h.trade_id is None else (h.strategy_name or "webhook")

        responses.append(HoldingResponse(
            id=h.id,
            portfolio_id=h.portfolio_id,
            trade_id=h.trade_id,
            ticker=h.ticker,
            direction=h.direction,
            entry_price=h.entry_price,
            qty=h.qty,
            entry_date=h.entry_date,
            strategy_name=h.strategy_name,
            notes=h.notes,
            is_active=h.is_active,
            source=source,
            current_price=current_price,
            unrealized_pnl=round(unrealized, 2) if unrealized is not None else None,
            unrealized_pnl_pct=round(unrealized_pct, 2) if unrealized_pct is not None else None,
            created_at=h.created_at,
        ))

    # Register tickers for price tracking
    for h in holdings:
        price_service.add_ticker(h.ticker)

    return responses


@router.post("/holdings", response_model=HoldingResponse)
async def create_holding(body: HoldingCreate, db: AsyncSession = Depends(get_db)):
    holding = PortfolioHolding(
        portfolio_id=body.portfolio_id,
        ticker=body.ticker.upper(),
        direction=body.direction,
        entry_price=body.entry_price,
        qty=body.qty,
        entry_date=body.entry_date,
        strategy_name=body.strategy_name,
        notes=body.notes,
    )
    db.add(holding)
    await db.commit()
    await db.refresh(holding)

    # Register ticker for price tracking
    price_service.add_ticker(holding.ticker)

    return HoldingResponse(
        id=holding.id,
        portfolio_id=holding.portfolio_id,
        trade_id=None,
        ticker=holding.ticker,
        direction=holding.direction,
        entry_price=holding.entry_price,
        qty=holding.qty,
        entry_date=holding.entry_date,
        strategy_name=holding.strategy_name,
        notes=holding.notes,
        is_active=holding.is_active,
        source="manual",
        current_price=None,
        unrealized_pnl=None,
        unrealized_pnl_pct=None,
        created_at=holding.created_at,
    )


@router.put("/holdings/{holding_id}", response_model=HoldingResponse)
async def update_holding(holding_id: str, body: HoldingUpdate, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(PortfolioHolding).where(PortfolioHolding.id == holding_id))
    holding = result.scalar_one_or_none()
    if not holding:
        raise HTTPException(404, "Holding not found")

    for field, value in body.model_dump(exclude_unset=True).items():
        if field == "ticker" and value is not None:
            value = value.upper()
        setattr(holding, field, value)

    await db.commit()
    await db.refresh(holding)

    current_price = price_service.get_price(holding.ticker)
    unrealized = None
    unrealized_pct = None
    if current_price is not None and holding.is_active:
        if holding.direction == "long":
            unrealized = (current_price - holding.entry_price) * holding.qty
        else:
            unrealized = (holding.entry_price - current_price) * holding.qty
        position_value = holding.entry_price * holding.qty
        unrealized_pct = (unrealized / position_value * 100) if position_value > 0 else 0.0

    source = "manual" if holding.trade_id is None else (holding.strategy_name or "webhook")

    return HoldingResponse(
        id=holding.id,
        portfolio_id=holding.portfolio_id,
        trade_id=holding.trade_id,
        ticker=holding.ticker,
        direction=holding.direction,
        entry_price=holding.entry_price,
        qty=holding.qty,
        entry_date=holding.entry_date,
        strategy_name=holding.strategy_name,
        notes=holding.notes,
        is_active=holding.is_active,
        source=source,
        current_price=current_price,
        unrealized_pnl=round(unrealized, 2) if unrealized is not None else None,
        unrealized_pnl_pct=round(unrealized_pct, 2) if unrealized_pct is not None else None,
        created_at=holding.created_at,
    )


@router.delete("/holdings/{holding_id}")
async def delete_holding(holding_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(PortfolioHolding).where(PortfolioHolding.id == holding_id))
    holding = result.scalar_one_or_none()
    if not holding:
        raise HTTPException(404, "Holding not found")

    await db.delete(holding)
    await db.commit()
    return {"status": "deleted"}


# ══════════════════════════════════════════════════════════════════════
# ACTION QUEUE ENDPOINTS
# ══════════════════════════════════════════════════════════════════════

@router.get("/actions", response_model=list[ActionResponse])
async def list_actions(
    status: str = "pending",
    portfolio_id: str | None = None,
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
):
    query = select(PortfolioAction)

    if status != "all":
        query = query.where(PortfolioAction.status == status)
    if portfolio_id:
        query = query.where(PortfolioAction.portfolio_id == portfolio_id)

    # Pending actions sorted by priority, others by created_at
    if status == "pending":
        query = query.order_by(PortfolioAction.priority_score.desc())
    else:
        query = query.order_by(PortfolioAction.created_at.desc())

    query = query.limit(limit)

    result = await db.execute(query)
    actions = result.scalars().all()

    # Auto-expire stale pending actions
    now = datetime.utcnow()
    expired_ids = []
    for a in actions:
        if a.status == "pending" and a.expires_at and a.expires_at < now:
            a.status = "expired"
            a.resolved_at = now
            expired_ids.append(a.id)

    if expired_ids:
        await db.commit()

    return actions


@router.get("/actions/stats", response_model=ActionStats)
async def get_action_stats(db: AsyncSession = Depends(get_db)):
    today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)

    # Pending count
    pending = await db.execute(
        select(func.count()).select_from(PortfolioAction)
        .where(PortfolioAction.status == "pending")
    )
    pending_count = pending.scalar() or 0

    # Approved today
    approved_today_q = await db.execute(
        select(func.count()).select_from(PortfolioAction)
        .where(PortfolioAction.status == "approved", PortfolioAction.resolved_at >= today_start)
    )
    approved_today = approved_today_q.scalar() or 0

    # Rejected today
    rejected_today_q = await db.execute(
        select(func.count()).select_from(PortfolioAction)
        .where(PortfolioAction.status == "rejected", PortfolioAction.resolved_at >= today_start)
    )
    rejected_today = rejected_today_q.scalar() or 0

    # Total approved with outcomes
    total_approved_q = await db.execute(
        select(func.count()).select_from(PortfolioAction)
        .where(PortfolioAction.status == "approved")
    )
    total_approved = total_approved_q.scalar() or 0

    # Hit rate (approved actions with outcome tracking)
    with_outcome = await db.execute(
        select(PortfolioAction)
        .where(
            PortfolioAction.status == "approved",
            PortfolioAction.outcome_correct.isnot(None),
        )
    )
    outcomes = with_outcome.scalars().all()
    hit_rate = None
    hit_rate_high = None
    if outcomes:
        correct = sum(1 for o in outcomes if o.outcome_correct)
        hit_rate = round(correct / len(outcomes) * 100, 1)

        high_conf = [o for o in outcomes if o.confidence >= 8]
        if high_conf:
            correct_high = sum(1 for o in high_conf if o.outcome_correct)
            hit_rate_high = round(correct_high / len(high_conf) * 100, 1)

    return ActionStats(
        pending_count=pending_count,
        approved_today=approved_today,
        rejected_today=rejected_today,
        total_approved=total_approved,
        hit_rate=hit_rate,
        hit_rate_high_confidence=hit_rate_high,
    )


@router.post("/actions/{action_id}/approve")
async def approve_action(action_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(PortfolioAction).where(PortfolioAction.id == action_id))
    action = result.scalar_one_or_none()
    if not action:
        raise HTTPException(404, "Action not found")
    if action.status != "pending":
        raise HTTPException(400, f"Action is already {action.status}")

    action.status = "approved"
    action.resolved_at = datetime.utcnow()
    await db.commit()

    return {"status": "approved", "action_id": action_id, "action_type": action.action_type, "ticker": action.ticker}


@router.post("/actions/{action_id}/reject")
async def reject_action(action_id: str, body: ActionReject | None = None, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(PortfolioAction).where(PortfolioAction.id == action_id))
    action = result.scalar_one_or_none()
    if not action:
        raise HTTPException(404, "Action not found")
    if action.status != "pending":
        raise HTTPException(400, f"Action is already {action.status}")

    action.status = "rejected"
    action.resolved_at = datetime.utcnow()
    if body and body.reason:
        action.reject_reason = body.reason
    await db.commit()

    return {"status": "rejected", "action_id": action_id}
