import asyncio
import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Portfolio
from app.models.portfolio_holding import PortfolioHolding
from app.services.alpaca_service import alpaca_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/execution", tags=["execution"])


# ── Schemas ───────────────────────────────────────────────────────────

class TestConnectionRequest(BaseModel):
    portfolio_id: str


class OrderRequest(BaseModel):
    portfolio_id: str
    ticker: str
    qty: float
    side: str  # "buy" or "sell"


class SyncRequest(BaseModel):
    portfolio_id: str


# ── Helpers ───────────────────────────────────────────────────────────

def _mask_key(key: str | None) -> str | None:
    if not key or len(key) < 8:
        return None
    return key[:4] + "..." + key[-4:]


async def _get_portfolio_with_creds(portfolio_id: str, db: AsyncSession) -> Portfolio:
    result = await db.execute(select(Portfolio).where(Portfolio.id == portfolio_id))
    portfolio = result.scalar_one_or_none()
    if not portfolio:
        raise HTTPException(404, "Portfolio not found")
    return portfolio


# ── Endpoints ─────────────────────────────────────────────────────────

@router.post("/test-connection")
async def test_connection(body: TestConnectionRequest, db: AsyncSession = Depends(get_db)):
    """Test Alpaca credentials for a portfolio."""
    portfolio = await _get_portfolio_with_creds(body.portfolio_id, db)

    if not portfolio.alpaca_api_key or not portfolio.alpaca_secret_key:
        return {"status": "error", "message": "No Alpaca credentials configured for this portfolio"}

    is_paper = portfolio.execution_mode == "paper"
    result = await alpaca_service.test_connection(
        api_key=portfolio.alpaca_api_key,
        secret_key=portfolio.alpaca_secret_key,
        paper=is_paper,
    )
    return result


@router.post("/order")
async def submit_order(body: OrderRequest, db: AsyncSession = Depends(get_db)):
    """Submit an order - routes through local, paper, or live based on portfolio execution_mode."""
    portfolio = await _get_portfolio_with_creds(body.portfolio_id, db)

    logger.info(
        f"ORDER REQUEST | portfolio={portfolio.id} mode={portfolio.execution_mode} "
        f"ticker={body.ticker} qty={body.qty} side={body.side}"
    )

    # ── LOCAL MODE: just update holdings in DB ──
    if portfolio.execution_mode == "local":
        holding_result = await _update_holding_local(
            db, portfolio, body.ticker, body.qty, body.side,
        )
        return {
            "status": "executed_local",
            "ticker": body.ticker,
            "qty": body.qty,
            "side": body.side,
            "holding_updated": True,
            **holding_result,
        }

    # ── PAPER / LIVE MODE: submit to Alpaca ──
    if not portfolio.alpaca_api_key or not portfolio.alpaca_secret_key:
        raise HTTPException(400, "No Alpaca credentials configured for this portfolio")

    is_paper = portfolio.execution_mode == "paper"

    # Safety: check max_order_amount
    if portfolio.max_order_amount and portfolio.max_order_amount > 0:
        from app.services.price_service import price_service
        current_price = price_service.get_price(body.ticker)
        if current_price:
            order_value = current_price * body.qty
            if order_value > portfolio.max_order_amount:
                raise HTTPException(
                    400,
                    f"Order value ${order_value:.2f} exceeds max_order_amount ${portfolio.max_order_amount:.2f}"
                )

    order_result = await alpaca_service.submit_order(
        api_key=portfolio.alpaca_api_key,
        secret_key=portfolio.alpaca_secret_key,
        paper=is_paper,
        ticker=body.ticker,
        qty=body.qty,
        side=body.side,
        max_order_amount=portfolio.max_order_amount,
    )

    logger.info(
        f"ORDER RESULT | portfolio={portfolio.id} mode={portfolio.execution_mode} "
        f"ticker={body.ticker} qty={body.qty} side={body.side} "
        f"order_id={order_result.get('order_id', 'N/A')} status={order_result.get('status', 'unknown')}"
    )

    if order_result.get("status") == "error":
        return order_result

    # Poll for fill (up to 5 seconds)
    order_id = order_result.get("order_id")
    fill_result = None
    if order_id:
        for _ in range(10):
            await asyncio.sleep(0.5)
            fill_result = await alpaca_service.get_order_status(
                api_key=portfolio.alpaca_api_key,
                secret_key=portfolio.alpaca_secret_key,
                paper=is_paper,
                order_id=order_id,
            )
            if fill_result.get("status") in ("filled", "partially_filled"):
                break

    # On fill, update holdings
    holding_updated = False
    if fill_result and fill_result.get("status") == "filled":
        fill_price = fill_result.get("filled_price")
        fill_qty = fill_result.get("filled_qty", body.qty)
        await _update_holding_local(
            db, portfolio, body.ticker, fill_qty, body.side, fill_price,
        )
        holding_updated = True

    return {
        **order_result,
        "fill": fill_result,
        "holding_updated": holding_updated,
        "mode": portfolio.execution_mode,
    }


@router.get("/order/{order_id}")
async def get_order_status(order_id: str, portfolio_id: str, db: AsyncSession = Depends(get_db)):
    """Check order fill status."""
    portfolio = await _get_portfolio_with_creds(portfolio_id, db)
    if not portfolio.alpaca_api_key or not portfolio.alpaca_secret_key:
        raise HTTPException(400, "No Alpaca credentials for this portfolio")

    is_paper = portfolio.execution_mode == "paper"
    return await alpaca_service.get_order_status(
        api_key=portfolio.alpaca_api_key,
        secret_key=portfolio.alpaca_secret_key,
        paper=is_paper,
        order_id=order_id,
    )


@router.get("/positions")
async def get_alpaca_positions(portfolio_id: str, db: AsyncSession = Depends(get_db)):
    """Get Alpaca account positions for reconciliation."""
    portfolio = await _get_portfolio_with_creds(portfolio_id, db)
    if not portfolio.alpaca_api_key or not portfolio.alpaca_secret_key:
        raise HTTPException(400, "No Alpaca credentials for this portfolio")

    is_paper = portfolio.execution_mode == "paper"
    positions = await alpaca_service.get_positions(
        api_key=portfolio.alpaca_api_key,
        secret_key=portfolio.alpaca_secret_key,
        paper=is_paper,
    )
    return positions


@router.post("/sync")
async def sync_positions(body: SyncRequest, db: AsyncSession = Depends(get_db)):
    """Sync Alpaca positions to portfolio holdings (reconciliation)."""
    portfolio = await _get_portfolio_with_creds(body.portfolio_id, db)
    if not portfolio.alpaca_api_key or not portfolio.alpaca_secret_key:
        raise HTTPException(400, "No Alpaca credentials for this portfolio")

    is_paper = portfolio.execution_mode == "paper"
    alpaca_positions = await alpaca_service.get_positions(
        api_key=portfolio.alpaca_api_key,
        secret_key=portfolio.alpaca_secret_key,
        paper=is_paper,
    )

    # Get current holdings
    holdings_result = await db.execute(
        select(PortfolioHolding).where(
            PortfolioHolding.portfolio_id == body.portfolio_id,
            PortfolioHolding.is_active == True,
        )
    )
    existing_holdings = {h.ticker: h for h in holdings_result.scalars().all()}

    synced = 0
    created = 0
    for pos in alpaca_positions:
        ticker = pos["symbol"]
        qty = pos["qty"]
        entry_price = pos["avg_entry_price"]

        if ticker in existing_holdings:
            h = existing_holdings[ticker]
            h.qty = qty
            h.entry_price = entry_price
            synced += 1
        else:
            new_holding = PortfolioHolding(
                portfolio_id=body.portfolio_id,
                ticker=ticker,
                direction="long" if pos.get("side", "long") == "long" else "short",
                entry_price=entry_price,
                qty=qty,
                entry_date=datetime.utcnow(),
                is_active=True,
                notes="alpaca_sync",
            )
            db.add(new_holding)
            created += 1

    await db.commit()
    logger.info(f"SYNC | portfolio={body.portfolio_id} synced={synced} created={created}")
    return {"status": "synced", "synced": synced, "created": created, "alpaca_positions": len(alpaca_positions)}


@router.post("/kill-switch")
async def kill_switch(db: AsyncSession = Depends(get_db)):
    """Immediately set ALL portfolios to execution_mode='local'. No confirmation needed."""
    result = await db.execute(
        select(Portfolio).where(Portfolio.execution_mode.in_(["paper", "live"]))
    )
    portfolios = result.scalars().all()
    count = 0
    for p in portfolios:
        p.execution_mode = "local"
        count += 1
    await db.commit()

    logger.warning(f"KILL SWITCH ACTIVATED | {count} portfolios set to local mode")
    return {"status": "killed", "portfolios_affected": count}


# ── Internal helper ───────────────────────────────────────────────────

async def _update_holding_local(
    db: AsyncSession,
    portfolio: Portfolio,
    ticker: str,
    qty: float,
    side: str,
    fill_price: float = None,
) -> dict:
    """Create or update a holding in the DB for a buy/sell."""
    ticker = ticker.upper()

    if side.lower() == "buy":
        # Check for existing active holding to add to
        existing = await db.execute(
            select(PortfolioHolding).where(
                PortfolioHolding.portfolio_id == portfolio.id,
                PortfolioHolding.ticker == ticker,
                PortfolioHolding.is_active == True,
            )
        )
        holding = existing.scalar_one_or_none()

        if holding:
            # Average up/down
            total_cost = holding.entry_price * holding.qty + (fill_price or holding.entry_price) * qty
            total_qty = holding.qty + qty
            holding.entry_price = total_cost / total_qty
            holding.qty = total_qty
            if holding.avg_cost is not None:
                holding.avg_cost = holding.entry_price
            if holding.total_shares is not None:
                holding.total_shares = total_qty
            await db.commit()
            return {"action": "updated_existing", "holding_id": holding.id}
        else:
            new_holding = PortfolioHolding(
                portfolio_id=portfolio.id,
                ticker=ticker,
                direction="long",
                entry_price=fill_price or 0,
                qty=qty,
                entry_date=datetime.utcnow(),
                is_active=True,
                notes="execution",
            )
            db.add(new_holding)
            await db.commit()
            await db.refresh(new_holding)
            return {"action": "created", "holding_id": new_holding.id}
    else:
        # Sell: find and reduce/close holding
        existing = await db.execute(
            select(PortfolioHolding).where(
                PortfolioHolding.portfolio_id == portfolio.id,
                PortfolioHolding.ticker == ticker,
                PortfolioHolding.is_active == True,
            )
        )
        holding = existing.scalar_one_or_none()

        if not holding:
            return {"action": "no_holding_found", "holding_id": None}

        if qty >= holding.qty:
            holding.is_active = False
            await db.commit()
            return {"action": "closed", "holding_id": holding.id}
        else:
            holding.qty -= qty
            if holding.total_shares is not None:
                holding.total_shares = holding.qty
            await db.commit()
            return {"action": "reduced", "holding_id": holding.id}
