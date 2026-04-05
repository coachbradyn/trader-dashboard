import asyncio
from app.utils.utc import utcnow
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db, async_session
from app.models import Portfolio
from app.models.portfolio_holding import PortfolioHolding
from app.services.alpaca_service import alpaca_service

logger = logging.getLogger(__name__)

# Background task tracking — prevents GC from dropping fire-and-forget tasks
_background_tasks: set[asyncio.Task] = set()

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

    # Phase 1 (inline, fast): poll 6 × 0.5s = 3s for fill confirmation
    order_id = order_result.get("order_id")
    fill_result = None
    if order_id:
        for _ in range(6):
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
    background_polling = False
    if fill_result and fill_result.get("status") == "filled":
        fill_price = fill_result.get("filled_price")
        fill_qty = fill_result.get("filled_qty", body.qty)
        await _update_holding_local(
            db, portfolio, body.ticker, fill_qty, body.side, fill_price,
        )
        holding_updated = True
    elif order_id and (not fill_result or fill_result.get("status") not in ("filled",)):
        # Phase 2 (background): continue polling every 2s for up to 60s
        background_polling = True
        _portfolio_id = portfolio.id
        _api_key = portfolio.alpaca_api_key
        _secret_key = portfolio.alpaca_secret_key
        _ticker = body.ticker
        _qty = body.qty
        _side = body.side

        async def _poll_for_delayed_fill():
            from app.services.henry_activity import log_activity
            try:
                for _ in range(30):  # 30 × 2s = 60s
                    await asyncio.sleep(2)
                    status = await alpaca_service.get_order_status(
                        api_key=_api_key,
                        secret_key=_secret_key,
                        paper=is_paper,
                        order_id=order_id,
                    )
                    if status.get("status") == "filled":
                        fill_price = status.get("filled_price")
                        fill_qty = status.get("filled_qty", _qty)
                        async with async_session() as bg_db:
                            bg_port_result = await bg_db.execute(
                                select(Portfolio).where(Portfolio.id == _portfolio_id)
                            )
                            bg_port = bg_port_result.scalar_one_or_none()
                            if bg_port:
                                await _update_holding_local(
                                    bg_db, bg_port, _ticker, fill_qty, _side, fill_price,
                                )
                        await log_activity(
                            f"Delayed fill confirmed: {_side} {_ticker} x{fill_qty} @ ${fill_price:.2f}",
                            "trade_execute", ticker=_ticker,
                        )
                        logger.info(f"Delayed fill confirmed: {_side} {_ticker} x{fill_qty} @ ${fill_price:.2f} (order {order_id})")
                        return
                    if status.get("status") in ("canceled", "expired", "rejected"):
                        await log_activity(
                            f"Order {order_id} for {_ticker} was {status.get('status')}",
                            "error", ticker=_ticker,
                        )
                        return
                # Timeout — no fill after 60s
                await log_activity(
                    f"Order {order_id} for {_ticker} did not confirm within 60s — verify manually",
                    "error", ticker=_ticker,
                )
                logger.warning(f"Order {order_id} for {_ticker} did not fill within 60s")
            except Exception as e:
                logger.error(f"Background fill poll failed for {order_id}: {e}")

        task = asyncio.create_task(_poll_for_delayed_fill())
        _background_tasks.add(task)
        task.add_done_callback(_background_tasks.discard)

    return {
        **order_result,
        "fill": fill_result,
        "holding_updated": holding_updated,
        "background_polling": background_polling,
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
                entry_date=utcnow(),
                is_active=True,
                notes="alpaca_sync",
            )
            db.add(new_holding)
            created += 1

    # Removal pass: close DB holdings that no longer exist at Alpaca
    alpaca_tickers = {pos["symbol"] for pos in alpaca_positions}
    closed = 0
    for ticker, holding in existing_holdings.items():
        if ticker not in alpaca_tickers:
            holding.is_active = False
            holding.notes = (holding.notes or "") + " | closed_by_alpaca_sync"
            closed += 1

    await db.commit()
    logger.info(f"SYNC | portfolio={body.portfolio_id} synced={synced} created={created} closed={closed}")
    return {"status": "synced", "synced": synced, "created": created, "closed": closed, "alpaca_positions": len(alpaca_positions)}


@router.get("/kill-switch")
async def kill_switch_status(db: AsyncSession = Depends(get_db)):
    """Check kill switch status — how many portfolios are in live/paper mode."""
    result = await db.execute(
        select(func.count(Portfolio.id)).where(Portfolio.execution_mode.in_(["paper", "live"]))
    )
    active_count = result.scalar() or 0
    return {"active_trading_portfolios": active_count, "status": "armed" if active_count > 0 else "safe"}


@router.post("/kill-switch")
async def kill_switch(body: dict = None, db: AsyncSession = Depends(get_db)):
    """Immediately set ALL portfolios to execution_mode='local'. Requires confirm=true."""
    if not body or not body.get("confirm"):
        raise HTTPException(400, "Kill switch requires confirm=true in request body")

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
                entry_date=utcnow(),
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
