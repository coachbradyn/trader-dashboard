import asyncio
import logging
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, func, desc, and_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models.watchlist_ticker import WatchlistTicker
from app.models.watchlist_summary import WatchlistSummary
from app.models.indicator_alert import IndicatorAlert
from app.models.trade import Trade
from app.models.trader import Trader

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/watchlist", tags=["watchlist"])


# ── Request / Response Schemas ───────────────────────────────────────────

class AddTickersRequest(BaseModel):
    tickers: list[str]
    notes: str | None = None

class RemoveTickerRequest(BaseModel):
    ticker: str

class WatchlistSignal(BaseModel):
    indicator: str
    value: float
    signal: str
    timeframe: str | None = None
    created_at: str

class StrategyPosition(BaseModel):
    strategy_name: str
    strategy_id: str
    direction: str
    entry_price: float
    current_price: float | None = None
    pnl_pct: float | None = None

class ConsensusInfo(BaseModel):
    direction: str  # "bullish" | "bearish" | "mixed" | "no_data"
    bullish_count: int
    bearish_count: int
    total_signals: int

class CachedSummaryInfo(BaseModel):
    summary: str
    generated_at: str
    is_stale: bool

class WatchlistTickerResponse(BaseModel):
    id: str
    ticker: str
    notes: str | None
    created_at: str
    latest_signals: list[WatchlistSignal]
    strategy_positions: list[StrategyPosition]
    consensus: ConsensusInfo
    cached_summary: CachedSummaryInfo | None
    last_alert_at: str | None


# ── Helper: get latest signal per indicator for a ticker ─────────────────

async def _get_latest_signals_per_indicator(ticker: str, db: AsyncSession) -> list[dict]:
    """Return only the most recent alert per indicator for this ticker."""
    # Subquery to get max created_at per indicator
    subq = (
        select(
            IndicatorAlert.indicator,
            func.max(IndicatorAlert.created_at).label("max_created_at"),
        )
        .where(IndicatorAlert.ticker == ticker)
        .group_by(IndicatorAlert.indicator)
        .subquery()
    )

    result = await db.execute(
        select(IndicatorAlert)
        .join(
            subq,
            and_(
                IndicatorAlert.indicator == subq.c.indicator,
                IndicatorAlert.created_at == subq.c.max_created_at,
            ),
        )
        .where(IndicatorAlert.ticker == ticker)
        .order_by(desc(IndicatorAlert.created_at))
    )
    alerts = result.scalars().all()

    return [
        {
            "indicator": a.indicator,
            "value": a.value,
            "signal": a.signal,
            "timeframe": a.timeframe,
            "created_at": a.created_at.isoformat(),
        }
        for a in alerts
    ]


# ── Helper: get strategy positions for a ticker ─────────────────────────

async def _get_strategy_positions(ticker: str, db: AsyncSession) -> list[dict]:
    """Get open strategy positions on this ticker, dynamically from traders table."""
    from app.services.price_service import price_service

    result = await db.execute(
        select(Trade)
        .options(selectinload(Trade.trader))
        .where(Trade.ticker == ticker, Trade.status == "open")
    )
    open_trades = result.scalars().all()

    positions = []
    for t in open_trades:
        current_price = price_service.get_price(t.ticker) or t.entry_price
        if t.direction == "long":
            pnl_pct = ((current_price - t.entry_price) / t.entry_price * 100)
        else:
            pnl_pct = ((t.entry_price - current_price) / t.entry_price * 100)

        positions.append({
            "strategy_name": t.trader.display_name,
            "strategy_id": t.trader.trader_id,
            "direction": t.direction,
            "entry_price": t.entry_price,
            "current_price": current_price,
            "pnl_pct": round(pnl_pct, 2),
        })
    return positions


# ── Helper: compute consensus ────────────────────────────────────────────

def _compute_consensus(signals: list[dict], positions: list[dict]) -> dict:
    """Count bullish vs bearish across indicator signals and strategy positions."""
    bullish = 0
    bearish = 0

    for s in signals:
        sig = s.get("signal", "").lower()
        if sig == "bullish":
            bullish += 1
        elif sig == "bearish":
            bearish += 1

    for p in positions:
        d = p.get("direction", "").lower()
        if d == "long":
            bullish += 1
        elif d == "short":
            bearish += 1

    total = bullish + bearish
    if total == 0:
        direction = "no_data"
    elif bullish > bearish:
        direction = "bullish"
    elif bearish > bullish:
        direction = "bearish"
    else:
        direction = "mixed"

    return {
        "direction": direction,
        "bullish_count": bullish,
        "bearish_count": bearish,
        "total_signals": total,
    }


# ── Helper: check summary staleness ─────────────────────────────────────

async def _get_cached_summary(ticker: str, db: AsyncSession) -> dict | None:
    """Return cached summary with staleness check."""
    result = await db.execute(
        select(WatchlistSummary).where(WatchlistSummary.ticker == ticker)
    )
    summary = result.scalar_one_or_none()
    if not summary:
        return None

    # Check staleness: count alerts since generation
    alert_count_result = await db.execute(
        select(func.count(IndicatorAlert.id))
        .where(IndicatorAlert.ticker == ticker)
    )
    current_alert_count = alert_count_result.scalar() or 0

    new_alerts_since = current_alert_count - summary.alert_count_at_generation
    age_hours = (datetime.utcnow() - summary.generated_at).total_seconds() / 3600

    # Check if any strategy trade happened since generation
    trade_since = await db.execute(
        select(func.count(Trade.id))
        .where(
            Trade.ticker == ticker,
            Trade.created_at > summary.generated_at,
        )
    )
    trades_since_count = trade_since.scalar() or 0

    is_stale = (
        new_alerts_since > 2
        or age_hours > 4
        or trades_since_count > 0
    )

    return {
        "summary": summary.summary,
        "generated_at": summary.generated_at.isoformat(),
        "is_stale": is_stale,
    }


# ── GET /watchlist — all active tickers with signal state ────────────────

@router.get("")
async def get_watchlist(db: AsyncSession = Depends(get_db)):
    """Get all active watchlist tickers with their latest signal state."""
    result = await db.execute(
        select(WatchlistTicker)
        .where(WatchlistTicker.is_active == True)
        .order_by(WatchlistTicker.created_at)
    )
    tickers = result.scalars().all()

    items = []
    for wt in tickers:
        signals = await _get_latest_signals_per_indicator(wt.ticker, db)
        positions = await _get_strategy_positions(wt.ticker, db)
        consensus = _compute_consensus(signals, positions)
        cached_summary = await _get_cached_summary(wt.ticker, db)

        # Find last alert time
        last_alert_result = await db.execute(
            select(func.max(IndicatorAlert.created_at))
            .where(IndicatorAlert.ticker == wt.ticker)
        )
        last_alert_at = last_alert_result.scalar()

        # Signal events for sparkline overlay (last 60 days of alerts)
        from datetime import timedelta as _td
        event_cutoff = datetime.utcnow() - _td(days=60)
        events_result = await db.execute(
            select(IndicatorAlert.created_at, IndicatorAlert.signal)
            .where(IndicatorAlert.ticker == wt.ticker, IndicatorAlert.created_at >= event_cutoff)
            .order_by(IndicatorAlert.created_at)
        )
        signal_events = [
            {"date": row[0].strftime("%Y-%m-%d"), "signal": row[1]}
            for row in events_result.all()
        ]

        # Trade events for sparkline overlay
        trade_events_result = await db.execute(
            select(Trade.entry_time, Trade.direction, Trade.status)
            .where(Trade.ticker == wt.ticker, Trade.entry_time >= event_cutoff, Trade.is_simulated == False)
            .order_by(Trade.entry_time)
        )
        trade_events = [
            {"date": row[0].strftime("%Y-%m-%d"), "direction": row[1], "status": row[2]}
            for row in trade_events_result.all()
        ]

        items.append({
            "id": wt.id,
            "ticker": wt.ticker,
            "notes": wt.notes,
            "created_at": wt.created_at.isoformat(),
            "latest_signals": signals,
            "strategy_positions": positions,
            "consensus": consensus,
            "cached_summary": cached_summary,
            "last_alert_at": last_alert_at.isoformat() if last_alert_at else None,
            "signal_events": signal_events,
            "trade_events": trade_events,
        })

    return items


# ── POST /watchlist — add tickers ────────────────────────────────────────

@router.post("")
async def add_tickers(req: AddTickersRequest, db: AsyncSession = Depends(get_db)):
    """Add one or more tickers to the watchlist."""
    added = []
    for raw_ticker in req.tickers:
        ticker = raw_ticker.strip().upper()
        if not ticker:
            continue

        # Check if already exists (may be soft-deleted)
        result = await db.execute(
            select(WatchlistTicker).where(WatchlistTicker.ticker == ticker)
        )
        existing = result.scalar_one_or_none()

        if existing:
            if not existing.is_active:
                existing.is_active = True
                existing.removed_at = None
                if req.notes is not None:
                    existing.notes = req.notes
                added.append(ticker)
            # Already active — skip silently
        else:
            wt = WatchlistTicker(
                ticker=ticker,
                notes=req.notes,
            )
            db.add(wt)
            added.append(ticker)

    await db.commit()

    # Fetch fundamentals immediately for newly added tickers (non-blocking)
    if added:
        async def _fetch_fundamentals_for_new_tickers(tickers: list[str]):
            try:
                from app.services.fmp_service import refresh_ticker
                for t in tickers:
                    await refresh_ticker(t)
            except Exception:
                pass

        asyncio.create_task(_fetch_fundamentals_for_new_tickers(added))

    return {"added": added, "count": len(added)}


# ── DELETE /watchlist/{ticker} — remove ticker (soft delete) ─────────────

@router.delete("/{ticker}")
async def remove_ticker(ticker: str, db: AsyncSession = Depends(get_db)):
    """Soft-delete a ticker from the watchlist."""
    ticker = ticker.upper()
    result = await db.execute(
        select(WatchlistTicker).where(
            WatchlistTicker.ticker == ticker,
            WatchlistTicker.is_active == True,
        )
    )
    wt = result.scalar_one_or_none()
    if not wt:
        raise HTTPException(404, f"{ticker} not on watchlist")

    wt.is_active = False
    wt.removed_at = datetime.utcnow()
    await db.commit()
    return {"removed": ticker}


# ── GET /watchlist/{ticker}/detail — expanded view ───────────────────────

@router.get("/{ticker}/detail")
async def get_ticker_detail(ticker: str, db: AsyncSession = Depends(get_db)):
    """Get full detail for a single watchlist ticker: all signals, positions, history, summary."""
    ticker = ticker.upper()

    # All indicator signals (not just latest per indicator)
    result = await db.execute(
        select(IndicatorAlert)
        .where(IndicatorAlert.ticker == ticker)
        .order_by(desc(IndicatorAlert.created_at))
        .limit(50)
    )
    all_alerts = result.scalars().all()

    all_signals = [
        {
            "id": a.id,
            "indicator": a.indicator,
            "value": a.value,
            "signal": a.signal,
            "timeframe": a.timeframe,
            "created_at": a.created_at.isoformat(),
        }
        for a in all_alerts
    ]

    # Strategy positions
    positions = await _get_strategy_positions(ticker, db)

    # Trade history (last 30 days, closed)
    history_cutoff = datetime.utcnow() - timedelta(days=30)
    hist_result = await db.execute(
        select(Trade)
        .options(selectinload(Trade.trader))
        .where(
            Trade.ticker == ticker,
            Trade.status == "closed",
            Trade.entry_time >= history_cutoff,
        )
        .order_by(desc(Trade.exit_time))
        .limit(20)
    )
    history_trades = hist_result.scalars().all()

    trade_history = [
        {
            "strategy_name": t.trader.display_name,
            "strategy_id": t.trader.trader_id,
            "direction": t.direction,
            "entry_price": t.entry_price,
            "exit_price": t.exit_price,
            "pnl_pct": t.pnl_percent or 0,
            "exit_reason": t.exit_reason,
            "entry_time": t.entry_time.isoformat() if t.entry_time else None,
            "exit_time": t.exit_time.isoformat() if t.exit_time else None,
        }
        for t in history_trades
    ]

    # Latest signals for consensus
    latest_signals = await _get_latest_signals_per_indicator(ticker, db)
    consensus = _compute_consensus(latest_signals, positions)
    cached_summary = await _get_cached_summary(ticker, db)

    return {
        "ticker": ticker,
        "all_signals": all_signals,
        "latest_signals": latest_signals,
        "strategy_positions": positions,
        "trade_history": trade_history,
        "consensus": consensus,
        "cached_summary": cached_summary,
    }


# ── POST /watchlist/{ticker}/refresh-summary — trigger AI summary regen ──

@router.post("/{ticker}/refresh-summary")
async def refresh_summary(ticker: str, db: AsyncSession = Depends(get_db)):
    """Trigger background regeneration of the AI summary for a ticker."""
    ticker = ticker.upper()

    # Verify ticker is on watchlist
    result = await db.execute(
        select(WatchlistTicker).where(
            WatchlistTicker.ticker == ticker,
            WatchlistTicker.is_active == True,
        )
    )
    wt = result.scalar_one_or_none()
    if not wt:
        raise HTTPException(404, f"{ticker} not on watchlist")

    # Fire background task
    from app.services.watchlist_ai import generate_watchlist_summary
    asyncio.create_task(generate_watchlist_summary(ticker))

    return {"status": "generating", "ticker": ticker}


# ── POST /watchlist/sync — sync holdings/trades to watchlist ──────────────

@router.post("/sync")
async def sync_watchlist(db: AsyncSession = Depends(get_db)):
    """Sync all tickers from active holdings and open trades to the watchlist."""
    from app.models.portfolio_holding import PortfolioHolding

    # Get all unique tickers from active holdings
    holding_result = await db.execute(
        select(PortfolioHolding.ticker)
        .where(PortfolioHolding.is_active == True)
        .distinct()
    )
    holding_tickers = {row[0] for row in holding_result.all()}

    # Get all unique tickers from open trades
    trade_result = await db.execute(
        select(Trade.ticker)
        .where(Trade.status == "open", Trade.is_simulated == False)
        .distinct()
    )
    trade_tickers = {row[0] for row in trade_result.all()}

    all_tickers = holding_tickers | trade_tickers

    wl_result = await db.execute(select(WatchlistTicker))
    existing = {wt.ticker: wt for wt in wl_result.scalars().all()}

    added = 0
    for ticker in all_tickers:
        if ticker in existing:
            if not existing[ticker].is_active:
                existing[ticker].is_active = True
                existing[ticker].removed_at = None
                added += 1
        else:
            db.add(WatchlistTicker(ticker=ticker))
            added += 1

    await db.commit()
    return {"synced": added, "total_tickers": len(all_tickers)}


# ── GET /watchlist/strategies — list available strategies dynamically ─────

@router.get("/strategies/list")
async def list_strategies(db: AsyncSession = Depends(get_db)):
    """Return all active strategies from the traders table."""
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
        }
        for t in traders
    ]
