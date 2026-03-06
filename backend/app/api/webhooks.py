import json
import logging
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.schemas.webhook import WebhookPayload
from app.services.trade_processor import process_webhook
from app.services.price_service import price_service
from app.models import Trade, Trader, ConflictResolution

logger = logging.getLogger(__name__)

router = APIRouter()


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
        cutoff = datetime.utcnow() - timedelta(days=14)
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

        # Call AI conflict resolver
        from app.services.ai_service import resolve_conflict
        ai_result = resolve_conflict(conflicting_signals, recent_formatted)

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


@router.post("/webhook")
async def receive_webhook(payload: WebhookPayload, db: AsyncSession = Depends(get_db)):
    try:
        # Check for strategy conflicts before processing
        await _check_for_conflicts(payload, db)

        trade = await process_webhook(payload, db)

        # Register ticker for price tracking
        price_service.add_ticker(payload.ticker)

        return {
            "status": "ok",
            "trade_id": trade.id,
            "signal": payload.signal,
            "ticker": payload.ticker,
            "direction": payload.dir,
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
