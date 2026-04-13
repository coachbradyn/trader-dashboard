"""
Hyperparameter Optimization API (Phase 7, System 10)
=====================================================

Read + admin endpoints for the Bayesian optimizer:
  GET  /api/optimization/status                 — current config + log + suggestion
  GET  /api/optimization/observations           — paginated observation history
  POST /api/optimization/admin/run-now          — trigger weekly cycle on demand
  POST /api/optimization/admin/adopt            — apply a suggestion or arbitrary config
  POST /api/optimization/admin/reject           — mark current suggestion rejected

Manual-approval flow per the brief — auto-apply within "safe bounds" is
a future enhancement, not in the initial build.
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select, desc, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import HenryStats
from app.services.hyperparameter_space import PARAMS, defaults

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/optimization", tags=["optimization"])


def _require_admin(secret: str) -> None:
    """Same pattern as /api/memory/admin/* — config-driven secret."""
    from app.config import get_settings
    settings = get_settings()
    if not settings.admin_secret:
        raise HTTPException(
            status_code=503,
            detail="ADMIN_SECRET not configured on the server.",
        )
    if secret != settings.admin_secret:
        raise HTTPException(status_code=403, detail="Forbidden")


def _serialize_params() -> list[dict]:
    return [
        {
            "name": p.name,
            "kind": p.kind,
            "low": p.low,
            "high": p.high,
            "default": p.default,
            "consumer": p.consumer,
            "notes": p.notes,
        }
        for p in PARAMS
    ]


@router.get("/status")
async def optimization_status(db: AsyncSession = Depends(get_db)):
    """
    Snapshot of everything the user needs to evaluate the optimizer:
      - search space + defaults
      - currently-active runtime config (which may differ from defaults
        if a suggestion was adopted)
      - observation count + best-so-far
      - latest suggestion (if any)
    """
    from app.services.runtime_config import all_current

    current = await all_current()

    # Pull observation summary stats
    obs_count_q = await db.execute(
        select(func.count(HenryStats.id)).where(
            HenryStats.stat_type == "bayesian_observation"
        )
    )
    n_obs = int(obs_count_q.scalar() or 0)

    obs_with_obj = list(
        (
            await db.execute(
                select(HenryStats)
                .where(HenryStats.stat_type == "bayesian_observation")
                .where(HenryStats.data.is_not(None))
                .order_by(desc(HenryStats.computed_at))
                .limit(20)
            )
        ).scalars().all()
    )
    valid = [
        r for r in obs_with_obj
        if r.data and r.data.get("objective")
        and r.data["objective"].get("adjusted_sharpe") is not None
    ]
    best = None
    if valid:
        best_row = max(valid, key=lambda r: r.data["objective"]["adjusted_sharpe"])
        best = {
            "objective": best_row.data["objective"],
            "params": best_row.data["params"],
            "ts": best_row.data.get("ts"),
        }
    latest = valid[0].data if valid else None

    suggestion_row = (
        await db.execute(
            select(HenryStats)
            .where(HenryStats.stat_type == "bayesian_suggestion")
            .order_by(desc(HenryStats.computed_at))
            .limit(1)
        )
    ).scalar_one_or_none()

    runtime_row = (
        await db.execute(
            select(HenryStats)
            .where(HenryStats.stat_type == "runtime_config")
            .order_by(desc(HenryStats.computed_at))
            .limit(1)
        )
    ).scalar_one_or_none()

    return {
        "search_space": _serialize_params(),
        "defaults": defaults(),
        "current_config": current,
        "current_config_source": (
            (runtime_row.data or {}).get("source", "defaults")
            if runtime_row else "defaults"
        ),
        "current_config_adopted_at": (
            (runtime_row.data or {}).get("adopted_at")
            if runtime_row else None
        ),
        "n_observations": n_obs,
        "n_observations_with_objective": len(valid),
        "latest_observation": latest,
        "best_observation": best,
        "latest_suggestion": (suggestion_row.data if suggestion_row else None),
    }


@router.get("/observations")
async def optimization_observations(
    limit: int = Query(50, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
):
    """Paginated observation log — most recent first. Useful for plotting
    objective trajectory over time."""
    rows = list(
        (
            await db.execute(
                select(HenryStats)
                .where(HenryStats.stat_type == "bayesian_observation")
                .order_by(desc(HenryStats.computed_at))
                .limit(limit)
            )
        ).scalars().all()
    )
    return {
        "count": len(rows),
        "observations": [
            {
                "id": r.id,
                "computed_at": r.computed_at.isoformat() + "Z" if r.computed_at else None,
                **(r.data or {}),
            }
            for r in rows
        ],
    }


@router.post("/admin/run-now")
async def admin_run_now(
    secret: str = Query(...),
    db: AsyncSession = Depends(get_db),
):
    """Trigger the weekly cycle synchronously. Useful for shaking out
    config issues without waiting until Sunday 22:00 ET."""
    _require_admin(secret)
    from app.services.bayesian_optimizer import run_weekly_cycle
    try:
        summary = await run_weekly_cycle(db)
        return {"ok": True, "summary": summary}
    except Exception as e:
        logger.exception("optimization run-now failed")
        return {"ok": False, "reason": f"{type(e).__name__}: {str(e)[:300]}"}


class AdoptRequest(BaseModel):
    # Either a full params dict, or just "adopt the latest suggestion".
    params: dict[str, float] | None = None
    adopt_latest_suggestion: bool = False
    notes: str | None = None


@router.post("/admin/adopt")
async def admin_adopt(
    body: AdoptRequest,
    secret: str = Query(...),
    db: AsyncSession = Depends(get_db),
):
    """
    Make a config active. Either:
      - pass `adopt_latest_suggestion=true` to adopt the most recent
        bayesian_suggestion row (the typical flow), OR
      - pass an explicit `params` dict for manual override.

    All values clamped through their HyperParam.clamp before persistence.
    """
    _require_admin(secret)
    from app.services.runtime_config import adopt
    from app.utils.utc import utcnow

    if body.adopt_latest_suggestion:
        row = (
            await db.execute(
                select(HenryStats)
                .where(HenryStats.stat_type == "bayesian_suggestion")
                .order_by(desc(HenryStats.computed_at))
                .limit(1)
            )
        ).scalar_one_or_none()
        if not row or not row.data:
            return {"ok": False, "reason": "No suggestion to adopt."}
        params = (row.data or {}).get("params") or {}
        params["__source__"] = "bayesian_suggestion"
        cleaned = await adopt(params)
        # Mark the suggestion adopted in place
        new_data = dict(row.data)
        new_data["adopted"] = True
        new_data["adopted_at"] = utcnow().isoformat() + "Z"
        if body.notes:
            new_data["notes"] = body.notes
        from sqlalchemy import update
        await db.execute(
            update(HenryStats).where(HenryStats.id == row.id).values(data=new_data)
        )
        await db.commit()
        return {"ok": True, "adopted": cleaned, "source": "bayesian_suggestion"}

    if not body.params:
        return {"ok": False, "reason": "Provide params or set adopt_latest_suggestion=true."}
    body.params["__source__"] = "manual"
    cleaned = await adopt(body.params)
    return {"ok": True, "adopted": cleaned, "source": "manual"}


@router.post("/admin/reject")
async def admin_reject(
    secret: str = Query(...),
    notes: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """Mark the current suggestion as rejected so the UI doesn't keep
    nagging. Does not affect the runtime config."""
    _require_admin(secret)
    from sqlalchemy import update
    from app.utils.utc import utcnow

    row = (
        await db.execute(
            select(HenryStats)
            .where(HenryStats.stat_type == "bayesian_suggestion")
            .order_by(desc(HenryStats.computed_at))
            .limit(1)
        )
    ).scalar_one_or_none()
    if not row or not row.data:
        return {"ok": False, "reason": "No suggestion to reject."}
    new_data = dict(row.data)
    new_data["rejected"] = True
    new_data["rejected_at"] = utcnow().isoformat() + "Z"
    if notes:
        new_data["notes"] = notes
    await db.execute(
        update(HenryStats).where(HenryStats.id == row.id).values(data=new_data)
    )
    await db.commit()
    return {"ok": True}
