import csv
import io
import logging
import re
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models import PortfolioAction, BacktestImport, BacktestTrade, PortfolioHolding, HenryMemory, Trader
from app.models.portfolio import Portfolio
from app.models.trade import Trade
from app.models.portfolio_trade import PortfolioTrade
from app.schemas.portfolio_manager import (
    HoldingCreate, HoldingUpdate, HoldingResponse,
    ActionResponse, ActionReject, ActionStats,
    BacktestImportResponse, BacktestTradeResponse,
)
from app.services.price_service import price_service
from app.services.chart_service import get_daily_chart

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/portfolio-manager", tags=["portfolio-manager"])


# ── FILENAME PARSER ──────────────────────────────────────────────────

def parse_backtest_filename(filename: str) -> dict:
    """
    Parse TradingView backtest CSV filename.
    Supported patterns:
      STRATEGY_VERSION_EXCHANGE_TICKER_DATE.csv  (full)
      STRATEGY_VERSION_TICKER_DATE.csv           (no exchange)
      STRATEGY_EXCHANGE_TICKER_DATE.csv          (no version)
      STRATEGY_TICKER_DATE.csv                   (minimal)
      STRATEGY_VERSION_EXCHANGE_TICKER.csv       (no date)
    Example: HENRY_v3.8_NASDAQ_NVDA_2026-03-17.csv
    """
    name = filename.rsplit(".", 1)[0]  # strip .csv
    parts = name.split("_")

    if len(parts) < 2:
        raise ValueError(f"Cannot parse filename '{filename}'. Expected at least STRATEGY_TICKER.csv")

    # Known exchange names to distinguish exchange from ticker
    EXCHANGES = {"NASDAQ", "NYSE", "AMEX", "ARCA", "BATS", "OTC", "TSX", "LSE", "CBOE", "CME", "NYMEX", "COMEX", "CBOT"}

    # Try to find version (starts with 'v' followed by digit)
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

    # Strip date parts from the end (YYYY-MM-DD or individual YYYY, MM, DD segments)
    # Handle both "2026-03-17" and "2026_03_17" formats
    while remaining and re.match(r"^\d{4}-\d{2}-\d{2}$", remaining[-1]):
        remaining = remaining[:-1]
    # Also strip trailing numeric segments that look like date parts (MM, DD, YYYY)
    while len(remaining) > 1 and re.match(r"^\d{1,4}$", remaining[-1]):
        remaining = remaining[:-1]

    # Handle "EXCHANGE:TICKER" colon format (e.g., "NASDAQ:NVDA" or "BATS:NOK")
    expanded = []
    for part in remaining:
        if ":" in part:
            expanded.extend(part.split(":"))
        else:
            expanded.append(part)
    remaining = expanded

    # Now remaining should be [EXCHANGE?, TICKER] or [TICKER]
    # Strategy: the LAST token is always the ticker (TradingView puts ticker right before date)
    # Everything before it that looks like an exchange/broker name is the exchange
    BROKERS = {"ROBINHOOD", "ALPACA", "IBKR", "SCHWAB", "FIDELITY", "TRADIER", "WEBULL", "ETRADE", "TD"}

    exchange = None
    ticker = None

    if len(remaining) >= 2:
        # Last token is the ticker, first token is exchange/broker
        ticker = remaining[-1]
        ex_candidate = remaining[0].upper()
        if ex_candidate in EXCHANGES or ex_candidate in BROKERS:
            exchange = remaining[0]
        # If there are 3+ tokens (e.g., exchange, subexchange, ticker), still take last as ticker
    elif len(remaining) == 1:
        # Single part — it's the ticker (unless it's clearly an exchange with no ticker)
        if remaining[0].upper() in EXCHANGES and remaining[0].upper() not in {"OTC", "ARCA"}:
            raise ValueError(
                f"Filename '{filename}' appears to have an exchange ({remaining[0]}) but no ticker. "
                f"Expected: STRATEGY_VERSION_EXCHANGE_TICKER_DATE.csv"
            )
        ticker = remaining[0]
    else:
        raise ValueError(f"Cannot extract ticker from filename '{filename}'")

    return {
        "strategy_name": strategy_name,
        "strategy_version": strategy_version,
        "exchange": exchange,
        "ticker": ticker.upper(),
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
                trade_date = datetime.now(timezone.utc)

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
            position_type=getattr(h, "position_type", "momentum") or "momentum",
            thesis=getattr(h, "thesis", None),
            catalyst_date=getattr(h, "catalyst_date", None),
            catalyst_description=getattr(h, "catalyst_description", None),
            max_allocation_pct=getattr(h, "max_allocation_pct", None),
            dca_enabled=getattr(h, "dca_enabled", False) or False,
            dca_threshold_pct=getattr(h, "dca_threshold_pct", None),
            avg_cost=getattr(h, "avg_cost", None),
            total_shares=getattr(h, "total_shares", None),
            created_at=h.created_at,
        ))

    # Register tickers for price tracking
    for h in holdings:
        price_service.add_ticker(h.ticker)

    return responses


@router.post("/holdings", response_model=HoldingResponse)
async def create_holding(body: HoldingCreate, db: AsyncSession = Depends(get_db)):
    logger = logging.getLogger(__name__)
    try:
        # Strip timezone info — DB column is TIMESTAMP WITHOUT TIME ZONE
        entry_date = body.entry_date.replace(tzinfo=None) if body.entry_date.tzinfo else body.entry_date
        ticker = body.ticker.upper()

        # Check for existing active holding with same ticker+direction+portfolio
        # If found, merge: average the entry price, sum the qty
        result = await db.execute(
            select(PortfolioHolding).where(
                PortfolioHolding.portfolio_id == body.portfolio_id,
                PortfolioHolding.ticker == ticker,
                PortfolioHolding.direction == body.direction,
                PortfolioHolding.is_active == True,
            )
        )
        existing = result.scalar_one_or_none()

        if existing:
            # Merge: weighted average entry price, sum qty
            old_cost = existing.entry_price * existing.qty
            new_cost = body.entry_price * body.qty
            total_qty = existing.qty + body.qty
            avg_price = (old_cost + new_cost) / total_qty if total_qty > 0 else body.entry_price

            existing.entry_price = round(avg_price, 4)
            existing.qty = round(total_qty, 6)
            # Keep the earlier entry date
            if entry_date < existing.entry_date:
                existing.entry_date = entry_date
            # Append notes if provided
            if body.notes:
                existing.notes = f"{existing.notes or ''}\n{body.notes}".strip()

            # Deduct cash from portfolio (buy = deploy capital)
            trade_cost = body.entry_price * body.qty
            portfolio_result = await db.execute(select(Portfolio).where(Portfolio.id == body.portfolio_id))
            portfolio = portfolio_result.scalar_one_or_none()
            if portfolio:
                portfolio.cash = max(0, (portfolio.cash or 0) - trade_cost)

            # For accumulation positions, track avg_cost and total_shares
            pos_type = getattr(body, "position_type", None) or getattr(existing, "position_type", "momentum")
            if pos_type == "accumulation":
                old_avg = existing.avg_cost or existing.entry_price
                old_shares = existing.total_shares or existing.qty - body.qty  # pre-merge qty
                new_avg = (old_avg * old_shares + body.entry_price * body.qty) / total_qty if total_qty > 0 else body.entry_price
                existing.avg_cost = round(new_avg, 4)
                existing.total_shares = round(total_qty, 6)

            # Update archetype fields if provided on merge
            if body.position_type and body.position_type != "momentum":
                existing.position_type = body.position_type
            if body.thesis is not None:
                existing.thesis = body.thesis
            if body.catalyst_date is not None:
                existing.catalyst_date = body.catalyst_date
            if body.catalyst_description is not None:
                existing.catalyst_description = body.catalyst_description
            if body.max_allocation_pct is not None:
                existing.max_allocation_pct = body.max_allocation_pct
            if body.dca_enabled:
                existing.dca_enabled = body.dca_enabled
            if body.dca_threshold_pct is not None:
                existing.dca_threshold_pct = body.dca_threshold_pct

            await db.commit()
            await db.refresh(existing)

            current_price = price_service.get_price(existing.ticker)
            unrealized = None
            unrealized_pct = None
            if current_price is not None:
                if existing.direction == "long":
                    unrealized = (current_price - existing.entry_price) * existing.qty
                else:
                    unrealized = (existing.entry_price - current_price) * existing.qty
                position_value = existing.entry_price * existing.qty
                unrealized_pct = (unrealized / position_value * 100) if position_value > 0 else 0.0

            source = "manual" if existing.trade_id is None else (existing.strategy_name or "webhook")

            return HoldingResponse(
                id=existing.id,
                portfolio_id=existing.portfolio_id,
                trade_id=existing.trade_id,
                ticker=existing.ticker,
                direction=existing.direction,
                entry_price=existing.entry_price,
                qty=existing.qty,
                entry_date=existing.entry_date,
                strategy_name=existing.strategy_name,
                notes=existing.notes,
                is_active=existing.is_active,
                source=source,
                current_price=current_price,
                unrealized_pnl=round(unrealized, 2) if unrealized is not None else None,
                unrealized_pnl_pct=round(unrealized_pct, 2) if unrealized_pct is not None else None,
                position_type=getattr(existing, "position_type", "momentum") or "momentum",
                thesis=getattr(existing, "thesis", None),
                catalyst_date=getattr(existing, "catalyst_date", None),
                catalyst_description=getattr(existing, "catalyst_description", None),
                max_allocation_pct=getattr(existing, "max_allocation_pct", None),
                dca_enabled=getattr(existing, "dca_enabled", False) or False,
                dca_threshold_pct=getattr(existing, "dca_threshold_pct", None),
                avg_cost=getattr(existing, "avg_cost", None),
                total_shares=getattr(existing, "total_shares", None),
                created_at=existing.created_at,
            )

        # No existing holding — create new
        # Deduct cash from portfolio (buy = deploy capital)
        trade_cost = body.entry_price * body.qty
        portfolio_result = await db.execute(select(Portfolio).where(Portfolio.id == body.portfolio_id))
        portfolio = portfolio_result.scalar_one_or_none()
        if portfolio:
            portfolio.cash = max(0, (portfolio.cash or 0) - trade_cost)

        # For accumulation positions, initialize avg_cost and total_shares
        init_avg_cost = body.entry_price if body.position_type == "accumulation" else None
        init_total_shares = body.qty if body.position_type == "accumulation" else None

        holding = PortfolioHolding(
            portfolio_id=body.portfolio_id,
            ticker=ticker,
            direction=body.direction,
            entry_price=body.entry_price,
            qty=body.qty,
            entry_date=entry_date,
            strategy_name=body.strategy_name,
            notes=body.notes,
            is_active=True,
            position_type=body.position_type,
            thesis=body.thesis,
            catalyst_date=body.catalyst_date,
            catalyst_description=body.catalyst_description,
            max_allocation_pct=body.max_allocation_pct,
            dca_enabled=body.dca_enabled,
            dca_threshold_pct=body.dca_threshold_pct,
            avg_cost=init_avg_cost,
            total_shares=init_total_shares,
            created_at=datetime.now(timezone.utc),
        )
        db.add(holding)
        await db.commit()
        await db.refresh(holding)

        # Register ticker for price tracking
        price_service.add_ticker(holding.ticker)

        # Auto-add ticker to watchlist
        try:
            from app.models.watchlist_ticker import WatchlistTicker
            wl_result = await db.execute(
                select(WatchlistTicker).where(WatchlistTicker.ticker == holding.ticker)
            )
            wl_existing = wl_result.scalar_one_or_none()
            if wl_existing:
                if not wl_existing.is_active:
                    wl_existing.is_active = True
                    wl_existing.removed_at = None
            else:
                db.add(WatchlistTicker(ticker=holding.ticker))
            await db.commit()
        except Exception:
            pass  # Non-blocking

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
            position_type=holding.position_type or "momentum",
            thesis=holding.thesis,
            catalyst_date=holding.catalyst_date,
            catalyst_description=holding.catalyst_description,
            max_allocation_pct=holding.max_allocation_pct,
            dca_enabled=holding.dca_enabled or False,
            dca_threshold_pct=holding.dca_threshold_pct,
            avg_cost=holding.avg_cost,
            total_shares=holding.total_shares,
            created_at=holding.created_at,
        )
    except Exception as e:
        logger.error(f"Failed to create holding: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


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
        position_type=getattr(holding, "position_type", "momentum") or "momentum",
        thesis=getattr(holding, "thesis", None),
        catalyst_date=getattr(holding, "catalyst_date", None),
        catalyst_description=getattr(holding, "catalyst_description", None),
        max_allocation_pct=getattr(holding, "max_allocation_pct", None),
        dca_enabled=getattr(holding, "dca_enabled", False) or False,
        dca_threshold_pct=getattr(holding, "dca_threshold_pct", None),
        avg_cost=getattr(holding, "avg_cost", None),
        total_shares=getattr(holding, "total_shares", None),
        created_at=holding.created_at,
    )


@router.delete("/holdings/{holding_id}")
async def delete_holding(holding_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(PortfolioHolding).where(PortfolioHolding.id == holding_id))
    holding = result.scalar_one_or_none()
    if not holding:
        raise HTTPException(404, "Holding not found")

    current_price = price_service.get_price(holding.ticker) if holding.is_active else holding.entry_price
    exit_price = current_price or holding.entry_price
    sell_value = exit_price * holding.qty

    # Calculate P&L
    if holding.direction == "long":
        pnl_dollars = (exit_price - holding.entry_price) * holding.qty
    else:
        pnl_dollars = (holding.entry_price - exit_price) * holding.qty
    pnl_pct = (pnl_dollars / (holding.entry_price * holding.qty) * 100) if holding.entry_price * holding.qty > 0 else 0

    # Create a closed trade record so the sale shows in trade history
    if holding.is_active:
        from app.models import Trade, Trader, PortfolioTrade
        from datetime import datetime

        # Find a trader to attribute to (use strategy_name or first active)
        trader = None
        if holding.strategy_name:
            trader_result = await db.execute(
                select(Trader).where(Trader.trader_id == holding.strategy_name).limit(1)
            )
            trader = trader_result.scalar_one_or_none()
        if not trader:
            trader_result = await db.execute(select(Trader).where(Trader.is_active == True).limit(1))
            trader = trader_result.scalar_one_or_none()

        if trader:
            trade = Trade(
                trader_id=trader.id,
                ticker=holding.ticker,
                direction=holding.direction,
                entry_price=holding.entry_price,
                exit_price=exit_price,
                qty=holding.qty,
                entry_time=holding.entry_date,
                exit_time=datetime.now(timezone.utc),
                exit_reason="manual_sell",
                status="closed",
                pnl_dollars=round(pnl_dollars, 2),
                pnl_percent=round(pnl_pct, 2),
                is_simulated=False,
            )
            db.add(trade)
            await db.flush()

            # Link to portfolio
            pt = PortfolioTrade(portfolio_id=holding.portfolio_id, trade_id=trade.id)
            db.add(pt)

        # Return cash to portfolio
        portfolio_result = await db.execute(select(Portfolio).where(Portfolio.id == holding.portfolio_id))
        portfolio = portfolio_result.scalar_one_or_none()
        if portfolio:
            portfolio.cash = (portfolio.cash or 0) + sell_value

    await db.delete(holding)
    await db.commit()
    return {
        "status": "sold",
        "ticker": holding.ticker,
        "qty": holding.qty,
        "exit_price": round(exit_price, 2),
        "pnl_dollars": round(pnl_dollars, 2),
        "pnl_pct": round(pnl_pct, 2),
    }


@router.get("/holdings/by-ticker/{ticker}")
async def get_holdings_by_ticker(ticker: str, db: AsyncSession = Depends(get_db)):
    """Get all holdings for a specific ticker across all portfolios. Used by stock profile page."""
    result = await db.execute(
        select(PortfolioHolding).where(
            PortfolioHolding.ticker == ticker.upper(),
            PortfolioHolding.is_active == True,
        )
    )
    holdings = result.scalars().all()

    return [
        {
            "id": h.id,
            "portfolio_id": h.portfolio_id,
            "ticker": h.ticker,
            "direction": h.direction,
            "entry_price": h.entry_price,
            "qty": h.qty,
            "entry_date": h.entry_date.isoformat() if h.entry_date else None,
            "strategy_name": h.strategy_name,
            "position_type": getattr(h, "position_type", "momentum") or "momentum",
            "thesis": getattr(h, "thesis", None),
            "catalyst_date": str(getattr(h, "catalyst_date", None)) if getattr(h, "catalyst_date", None) else None,
            "catalyst_description": getattr(h, "catalyst_description", None),
            "max_allocation_pct": getattr(h, "max_allocation_pct", None),
            "dca_enabled": getattr(h, "dca_enabled", False) or False,
            "dca_threshold_pct": getattr(h, "dca_threshold_pct", None),
            "avg_cost": getattr(h, "avg_cost", None),
            "total_shares": getattr(h, "total_shares", None),
        }
        for h in holdings
    ]


@router.put("/holdings/by-ticker/{ticker}/thesis")
async def update_ticker_thesis(ticker: str, body: dict, db: AsyncSession = Depends(get_db)):
    """Update thesis and position type for all holdings of a specific ticker."""
    result = await db.execute(
        select(PortfolioHolding).where(
            PortfolioHolding.ticker == ticker.upper(),
            PortfolioHolding.is_active == True,
        )
    )
    holdings = result.scalars().all()
    if not holdings:
        raise HTTPException(404, f"No active holdings found for {ticker.upper()}")

    updated = 0
    for h in holdings:
        if "thesis" in body:
            h.thesis = body["thesis"]
        if "position_type" in body:
            h.position_type = body["position_type"]
        if "catalyst_date" in body:
            from datetime import date as date_type
            h.catalyst_date = date_type.fromisoformat(body["catalyst_date"]) if body["catalyst_date"] else None
        if "catalyst_description" in body:
            h.catalyst_description = body["catalyst_description"]
        if "max_allocation_pct" in body:
            h.max_allocation_pct = body["max_allocation_pct"]
        if "dca_enabled" in body:
            h.dca_enabled = body["dca_enabled"]
        if "dca_threshold_pct" in body:
            h.dca_threshold_pct = body["dca_threshold_pct"]
        updated += 1

    await db.commit()
    return {"status": "updated", "holdings_updated": updated}


# ══════════════════════════════════════════════════════════════════════
# PORTFOLIO VALUE HISTORY
# ══════════════════════════════════════════════════════════════════════

@router.get("/portfolio-history")
async def get_portfolio_history(
    portfolio_id: str,
    days: int = 90,
    db: AsyncSession = Depends(get_db),
):
    """
    Build daily portfolio value history from yfinance close prices.
    Returns [{date, value, cost_basis}] for each trading day in the range.
    """
    # Fetch active holdings for the portfolio
    result = await db.execute(
        select(PortfolioHolding).where(
            PortfolioHolding.portfolio_id == portfolio_id,
            PortfolioHolding.is_active == True,
        )
    )
    holdings = result.scalars().all()

    if not holdings:
        return []

    # Fetch daily price data for each unique ticker
    tickers = list({h.ticker for h in holdings})
    price_data: dict[str, dict[str, float]] = {}  # ticker -> {date_str: close}

    for ticker in tickers:
        chart = await get_daily_chart(ticker, days)
        price_data[ticker] = {point["date"]: point["close"] for point in chart}

    # Collect all unique dates across all tickers, sorted
    all_dates: set[str] = set()
    for date_map in price_data.values():
        all_dates.update(date_map.keys())
    sorted_dates = sorted(all_dates)

    # Build daily value and cost basis
    history = []
    for date_str in sorted_dates:
        date_dt = datetime.strptime(date_str, "%Y-%m-%d")
        daily_value = 0.0
        daily_cost = 0.0

        for h in holdings:
            # Only count this holding if entry_date is on or before this date
            entry_date_str = h.entry_date.strftime("%Y-%m-%d")
            if entry_date_str > date_str:
                continue

            close = price_data.get(h.ticker, {}).get(date_str)
            if close is None:
                continue

            if h.direction == "long":
                daily_value += h.qty * close
            else:
                # Short: value = entry_cost + (entry_price - close) * qty
                daily_value += h.qty * h.entry_price + (h.entry_price - close) * h.qty

            daily_cost += h.entry_price * h.qty

        # Skip days where we have no data at all
        if daily_cost == 0.0 and daily_value == 0.0:
            continue

        history.append({
            "date": date_str,
            "value": round(daily_value, 2),
            "cost_basis": round(daily_cost, 2),
        })

    return history


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
    now = datetime.now(timezone.utc)
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
    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)

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
    action.resolved_at = datetime.now(timezone.utc)
    await db.commit()

    # Save user decision context (non-blocking)
    import asyncio
    from app.services.ai_service import save_context
    asyncio.create_task(save_context(
        content=f"User APPROVED {action.action_type} {action.ticker} (conf {action.confidence})",
        context_type="user_decision",
        ticker=action.ticker,
        portfolio_id=action.portfolio_id,
        action_id=action.id,
        expires_days=30,
    ))

    # Execute via Alpaca if portfolio has execution_mode != "local"
    order_result = None
    from app.models import Portfolio
    p_result = await db.execute(select(Portfolio).where(Portfolio.id == action.portfolio_id))
    p = p_result.scalar_one_or_none()
    if p and p.execution_mode in ("paper", "live"):
        from app.services.alpaca_service import alpaca_service
        is_paper = p.execution_mode == "paper"
        side = "buy" if action.action_type in ("BUY", "ADD", "DCA") else "sell"
        if p.alpaca_api_key and p.alpaca_secret_key and action.suggested_qty:
            order_result = await alpaca_service.submit_order(
                api_key=p.alpaca_api_key,
                secret_key=p.alpaca_secret_key,
                paper=is_paper,
                ticker=action.ticker,
                qty=action.suggested_qty,
                side=side,
            )
            logger.info(
                f"ACTION APPROVE ORDER | portfolio={p.id} mode={p.execution_mode} "
                f"action={action.action_type} ticker={action.ticker} qty={action.suggested_qty} "
                f"order_result={order_result.get('status', 'unknown')}"
            )

    return {
        "status": "approved",
        "action_id": action_id,
        "action_type": action.action_type,
        "ticker": action.ticker,
        "order_result": order_result,
    }


@router.post("/actions/{action_id}/reject")
async def reject_action(action_id: str, body: ActionReject | None = None, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(PortfolioAction).where(PortfolioAction.id == action_id))
    action = result.scalar_one_or_none()
    if not action:
        raise HTTPException(404, "Action not found")
    if action.status != "pending":
        raise HTTPException(400, f"Action is already {action.status}")

    action.status = "rejected"
    action.resolved_at = datetime.now(timezone.utc)
    reason_text = ""
    if body and body.reason:
        action.reject_reason = body.reason
        reason_text = f": {body.reason}"
    await db.commit()

    # Save user decision context (non-blocking)
    import asyncio
    from app.services.ai_service import save_context
    asyncio.create_task(save_context(
        content=f"User REJECTED {action.action_type} {action.ticker} (conf {action.confidence}){reason_text}",
        context_type="user_decision",
        ticker=action.ticker,
        portfolio_id=action.portfolio_id,
        action_id=action.id,
        expires_days=30,
    ))

    return {"status": "rejected", "action_id": action_id}


# ══════════════════════════════════════════════════════════════════════
# BROKERAGE TRADE CSV IMPORT
# ══════════════════════════════════════════════════════════════════════

BROKERAGE_PATTERNS = {
    "robinhood": {
        "headers": ["activity date", "instrument", "trans code", "quantity", "price", "amount"],
        "mapping": {
            "date": "activity date",
            "ticker": "instrument",
            "action": "trans code",
            "qty": "quantity",
            "price": "price",
            "amount": "amount",
            "description": "description",
        },
        "action_map": {"buy": "buy", "sell": "sell"},
        # Note: BTO/STC/OEXP are options — handled by skip logic.
        # CDIV handled as dividend. SLIP/ACH/INT/GOLD/FUTSWP/ITRF/DCF/DTAX all skipped.
    },
    "schwab": {
        "headers": ["date", "action", "symbol", "quantity", "price", "amount"],
        "mapping": {
            "date": "date",
            "ticker": "symbol",
            "action": "action",
            "qty": "quantity",
            "price": "price",
            "amount": "amount",
        },
        "action_map": {"buy": "buy", "sell": "sell", "buy to open": "buy", "sell to close": "sell"},
    },
    "fidelity": {
        "headers": ["run date", "action", "symbol", "quantity", "price ($)", "amount ($)"],
        "mapping": {
            "date": "run date",
            "ticker": "symbol",
            "action": "action",
            "qty": "quantity",
            "price": "price ($)",
            "amount": "amount ($)",
        },
        "action_map": {"you bought": "buy", "you sold": "sell", "bought": "buy", "sold": "sell"},
    },
    "webull": {
        "headers": ["symbol", "side", "qty", "price", "total"],
        "mapping": {
            "date": "create time",
            "ticker": "symbol",
            "action": "side",
            "qty": "qty",
            "price": "price",
            "amount": "total",
        },
        "action_map": {"buy": "buy", "sell": "sell"},
    },
    "etrade": {
        "headers": ["transaction date", "transaction type", "symbol", "quantity", "price", "amount"],
        "mapping": {
            "date": "transaction date",
            "ticker": "symbol",
            "action": "transaction type",
            "qty": "quantity",
            "price": "price",
            "amount": "amount",
        },
        "action_map": {"bought": "buy", "sold": "sell", "buy": "buy", "sell": "sell"},
    },
}

# Trans codes / action keywords indicating non-equity transactions to skip
SKIP_TRANS_CODES = {"cdiv", "slip", "int", "gold", "ach", "futswp", "itrf", "dcf", "dtax",
                    "oexp", "bto", "stc", "btc", "sto", "gmpc"}
SKIP_KEYWORDS = {"dividend", "interest", "fee", "journal", "transfer", "acat", "reinvest",
                 "margin", "adjustment", "wire", "deposit", "withdrawal",
                 "expired", "assigned", "exercised", "stock lending",
                 "gold plan", "gold subscription", "event contracts",
                 "debit card transfer"}


def _clean_numeric(val: str | None) -> float | None:
    """Strip $, commas, whitespace from numeric fields and parse."""
    if not val or not val.strip():
        return None
    cleaned = val.strip().replace("$", "").replace(",", "").replace("(", "-").replace(")", "")
    try:
        return float(cleaned)
    except ValueError:
        return None


def _parse_date_flexible(val: str | None) -> str | None:
    """Parse various date formats into YYYY-MM-DD string."""
    if not val or not val.strip():
        return None
    s = val.strip()
    # Try multiple formats
    formats = [
        "%Y-%m-%d",
        "%m/%d/%Y",
        "%m-%d-%Y",
        "%m/%d/%y",
        "%m-%d-%y",
        "%Y/%m/%d",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
        "%m/%d/%Y %H:%M:%S",
        "%m/%d/%Y %I:%M:%S %p",
        "%b %d, %Y",
        "%B %d, %Y",
        "%d-%b-%Y",
        "%Y-%m-%dT%H:%M:%S.%f",
    ]
    for fmt in formats:
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    # Last resort: try dateutil-style parsing
    # Just grab first 10 chars if it looks like YYYY-MM-DD
    if len(s) >= 10 and s[4] in "-/" and s[7] in "-/":
        try:
            return datetime.strptime(s[:10], "%Y-%m-%d").strftime("%Y-%m-%d")
        except ValueError:
            pass
    return None


def _detect_brokerage(headers: list[str]) -> str | None:
    """Detect brokerage from CSV column headers."""
    lower_headers = set(h.lower().strip() for h in headers)
    for name, pattern in BROKERAGE_PATTERNS.items():
        required = set(h.lower() for h in pattern["headers"])
        if required.issubset(lower_headers):
            return name
    return None


def _should_skip_row(row: dict, action_col: str) -> bool:
    """Check if a row is a non-equity transaction that should be skipped."""
    action_val = (row.get(action_col) or "").lower().strip()
    # Check action column for skip keywords
    for kw in SKIP_KEYWORDS:
        if kw in action_val:
            return True
    # Also check other columns for option-like patterns (e.g. ticker containing spaces or option descriptors)
    return False


def _resolve_action(action_val: str, action_map: dict) -> str | None:
    """Resolve an action string to 'buy' or 'sell' using the brokerage action map."""
    lower = action_val.lower().strip()
    # Direct match
    if lower in action_map:
        return action_map[lower]
    # Partial match (e.g. "Sell to Close" contains "sell")
    for key, resolved in action_map.items():
        if key in lower:
            return resolved
    return None


def _preprocess_robinhood_csv(content: str) -> str:
    """
    Robinhood CSVs have multi-line fields (CUSIP info wraps to next line).
    Python's csv module handles this if the fields are properly quoted,
    but Robinhood's quotes can be inconsistent. Pre-process to collapse
    multi-line values and strip the disclaimer footer.
    """
    lines = content.split("\n")
    cleaned = []
    for line in lines:
        # Skip disclaimer footer
        stripped = line.strip().strip('"')
        if stripped.startswith("The data provided is for informational"):
            break
        if not stripped:
            # Skip empty lines but don't break — Robinhood has blank lines
            if cleaned and not cleaned[-1].endswith('"'):
                # This blank line might be inside a quoted field, skip it
                continue
            cleaned.append(line)
            continue
        # Check if this line is a continuation (doesn't start with a date pattern or quote-date)
        # Robinhood data rows start with "M/D/YYYY" pattern
        if cleaned and not re.match(r'^"?\d{1,2}/\d{1,2}/\d{4}', stripped):
            # This is a continuation of the previous row (e.g., CUSIP line)
            # Append to previous line with a space
            if cleaned:
                cleaned[-1] = cleaned[-1].rstrip() + " " + stripped
                continue
        cleaned.append(line)
    return "\n".join(cleaned)


def _parse_trades_from_csv(content: str, mapping: dict, action_map: dict) -> list[dict]:
    """Parse CSV content into normalized trade dicts using the given column mapping and action map."""
    # Pre-process Robinhood-style multi-line CSVs
    content = _preprocess_robinhood_csv(content)

    reader = csv.DictReader(io.StringIO(content))
    trades = []

    # Build a case-insensitive lookup for the actual CSV column names
    fieldnames = reader.fieldnames or []
    col_lookup = {f.lower().strip(): f for f in fieldnames}

    def get_val(row: dict, mapped_name: str) -> str | None:
        if not mapped_name:
            return None
        if mapped_name in row:
            return row[mapped_name]
        actual = col_lookup.get(mapped_name.lower().strip())
        if actual and actual in row:
            return row[actual]
        return None

    for row in reader:
        # Get action/trans code value
        action_raw = get_val(row, mapping["action"]) or ""
        action_lower = action_raw.lower().strip()

        # Skip by trans code (Robinhood uses specific codes)
        if action_lower in SKIP_TRANS_CODES:
            continue

        # Also check description for skip keywords
        description = get_val(row, mapping.get("description", "")) or ""
        desc_lower = description.lower()
        skip = False
        for kw in SKIP_KEYWORDS:
            if kw in action_lower or kw in desc_lower:
                skip = True
                break
        if skip:
            continue

        # Check if this is an options trade by looking at description
        # Options have patterns like "AAPL 1/24/2025 Call $230.00" or "Option Expiration"
        if re.search(r'\b(call|put)\b.*\$[\d.]+', desc_lower) or "option" in desc_lower:
            continue

        # Resolve action
        action = _resolve_action(action_raw, action_map)
        if not action:
            continue

        # Get ticker
        ticker_raw = get_val(row, mapping["ticker"])
        if not ticker_raw or not ticker_raw.strip():
            continue
        ticker = ticker_raw.strip().upper()
        # Skip if ticker looks like an option or is too long
        if " " in ticker or len(ticker) > 6:
            continue
        # Skip empty ticker rows (non-instrument rows like ACH deposits)
        if not ticker or ticker == "":
            continue

        # Parse numeric fields
        qty_val = _clean_numeric(get_val(row, mapping["qty"]))
        price_val = _clean_numeric(get_val(row, mapping["price"]))
        amount_val = _clean_numeric(get_val(row, mapping.get("amount", "")))

        if qty_val is None or price_val is None:
            continue
        if qty_val == 0 or price_val == 0:
            continue

        # Handle negative quantities
        qty_val = abs(qty_val)
        price_val = abs(price_val)
        if amount_val is not None:
            amount_val = abs(amount_val)
        else:
            amount_val = round(qty_val * price_val, 2)

        # Parse date
        date_str = _parse_date_flexible(get_val(row, mapping["date"]))
        if not date_str:
            date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        trades.append({
            "date": date_str,
            "ticker": ticker,
            "action": action,
            "qty": round(qty_val, 6),
            "price": round(price_val, 4),
            "amount": round(amount_val, 2),
        })

    return trades


def _build_summary(trades: list[dict]) -> dict:
    """Build a summary dict from a list of parsed trade dicts."""
    buys = sum(1 for t in trades if t["action"] == "buy")
    sells = sum(1 for t in trades if t["action"] == "sell")
    tickers = sorted(set(t["ticker"] for t in trades))
    dates = [t["date"] for t in trades if t["date"]]
    date_range = ""
    if dates:
        date_range = f"{min(dates)} to {max(dates)}"
    return {
        "total_trades": len(trades),
        "buys": buys,
        "sells": sells,
        "tickers": tickers,
        "date_range": date_range,
    }


@router.post("/import-trades/preview")
async def preview_import_trades(file: UploadFile = File(...)):
    """
    Upload a brokerage CSV and get a parsed preview.
    Auto-detects brokerage format; returns needs_mapping if unknown.
    """
    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise HTTPException(400, "File must be a CSV")

    raw = await file.read()
    content = raw.decode("utf-8-sig")  # handle BOM

    reader = csv.DictReader(io.StringIO(content))
    headers = reader.fieldnames or []

    if not headers:
        raise HTTPException(400, "CSV has no column headers")

    brokerage = _detect_brokerage(headers)

    if not brokerage:
        # Return needs_mapping response with sample rows
        sample_reader = csv.DictReader(io.StringIO(content))
        sample_rows = []
        for i, row in enumerate(sample_reader):
            if i >= 3:
                break
            sample_rows.append(dict(row))
        return {
            "status": "needs_mapping",
            "headers": headers,
            "sample_rows": sample_rows,
        }

    pattern = BROKERAGE_PATTERNS[brokerage]
    trades = _parse_trades_from_csv(content, pattern["mapping"], pattern["action_map"])

    if not trades:
        raise HTTPException(400, "No valid equity trades found in the CSV")

    return {
        "status": "ready",
        "brokerage": brokerage,
        "trades": trades,
        "summary": _build_summary(trades),
    }


@router.post("/import-trades/parse-with-mapping")
async def parse_with_mapping(
    file: UploadFile = File(...),
    mapping: str = Form(""),
):
    """
    Parse a CSV with user-provided column mapping.
    mapping is a JSON string: {"date": "col", "ticker": "col", "action": "col", "qty": "col", "price": "col"}
    """
    import json as json_mod

    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise HTTPException(400, "File must be a CSV")

    if not mapping:
        raise HTTPException(400, "Column mapping is required")

    try:
        col_mapping = json_mod.loads(mapping)
    except json_mod.JSONDecodeError:
        raise HTTPException(400, "Invalid JSON in mapping parameter")

    required_keys = {"date", "ticker", "action", "qty", "price"}
    if not required_keys.issubset(col_mapping.keys()):
        raise HTTPException(400, f"Mapping must include: {required_keys}")

    raw = await file.read()
    content = raw.decode("utf-8-sig")

    # Generic action map for user-provided mappings
    generic_action_map = {
        "buy": "buy", "sell": "sell",
        "bought": "buy", "sold": "sell",
        "buy to open": "buy", "sell to close": "sell",
        "buy to close": "buy", "sell to open": "sell",
        "bto": "buy", "stc": "sell", "btc": "buy", "sto": "sell",
        "you bought": "buy", "you sold": "sell",
        "long": "buy", "short": "sell",
    }

    trades = _parse_trades_from_csv(content, col_mapping, generic_action_map)

    if not trades:
        raise HTTPException(400, "No valid equity trades found with the provided mapping")

    return {
        "status": "ready",
        "brokerage": "custom",
        "trades": trades,
        "summary": _build_summary(trades),
    }


@router.post("/import-trades/confirm")
async def confirm_import_trades(
    body: dict,
    db: AsyncSession = Depends(get_db),
):
    """
    Confirm and execute a trade import into a portfolio.
    Body: { portfolio_id, trades: [{date, ticker, action, qty, price, amount}], clear_existing: bool }

    Process: First deletes all existing imported holdings (strategy_name="import")
    for this portfolio, then replays all trades chronologically from scratch.
    This ensures correct net positions even across multiple CSV uploads.
    """
    portfolio_id = body.get("portfolio_id")
    trades = body.get("trades", [])
    clear_existing = body.get("clear_existing", True)

    if not portfolio_id:
        raise HTTPException(400, "portfolio_id is required")
    if not trades:
        raise HTTPException(400, "No trades to import")

    # Clear existing imported holdings first for a clean replay
    if clear_existing:
        existing_imports = await db.execute(
            select(PortfolioHolding).where(
                PortfolioHolding.portfolio_id == portfolio_id,
                PortfolioHolding.strategy_name == "import",
            )
        )
        for h in existing_imports.scalars().all():
            await db.delete(h)
        await db.flush()

    # Get or create the synthetic "import" trader for brokerage imports
    import uuid as uuid_mod
    from app.utils.auth import generate_api_key, hash_api_key

    result = await db.execute(
        select(Trader).where(Trader.trader_id == "brokerage-import")
    )
    import_trader = result.scalar_one_or_none()
    if not import_trader:
        raw_key = generate_api_key()
        import_trader = Trader(
            id=str(uuid_mod.uuid4()),
            trader_id="brokerage-import",
            display_name="Brokerage Import",
            strategy_name="Imported Trades",
            description="Trades imported from brokerage CSV exports",
            api_key_hash=hash_api_key(raw_key),
            is_active=True,
        )
        db.add(import_trader)
        await db.flush()

    # Also clear old imported trades for this portfolio
    if clear_existing:
        old_pt_result = await db.execute(
            select(PortfolioTrade).where(PortfolioTrade.portfolio_id == portfolio_id)
        )
        old_pts = old_pt_result.scalars().all()
        old_trade_ids = [pt.trade_id for pt in old_pts]
        if old_trade_ids:
            # Only delete trades from the import trader
            old_trades_result = await db.execute(
                select(Trade).where(
                    Trade.id.in_(old_trade_ids),
                    Trade.trader_id == import_trader.id,
                )
            )
            for t in old_trades_result.scalars().all():
                # Delete portfolio_trade links first
                await db.execute(
                    select(PortfolioTrade).where(PortfolioTrade.trade_id == t.id)
                )
                for pt in (await db.execute(select(PortfolioTrade).where(PortfolioTrade.trade_id == t.id))).scalars().all():
                    await db.delete(pt)
                await db.delete(t)
            await db.flush()

    # Sort trades chronologically (oldest first)
    trades.sort(key=lambda t: t.get("date", ""))

    # Process all trades: compute net positions AND create trade records
    positions: dict[str, dict] = {}  # ticker -> {qty, total_cost, first_date, buys: [...]}
    holdings_created = 0
    holdings_closed = 0
    trades_created = 0

    for trade in trades:
        ticker = trade.get("ticker", "").upper()
        action = trade.get("action", "")
        qty = float(trade.get("qty", 0))
        price = float(trade.get("price", 0))
        date_str = trade.get("date", "")

        if not ticker or not action or qty <= 0 or price <= 0:
            continue

        try:
            entry_date = datetime.strptime(date_str, "%Y-%m-%d")
        except (ValueError, TypeError):
            entry_date = datetime.now(timezone.utc)

        if action == "buy":
            if ticker not in positions:
                positions[ticker] = {"qty": 0.0, "total_cost": 0.0, "first_date": entry_date, "buys": []}
            pos = positions[ticker]
            pos["total_cost"] += price * qty
            pos["qty"] += qty
            if entry_date < pos["first_date"]:
                pos["first_date"] = entry_date
            pos["buys"].append({"price": price, "qty": qty, "date": entry_date})

        elif action == "sell":
            if ticker in positions and positions[ticker]["qty"] > 0:
                pos = positions[ticker]
                sell_qty = min(qty, pos["qty"])
                avg_entry = pos["total_cost"] / pos["qty"] if pos["qty"] > 0 else price

                # Create a closed trade record (entry + exit paired)
                pnl_dollars = (price - avg_entry) * sell_qty
                pnl_pct = ((price - avg_entry) / avg_entry * 100) if avg_entry > 0 else 0

                trade_record = Trade(
                    id=str(uuid_mod.uuid4()),
                    trader_id=import_trader.id,
                    ticker=ticker,
                    direction="long",
                    entry_price=round(avg_entry, 4),
                    qty=round(sell_qty, 6),
                    entry_time=pos["first_date"],
                    exit_price=round(price, 4),
                    exit_time=entry_date,
                    exit_reason="brokerage_sell",
                    pnl_dollars=round(pnl_dollars, 2),
                    pnl_percent=round(pnl_pct, 2),
                    status="closed",
                    is_simulated=False,
                    created_at=datetime.now(timezone.utc),
                )
                db.add(trade_record)
                db.add(PortfolioTrade(
                    id=str(uuid_mod.uuid4()),
                    portfolio_id=portfolio_id,
                    trade_id=trade_record.id,
                ))
                trades_created += 1

                # Update position
                cost_per_share = pos["total_cost"] / pos["qty"]
                pos["total_cost"] -= cost_per_share * sell_qty
                pos["qty"] -= sell_qty
                if pos["qty"] <= 0.0001:
                    pos["qty"] = 0
                    pos["total_cost"] = 0
                    holdings_closed += 1

    # Write final positions as holdings (only those with qty > 0)
    for ticker, pos in positions.items():
        if pos["qty"] > 0.0001:
            avg_price = pos["total_cost"] / pos["qty"] if pos["qty"] > 0 else 0
            holding = PortfolioHolding(
                portfolio_id=portfolio_id,
                ticker=ticker,
                direction="long",
                entry_price=round(avg_price, 4),
                qty=round(pos["qty"], 6),
                entry_date=pos["first_date"],
                strategy_name="import",
                notes=f"Imported from CSV",
                is_active=True,
                created_at=datetime.now(timezone.utc),
            )
            db.add(holding)
            holdings_created += 1
            price_service.add_ticker(ticker)

    await db.commit()

    return {
        "imported": trades_created,
        "holdings_created": holdings_created,
        "holdings_updated": 0,
        "holdings_closed": holdings_closed,
        "trades_created": trades_created,
    }


# ── Debug / Seed ─────────────────────────────────────────────────────

@router.post("/actions/seed")
async def seed_actions(portfolio_id: str, db: AsyncSession = Depends(get_db)):
    """Seed sample actions for testing the action queue UI."""
    import uuid
    samples = [
        {
            "ticker": "NVDA", "direction": "long", "action_type": "BUY",
            "suggested_qty": 2.0, "suggested_price": 120.50, "current_price": 119.80,
            "confidence": 9,
            "reasoning": "S1 fired a long entry on NVDA at $120.50. Backtest data shows S1 has a 100% win rate on NVDA across 9 trades with avg gain of 4.07%. Portfolio currently has 0.292 shares — room to add. Max adverse excursion averaged -4.4% so set stops accordingly.",
            "trigger_type": "SIGNAL", "priority_score": 18.0,
        },
        {
            "ticker": "TSLA", "direction": "long", "action_type": "TRIM",
            "suggested_qty": 1.5, "suggested_price": 285.00, "current_price": 288.40,
            "confidence": 7,
            "reasoning": "TSLA position is up 17.3% from entry and approaching the 95th percentile of S1 historical gains on this ticker. Average winning trade for S1 on TSLA closes at +12.8%. Consider trimming 40% to lock in profits while keeping exposure.",
            "trigger_type": "SCHEDULED", "priority_score": 7.0,
        },
        {
            "ticker": "PLTR", "direction": "long", "action_type": "CLOSE",
            "suggested_qty": 8.0, "suggested_price": 72.10, "current_price": 71.85,
            "confidence": 6,
            "reasoning": "PLTR has been in this position for 64 days — well beyond S2's average hold time of 28 days on this ticker. The trend has flattened and ADX is declining. S2 historically loses money on holds exceeding 45 days for PLTR.",
            "trigger_type": "SCHEDULED", "priority_score": 6.0,
        },
        {
            "ticker": "AMD", "direction": "long", "action_type": "ADD",
            "suggested_qty": 3.0, "suggested_price": 158.30, "current_price": 157.90,
            "confidence": 8,
            "reasoning": "S3 impulse breakout just confirmed on AMD 4h chart. You already hold 6.2 shares from S3 at $162.40. This pullback entry at $158.30 lowers your avg cost. S3 has 68% win rate on AMD with profit factor 1.85.",
            "trigger_type": "SIGNAL", "priority_score": 16.0,
        },
        {
            "ticker": "AAPL", "direction": "long", "action_type": "REBALANCE",
            "suggested_qty": None, "suggested_price": None, "current_price": 225.40,
            "confidence": 5,
            "reasoning": "AAPL currently represents 28.3% of portfolio value — above the 25% concentration threshold. Consider trimming to bring allocation below 25%. All other metrics are healthy.",
            "trigger_type": "THRESHOLD", "priority_score": 15.0,
        },
    ]

    created = []
    for s in samples:
        action = PortfolioAction(
            id=str(uuid.uuid4()),
            portfolio_id=portfolio_id,
            ticker=s["ticker"],
            direction=s["direction"],
            action_type=s["action_type"],
            suggested_qty=s["suggested_qty"],
            suggested_price=s["suggested_price"],
            current_price=s["current_price"],
            confidence=s["confidence"],
            reasoning=s["reasoning"],
            trigger_type=s["trigger_type"],
            trigger_ref=None,
            priority_score=s["priority_score"],
            status="pending",
            expires_at=datetime.now(timezone.utc) + timedelta(hours=4 if s["trigger_type"] == "SIGNAL" else 24),
            created_at=datetime.now(timezone.utc),
        )
        db.add(action)
        created.append(s["ticker"])

    await db.commit()
    return {"status": "seeded", "actions": created}


# ══════════════════════════════════════════════════════════════════════
# HENRY MEMORY ENDPOINTS
# ══════════════════════════════════════════════════════════════════════

@router.get("/memory")
async def list_memories(
    memory_type: str | None = None,
    strategy_id: str | None = None,
    ticker: str | None = None,
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
):
    """List Henry's memory entries, optionally filtered."""
    query = select(HenryMemory).order_by(HenryMemory.importance.desc(), HenryMemory.updated_at.desc())

    if memory_type:
        query = query.where(HenryMemory.memory_type == memory_type)
    if strategy_id:
        query = query.where(HenryMemory.strategy_id == strategy_id)
    if ticker:
        query = query.where(HenryMemory.ticker == ticker.upper())

    query = query.limit(limit)
    result = await db.execute(query)
    memories = result.scalars().all()

    return [
        {
            "id": m.id,
            "memory_type": m.memory_type,
            "strategy_id": m.strategy_id,
            "ticker": m.ticker,
            "content": m.content,
            "importance": m.importance,
            "reference_count": m.reference_count,
            "validated": m.validated,
            "source": m.source,
            "created_at": m.created_at.isoformat() if m.created_at else None,
            "updated_at": m.updated_at.isoformat() if m.updated_at else None,
        }
        for m in memories
    ]


@router.post("/memory")
async def add_memory(
    content: str,
    memory_type: str = "preference",
    strategy_id: str | None = None,
    ticker: str | None = None,
    importance: int = 7,
    db: AsyncSession = Depends(get_db),
):
    """Manually add a memory entry (e.g., user preferences, manual observations)."""
    memory = HenryMemory(
        memory_type=memory_type,
        strategy_id=strategy_id,
        ticker=ticker.upper() if ticker else None,
        content=content,
        importance=importance,
        source="user",
    )
    db.add(memory)
    await db.commit()
    await db.refresh(memory)
    return {"status": "saved", "id": memory.id}


@router.delete("/memory/{memory_id}")
async def delete_memory(memory_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(HenryMemory).where(HenryMemory.id == memory_id))
    memory = result.scalar_one_or_none()
    if not memory:
        raise HTTPException(404, "Memory not found")
    await db.delete(memory)
    await db.commit()
    return {"status": "deleted"}


# ══════════════════════════════════════════════════════════════════════
# STRATEGY DESCRIPTION ENDPOINTS
# ══════════════════════════════════════════════════════════════════════

@router.get("/strategies")
async def list_strategies_with_descriptions(db: AsyncSession = Depends(get_db)):
    """List all strategies with their descriptions for Henry's reference."""
    result = await db.execute(
        select(Trader).where(Trader.is_active == True).order_by(Trader.created_at)
    )
    traders = result.scalars().all()

    return [
        {
            "id": t.id,
            "trader_id": t.trader_id,
            "display_name": t.display_name,
            "strategy_name": t.strategy_name,
            "description": t.description,
            "strategy_description": t.strategy_description,
        }
        for t in traders
    ]


# ══════════════════════════════════════════════════════════════════════
# HENRY CONTEXT ENDPOINTS
# ══════════════════════════════════════════════════════════════════════

@router.get("/context")
async def list_context(
    ticker: str | None = None,
    strategy: str | None = None,
    context_type: str | None = None,
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
):
    """List Henry's context entries, optionally filtered."""
    from app.models import HenryContext

    query = select(HenryContext).order_by(HenryContext.created_at.desc())

    if ticker:
        query = query.where(HenryContext.ticker == ticker.upper())
    if strategy:
        query = query.where(HenryContext.strategy == strategy)
    if context_type:
        query = query.where(HenryContext.context_type == context_type)

    query = query.limit(limit)
    result = await db.execute(query)
    contexts = result.scalars().all()

    return [
        {
            "id": c.id,
            "context_type": c.context_type,
            "ticker": c.ticker,
            "strategy": c.strategy,
            "portfolio_id": c.portfolio_id,
            "content": c.content,
            "confidence": c.confidence,
            "action_id": c.action_id,
            "trade_id": c.trade_id,
            "created_at": c.created_at.isoformat() if c.created_at else None,
            "expires_at": c.expires_at.isoformat() if c.expires_at else None,
        }
        for c in contexts
    ]


@router.get("/stats")
async def list_stats(
    stat_type: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    """List latest Henry stats entries, optionally filtered by stat_type."""
    from app.models import HenryStats

    query = select(HenryStats).order_by(HenryStats.computed_at.desc())

    if stat_type:
        query = query.where(HenryStats.stat_type == stat_type)

    query = query.limit(50)
    result = await db.execute(query)
    stats = result.scalars().all()

    return [
        {
            "id": s.id,
            "stat_type": s.stat_type,
            "strategy": s.strategy,
            "ticker": s.ticker,
            "portfolio_id": s.portfolio_id,
            "data": s.data,
            "period_days": s.period_days,
            "computed_at": s.computed_at.isoformat() if s.computed_at else None,
        }
        for s in stats
    ]


@router.delete("/context/{context_id}")
async def delete_context(context_id: str, db: AsyncSession = Depends(get_db)):
    """Delete a specific context entry."""
    from app.models import HenryContext

    result = await db.execute(select(HenryContext).where(HenryContext.id == context_id))
    ctx = result.scalar_one_or_none()
    if not ctx:
        raise HTTPException(404, "Context entry not found")
    await db.delete(ctx)
    await db.commit()
    return {"status": "deleted"}


@router.put("/strategies/{trader_id}/description")
async def update_strategy_description(
    trader_id: str,
    description: str,
    db: AsyncSession = Depends(get_db),
):
    """Update a strategy's rich description that Henry uses for analysis."""
    result = await db.execute(
        select(Trader).where(Trader.trader_id == trader_id)
    )
    trader = result.scalar_one_or_none()
    if not trader:
        raise HTTPException(404, f"Strategy '{trader_id}' not found")

    trader.strategy_description = description
    await db.commit()

    return {"status": "updated", "trader_id": trader_id}
