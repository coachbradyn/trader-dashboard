"""
Scanner API Routes
==================
Endpoints for the FMP-powered stock scanner.
"""

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/scanner", tags=["scanner"])


# ── Request Schemas ─────────────────────────────────────────────────

class UpdateCriteriaRequest(BaseModel):
    min_price: float | None = None
    min_volume: int | None = None
    min_market_cap: int | None = None
    max_market_cap: int | None = None
    sectors: list[str] | None = None
    technical_filters: dict | None = None
    fundamental_filters: dict | None = None


# ── GET /scanner/results ────────────────────────────────────────────

@router.get("/results")
async def get_scanner_results(limit: int = 20):
    """Get latest scanner opportunities (pending OPPORTUNITY actions)."""
    from app.services.scanner_service import get_scanner_results as _get_results
    results = await _get_results(limit=limit)
    return {"results": results, "count": len(results)}


# ── GET /scanner/history ────────────────────────────────────────────

@router.get("/history")
async def get_scanner_history(limit: int = 50):
    """Get past scanner results with outcomes."""
    from app.services.scanner_service import get_scanner_history as _get_history
    history = await _get_history(limit=limit)
    return {"history": history, "count": len(history)}


# ── POST /scanner/run ───────────────────────────────────────────────

@router.post("/run")
async def run_scanner_manual():
    """Trigger a scanner run. Runs synchronously and returns results."""
    from app.services.scanner_service import run_scanner
    from app.services.fmp_service import get_api_usage

    usage = get_api_usage()
    if usage["throttled"]:
        raise HTTPException(429, detail="FMP API rate limit reached. Scanner cannot run.")

    try:
        results = await run_scanner()
        return {
            "status": "complete",
            "message": f"Scanner found {len(results)} opportunities.",
            "count": len(results),
            "fmp_usage": get_api_usage(),
        }
    except Exception as e:
        logger.error(f"Manual scanner run failed: {e}", exc_info=True)
        return {
            "status": "error",
            "message": f"Scanner failed: {type(e).__name__}: {str(e)[:200]}",
            "fmp_usage": get_api_usage(),
        }


# ── GET /scanner/criteria ──────────────────────────────────────────

@router.get("/criteria")
async def get_criteria():
    """Get current screening criteria."""
    from app.services.scanner_service import get_scanner_criteria
    criteria = await get_scanner_criteria()
    return {"criteria": criteria}


# ── PUT /scanner/criteria ──────────────────────────────────────────

@router.put("/criteria")
async def update_criteria(req: UpdateCriteriaRequest):
    """Update screening criteria. Partial updates are merged with defaults."""
    from app.services.scanner_service import update_scanner_criteria
    update_data = {k: v for k, v in req.model_dump().items() if v is not None}
    if not update_data:
        raise HTTPException(400, detail="No criteria fields provided")
    updated = await update_scanner_criteria(update_data)
    return {"criteria": updated, "message": "Criteria updated"}


# ── GET /scanner/stats ─────────────────────────────────────────────

@router.get("/stats")
async def get_scanner_stats():
    """Get scanner accuracy and performance stats."""
    from app.services.scanner_service import get_scanner_stats as _get_stats
    stats = await _get_stats()
    return stats


# ── SCAN PROFILES ──────────────────────────────────────────────────

@router.get("/profiles")
async def get_scan_profiles_route():
    """Get all scan profiles with their criteria and market conditions."""
    from app.services.scanner_service import get_scan_profiles
    profiles = await get_scan_profiles()
    return {"profiles": profiles}


class SaveProfileRequest(BaseModel):
    id: str
    name: str
    description: str = ""
    enabled: bool = True
    market_conditions: dict = {}
    criteria: dict = {}


@router.put("/profiles/{profile_id}")
async def save_profile_route(profile_id: str, req: SaveProfileRequest):
    """Create or update a scan profile."""
    from app.services.scanner_service import save_single_profile
    profile_data = req.model_dump()
    profile_data["id"] = profile_id
    profiles = await save_single_profile(profile_data)
    return {"profiles": profiles, "message": f"Profile '{req.name}' saved"}


@router.delete("/profiles/{profile_id}")
async def delete_profile_route(profile_id: str):
    """Delete a scan profile (built-in profiles are disabled instead)."""
    from app.services.scanner_service import delete_profile
    profiles = await delete_profile(profile_id)
    return {"profiles": profiles, "message": f"Profile '{profile_id}' removed"}


@router.post("/run/{profile_id}")
async def run_scanner_with_profile(profile_id: str):
    """Run scanner with a specific profile. Returns immediately, runs in background."""
    from app.services.scanner_service import get_scan_profiles, run_scanner
    from app.services.fmp_service import get_api_usage

    usage = get_api_usage()
    if usage["throttled"]:
        raise HTTPException(429, detail="FMP API rate limit reached")

    profiles = await get_scan_profiles()
    profile = next((p for p in profiles if p["id"] == profile_id), None)
    if not profile:
        raise HTTPException(404, detail=f"Profile '{profile_id}' not found")

    async def _run():
        try:
            await run_scanner(
                profile_criteria=profile.get("criteria"),
                profile_name=profile.get("name", profile_id),
            )
        except Exception as e:
            logger.error(f"Profile scanner run failed: {e}")

    asyncio.create_task(_run())
    return {
        "status": "running",
        "profile": profile_id,
        "message": f"Running '{profile.get('name', profile_id)}' scan in background",
    }


# ── GET /scanner/fmp-usage ─────────────────────────────────────────

@router.get("/fmp-usage")
async def get_fmp_usage():
    """Get FMP API usage for today."""
    from app.services.fmp_service import get_api_usage
    return get_api_usage()
