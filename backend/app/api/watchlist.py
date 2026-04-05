import asyncio
from app.utils.utc import utcnow
import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, func, desc, and_, literal_column, over
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload, joinedload

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
            "created_at": (a.created_at.isoformat() + "Z") if a.created_at else None,
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
    age_hours = (utcnow() - summary.generated_at).total_seconds() / 3600

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
        "generated_at": (summary.generated_at.isoformat() + "Z") if summary.generated_at else None,
        "is_stale": is_stale,
    }


# ── GET /watchlist — all active tickers with signal state ────────────────

@router.get("")
async def get_watchlist(db: AsyncSession = Depends(get_db)):
    """Get all active watchlist tickers with their latest signal state.

    Uses batched queries (8 total, independent of ticker count) instead of
    per-ticker loops to avoid N+1 query patterns.
    """
    from app.services.price_service import price_service
    from collections import defaultdict

    # ── Query 1: Active watchlist tickers ──────────────────────────────
    result = await db.execute(
        select(WatchlistTicker)
        .where(WatchlistTicker.is_active == True)
        .order_by(WatchlistTicker.created_at)
    )
    wt_rows = result.scalars().all()
    if not wt_rows:
        return []

    ticker_list = [wt.ticker for wt in wt_rows]

    # ── Query 2: Latest signal per (ticker, indicator) via window fn ───
    # ROW_NUMBER partitioned by (ticker, indicator) ordered by created_at desc
    row_num = func.row_number().over(
        partition_by=[IndicatorAlert.ticker, IndicatorAlert.indicator],
        order_by=IndicatorAlert.created_at.desc(),
    ).label("rn")
    signals_subq = (
        select(
            IndicatorAlert.ticker,
            IndicatorAlert.indicator,
            IndicatorAlert.value,
            IndicatorAlert.signal,
            IndicatorAlert.timeframe,
            IndicatorAlert.created_at,
            row_num,
        )
        .where(IndicatorAlert.ticker.in_(ticker_list))
        .subquery()
    )
    signals_result = await db.execute(
        select(
            signals_subq.c.ticker,
            signals_subq.c.indicator,
            signals_subq.c.value,
            signals_subq.c.signal,
            signals_subq.c.timeframe,
            signals_subq.c.created_at,
        ).where(signals_subq.c.rn == 1)
    )
    signals_by_ticker: dict[str, list[dict]] = defaultdict(list)
    for row in signals_result.all():
        signals_by_ticker[row[0]].append({
            "indicator": row[1],
            "value": row[2],
            "signal": row[3],
            "timeframe": row[4],
            "created_at": (row[5].isoformat() + "Z") if row[5] else None,
        })

    # ── Query 3: Open trades with joined trader (positions) ────────────
    positions_result = await db.execute(
        select(Trade)
        .options(joinedload(Trade.trader))
        .where(Trade.ticker.in_(ticker_list), Trade.status == "open")
    )
    positions_by_ticker: dict[str, list[dict]] = defaultdict(list)
    for t in positions_result.unique().scalars().all():
        current_price = price_service.get_price(t.ticker) or t.entry_price
        if t.direction == "long":
            pnl_pct = ((current_price - t.entry_price) / t.entry_price * 100)
        else:
            pnl_pct = ((t.entry_price - current_price) / t.entry_price * 100)
        positions_by_ticker[t.ticker].append({
            "strategy_name": t.trader.display_name,
            "strategy_id": t.trader.trader_id,
            "direction": t.direction,
            "entry_price": t.entry_price,
            "current_price": current_price,
            "pnl_pct": round(pnl_pct, 2),
        })

    # ── Query 4: Cached summaries ──────────────────────────────────────
    summaries_result = await db.execute(
        select(WatchlistSummary).where(WatchlistSummary.ticker.in_(ticker_list))
    )
    summaries_by_ticker = {s.ticker: s for s in summaries_result.scalars().all()}

    # ── Query 5: Last alert time per ticker ────────────────────────────
    last_alert_result = await db.execute(
        select(IndicatorAlert.ticker, func.max(IndicatorAlert.created_at))
        .where(IndicatorAlert.ticker.in_(ticker_list))
        .group_by(IndicatorAlert.ticker)
    )
    last_alert_by_ticker = {row[0]: row[1] for row in last_alert_result.all()}

    # ── Query 6: Total alert count per ticker (for staleness check) ────
    alert_count_result = await db.execute(
        select(IndicatorAlert.ticker, func.count(IndicatorAlert.id))
        .where(IndicatorAlert.ticker.in_(ticker_list))
        .group_by(IndicatorAlert.ticker)
    )
    alert_counts = {row[0]: row[1] for row in alert_count_result.all()}

    # ── Query 7: Signal events for sparkline (last 60 days, capped) ────
    event_cutoff = utcnow() - timedelta(days=60)
    events_row_num = func.row_number().over(
        partition_by=IndicatorAlert.ticker,
        order_by=IndicatorAlert.created_at.desc(),
    ).label("rn")
    events_subq = (
        select(
            IndicatorAlert.ticker,
            IndicatorAlert.created_at,
            IndicatorAlert.signal,
            events_row_num,
        )
        .where(
            IndicatorAlert.ticker.in_(ticker_list),
            IndicatorAlert.created_at >= event_cutoff,
        )
        .subquery()
    )
    events_result = await db.execute(
        select(
            events_subq.c.ticker,
            events_subq.c.created_at,
            events_subq.c.signal,
        )
        .where(events_subq.c.rn <= 30)
        .order_by(events_subq.c.ticker, events_subq.c.created_at)
    )
    signal_events_by_ticker: dict[str, list[dict]] = defaultdict(list)
    for row in events_result.all():
        signal_events_by_ticker[row[0]].append({
            "date": row[1].strftime("%Y-%m-%d"),
            "signal": row[2],
        })

    # ── Query 8: Trade events for sparkline (last 60 days) ─────────────
    trade_events_result = await db.execute(
        select(Trade.ticker, Trade.entry_time, Trade.direction, Trade.status)
        .where(
            Trade.ticker.in_(ticker_list),
            Trade.entry_time >= event_cutoff,
            Trade.is_simulated == False,
        )
        .order_by(Trade.ticker, Trade.entry_time)
    )
    trade_events_by_ticker: dict[str, list[dict]] = defaultdict(list)
    for row in trade_events_result.all():
        trade_events_by_ticker[row[0]].append({
            "date": row[1].strftime("%Y-%m-%d"),
            "direction": row[2],
            "status": row[3],
        })

    # Also batch: trades-since-generation count for staleness (avoids N queries)
    # Build a union of (ticker, generated_at) from summaries to count trades since
    trades_since_by_ticker: dict[str, int] = {}
    summary_tickers_with_gen = {
        tk: s.generated_at for tk, s in summaries_by_ticker.items()
        if s.generated_at is not None
    }
    if summary_tickers_with_gen:
        # Single query: count trades per ticker created after its summary generation
        # Use the earliest generation time as a floor, then filter in Python
        earliest_gen = min(summary_tickers_with_gen.values())
        trades_since_result = await db.execute(
            select(Trade.ticker, Trade.created_at)
            .where(
                Trade.ticker.in_(list(summary_tickers_with_gen.keys())),
                Trade.created_at > earliest_gen,
            )
        )
        # Count per ticker only trades after that ticker's generation time
        for row in trades_since_result.all():
            gen_at = summary_tickers_with_gen.get(row[0])
            if gen_at and row[1] > gen_at:
                trades_since_by_ticker[row[0]] = trades_since_by_ticker.get(row[0], 0) + 1

    # ── Assemble response ──────────────────────────────────────────────
    items = []
    now = utcnow()
    for wt in wt_rows:
        tk = wt.ticker
        signals = signals_by_ticker.get(tk, [])
        positions = positions_by_ticker.get(tk, [])
        consensus = _compute_consensus(signals, positions)

        # Compute staleness from batched data
        cached_summary = None
        summary_obj = summaries_by_ticker.get(tk)
        if summary_obj:
            current_alert_count = alert_counts.get(tk, 0)
            new_alerts_since = current_alert_count - summary_obj.alert_count_at_generation
            age_hours = (now - summary_obj.generated_at).total_seconds() / 3600
            trades_since_count = trades_since_by_ticker.get(tk, 0)
            is_stale = (
                new_alerts_since > 2
                or age_hours > 4
                or trades_since_count > 0
            )
            cached_summary = {
                "summary": summary_obj.summary,
                "generated_at": (summary_obj.generated_at.isoformat() + "Z") if summary_obj.generated_at else None,
                "is_stale": is_stale,
            }

        last_alert_at = last_alert_by_ticker.get(tk)

        items.append({
            "id": wt.id,
            "ticker": tk,
            "notes": wt.notes,
            "created_at": (wt.created_at.isoformat() + "Z") if wt.created_at else None,
            "latest_signals": signals,
            "strategy_positions": positions,
            "consensus": consensus,
            "cached_summary": cached_summary,
            "last_alert_at": (last_alert_at.isoformat() + "Z") if last_alert_at else None,
            "signal_events": signal_events_by_ticker.get(tk, []),
            "trade_events": trade_events_by_ticker.get(tk, []),
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
    wt.removed_at = utcnow()
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
            "created_at": (a.created_at.isoformat() + "Z") if a.created_at else None,
        }
        for a in all_alerts
    ]

    # Strategy positions
    positions = await _get_strategy_positions(ticker, db)

    # Trade history (last 30 days, closed)
    history_cutoff = utcnow() - timedelta(days=30)
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
            "entry_time": (t.entry_time.isoformat() + "Z") if t.entry_time else None,
            "exit_time": (t.exit_time.isoformat() + "Z") if t.exit_time else None,
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


# ── GET /watchlist/fundamentals — bulk fundamentals for all watchlist tickers ──

@router.get("/fundamentals")
async def get_watchlist_fundamentals(db: AsyncSession = Depends(get_db)):
    """Return cached FMP fundamentals for all active watchlist tickers."""
    try:
        from app.models.ticker_fundamentals import TickerFundamentals

        result = await db.execute(
            select(WatchlistTicker.ticker).where(WatchlistTicker.is_active == True)
        )
        tickers = [row[0] for row in result.all()]

        if not tickers:
            return {}

        result = await db.execute(
            select(TickerFundamentals).where(TickerFundamentals.ticker.in_(tickers))
        )
        fundamentals = result.scalars().all()

        out = {}
        for f in fundamentals:
            out[f.ticker] = {
                "company_name": f.company_name,
                "sector": f.sector,
                "industry": f.industry,
                "market_cap": f.market_cap,
                "pe_ratio": f.pe_ratio,
                "forward_pe": getattr(f, "forward_pe", None),
                "analyst_rating": f.analyst_rating,
                "analyst_count": f.analyst_count,
                "analyst_target_consensus": f.analyst_target_consensus,
                "earnings_date": f.earnings_date.isoformat() if f.earnings_date else None,
                "earnings_time": f.earnings_time,
                "eps_surprise_last": f.eps_surprise_last,
                "dcf_value": getattr(f, "dcf_value", None),
                "dcf_diff_pct": getattr(f, "dcf_diff_pct", None),
                "short_interest_pct": f.short_interest_pct,
                "insider_net_90d": getattr(f, "insider_net_90d", None),
                "beta": getattr(f, "beta", None),
                "dividend_yield": getattr(f, "dividend_yield", None),
                "updated_at": (f.updated_at.isoformat() + "Z") if f.updated_at else None,
            }
        return out
    except Exception:
        return {}


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
