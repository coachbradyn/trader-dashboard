import csv
import io
import logging
import re
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models import PortfolioAction, BacktestImport, BacktestTrade, PortfolioHolding, HenryMemory, Trader
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
                created_at=existing.created_at,
            )

        # No existing holding — create new
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
            created_at=datetime.utcnow(),
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
        },
        "action_map": {"buy": "buy", "sell": "sell", "stc": "sell", "btc": "buy", "bto": "buy", "sto": "sell"},
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

# Keywords indicating non-equity transactions to skip
SKIP_KEYWORDS = {"dividend", "interest", "fee", "journal", "transfer", "acat", "reinvest",
                 "margin", "adjustment", "wire", "ach", "deposit", "withdrawal", "option",
                 "call", "put", "expired", "assigned", "exercised"}


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


def _parse_trades_from_csv(content: str, mapping: dict, action_map: dict) -> list[dict]:
    """Parse CSV content into normalized trade dicts using the given column mapping and action map."""
    reader = csv.DictReader(io.StringIO(content))
    trades = []

    # Build a case-insensitive lookup for the actual CSV column names
    fieldnames = reader.fieldnames or []
    col_lookup = {f.lower().strip(): f for f in fieldnames}

    def get_val(row: dict, mapped_name: str) -> str | None:
        # mapped_name is the value from our mapping dict (e.g., "activity date")
        # Try exact match first, then case-insensitive
        if mapped_name in row:
            return row[mapped_name]
        actual = col_lookup.get(mapped_name.lower().strip())
        if actual and actual in row:
            return row[actual]
        return None

    for row in reader:
        # Get action value and check for skip
        action_raw = get_val(row, mapping["action"]) or ""
        action_lower = action_raw.lower().strip()

        # Skip non-equity rows
        skip = False
        for kw in SKIP_KEYWORDS:
            if kw in action_lower:
                skip = True
                break
        if skip:
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
        # Skip if ticker looks like an option (contains space or more than 6 chars with digits)
        if " " in ticker or len(ticker) > 6:
            continue

        # Parse numeric fields
        qty_val = _clean_numeric(get_val(row, mapping["qty"]))
        price_val = _clean_numeric(get_val(row, mapping["price"]))
        amount_val = _clean_numeric(get_val(row, mapping.get("amount", "")))

        if qty_val is None or price_val is None:
            continue
        if qty_val == 0 or price_val == 0:
            continue

        # Handle negative quantities (some brokerages use negative for sells)
        qty_val = abs(qty_val)
        price_val = abs(price_val)
        if amount_val is not None:
            amount_val = abs(amount_val)
        else:
            amount_val = round(qty_val * price_val, 2)

        # Parse date
        date_str = _parse_date_flexible(get_val(row, mapping["date"]))
        if not date_str:
            date_str = datetime.utcnow().strftime("%Y-%m-%d")

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
    Body: { portfolio_id, trades: [{date, ticker, action, qty, price, amount}] }
    """
    portfolio_id = body.get("portfolio_id")
    trades = body.get("trades", [])

    if not portfolio_id:
        raise HTTPException(400, "portfolio_id is required")
    if not trades:
        raise HTTPException(400, "No trades to import")

    # Sort trades chronologically (oldest first)
    trades.sort(key=lambda t: t.get("date", ""))

    holdings_created = 0
    holdings_updated = 0
    holdings_closed = 0
    imported = 0

    for trade in trades:
        ticker = trade.get("ticker", "").upper()
        action = trade.get("action", "")
        qty = float(trade.get("qty", 0))
        price = float(trade.get("price", 0))
        date_str = trade.get("date", "")

        if not ticker or not action or qty <= 0 or price <= 0:
            continue

        # Parse date
        try:
            entry_date = datetime.strptime(date_str, "%Y-%m-%d")
        except (ValueError, TypeError):
            entry_date = datetime.utcnow()

        if action == "buy":
            # Check for existing active holding with same ticker+portfolio, direction=long
            result = await db.execute(
                select(PortfolioHolding).where(
                    PortfolioHolding.portfolio_id == portfolio_id,
                    PortfolioHolding.ticker == ticker,
                    PortfolioHolding.direction == "long",
                    PortfolioHolding.is_active == True,
                )
            )
            existing = result.scalar_one_or_none()

            if existing:
                # Merge: weighted average price, sum qty
                old_cost = existing.entry_price * existing.qty
                new_cost = price * qty
                total_qty = existing.qty + qty
                avg_price = (old_cost + new_cost) / total_qty if total_qty > 0 else price

                existing.entry_price = round(avg_price, 4)
                existing.qty = round(total_qty, 6)
                if entry_date < existing.entry_date:
                    existing.entry_date = entry_date
                holdings_updated += 1
            else:
                # Create new holding
                holding = PortfolioHolding(
                    portfolio_id=portfolio_id,
                    ticker=ticker,
                    direction="long",
                    entry_price=round(price, 4),
                    qty=round(qty, 6),
                    entry_date=entry_date,
                    strategy_name="import",
                    notes=f"Imported from CSV",
                    is_active=True,
                    created_at=datetime.utcnow(),
                )
                db.add(holding)
                holdings_created += 1

        elif action == "sell":
            # Find matching active holding
            result = await db.execute(
                select(PortfolioHolding).where(
                    PortfolioHolding.portfolio_id == portfolio_id,
                    PortfolioHolding.ticker == ticker,
                    PortfolioHolding.direction == "long",
                    PortfolioHolding.is_active == True,
                )
            )
            existing = result.scalar_one_or_none()

            if existing:
                remaining = existing.qty - qty
                if remaining <= 0.0001:
                    # Full close
                    existing.is_active = False
                    existing.qty = 0
                    holdings_closed += 1
                else:
                    existing.qty = round(remaining, 6)
                    holdings_updated += 1

        imported += 1

        # Flush periodically to avoid stale reads in subsequent iterations
        if imported % 50 == 0:
            await db.flush()

    await db.commit()

    return {
        "imported": imported,
        "holdings_created": holdings_created,
        "holdings_updated": holdings_updated,
        "holdings_closed": holdings_closed,
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
            expires_at=datetime.utcnow() + timedelta(hours=4 if s["trigger_type"] == "SIGNAL" else 24),
            created_at=datetime.utcnow(),
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
