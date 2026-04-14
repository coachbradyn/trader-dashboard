import hashlib
import logging
from app.utils.utc import utcnow
import re
from datetime import datetime, timedelta, timezone

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
from app.utils.api_key_cache import (
    get_cached_trader_id,
    remember as remember_key,
    bcrypt_check,
)
from app.services.chart_service import get_daily_chart
from app.utils.dedup import make_screener_fingerprint, is_duplicate_webhook

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/screener", tags=["screener"])


def _slugify(name: str) -> str:
    """Generate a URL-safe slug from a name."""
    slug = re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-')
    return slug[:50] if slug else "unnamed-strategy"


def _fast_hash_key(raw_key: str) -> str:
    """SHA-256 digest of a raw API key for fast DB lookup."""
    return hashlib.sha256(raw_key.encode()).hexdigest()


async def _authenticate_screener_key(raw_key: str, db: AsyncSession) -> Trader | None:
    """
    Resolve a raw API key to a Trader. Cache-first; bcrypt fallback.

    Flow:
      1. Cache hit → load trader by ID (single indexed query, ~5ms) and
         return immediately. No bcrypt.
      2. Cache miss → check whether any Trader.api_key_hash matches the
         key's SHA-256 digest (legacy fast path for keys originally
         stored as SHA-256). Rare; here for compatibility.
      3. Still no match → bcrypt scan over traders, but each bcrypt call
         runs via asyncio.to_thread so it doesn't block the event loop.
         First match wins and is cached.
      4. Still no match → bcrypt scan over unclaimed AllowlistedKey
         rows. Matching key auto-creates a trader and is cached.

    Returns the authenticated Trader or None. Never raises — callers
    raise HTTP 401 on None.
    """
    # (1) Cache hit — the happy path after the first request per key.
    cached_id = get_cached_trader_id(raw_key)
    if cached_id:
        row = (
            await db.execute(select(Trader).where(Trader.id == cached_id).limit(1))
        ).scalar_one_or_none()
        if row is not None:
            return row
        # Stale cache (trader deleted) — fall through to full auth.

    # (2) Legacy SHA-256 fast path. Our traders currently store bcrypt
    # hashes so this almost never hits, but leaving it cheap + correct.
    key_digest = _fast_hash_key(raw_key)
    row = (
        await db.execute(
            select(Trader).where(Trader.api_key_hash == key_digest).limit(1)
        )
    ).scalar_one_or_none()
    if row is not None:
        await remember_key(raw_key, row.id)
        return row

    # (3) Bcrypt scan over traders, off the event loop.
    traders = list((await db.execute(select(Trader))).scalars().all())
    for t in traders:
        if await bcrypt_check(raw_key, t.api_key_hash):
            await remember_key(raw_key, t.id)
            return t

    # (4) Bcrypt scan over unclaimed AllowlistedKey rows.
    unclaimed = list(
        (
            await db.execute(
                select(AllowlistedKey).where(AllowlistedKey.claimed_by_id.is_(None))
            )
        ).scalars().all()
    )
    for ak in unclaimed:
        if await bcrypt_check(raw_key, ak.api_key_hash):
            slug = f"strategy-{ak.id[:8]}"
            new_trader = Trader(
                trader_id=slug,
                display_name=ak.label or "Unnamed Strategy",
                api_key_hash=ak.api_key_hash,
            )
            db.add(new_trader)
            await db.flush()
            ak.claimed_by_id = new_trader.id
            await remember_key(raw_key, new_trader.id)
            return new_trader

    return None


@router.post("/webhook")
async def screener_webhook(payload: ScreenerWebhookPayload, db: AsyncSession = Depends(get_db)):
    # Authenticate via the shared API key cache (sha256(raw_key) → trader_id).
    # Phase 2 Fix 1 — bcrypt is CPU-bound and blocks the event loop, so we
    # (a) cache successful resolutions to skip bcrypt on repeat hits and
    # (b) run any unavoidable bcrypt calls in the thread pool so concurrent
    # requests aren't serialized by the single event loop.
    authenticated_trader = await _authenticate_screener_key(payload.key, db)
    if not authenticated_trader:
        raise HTTPException(401, "Invalid API key")

    # Screener-specific idempotency check
    fp = make_screener_fingerprint(
        trader=authenticated_trader.trader_id,
        ticker=payload.ticker,
        indicator=payload.indicator,
        signal=payload.signal,
        timeframe=payload.tf or "",
        unix_time=payload.time or 0,
    )
    if is_duplicate_webhook(fp):
        return {"status": "duplicate", "alert_id": None, "ticker": payload.ticker.upper()}

    # Update last webhook timestamp
    authenticated_trader.last_webhook_at = utcnow()

    # Create alert
    alert_time = (
        datetime.fromtimestamp(payload.time / 1000, tz=timezone.utc).replace(tzinfo=None)
        if payload.time
        else utcnow()
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

    # Invalidate cached Henry analysis for this ticker
    from app.services.henry_cache import invalidate_by_ticker
    await invalidate_by_ticker(db, alert.ticker)
    await db.commit()

    # Check if this ticker is on the watchlist and trigger staleness check
    import asyncio
    from app.services.watchlist_ai import check_and_regenerate_if_stale
    asyncio.create_task(check_and_regenerate_if_stale(alert.ticker))

    # Screener-to-memory bridge (intelligence upgrade Phase 2, System 3):
    # if N+ distinct indicators have fired on this ticker in the last 48h,
    # save a confluence memory so semantic retrieval surfaces it on any
    # future signal evaluation for this ticker. Fire-and-forget — we don't
    # want a slow memory save to delay the webhook ACK.
    asyncio.create_task(_maybe_save_screener_confluence(alert.ticker))

    return {"status": "ok", "alert_id": alert.id, "ticker": alert.ticker}


# Confluence threshold and lookback — kept module-level so they're easy to
# tune without touching the webhook handler.
CONFLUENCE_LOOKBACK_HOURS = 48
CONFLUENCE_MIN_INDICATORS = 3


async def _maybe_save_screener_confluence(ticker: str) -> None:
    """
    Count distinct indicators that fired on `ticker` in the last
    CONFLUENCE_LOOKBACK_HOURS. If at or above CONFLUENCE_MIN_INDICATORS,
    build a confluence summary and save it as a HenryMemory.

    Idempotent at the memory layer — save_memory's content-hash dedup
    skips identical summaries within 30 days. New indicator firing on the
    same ticker → new content → new memory.
    """
    import logging
    from datetime import timedelta
    from sqlalchemy import select, func
    from app.database import async_session
    from app.models import IndicatorAlert
    from app.services.ai_service import save_memory

    logger = logging.getLogger(__name__)

    try:
        cutoff = utcnow() - timedelta(hours=CONFLUENCE_LOOKBACK_HOURS)
        async with async_session() as db:
            # Pull distinct indicator names + their bullish/bearish/neutral
            # signal so we can characterize the confluence direction.
            result = await db.execute(
                select(
                    IndicatorAlert.indicator,
                    IndicatorAlert.signal,
                    func.count(IndicatorAlert.id).label("n"),
                )
                .where(
                    IndicatorAlert.ticker == ticker.upper(),
                    IndicatorAlert.created_at >= cutoff,
                )
                .group_by(IndicatorAlert.indicator, IndicatorAlert.signal)
            )
            rows = result.all()
            distinct_indicators = {r.indicator for r in rows}
            if len(distinct_indicators) < CONFLUENCE_MIN_INDICATORS:
                return

            # Direction: count alert frequencies by signal across all rows.
            signal_counts = {"bullish": 0, "bearish": 0, "neutral": 0}
            for r in rows:
                sig = (r.signal or "neutral").lower()
                if sig not in signal_counts:
                    sig = "neutral"
                signal_counts[sig] += int(r.n or 0)
            total = sum(signal_counts.values()) or 1
            bullish_pct = signal_counts["bullish"] / total
            bearish_pct = signal_counts["bearish"] / total
            if bullish_pct > 0.7:
                direction = "bullish"
            elif bearish_pct > 0.7:
                direction = "bearish"
            else:
                direction = "mixed"

            # Importance scaled to alert breadth: 3 indicators = 5,
            # 5+ = 7. Caps at 8 to leave headroom above for trade-outcome
            # memories that have actual P&L attached.
            n_indicators = len(distinct_indicators)
            importance = min(8, 4 + (n_indicators - 2))

            indicator_list = sorted(distinct_indicators)
            today = utcnow().date().isoformat()
            content = (
                f"Screener confluence on {ticker.upper()} "
                f"({today}, last {CONFLUENCE_LOOKBACK_HOURS}h): "
                f"{n_indicators} distinct indicators fired "
                f"({signal_counts['bullish']} bullish / "
                f"{signal_counts['bearish']} bearish / "
                f"{signal_counts['neutral']} neutral signals). "
                f"Direction: {direction}. "
                f"Indicators: {', '.join(indicator_list)}."
            )

            await save_memory(
                content=content,
                memory_type="observation",
                ticker=ticker.upper(),
                strategy_id=None,
                importance=importance,
                source="screener_confluence",
            )
            logger.info(
                f"Screener confluence memory saved: {ticker.upper()} "
                f"({n_indicators} indicators, {direction})"
            )
    except Exception as e:
        logger.debug(f"_maybe_save_screener_confluence({ticker}) failed: {e}")


@router.get("/alerts", response_model=list[AlertResponse])
async def get_alerts(
    ticker: str | None = None,
    indicator: str | None = None,
    signal: str | None = None,
    hours: int = 24,
    limit: int = 200,
    db: AsyncSession = Depends(get_db),
):
    cutoff = utcnow() - timedelta(hours=hours)
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
    cutoff = utcnow() - timedelta(hours=hours)

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
    force_refresh: bool = False,
    db: AsyncSession = Depends(get_db),
):
    """Trigger a Claude analysis for a specific ticker. Returns cached if fresh."""
    from sqlalchemy.orm import selectinload
    from app.models.trade import Trade as TradeModel
    from app.services.screener_ai import analyze_single_ticker
    from app.services.henry_cache import get_cached, set_cached, _make_hash

    hours = body.hours if body else 24
    ticker = ticker.upper()
    cutoff = utcnow() - timedelta(hours=hours)

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

    # Check cache — hash the alert IDs to detect new data
    cache_key = f"ticker_analysis:{ticker}"
    data_hash = _make_hash([a.id for a in alerts])

    if not force_refresh:
        cached = await get_cached(db, cache_key, max_age_hours=4, data_hash=data_hash)
        if cached:
            return TickerAnalysisResponse(ticker=ticker, generated_at=utcnow(), **cached)

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
    history_cutoff = utcnow() - timedelta(days=30)
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
    analysis = await analyze_single_ticker(
        ticker=ticker,
        alerts=alert_dicts,
        chart_data=chart_data,
        portfolio_positions=positions_list,
        trade_history=history_list,
    )

    # 6. Cache the result
    await set_cached(db, cache_key, "ticker_analysis", analysis, ticker=ticker, data_hash=data_hash)
    await db.commit()

    return TickerAnalysisResponse(
        ticker=ticker,
        generated_at=utcnow(),
        **analysis,
    )
