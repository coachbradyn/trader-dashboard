import asyncio
import os
from app.utils.utc import utcnow
import logging
from datetime import datetime, timezone, timedelta as _timedelta

from fastapi import APIRouter, Depends, HTTPException, Header
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


# Defined up-here so reconcile-all (and any future privileged endpoint
# defined before the kill-switch block) can reference it as a Depends.
def _verify_kill_switch_token(authorization: str = Header(None)):
    """Require a bearer token for kill-switch / reconcile operations.
    Falls back to allow-all when no token is configured so local dev
    isn't blocked, matching the original kill-switch behavior."""
    token = os.environ.get("KILL_SWITCH_TOKEN") or os.environ.get("DASHBOARD_API_KEY")
    if not token:
        return
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(403, "Endpoint requires Authorization: Bearer <token>")
    if authorization.split(" ", 1)[1] != token:
        raise HTTPException(403, "Invalid token")


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

# ── Options order endpoint ────────────────────────────────────────────

class OptionsLegRequest(BaseModel):
    option_symbol: str
    qty: int
    side: str  # "buy" | "sell"


class OptionsOrderRequest(BaseModel):
    portfolio_id: str
    strategy_type: str
    legs: list[OptionsLegRequest]
    limit_price: float | None = None   # net debit (positive) / credit (negative)
    max_risk_dollars: float | None = None  # computed max loss, for safety check
    notes: str | None = None


async def _today_options_count(portfolio_id: str, db: AsyncSession) -> int:
    """Count options legs opened today for this portfolio (one multi-leg
    strategy counts as one trade since legs share a spread_group_id)."""
    from app.models.options_trade import OptionsTrade
    start_of_day = datetime.combine(
        utcnow().date(), datetime.min.time(), tzinfo=timezone.utc
    )
    rows = await db.execute(
        select(OptionsTrade.spread_group_id, OptionsTrade.id)
        .where(
            OptionsTrade.portfolio_id == portfolio_id,
            OptionsTrade.entry_time >= start_of_day,
        )
    )
    groups: set[str] = set()
    singles = 0
    for gid, _id in rows.all():
        if gid:
            groups.add(gid)
        else:
            singles += 1
    return len(groups) + singles


@router.post("/options-order")
async def submit_options_order(body: OptionsOrderRequest, db: AsyncSession = Depends(get_db)):
    """Submit an options order (single-leg or multi-leg). Enforces safety
    rails: options level, covered short legs, min DTE, daily trade cap,
    and max risk. This is the final choke point — even if the strategy
    selector or UI is bypassed, orders still can't exceed the configured
    level."""
    import uuid as _uuid
    from datetime import date as _date
    from app.models.options_trade import OptionsTrade, STRATEGY_MIN_LEVEL
    from app.services.options_service import _parse_occ_symbol
    from app.models.henry_cache import HenryCache
    from sqlalchemy import select as _select

    portfolio = await _get_portfolio_with_creds(body.portfolio_id, db)
    level = int(getattr(portfolio, "options_level", 0) or 0)
    if level <= 0:
        raise HTTPException(400, "Options trading is disabled for this portfolio (level 0)")

    # 1. Strategy permitted at this level?
    min_level = STRATEGY_MIN_LEVEL.get(body.strategy_type)
    if min_level is None:
        raise HTTPException(400, f"Unknown strategy_type: {body.strategy_type}")
    if level < min_level:
        raise HTTPException(
            400,
            f"Strategy {body.strategy_type} requires options level {min_level}; "
            f"portfolio is level {level}",
        )

    # 2. Legs shape + OCC parseable?
    if not body.legs:
        raise HTTPException(400, "Order must have at least one leg")
    parsed_legs = []
    for leg in body.legs:
        p = _parse_occ_symbol(leg.option_symbol)
        if not p:
            raise HTTPException(400, f"Cannot parse OCC symbol {leg.option_symbol}")
        if leg.qty <= 0:
            raise HTTPException(400, "leg.qty must be positive")
        if leg.side.lower() not in ("buy", "sell"):
            raise HTTPException(400, "leg.side must be buy or sell")
        parsed_legs.append({"leg": leg, "parsed": p})

    # 3. Min-DTE guard (opening only; closing short-dated positions is fine
    #    but this endpoint is for opens — the notes/strategy_type distinguish
    #    close flows in a future extension).
    today = _date.today()
    defaults_row = (await db.execute(
        _select(HenryCache).where(HenryCache.cache_key == "options:defaults")
    )).scalar_one_or_none()
    min_dte = 7
    if defaults_row and isinstance(defaults_row.content, dict):
        try:
            min_dte = int(defaults_row.content.get("min_dte", 7))
        except (TypeError, ValueError):
            min_dte = 7
    for item in parsed_legs:
        dte = (item["parsed"]["expiration"] - today).days
        if dte < min_dte:
            raise HTTPException(
                400,
                f"Leg {item['leg'].option_symbol} is {dte} DTE — below the {min_dte} DTE minimum",
            )

    # 4. No naked shorts. Every short leg must be either (a) covered by an
    #    equity position (covered_call / cash_secured_put — handled by
    #    strategy_type), or (b) paired with a long leg at the same expiry
    #    in the same ticker (spreads).
    short_legs = [i for i in parsed_legs if i["leg"].side.lower() == "sell"]
    long_legs = [i for i in parsed_legs if i["leg"].side.lower() == "buy"]
    if short_legs and body.strategy_type not in ("covered_call", "cash_secured_put"):
        # For spreads: each short leg needs a corresponding long leg in the
        # same underlying at a paired strike. We don't fully model every
        # combination here — we just require at least as many long legs as
        # short legs and that all legs share the underlying.
        if len(long_legs) < len(short_legs):
            raise HTTPException(400, "Short legs require matching long legs (no naked selling)")
        underlyings = {i["parsed"]["root"] for i in parsed_legs}
        if len(underlyings) != 1:
            raise HTTPException(400, "All legs must share the same underlying")

    # 5. Daily trade cap
    max_daily = getattr(portfolio, "max_options_daily_trades", None)
    if max_daily is None:
        max_daily = 5
        if defaults_row and isinstance(defaults_row.content, dict):
            try:
                max_daily = int(defaults_row.content.get("max_daily_trades", 5))
            except (TypeError, ValueError):
                pass
    todays = await _today_options_count(body.portfolio_id, db)
    if todays >= max_daily:
        raise HTTPException(
            400,
            f"Daily options trade limit reached ({todays}/{max_daily})",
        )

    # 6. Max risk cap
    max_risk_limit = getattr(portfolio, "max_options_risk", None)
    if max_risk_limit is None:
        max_risk_limit = 2000.0
        if defaults_row and isinstance(defaults_row.content, dict):
            try:
                max_risk_limit = float(defaults_row.content.get("max_risk_per_trade", 2000.0))
            except (TypeError, ValueError):
                pass
    if body.max_risk_dollars is not None and body.max_risk_dollars > max_risk_limit:
        raise HTTPException(
            400,
            f"Order max risk ${body.max_risk_dollars:.0f} exceeds portfolio cap ${max_risk_limit:.0f}",
        )

    # 7. Submit to Alpaca (only when execution_mode is paper/live)
    submitted_result: dict = {"status": "local-only", "message": "Execution mode is 'local' — recorded without broker submission."}
    api_key = portfolio.alpaca_api_key_decrypted
    secret_key = portfolio.alpaca_secret_key_decrypted
    paper = (portfolio.execution_mode or "local").lower() == "paper"

    if portfolio.execution_mode in ("paper", "live") and api_key and secret_key:
        if len(body.legs) == 1:
            leg = body.legs[0]
            submitted_result = await alpaca_service.submit_options_order(
                api_key=api_key, secret_key=secret_key, paper=paper,
                option_symbol=leg.option_symbol,
                qty=leg.qty, side=leg.side, limit_price=body.limit_price,
            )
        else:
            if body.limit_price is None:
                raise HTTPException(400, "limit_price required for multi-leg orders")
            submitted_result = await alpaca_service.submit_multi_leg_order(
                api_key=api_key, secret_key=secret_key, paper=paper,
                legs=[{"option_symbol": l.option_symbol, "qty": l.qty, "side": l.side} for l in body.legs],
                limit_price=body.limit_price,
            )
        if submitted_result.get("status") == "error":
            raise HTTPException(502, f"Alpaca rejected: {submitted_result.get('message')}")

    # 8. Record legs in options_trades
    spread_group_id = str(_uuid.uuid4()) if len(body.legs) > 1 else None
    alpaca_order_id = submitted_result.get("order_id") if submitted_result else None

    for item in parsed_legs:
        leg = item["leg"]
        p = item["parsed"]
        direction = "long" if leg.side.lower() == "buy" else "short"
        entry_premium = body.limit_price if len(body.legs) == 1 and body.limit_price else 0.0
        db.add(OptionsTrade(
            portfolio_id=body.portfolio_id,
            ticker=p["root"],
            option_symbol=leg.option_symbol,
            option_type=p["option_type"],
            strike=p["strike"],
            expiration=p["expiration"],
            direction=direction,
            quantity=int(leg.qty),
            entry_premium=float(entry_premium),
            strategy_type=body.strategy_type,
            spread_group_id=spread_group_id,
            alpaca_order_id=alpaca_order_id,
            notes=body.notes,
        ))
    await db.commit()

    return {
        "status": "accepted",
        "spread_group_id": spread_group_id,
        "alpaca": submitted_result,
        "legs": [
            {"option_symbol": l.option_symbol, "qty": l.qty, "side": l.side}
            for l in body.legs
        ],
    }


@router.get("/options-positions")
async def get_alpaca_options_positions(portfolio_id: str, db: AsyncSession = Depends(get_db)):
    """Live options positions from Alpaca for a portfolio (reconciliation view)."""
    portfolio = await _get_portfolio_with_creds(portfolio_id, db)
    api_key = portfolio.alpaca_api_key_decrypted
    secret_key = portfolio.alpaca_secret_key_decrypted
    if not api_key or not secret_key:
        raise HTTPException(400, "Portfolio has no Alpaca credentials configured")
    paper = (portfolio.execution_mode or "local").lower() == "paper"
    return await alpaca_service.get_options_positions(api_key, secret_key, paper)


@router.post("/test-connection")
async def test_connection(body: TestConnectionRequest, db: AsyncSession = Depends(get_db)):
    """Test Alpaca credentials for a portfolio."""
    portfolio = await _get_portfolio_with_creds(body.portfolio_id, db)

    if not portfolio.alpaca_api_key or not portfolio.alpaca_secret_key:
        return {"status": "error", "message": "No Alpaca credentials configured for this portfolio"}

    is_paper = portfolio.execution_mode == "paper"
    result = await alpaca_service.test_connection(
        api_key=portfolio.alpaca_api_key_decrypted,
        secret_key=portfolio.alpaca_secret_key_decrypted,
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
        api_key=portfolio.alpaca_api_key_decrypted,
        secret_key=portfolio.alpaca_secret_key_decrypted,
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

    # On fill or partial fill, update holdings
    holding_updated = False
    background_polling = False
    recorded_fill_qty = 0.0
    if fill_result and fill_result.get("status") in ("filled", "partially_filled"):
        fill_price = fill_result.get("filled_price")
        fill_qty = fill_result.get("filled_qty", body.qty if fill_result.get("status") == "filled" else 0)
        if fill_qty and fill_qty > 0:
            await _update_holding_local(
                db, portfolio, body.ticker, fill_qty, body.side, fill_price,
            )
            holding_updated = True
            recorded_fill_qty = fill_qty

    if order_id and (not fill_result or fill_result.get("status") not in ("filled",)):
        # Phase 2 (background): continue polling every 2s for up to 60s
        background_polling = True
        _portfolio_id = portfolio.id
        _api_key = portfolio.alpaca_api_key_decrypted
        _secret_key = portfolio.alpaca_secret_key_decrypted
        _ticker = body.ticker
        _qty = body.qty
        _side = body.side

        _prev_filled = recorded_fill_qty  # qty already recorded in Phase 1

        async def _poll_for_delayed_fill():
            from app.services.henry_activity import log_activity
            cumulative_filled = _prev_filled
            try:
                for _ in range(30):  # 30 × 2s = 60s
                    await asyncio.sleep(2)
                    status = await alpaca_service.get_order_status(
                        api_key=_api_key,
                        secret_key=_secret_key,
                        paper=is_paper,
                        order_id=order_id,
                    )
                    if status.get("status") in ("filled", "partially_filled"):
                        fill_price = status.get("filled_price")
                        fill_qty = status.get("filled_qty", _qty if status.get("status") == "filled" else 0)
                        delta = fill_qty - cumulative_filled if fill_qty else 0
                        if delta > 0:
                            async with async_session() as bg_db:
                                bg_port_result = await bg_db.execute(
                                    select(Portfolio).where(Portfolio.id == _portfolio_id)
                                )
                                bg_port = bg_port_result.scalar_one_or_none()
                                if bg_port:
                                    await _update_holding_local(
                                        bg_db, bg_port, _ticker, delta, _side, fill_price,
                                    )
                            cumulative_filled = fill_qty
                            await log_activity(
                                f"Delayed fill confirmed: {_side} {_ticker} x{fill_qty} @ ${fill_price:.2f}",
                                "trade_execute", ticker=_ticker,
                            )
                            logger.info(f"Delayed fill confirmed: {_side} {_ticker} x{fill_qty} @ ${fill_price:.2f} (order {order_id})")
                        if status.get("status") == "filled":
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
        api_key=portfolio.alpaca_api_key_decrypted,
        secret_key=portfolio.alpaca_secret_key_decrypted,
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
        api_key=portfolio.alpaca_api_key_decrypted,
        secret_key=portfolio.alpaca_secret_key_decrypted,
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
        api_key=portfolio.alpaca_api_key_decrypted,
        secret_key=portfolio.alpaca_secret_key_decrypted,
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

    # Sync cash balance from Alpaca account
    cash_synced = False
    alpaca_cash = None
    try:
        account_info = await alpaca_service.get_account_info(
            api_key=portfolio.alpaca_api_key_decrypted,
            secret_key=portfolio.alpaca_secret_key_decrypted,
            paper=is_paper,
        )
        if account_info.get("status") == "connected":
            alpaca_cash = account_info.get("cash")
            if alpaca_cash is not None:
                portfolio.cash = alpaca_cash
                cash_synced = True
    except Exception as e:
        logger.warning(f"Cash sync failed for portfolio {body.portfolio_id}: {e}")

    await db.commit()
    logger.info(f"SYNC | portfolio={body.portfolio_id} synced={synced} created={created} closed={closed} cash_synced={cash_synced}")
    return {
        "status": "synced",
        "synced": synced,
        "created": created,
        "closed": closed,
        "alpaca_positions": len(alpaca_positions),
        "cash_synced": cash_synced,
        "alpaca_cash": alpaca_cash,
    }


# ─────────────────────────────────────────────────────────────────────
# Reconcile-all: heal residual drift left behind by older bugs
# ─────────────────────────────────────────────────────────────────────

async def _alpaca_sync_one(portfolio: Portfolio, db: AsyncSession) -> dict:
    """Run the same body as sync_positions for a single portfolio,
    but as a callable instead of an HTTP handler. Returns the same
    summary dict the /execution/sync endpoint produces.

    Reused by /execution/reconcile-all so paper/live portfolios get
    the broker-canonical reconciliation in the same sweep.
    """
    if not portfolio.alpaca_api_key or not portfolio.alpaca_secret_key:
        return {"status": "skipped", "reason": "no_alpaca_creds"}
    is_paper = (portfolio.execution_mode or "").lower() == "paper"
    alpaca_positions = await alpaca_service.get_positions(
        api_key=portfolio.alpaca_api_key_decrypted,
        secret_key=portfolio.alpaca_secret_key_decrypted,
        paper=is_paper,
    )

    holdings_result = await db.execute(
        select(PortfolioHolding).where(
            PortfolioHolding.portfolio_id == portfolio.id,
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
            db.add(PortfolioHolding(
                portfolio_id=portfolio.id,
                ticker=ticker,
                direction="long" if pos.get("side", "long") == "long" else "short",
                entry_price=entry_price,
                qty=qty,
                entry_date=utcnow(),
                is_active=True,
                notes="alpaca_sync",
            ))
            created += 1

    alpaca_tickers = {pos["symbol"] for pos in alpaca_positions}
    closed = 0
    for ticker, holding in existing_holdings.items():
        if ticker not in alpaca_tickers:
            holding.is_active = False
            holding.notes = (holding.notes or "") + " | closed_by_alpaca_sync"
            closed += 1

    cash_synced = False
    alpaca_cash = None
    try:
        account_info = await alpaca_service.get_account_info(
            api_key=portfolio.alpaca_api_key_decrypted,
            secret_key=portfolio.alpaca_secret_key_decrypted,
            paper=is_paper,
        )
        if account_info.get("status") == "connected":
            alpaca_cash = account_info.get("cash")
            if alpaca_cash is not None:
                portfolio.cash = alpaca_cash
                cash_synced = True
    except Exception as e:
        logger.warning(f"Cash sync failed for portfolio {portfolio.id}: {e}")

    return {
        "status": "synced",
        "synced": synced,
        "created": created,
        "closed": closed,
        "alpaca_positions": len(alpaca_positions),
        "cash_synced": cash_synced,
        "alpaca_cash": alpaca_cash,
    }


async def _close_orphan_holdings(portfolio: Portfolio, db: AsyncSession) -> dict:
    """Close any active PortfolioHolding whose ticker+direction matches
    a recently-closed Trade on the same portfolio. This is the residual
    drift the older autonomous-close path left behind: the Trade row
    was flipped to closed but the corresponding PortfolioHolding was
    never updated, so the Holdings list kept rendering positions Henry
    had already exited.

    Idempotent — runs against the same set every time and only acts
    when there's still drift. Limited to closes within the last 30
    days to keep the join cheap and avoid resurrecting ancient state.
    """
    from app.models import Trade, PortfolioTrade
    cutoff = utcnow() - _timedelta(days=30)

    closed_trade_rows = (await db.execute(
        select(Trade.ticker, Trade.direction)
        .join(PortfolioTrade, PortfolioTrade.trade_id == Trade.id)
        .where(
            PortfolioTrade.portfolio_id == portfolio.id,
            Trade.status == "closed",
            Trade.exit_time != None,
            Trade.exit_time >= cutoff,
        )
    )).all()
    closed_keys = {(t, d) for t, d in closed_trade_rows}

    if not closed_keys:
        return {"closed_orphan_holdings": 0}

    holdings_result = await db.execute(
        select(PortfolioHolding).where(
            PortfolioHolding.portfolio_id == portfolio.id,
            PortfolioHolding.is_active == True,
        )
    )
    closed = 0
    for h in holdings_result.scalars().all():
        if (h.ticker, h.direction) in closed_keys:
            h.is_active = False
            h.notes = (h.notes or "") + " | reconcile_all_orphan_close"
            closed += 1
    return {"closed_orphan_holdings": closed}


@router.post("/reconcile-all")
async def reconcile_all(
    db: AsyncSession = Depends(get_db),
    _auth=Depends(_verify_kill_switch_token),
):
    """One-shot sweep that brings every portfolio's holdings into
    alignment. Two passes per portfolio:

      1. Paper/live portfolios with Alpaca creds — pull broker
         positions and rewrite DB holdings + cash to match.
      2. Every portfolio (including local AI-managed) — close any
         active PortfolioHolding whose ticker+direction matches a
         Trade that's already closed on the same portfolio. This
         cleans up the residual drift from before the sync-close
         fix in autonomous_trading + ai_portfolio.

    Returns a per-portfolio diff. Both passes are idempotent so this
    is safe to re-run any time state drifts. Token-gated like the
    kill switch (Authorization: Bearer <KILL_SWITCH_TOKEN>) since it
    can move cash + close positions across every account.
    """
    result = await db.execute(select(Portfolio).where(Portfolio.is_active == True))
    portfolios = result.scalars().all()

    report = []
    for p in portfolios:
        per: dict = {
            "portfolio_id": p.id,
            "portfolio_name": p.name,
            "execution_mode": p.execution_mode,
        }
        # Pass 1: Alpaca sync (paper/live only)
        if (p.execution_mode or "").lower() in ("paper", "live") and p.alpaca_api_key:
            try:
                per["alpaca"] = await _alpaca_sync_one(p, db)
            except Exception as e:
                logger.error(f"reconcile-all alpaca sync failed for {p.id}: {e}")
                per["alpaca"] = {"status": "error", "error": str(e)[:200]}
        else:
            per["alpaca"] = {"status": "skipped", "reason": "local_or_no_creds"}

        # Pass 2: orphan-holding sweep (every portfolio).
        try:
            per["orphan_sweep"] = await _close_orphan_holdings(p, db)
        except Exception as e:
            logger.error(f"reconcile-all orphan sweep failed for {p.id}: {e}")
            per["orphan_sweep"] = {"status": "error", "error": str(e)[:200]}

        report.append(per)

    await db.commit()
    logger.warning(
        f"RECONCILE-ALL ran across {len(portfolios)} portfolios — "
        f"orphans closed: {sum((r.get('orphan_sweep', {}).get('closed_orphan_holdings') or 0) for r in report)}"
    )
    return {"portfolios_processed": len(portfolios), "report": report}


@router.get("/kill-switch")
async def kill_switch_status(db: AsyncSession = Depends(get_db), _auth=Depends(_verify_kill_switch_token)):
    """Check kill switch status — how many portfolios are in live/paper mode."""
    result = await db.execute(
        select(func.count(Portfolio.id)).where(Portfolio.execution_mode.in_(["paper", "live"]))
    )
    active_count = result.scalar() or 0
    return {"active_trading_portfolios": active_count, "status": "armed" if active_count > 0 else "safe"}


@router.post("/kill-switch")
async def kill_switch(body: dict = None, db: AsyncSession = Depends(get_db), _auth=Depends(_verify_kill_switch_token)):
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
