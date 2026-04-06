import asyncio
import json
import time
from app.utils.utc import utcnow
import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db, async_session
from app.schemas.webhook import WebhookPayload
from app.services.trade_processor import process_webhook
from app.services.price_service import price_service
from app.models import Trade, Trader, ConflictResolution
from app.utils.dedup import make_webhook_fingerprint, is_duplicate_webhook

logger = logging.getLogger(__name__)

router = APIRouter()

# ---------------------------------------------------------------------------
# Per-trader token-bucket rate limiter (in-process, no external deps)
# ---------------------------------------------------------------------------
MAX_WEBHOOKS_PER_MINUTE = 60
_rate_buckets: dict[str, list[float]] = defaultdict(list)


def _check_rate_limit(trader_id: str) -> None:
    """Raise HTTP 429 if *trader_id* exceeds MAX_WEBHOOKS_PER_MINUTE."""
    now = time.monotonic()
    bucket = _rate_buckets[trader_id]
    # Prune timestamps older than 60 s
    cutoff = now - 60
    _rate_buckets[trader_id] = bucket = [ts for ts in bucket if ts > cutoff]
    if len(bucket) >= MAX_WEBHOOKS_PER_MINUTE:
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded for trader {trader_id}: max {MAX_WEBHOOKS_PER_MINUTE}/min",
        )
    bucket.append(now)


# ---------------------------------------------------------------------------
# AI concurrency semaphore — at most 3 concurrent AI calls from webhooks
# ---------------------------------------------------------------------------
_ai_semaphore = asyncio.Semaphore(3)

# ---------------------------------------------------------------------------
# Background-task tracking set — prevents GC from dropping fire-and-forget tasks
# ---------------------------------------------------------------------------
_background_tasks: set[asyncio.Task] = set()


async def _check_for_conflicts(payload: WebhookPayload, db: AsyncSession):
    """
    When a new entry signal arrives, check if any other strategy has an
    opposing open position on the same ticker. If so, call the AI conflict
    resolver and store the result.
    """
    if payload.signal != "entry":
        return

    try:
        # Find opposing open trades on the same ticker from different traders
        opposing_dir = "short" if payload.dir == "long" else "long"
        result = await db.execute(
            select(Trade)
            .options(selectinload(Trade.trader))
            .where(
                Trade.ticker == payload.ticker,
                Trade.status == "open",
                Trade.direction == opposing_dir,
            )
        )
        opposing_trades = result.scalars().all()

        if not opposing_trades:
            return

        # Filter to trades from different strategies
        opposing_from_others = [
            t for t in opposing_trades
            if t.trader.trader_id != payload.trader
        ]

        if not opposing_from_others:
            return

        # Build conflicting signals list
        conflicting_signals = [
            {
                "trader": payload.trader,
                "dir": payload.dir,
                "ticker": payload.ticker,
                "price": payload.price,
                "sig": payload.sig or 0,
                "adx": payload.adx or 0,
                "atr": payload.atr or 0,
            }
        ]

        strategies_involved = [payload.trader]
        for t in opposing_from_others:
            conflicting_signals.append({
                "trader": t.trader.trader_id,
                "dir": t.direction,
                "ticker": t.ticker,
                "price": t.entry_price,
                "sig": t.entry_signal_strength or 0,
                "adx": t.entry_adx or 0,
                "atr": t.entry_atr or 0,
            })
            if t.trader.trader_id not in strategies_involved:
                strategies_involved.append(t.trader.trader_id)

        # Get recent trade history for context
        cutoff = utcnow() - timedelta(days=14)
        result = await db.execute(
            select(Trade)
            .options(selectinload(Trade.trader))
            .where(Trade.created_at >= cutoff, Trade.status == "closed")
            .order_by(Trade.created_at.desc())
            .limit(100)
        )
        recent_trades = result.scalars().all()

        recent_formatted = []
        for rt in recent_trades:
            recent_formatted.append({
                "signal": "exit",
                "trader": rt.trader.trader_id,
                "dir": rt.direction,
                "ticker": rt.ticker,
                "price": rt.exit_price or rt.entry_price,
                "pnl_pct": rt.pnl_percent or 0,
                "bars_in_trade": rt.bars_in_trade or 0,
                "exit_reason": rt.exit_reason or "unknown",
                "tf": rt.timeframe or "?",
            })

        # Call AI conflict resolver (under semaphore)
        from app.services.ai_service import resolve_conflict
        async with _ai_semaphore:
            ai_result = await resolve_conflict(conflicting_signals, recent_formatted)

        # Store the conflict resolution
        conflict = ConflictResolution(
            ticker=payload.ticker,
            strategies=json.dumps(strategies_involved),
            recommendation=ai_result.get("recommendation", "STAY_FLAT"),
            confidence=ai_result.get("confidence", 5),
            reasoning=ai_result.get("reasoning", ""),
            signals=conflicting_signals,
        )
        db.add(conflict)
        # Will be committed by the caller

    except Exception as e:
        logger.warning(f"Conflict detection failed (non-blocking): {e}")


@router.post("/webhook/replay/{inbox_id}")
async def replay_webhook(inbox_id: str):
    """Replay a failed/pending webhook from the inbox."""
    try:
        from app.models.webhook_inbox import WebhookInbox
        async with async_session() as db:
            result = await db.execute(select(WebhookInbox).where(WebhookInbox.id == inbox_id))
            entry = result.scalar_one_or_none()
            if not entry:
                raise HTTPException(404, "Inbox entry not found")
            if entry.status == "processed":
                return {"status": "already_processed", "id": inbox_id}

            payload = WebhookPayload(**entry.payload)
            trade = await process_webhook(payload, db)
            entry.status = "processed"
            entry.processed_at = utcnow()
            await db.commit()
            return {"status": "replayed", "trade_id": trade.id, "ticker": trade.ticker}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(400, f"Replay failed: {e}")


@router.get("/webhook/failed")
async def list_failed_webhooks():
    """List all failed/pending webhook inbox entries for manual replay."""
    try:
        from app.models.webhook_inbox import WebhookInbox
        async with async_session() as db:
            result = await db.execute(
                select(WebhookInbox)
                .where(WebhookInbox.status.in_(["failed", "pending", "validation_error"]))
                .order_by(WebhookInbox.created_at.desc())
                .limit(50)
            )
            entries = result.scalars().all()
            return [
                {
                    "id": e.id,
                    "status": e.status,
                    "payload": e.payload,
                    "error": e.error_message,
                    "created_at": e.created_at.isoformat() + "Z" if e.created_at else None,
                }
                for e in entries
            ]
    except Exception as e:
        return []


@router.post("/webhook")
async def receive_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    # Auto-route: if payload has "indicator" but no "trader", it's a scanner alert
    raw_body = await request.body()
    try:
        raw_json = json.loads(raw_body) if raw_body else {}
    except Exception:
        raw_json = {}

    if "indicator" in raw_json and "trader" not in raw_json:
        # Forward to screener webhook handler
        from app.schemas.screener import ScreenerWebhookPayload
        from app.api.screener import screener_webhook
        screener_payload = ScreenerWebhookPayload(**raw_json)
        return await screener_webhook(screener_payload, db)

    payload = WebhookPayload(**raw_json)
    # 1. Rate-limit check (per trader) — fast, before any DB work
    _check_rate_limit(payload.trader)

    # 2. Idempotency check — reject duplicate signals
    fp = make_webhook_fingerprint(
        trader=payload.trader,
        ticker=payload.ticker,
        signal=payload.signal,
        direction=payload.dir,
        price=payload.price,
        unix_time=payload.time or 0,
    )
    if is_duplicate_webhook(fp):
        return {"status": "duplicate", "trade_id": None}

    # Write-ahead: persist to inbox before processing (crash-safe)
    inbox_id = None
    try:
        from app.models.webhook_inbox import WebhookInbox
        inbox_entry = WebhookInbox(
            fingerprint=fp,
            payload=payload.model_dump(),
            status="pending",
        )
        db.add(inbox_entry)
        await db.flush()
        inbox_id = inbox_entry.id
    except Exception:
        pass  # Inbox table may not exist yet — continue without it

    try:
        # Process the trade FIRST — this is the critical path
        trade = await process_webhook(payload, db)

        # Mark inbox entry as processed
        if inbox_id:
            try:
                from app.models.webhook_inbox import WebhookInbox as _WI
                inbox_result = await db.execute(
                    select(_WI).where(_WI.id == inbox_id)
                )
                inbox_obj = inbox_result.scalar_one_or_none()
                if inbox_obj:
                    inbox_obj.status = "processed"
                    inbox_obj.processed_at = utcnow()
            except Exception:
                pass

        # Register ticker for price tracking
        price_service.add_ticker(payload.ticker)

        # Everything else runs in the background AFTER we return 200 to TradingView
        async def _bg_tasks():
            """Run all non-critical tasks after responding to TradingView."""
            try:
                # Conflict detection (involves AI call — can be slow)
                try:
                    from app.database import async_session as _as
                    async with _as() as bg_db:
                        await _check_for_conflicts(payload, bg_db)
                        await bg_db.commit()
                except Exception as e:
                    logger.debug(f"Conflict detection failed: {e}")

                # Invalidate cached analysis
                try:
                    from app.database import async_session as _as2
                    from app.services.henry_cache import invalidate_by_ticker
                    async with _as2() as cache_db:
                        await invalidate_by_ticker(cache_db, payload.ticker)
                        await cache_db.commit()
                except Exception:
                    pass

                # Check watchlist summary staleness
                try:
                    from app.services.watchlist_ai import check_and_regenerate_if_stale
                    await check_and_regenerate_if_stale(payload.ticker)
                except Exception:
                    pass

                # Route to AI portfolio evaluation (under semaphore)
                try:
                    from app.services.ai_portfolio import evaluate_signal_for_ai_portfolio, process_exit_for_ai_portfolio
                    from app.database import async_session as _as3
                    async with _as3() as trader_db:
                        trader_result = await trader_db.execute(
                            select(Trader).where(Trader.trader_id == payload.trader)
                        )
                        trader_obj = trader_result.scalar_one_or_none()
                    if trader_obj:
                        async with _ai_semaphore:
                            if payload.signal == "entry":
                                await evaluate_signal_for_ai_portfolio(trade, trader_obj, payload.model_dump())
                            elif payload.signal == "exit":
                                await process_exit_for_ai_portfolio(trade, trader_obj)
                except Exception as e:
                    logger.warning(f"AI portfolio routing failed: {e}")

            except Exception as e:
                logger.error(f"Webhook background tasks failed: {e}")

        task = asyncio.create_task(_bg_tasks())
        _background_tasks.add(task)
        task.add_done_callback(_background_tasks.discard)

        # Return immediately — TradingView gets 200 OK fast
        return {
            "status": "ok",
            "trade_id": trade.id,
            "signal": payload.signal,
            "ticker": payload.ticker,
            "direction": payload.dir,
        }
    except ValueError as e:
        if inbox_id:
            try:
                from app.models.webhook_inbox import WebhookInbox as _WI2
                inbox_result = await db.execute(select(_WI2).where(_WI2.id == inbox_id))
                inbox_obj = inbox_result.scalar_one_or_none()
                if inbox_obj:
                    inbox_obj.status = "failed"
                    inbox_obj.error_message = str(e)[:500]
                    inbox_obj.processed_at = utcnow()
                    await db.commit()
            except Exception:
                pass
        raise HTTPException(status_code=400, detail=str(e))
