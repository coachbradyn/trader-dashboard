import logging
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models import Portfolio, Trader, PortfolioStrategy, Trade, PortfolioTrade
from app.models.allowlisted_key import AllowlistedKey
from app.schemas.settings import (
    PortfolioCreate, PortfolioFullUpdate, PortfolioSettingsResponse,
    TraderUpdate, TraderSettingsResponse,
    AllowlistedKeyCreate, AllowlistedKeyResponse,
    StrategyAssignment,
)
from app.utils.auth import generate_api_key, hash_api_key

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/settings", tags=["settings"])


# ── PORTFOLIO MANAGEMENT ──────────────────────────────────────────────

@router.get("/portfolios", response_model=list[PortfolioSettingsResponse])
async def list_portfolios(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Portfolio)
        .options(selectinload(Portfolio.strategies).selectinload(PortfolioStrategy.trader))
        .order_by(Portfolio.created_at.desc())
    )
    portfolios = result.scalars().all()

    responses = []
    for p in portfolios:
        strats = [
            {
                "trader_id": s.trader.id,
                "trader_slug": s.trader.trader_id,
                "display_name": s.trader.display_name,
                "direction_filter": s.direction_filter,
            }
            for s in p.strategies
        ]
        responses.append(PortfolioSettingsResponse(
            id=p.id,
            name=p.name,
            description=p.description,
            initial_capital=p.initial_capital,
            cash=p.cash,
            status=p.status,
            max_pct_per_trade=p.max_pct_per_trade,
            max_open_positions=p.max_open_positions,
            max_drawdown_pct=p.max_drawdown_pct,
            execution_mode=p.execution_mode or "local",
            max_order_amount=p.max_order_amount,
            has_alpaca_credentials=bool(p.alpaca_api_key and p.alpaca_secret_key),
            alpaca_key_preview=(p.alpaca_api_key[:4] + "...") if p.alpaca_api_key and len(p.alpaca_api_key) >= 4 else None,
            ai_evaluation_enabled=getattr(p, "ai_evaluation_enabled", False) or False,
            created_at=p.created_at,
            strategies=strats,
        ))
    return responses


@router.post("/portfolios", response_model=PortfolioSettingsResponse)
async def create_portfolio(body: PortfolioCreate, db: AsyncSession = Depends(get_db)):
    portfolio = Portfolio(
        name=body.name,
        description=body.description,
        initial_capital=body.initial_capital,
        cash=body.initial_capital,
        max_pct_per_trade=body.max_pct_per_trade,
        max_open_positions=body.max_open_positions,
        max_drawdown_pct=body.max_drawdown_pct,
    )
    db.add(portfolio)
    await db.commit()
    await db.refresh(portfolio)
    return PortfolioSettingsResponse(
        id=portfolio.id,
        name=portfolio.name,
        description=portfolio.description,
        initial_capital=portfolio.initial_capital,
        cash=portfolio.cash,
        status=portfolio.status,
        max_pct_per_trade=portfolio.max_pct_per_trade,
        max_open_positions=portfolio.max_open_positions,
        max_drawdown_pct=portfolio.max_drawdown_pct,
        execution_mode=portfolio.execution_mode or "local",
        max_order_amount=portfolio.max_order_amount,
        has_alpaca_credentials=False,
        created_at=portfolio.created_at,
        strategies=[],
    )


@router.put("/portfolios/{portfolio_id}")
async def update_portfolio(portfolio_id: str, body: PortfolioFullUpdate, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Portfolio).where(Portfolio.id == portfolio_id))
    portfolio = result.scalar_one_or_none()
    if not portfolio:
        raise HTTPException(404, "Portfolio not found")

    if body.portfolio:
        if body.portfolio.name is not None:
            portfolio.name = body.portfolio.name
        if body.portfolio.description is not None:
            portfolio.description = body.portfolio.description
        if body.portfolio.max_pct_per_trade is not None:
            portfolio.max_pct_per_trade = body.portfolio.max_pct_per_trade
        if body.portfolio.max_open_positions is not None:
            portfolio.max_open_positions = body.portfolio.max_open_positions
        if body.portfolio.max_drawdown_pct is not None:
            portfolio.max_drawdown_pct = body.portfolio.max_drawdown_pct
        if body.portfolio.execution_mode is not None:
            portfolio.execution_mode = body.portfolio.execution_mode
        if body.portfolio.alpaca_api_key is not None:
            portfolio.alpaca_api_key = body.portfolio.alpaca_api_key
        if body.portfolio.alpaca_secret_key is not None:
            portfolio.alpaca_secret_key = body.portfolio.alpaca_secret_key
        if body.portfolio.max_order_amount is not None:
            portfolio.max_order_amount = body.portfolio.max_order_amount
        if hasattr(body.portfolio, "ai_evaluation_enabled") and body.portfolio.ai_evaluation_enabled is not None:
            portfolio.ai_evaluation_enabled = body.portfolio.ai_evaluation_enabled

    if body.strategies is not None:
        # Remove existing strategy links
        result = await db.execute(
            select(PortfolioStrategy).where(PortfolioStrategy.portfolio_id == portfolio_id)
        )
        existing = result.scalars().all()
        for s in existing:
            await db.delete(s)

        # Create new links
        for sa in body.strategies:
            link = PortfolioStrategy(
                portfolio_id=portfolio_id,
                trader_id=sa.trader_id,
                direction_filter=sa.direction_filter,
            )
            db.add(link)

    await db.commit()
    return {"status": "updated"}


@router.patch("/portfolios/{portfolio_id}/archive")
async def archive_portfolio(portfolio_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Portfolio).where(Portfolio.id == portfolio_id))
    portfolio = result.scalar_one_or_none()
    if not portfolio:
        raise HTTPException(404, "Portfolio not found")

    portfolio.status = "archived"
    portfolio.is_active = False
    await db.commit()
    return {"status": "archived"}


@router.delete("/portfolios/{portfolio_id}")
async def delete_portfolio(portfolio_id: str, db: AsyncSession = Depends(get_db)):
    """Permanently delete a portfolio and all its related data."""
    from app.models import PortfolioSnapshot, DailyStats, PortfolioAction
    from app.models.portfolio_holding import PortfolioHolding

    result = await db.execute(select(Portfolio).where(Portfolio.id == portfolio_id))
    portfolio = result.scalar_one_or_none()
    if not portfolio:
        raise HTTPException(404, "Portfolio not found")

    # Delete portfolio_trades (junction table)
    pt_result = await db.execute(
        select(PortfolioTrade).where(PortfolioTrade.portfolio_id == portfolio_id)
    )
    for pt in pt_result.scalars().all():
        await db.delete(pt)

    # Delete snapshots
    snap_result = await db.execute(
        select(PortfolioSnapshot).where(PortfolioSnapshot.portfolio_id == portfolio_id)
    )
    for s in snap_result.scalars().all():
        await db.delete(s)

    # Delete daily stats
    ds_result = await db.execute(
        select(DailyStats).where(DailyStats.portfolio_id == portfolio_id)
    )
    for d in ds_result.scalars().all():
        await db.delete(d)

    # Delete portfolio strategies
    ps_result = await db.execute(
        select(PortfolioStrategy).where(PortfolioStrategy.portfolio_id == portfolio_id)
    )
    for ps in ps_result.scalars().all():
        await db.delete(ps)

    # Delete holdings
    h_result = await db.execute(
        select(PortfolioHolding).where(PortfolioHolding.portfolio_id == portfolio_id)
    )
    for h in h_result.scalars().all():
        await db.delete(h)

    # Delete actions
    a_result = await db.execute(
        select(PortfolioAction).where(PortfolioAction.portfolio_id == portfolio_id)
    )
    for a in a_result.scalars().all():
        await db.delete(a)

    # Delete simulated trades linked to this portfolio
    sim_result = await db.execute(
        select(Trade).where(Trade.is_simulated == True)
        .join(PortfolioTrade, PortfolioTrade.trade_id == Trade.id, isouter=True)
        .where(PortfolioTrade.portfolio_id == portfolio_id)
    )
    # The portfolio trades are already deleted above, so simulated trades linked
    # only to this portfolio are now orphaned — but we already deleted the links.

    # Delete the portfolio itself
    await db.delete(portfolio)
    await db.commit()

    return {"status": "deleted", "id": portfolio_id}


@router.delete("/traders/{trader_slug}")
async def delete_trader(trader_slug: str, db: AsyncSession = Depends(get_db)):
    """Permanently delete a strategy/trader and all its trades."""
    result = await db.execute(select(Trader).where(Trader.trader_id == trader_slug))
    trader = result.scalar_one_or_none()
    if not trader:
        raise HTTPException(404, "Trader not found")

    # Delete portfolio_trades for this trader's trades
    trade_result = await db.execute(select(Trade).where(Trade.trader_id == trader.id))
    trades = trade_result.scalars().all()
    trade_ids = [t.id for t in trades]

    if trade_ids:
        pt_result = await db.execute(
            select(PortfolioTrade).where(PortfolioTrade.trade_id.in_(trade_ids))
        )
        for pt in pt_result.scalars().all():
            await db.delete(pt)

    # Delete the trades
    for t in trades:
        await db.delete(t)

    # Delete portfolio_strategies
    ps_result = await db.execute(
        select(PortfolioStrategy).where(PortfolioStrategy.trader_id == trader.id)
    )
    for ps in ps_result.scalars().all():
        await db.delete(ps)

    # Delete the trader
    await db.delete(trader)
    await db.commit()

    return {"status": "deleted", "trader_id": trader_slug}


# ── TRADER / STRATEGY MANAGEMENT ──────────────────────────────────────

@router.get("/traders", response_model=list[TraderSettingsResponse])
async def list_traders(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Trader)
        .options(selectinload(Trader.portfolio_strategies).selectinload(PortfolioStrategy.portfolio))
        .order_by(Trader.created_at.desc())
    )
    traders = result.scalars().all()

    responses = []
    for t in traders:
        # Count trades
        trade_count_result = await db.execute(
            select(func.count()).select_from(Trade).where(Trade.trader_id == t.id)
        )
        trade_count = trade_count_result.scalar() or 0

        portfolios = [
            {
                "portfolio_id": ps.portfolio.id,
                "portfolio_name": ps.portfolio.name,
                "direction_filter": ps.direction_filter,
            }
            for ps in t.portfolio_strategies
        ]

        responses.append(TraderSettingsResponse(
            id=t.id,
            trader_id=t.trader_id,
            display_name=t.display_name,
            strategy_name=t.strategy_name,
            description=t.description,
            is_active=t.is_active,
            created_at=t.created_at,
            last_webhook_at=t.last_webhook_at,
            portfolio_count=len(portfolios),
            trade_count=trade_count,
            portfolios=portfolios,
        ))
    return responses


@router.put("/traders/{trader_slug}")
async def update_trader(trader_slug: str, body: TraderUpdate, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Trader).where(Trader.trader_id == trader_slug))
    trader = result.scalar_one_or_none()
    if not trader:
        raise HTTPException(404, "Trader not found")

    if body.display_name is not None:
        trader.display_name = body.display_name
    if body.strategy_name is not None:
        trader.strategy_name = body.strategy_name
    if body.description is not None:
        trader.description = body.description

    await db.commit()
    return {"status": "updated"}


@router.post("/traders/{trader_slug}/rotate-key")
async def rotate_trader_key(trader_slug: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Trader).where(Trader.trader_id == trader_slug))
    trader = result.scalar_one_or_none()
    if not trader:
        raise HTTPException(404, "Trader not found")

    new_key = generate_api_key()
    trader.api_key_hash = hash_api_key(new_key)
    await db.commit()
    return {"api_key": new_key, "message": "Key rotated. Copy this key now — it won't be shown again."}


# ── ALLOWLISTED KEY MANAGEMENT ────────────────────────────────────────

@router.get("/keys", response_model=list[AllowlistedKeyResponse])
async def list_keys(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(AllowlistedKey).order_by(AllowlistedKey.created_at.desc())
    )
    return result.scalars().all()


@router.post("/keys/generate")
async def generate_key(body: AllowlistedKeyCreate, db: AsyncSession = Depends(get_db)):
    raw_key = generate_api_key()
    hashed = hash_api_key(raw_key)

    key = AllowlistedKey(api_key_hash=hashed, label=body.label)
    db.add(key)
    await db.commit()
    await db.refresh(key)

    return {
        "id": key.id,
        "api_key": raw_key,
        "label": key.label,
        "message": "Copy this key now — it won't be shown again.",
    }


@router.delete("/keys/{key_id}")
async def revoke_key(key_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(AllowlistedKey).where(AllowlistedKey.id == key_id))
    key = result.scalar_one_or_none()
    if not key:
        raise HTTPException(404, "Key not found")
    if key.claimed_by_id:
        raise HTTPException(400, "Cannot revoke a claimed key")

    await db.delete(key)
    await db.commit()
    return {"status": "revoked"}
