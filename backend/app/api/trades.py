import csv
import io

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models import Trade, PortfolioTrade, Trader
from app.schemas.trade import TradeResponse

router = APIRouter()


@router.get("/trades", response_model=list[TradeResponse])
async def get_trades(
    trader_id: str | None = Query(None),
    portfolio_id: str | None = Query(None),
    status: str | None = Query(None),
    limit: int = Query(50, le=200),
    offset: int = Query(0),
    db: AsyncSession = Depends(get_db),
):
    query = select(Trade).options(selectinload(Trade.trader)).where(Trade.is_simulated == False)

    if trader_id:
        query = query.join(Trader).where(Trader.trader_id == trader_id)
    if portfolio_id:
        query = query.join(PortfolioTrade).where(PortfolioTrade.portfolio_id == portfolio_id)
    if status:
        query = query.where(Trade.status == status)

    query = query.order_by(Trade.created_at.desc()).offset(offset).limit(limit)
    result = await db.execute(query)
    trades = result.scalars().all()

    return [
        TradeResponse(
            id=t.id,
            trader_id=t.trader.trader_id,
            trader_name=t.trader.display_name,
            ticker=t.ticker,
            direction=t.direction,
            entry_price=t.entry_price,
            qty=t.qty,
            entry_signal_strength=t.entry_signal_strength,
            entry_adx=t.entry_adx,
            stop_price=t.stop_price,
            timeframe=t.timeframe,
            entry_time=t.entry_time,
            exit_price=t.exit_price,
            exit_reason=t.exit_reason,
            exit_time=t.exit_time,
            bars_in_trade=t.bars_in_trade,
            pnl_dollars=t.pnl_dollars,
            pnl_percent=t.pnl_percent,
            status=t.status,
        )
        for t in trades
    ]


@router.get("/trades/export")
async def export_trades_csv(
    status: str = Query("closed"),
    db: AsyncSession = Depends(get_db),
):
    """Export trades as CSV for tax reporting or offline analysis."""
    result = await db.execute(
        select(Trade)
        .options(selectinload(Trade.trader))
        .where(Trade.status == status, Trade.is_simulated == False)
        .order_by(desc(Trade.entry_time))
    )
    trades = result.scalars().all()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "ticker", "direction", "strategy", "entry_price", "exit_price",
        "qty", "pnl_dollars", "pnl_percent", "entry_time", "exit_time",
        "exit_reason", "timeframe", "bars_in_trade",
    ])
    for t in trades:
        writer.writerow([
            t.ticker, t.direction, t.trader.trader_id if t.trader else "",
            t.entry_price, t.exit_price or "", t.qty,
            round(t.pnl_dollars, 2) if t.pnl_dollars else "",
            round(t.pnl_percent, 2) if t.pnl_percent else "",
            t.entry_time.isoformat() if t.entry_time else "",
            t.exit_time.isoformat() if t.exit_time else "",
            t.exit_reason or "", t.timeframe or "",
            t.bars_in_trade or "",
        ])

    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=trades_export.csv"},
    )
