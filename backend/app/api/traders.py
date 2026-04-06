from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models import Trader, PortfolioStrategy
from app.schemas.trader import TraderResponse

router = APIRouter()


@router.get("/traders", response_model=list[TraderResponse])
async def get_traders(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Trader)
        .where(Trader.is_active == True)
        .options(selectinload(Trader.portfolio_strategies).selectinload(PortfolioStrategy.portfolio))
    )
    traders = result.scalars().all()

    return [
        TraderResponse(
            id=t.id,
            trader_id=t.trader_id,
            display_name=t.display_name,
            strategy_name=t.strategy_name,
            description=t.description,
            is_active=t.is_active,
            created_at=t.created_at,
            portfolios=[ps.portfolio.name for ps in t.portfolio_strategies],
        )
        for t in traders
    ]


@router.get("/traders/{trader_slug}", response_model=TraderResponse)
async def get_trader(trader_slug: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Trader)
        .where(Trader.trader_id == trader_slug)
        .options(selectinload(Trader.portfolio_strategies).selectinload(PortfolioStrategy.portfolio))
    )
    trader = result.scalar_one_or_none()
    if not trader:
        raise HTTPException(404, "Trader not found")

    return TraderResponse(
        id=trader.id,
        trader_id=trader.trader_id,
        display_name=trader.display_name,
        strategy_name=trader.strategy_name,
        description=trader.description,
        is_active=trader.is_active,
        created_at=trader.created_at,
        portfolios=[ps.portfolio.name for ps in trader.portfolio_strategies],
    )


@router.patch("/traders/{trader_slug}/toggle-active")
async def toggle_trader_active(trader_slug: str, db: AsyncSession = Depends(get_db)):
    """Pause or resume a strategy's webhook processing."""
    result = await db.execute(
        select(Trader).where(Trader.trader_id == trader_slug)
    )
    trader = result.scalar_one_or_none()
    if not trader:
        raise HTTPException(404, "Trader not found")

    trader.is_active = not trader.is_active
    await db.commit()
    return {"trader_id": trader.trader_id, "is_active": trader.is_active}
