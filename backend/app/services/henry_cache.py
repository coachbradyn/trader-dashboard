"""
Henry Cache Service
=====================
Caches all of Henry's AI outputs. Returns cached results when data hasn't changed.
Invalidated by: new webhooks, manual refresh, scheduled refresh.
"""

import hashlib
from app.utils.utc import utcnow
import json
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.henry_cache import HenryCache

logger = logging.getLogger(__name__)

# Max cache age per type (hours). After this, cache is considered stale even if
# no new data arrived.
MAX_AGE_HOURS = {
    "ticker_analysis": 4,
    "signal_eval": 1,
    "scheduled_review": 12,
    "screener_batch": 1,
}
DEFAULT_MAX_AGE = 4


def _make_hash(data: dict | list | str) -> str:
    """Create a short hash of the input data to detect changes."""
    raw = json.dumps(data, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


async def get_cached(
    db: AsyncSession,
    cache_key: str,
    max_age_hours: float | None = None,
    data_hash: str | None = None,
) -> dict | None:
    """
    Return cached result if fresh. Returns None if:
    - No cache entry exists
    - Cache is marked stale
    - Cache is older than max_age_hours
    - data_hash doesn't match (underlying data changed)
    """
    result = await db.execute(
        select(HenryCache).where(HenryCache.cache_key == cache_key)
    )
    entry = result.scalar_one_or_none()
    if not entry:
        return None

    # Check staleness flag
    if entry.is_stale:
        return None

    # Check age
    age_limit = max_age_hours or MAX_AGE_HOURS.get(entry.cache_type, DEFAULT_MAX_AGE)
    age_hours = (utcnow() - entry.generated_at).total_seconds() / 3600
    if age_hours > age_limit:
        return None

    # Check if underlying data changed
    if data_hash and entry.data_hash and entry.data_hash != data_hash:
        return None

    return entry.content


async def set_cached(
    db: AsyncSession,
    cache_key: str,
    cache_type: str,
    content: dict,
    ticker: str | None = None,
    strategy: str | None = None,
    data_hash: str | None = None,
) -> None:
    """Store or update a cache entry."""
    result = await db.execute(
        select(HenryCache).where(HenryCache.cache_key == cache_key)
    )
    existing = result.scalar_one_or_none()

    if existing:
        existing.content = content
        existing.cache_type = cache_type
        existing.ticker = ticker
        existing.strategy = strategy
        existing.is_stale = False
        existing.generated_at = utcnow()
        existing.data_hash = data_hash
    else:
        entry = HenryCache(
            cache_key=cache_key,
            cache_type=cache_type,
            content=content,
            ticker=ticker,
            strategy=strategy,
            data_hash=data_hash,
        )
        db.add(entry)


async def invalidate_by_ticker(db: AsyncSession, ticker: str) -> int:
    """Mark all cache entries for a ticker as stale. Called on new webhooks/alerts."""
    result = await db.execute(
        update(HenryCache)
        .where(HenryCache.ticker == ticker)
        .values(is_stale=True)
    )
    return result.rowcount


async def invalidate_by_type(db: AsyncSession, cache_type: str) -> int:
    """Mark all cache entries of a type as stale. Called on scheduled refreshes."""
    result = await db.execute(
        update(HenryCache)
        .where(HenryCache.cache_type == cache_type)
        .values(is_stale=True)
    )
    return result.rowcount


async def invalidate_all(db: AsyncSession) -> int:
    """Mark everything stale. Called on manual full refresh."""
    result = await db.execute(
        update(HenryCache).values(is_stale=True)
    )
    return result.rowcount


async def cleanup_old_cache(db: AsyncSession, days: int = 7) -> int:
    """Delete cache entries older than N days."""
    cutoff = utcnow() - timedelta(days=days)
    result = await db.execute(
        delete(HenryCache).where(HenryCache.generated_at < cutoff)
    )
    return result.rowcount
