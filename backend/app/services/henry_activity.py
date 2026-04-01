"""
Henry Activity Log
==================
Tracks everything Henry does in a log that the user can see in real-time.
Stores entries in henry_context with context_type="activity".
Also provides a chat endpoint where the user can ask Henry about his decisions.
"""

import logging
from datetime import datetime, timedelta

from sqlalchemy import select, desc

from app.database import async_session
from app.models.henry_context import HenryContext

logger = logging.getLogger(__name__)

# Activity types for categorization
ACTIVITY_TYPES = {
    "scan_start": "🔍 Scanning",
    "scan_result": "📊 Scan Result",
    "scan_profile": "📋 Profile",
    "trade_execute": "💰 Trade",
    "trade_skip": "⏭️ Skipped",
    "trade_exit": "🚪 Exit",
    "pattern_detect": "🔎 Pattern",
    "analysis": "🧠 Analysis",
    "error": "❌ Error",
    "status": "ℹ️ Status",
}


async def log_activity(
    message: str,
    activity_type: str = "status",
    ticker: str | None = None,
    details: str | None = None,
) -> None:
    """Log a Henry activity entry. Non-blocking."""
    try:
        content = message
        if details:
            content = f"{message} | {details}"

        async with async_session() as db:
            entry = HenryContext(
                content=content[:500],
                context_type="activity",
                ticker=ticker,
                strategy=activity_type,  # Reuse strategy field for activity_type
                expires_at=datetime.utcnow() + timedelta(days=7),  # Activity log expires after 7 days
            )
            db.add(entry)
            await db.commit()
    except Exception as e:
        logger.debug(f"Failed to log activity: {e}")


async def get_activity_log(limit: int = 50, ticker: str | None = None) -> list[dict]:
    """Get recent activity log entries."""
    try:
        async with async_session() as db:
            query = (
                select(HenryContext)
                .where(HenryContext.context_type == "activity")
                .order_by(desc(HenryContext.created_at))
                .limit(limit)
            )
            if ticker:
                query = query.where(HenryContext.ticker == ticker)

            result = await db.execute(query)
            entries = result.scalars().all()

            return [
                {
                    "id": e.id,
                    "message": e.content,
                    "activity_type": e.strategy or "status",
                    "activity_label": ACTIVITY_TYPES.get(e.strategy or "status", e.strategy or "status"),
                    "ticker": e.ticker,
                    "created_at": (e.created_at.isoformat() + "Z") if e.created_at else None,
                }
                for e in entries
            ]
    except Exception:
        return []
