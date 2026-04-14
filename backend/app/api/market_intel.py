"""
Market Intel API
================
Single Gemini-grounded endpoint feeding the home dashboard's Sector,
Macro News, and The Play cards. See services/market_intel_ai.py for the
prompt, cache, and fallback logic.
"""
from fastapi import APIRouter, Query

from app.services.market_intel_ai import get_market_intel

router = APIRouter(prefix="/ai", tags=["ai"])


@router.get("/market-intel")
async def market_intel(refresh: bool = Query(False, description="Bypass cache")):
    return await get_market_intel(force_refresh=refresh)
