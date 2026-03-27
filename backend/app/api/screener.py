import logging
import re
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, func, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Trader
from app.models.indicator_alert import IndicatorAlert
from app.models.allowlisted_key import AllowlistedKey
from app.models.screener_analysis import ScreenerAnalysis
from app.schemas.screener import (
    ScreenerWebhookPayload, AlertResponse, TickerAggregation, ScreenerPickResponse,
    TickerAnalysisRequest, TickerAnalysisResponse,
)
from app.utils.auth import verify_api_key
from app.services.chart_service import get_daily_chart

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/screener", tags=["screener"])


def _slugify(name: str) -> str:
    """Generate a URL-safe slug from a name."""
    slug = re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-')
    return slug[:50] if slug else "unnamed-strategy"


@router.post("/webhook")
async def screener_webhook(payload: ScreenerWebhookPayload, db: AsyncSession = Depends(get_db)):
    # Validate API key — same flow as trade webhook
    # 1. Check known traders
    result = await db.execute(select(Trader))
    traders = result.scalars().all()

    authenticated_trader = None
    for t in traders:
        if verify_api_key(payload.key, t.api_key_hash):
            authenticated_trader = t
            break

    # 2. Check allowlisted keys if no trader match
    if not authenticated_trader:
        result = await db.execute(
            select(AllowlistedKey).where(AllowlistedKey.claimed_by_id.is_(None))
        )
        unclaimed_keys = result.scalars().all()

        for ak in unclaimed_keys:
            if verify_api_key(payload.key, ak.api_key_hash):
                # Auto-create trader
                from app.utils.auth import hash_api_key
                slug = f"strategy-{ak.id[:8]}"
                new_trader = Trader(
                    trader_id=slug,
                    display_name=ak.label or "Unnamed Strategy",
                    api_key_hash=ak.api_key_hash,
                )
                db.add(new_trader)
                await db.flush()
                ak.claimed_by_id = new_trader.id
                authenticated_trader = new_trader
                break

        if not authenticated_trader:
            raise HTTPException(401, "Invalid API key")

    # Update last webhook timestamp
    authenticated_trader.last_webhook_at = datetime.utcnow()

    # Create alert
    alert_time = (
        datetime.utcfromtimestamp(payload.time / 1000)
        if payload.time
        else datetime.utcnow()
    )

    alert = IndicatorAlert(
        ticker=payload.ticker.upper(),
        indicator=payload.indicator.upper(),
        value=payload.value or 0.0,
        signal=payload.signal,
        timeframe=payload.tf,
        metadata_extra=payload.metadata,
        created_at=alert_time,
    )
    db.add(alert)
    await db.commit()
    await db.refresh(alert)

    # Check if this ticker is on the watchlist and trigger staleness check
    import asyncio
    from app.services.watchlist_ai import check_and_regenerate_if_stale
    asyncio.create_task(check_and_regenerate_if_stale(alert.ticker))

    return {"status": "ok", "alert_id": alert.id, "ticker": alert.ticker}


@router.get("/alerts", response_model=list[AlertResponse])
async def get_alerts(
    ticker: str | None = None,
    indicator: str | None = None,
    signal: str | None = None,
    hours: int = 24,
    limit: int = 200,
    db: AsyncSession = Depends(get_db),
):
    cutoff = datetime.utcnow() - timedelta(hours=hours)
    query = select(IndicatorAlert).where(IndicatorAlert.created_at >= cutoff)

    if ticker:
        query = query.where(IndicatorAlert.ticker == ticker.upper())
    if indicator:
        query = query.where(IndicatorAlert.indicator == indicator.upper())
    if signal:
        query = query.where(IndicatorAlert.signal == signal)

    query = query.order_by(desc(IndicatorAlert.created_at)).limit(limit)
    result = await db.execute(query)
    alerts = result.scalars().all()

    return [
        AlertResponse(
            id=a.id,
            ticker=a.ticker,
            indicator=a.indicator,
            value=a.value,
            signal=a.signal,
            timeframe=a.timeframe,
            created_at=a.created_at,
        )
        for a in alerts
    ]


@router.get("/tickers")
async def get_ticker_aggregations(
    hours: int = 24,
    db: AsyncSession = Depends(get_db),
):
    cutoff = datetime.utcnow() - timedelta(hours=hours)

    # Get all alerts within window
    result = await db.execute(
        select(IndicatorAlert)
        .where(IndicatorAlert.created_at >= cutoff)
        .order_by(desc(IndicatorAlert.created_at))
    )
    alerts = result.scalars().all()

    # Aggregate by ticker
    ticker_map: dict[str, dict] = {}
    for a in alerts:
        if a.ticker not in ticker_map:
            ticker_map[a.ticker] = {
                "ticker": a.ticker,
                "alert_count": 0,
                "latest_signal": a.signal,
                "indicators": set(),
                "latest_alert_at": a.created_at,
                "alerts": [],
            }
        entry = ticker_map[a.ticker]
        entry["alert_count"] += 1
        entry["indicators"].add(a.indicator)
        entry["alerts"].append({
            "id": a.id,
            "indicator": a.indicator,
            "value": a.value,
            "signal": a.signal,
            "timeframe": a.timeframe,
            "created_at": a.created_at.isoformat(),
        })

    # Convert sets and sort by alert count
    result_list = []
    for t in sorted(ticker_map.values(), key=lambda x: x["alert_count"], reverse=True):
        t["indicators"] = list(t["indicators"])
        t["latest_alert_at"] = t["latest_alert_at"].isoformat()
        result_list.append(t)

    return result_list


@router.get("/chart/{ticker}")
async def get_chart(ticker: str, days: int = 60):
    data = await get_daily_chart(ticker.upper(), days)
    if not data:
        raise HTTPException(404, f"No chart data for {ticker}")
    return data


@router.get("/analysis/latest", response_model=ScreenerPickResponse | None)
async def get_latest_analysis(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(ScreenerAnalysis).order_by(desc(ScreenerAnalysis.generated_at)).limit(1)
    )
    analysis = result.scalar_one_or_none()
    if not analysis:
        return None
    return ScreenerPickResponse(
        id=analysis.id,
        picks=analysis.picks,
        market_context=analysis.market_context,
        alerts_analyzed=analysis.alerts_analyzed,
        generated_at=analysis.generated_at,
    )


@router.post("/analyze/{ticker}", response_model=TickerAnalysisResponse)
async def analyze_ticker(
    ticker: str,
    body: TickerAnalysisRequest | None = None,
    db: AsyncSession = Depends(get_db),
):
    """Trigger a live Claude analysis for a specific ticker when its card is opened."""
    from sqlalchemy.orm import selectinload
    from app.models.trade import Trade as TradeModel
    from app.services.screener_ai import analyze_single_ticker

    hours = body.hours if body else 24
    ticker = ticker.upper()
    cutoff = datetime.utcnow() - timedelta(hours=hours)

    # 1. Gather recent alerts for this ticker
    result = await db.execute(
        select(IndicatorAlert)
        .where(IndicatorAlert.ticker == ticker)
        .where(IndicatorAlert.created_at >= cutoff)
        .order_by(desc(IndicatorAlert.created_at))
    )
    alerts = result.scalars().all()

    if not alerts:
        raise HTTPException(404, f"No recent alerts for {ticker}")

    alert_dicts = [
        {
            "indicator": a.indicator,
            "value": a.value,
            "signal": a.signal,
            "timeframe": a.timeframe,
            "created_at": a.created_at.isoformat(),
            "metadata": a.metadata_extra,
        }
        for a in alerts
    ]

    # 2. Fetch chart data
    chart_data = await get_daily_chart(ticker, 60)

    # 3. Get current portfolio positions for this ticker
    pos_result = await db.execute(
        select(TradeModel)
        .options(selectinload(TradeModel.trader))
        .where(TradeModel.ticker == ticker)
        .where(TradeModel.status == "open")
    )
    open_positions = pos_result.scalars().all()

    positions_list = [
        {
            "trader": t.trader.trader_id,
            "strategy_name": t.trader.display_name,
            "dir": t.direction,
            "entry_price": t.entry_price,
            "current_price": t.entry_price,
            "pnl_pct": 0,
        }
        for t in open_positions
    ]

    # 4. Get trade history for this ticker (last 30 days)
    history_cutoff = datetime.utcnow() - timedelta(days=30)
    hist_result = await db.execute(
        select(TradeModel)
        .options(selectinload(TradeModel.trader))
        .where(TradeModel.ticker == ticker)
        .where(TradeModel.status == "closed")
        .where(TradeModel.entry_time >= history_cutoff)
        .order_by(desc(TradeModel.exit_time))
        .limit(20)
    )
    history_trades = hist_result.scalars().all()

    history_list = [
        {
            "trader": t.trader.trader_id,
            "strategy_name": t.trader.display_name,
            "dir": t.direction,
            "entry_price": t.entry_price,
            "exit_price": t.exit_price,
            "pnl_pct": t.pnl_percent or 0,
            "exit_reason": t.exit_reason,
            "bars_in_trade": t.bars_in_trade,
            "entry_time": t.entry_time.isoformat() if t.entry_time else None,
            "exit_time": t.exit_time.isoformat() if t.exit_time else None,
        }
        for t in history_trades
    ]

    # 5. Call AI analysis
    analysis = analyze_single_ticker(
        ticker=ticker,
        alerts=alert_dicts,
        chart_data=chart_data,
        portfolio_positions=positions_list,
        trade_history=history_list,
    )

    return TickerAnalysisResponse(
        ticker=ticker,
        generated_at=datetime.utcnow(),
        **analysis,
    )
