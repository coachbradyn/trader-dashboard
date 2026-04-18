"""
Homepage API Endpoints
======================
Gemini-driven market context surfaces (news digest, upcoming events,
sector analysis) for the home/AI page. Each endpoint runs a Gemini
function-calling round-trip via ``ai_service`` and caches the result for
5 minutes so repeated homepage refreshes don't re-bill Gemini.
"""

import asyncio
import logging
import time

from fastapi import APIRouter, Query

from app.services.ai_service import news_digest, upcoming_events, sector_analysis

logger = logging.getLogger(__name__)
router = APIRouter()

_CACHE_TTL_SECONDS = 300

# Tiny in-process cache. Each entry is (text, generated_at_epoch).
# Acceptable because these surfaces are user-agnostic and the data
# updates much slower than the 5-minute window.
_cache: dict[str, tuple[str, float]] = {}
_locks: dict[str, asyncio.Lock] = {}


def _lock_for(key: str) -> asyncio.Lock:
    if key not in _locks:
        _locks[key] = asyncio.Lock()
    return _locks[key]


async def _cached(key: str, generator):
    now = time.time()
    entry = _cache.get(key)
    if entry and (now - entry[1]) < _CACHE_TTL_SECONDS:
        return entry[0], entry[1]

    async with _lock_for(key):
        entry = _cache.get(key)
        if entry and (time.time() - entry[1]) < _CACHE_TTL_SECONDS:
            return entry[0], entry[1]
        text = await generator()
        ts = time.time()
        _cache[key] = (text, ts)
        return text, ts


def _envelope(text: str, generated_at: float) -> dict:
    return {
        "text": text,
        "generated_at": generated_at,
        "cache_ttl_seconds": _CACHE_TTL_SECONDS,
    }


@router.get("/homepage/news-digest")
async def get_news_digest():
    """Top market-moving stories of the morning, narrated by Gemini."""
    text, ts = await _cached("news_digest", news_digest)
    return _envelope(text, ts)


@router.get("/homepage/upcoming-events")
async def get_upcoming_events(window: int = Query(7, ge=1, le=30, description="Days ahead")):
    """High-impact upcoming events (earnings, econ, dividends, splits, IPOs)."""
    key = f"upcoming_events:{window}"
    text, ts = await _cached(key, lambda: upcoming_events(window))
    return _envelope(text, ts)


@router.get("/homepage/sector-analysis")
async def get_sector_analysis():
    """Sector performance + rotation read, narrated by Gemini."""
    text, ts = await _cached("sector_analysis", sector_analysis)
    return _envelope(text, ts)
