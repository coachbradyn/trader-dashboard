"""
AI-scoped endpoints for the home dashboard
==========================================
Two endpoints serve the /ai home page:

- GET /api/ai/market-intel
    Gemini-grounded sector + macro + play. See services/market_intel_ai.py
    for prompt and cache logic.

- GET /api/ai/home-snapshot
    Consolidates the three fetches the home page used to poll separately
    (portfolios, pending actions, action stats). One round-trip instead of
    three, which — combined with the frontend's visibility-aware pause —
    cuts background polling by ~3x for idle tabs.
"""
import asyncio
import logging

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.services.market_intel_ai import get_market_intel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ai", tags=["ai"])


@router.get("/market-intel")
async def market_intel(refresh: bool = Query(False, description="Bypass cache")):
    return await get_market_intel(force_refresh=refresh)


@router.get("/home-snapshot")
async def home_snapshot(db: AsyncSession = Depends(get_db)):
    """Return the three data blobs the home page polls. Runs the underlying
    handlers in parallel so the consolidated endpoint isn't slower than
    doing them client-side — it's faster because it avoids 3 TLS handshakes
    and 3 auth middleware passes per poll.

    Each handler is wrapped in try/except so one failure doesn't blank the
    other two fields on the client.
    """
    from app.api.portfolios import get_portfolios
    from app.api.portfolio_manager import list_actions, get_action_stats

    async def _safe(coro, default):
        try:
            return await coro
        except Exception as e:
            logger.warning(f"home-snapshot sub-call failed: {e}")
            return default

    portfolios, actions, action_stats = await asyncio.gather(
        _safe(get_portfolios(db), []),
        _safe(list_actions(status="pending", portfolio_id=None, limit=50, db=db), []),
        _safe(get_action_stats(db), None),
    )
    return {
        "portfolios": portfolios,
        "actions": actions,
        "action_stats": action_stats,
    }
