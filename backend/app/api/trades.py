from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
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
