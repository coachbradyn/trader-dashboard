"""
Runtime Hyperparameter Config (Phase 7, System 10)
===================================================

Single read path for any consumer that wants the currently-active value
of a tunable hyperparameter. Reads from the latest
HenryStats(stat_type='runtime_config') row; falls back to the defaults
defined in hyperparameter_space.

Cached in-process (60s TTL) so per-call lookups during prompt building
are essentially free. The Bayesian "adopt" admin endpoint invalidates
the cache so changes take effect on the very next call without restart.

This module is intentionally thin — it does NOT decide what's best,
only what's currently configured. The optimizer writes; consumers read.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Optional

from app.services.hyperparameter_space import PARAMS, by_name, defaults

logger = logging.getLogger(__name__)


_CACHE: dict[str, Any] = {
    "values": None,         # {name: value} or None
    "loaded_at": 0.0,
}
_CACHE_TTL_SECONDS = 60.0


def invalidate_cache() -> None:
    """Force re-read on the next get() call. Invoke after adopting a
    new config so consumers pick up the change immediately."""
    _CACHE["values"] = None
    _CACHE["loaded_at"] = 0.0


async def _load() -> dict[str, float]:
    """Resolve {name: value}: HenryStats row → defaults fallback."""
    try:
        from sqlalchemy import select
        from app.database import async_session
        from app.models import HenryStats
        async with async_session() as db:
            row = (
                await db.execute(
                    select(HenryStats)
                    .where(HenryStats.stat_type == "runtime_config")
                    .order_by(HenryStats.computed_at.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
    except Exception as e:
        logger.debug(f"runtime_config _load failed: {e}")
        row = None

    base = defaults()
    if row and row.data:
        stored = (row.data or {}).get("params") or {}
        for name in base.keys():
            if name in stored:
                p = by_name(name)
                if p is not None:
                    base[name] = p.clamp(stored.get(name))
    return base


async def get_async(name: str) -> float:
    """Async getter — preferred from async paths."""
    if (
        _CACHE["values"] is not None
        and time.time() - _CACHE["loaded_at"] < _CACHE_TTL_SECONDS
    ):
        v = _CACHE["values"].get(name)
        if v is not None:
            return v
    values = await _load()
    _CACHE["values"] = values
    _CACHE["loaded_at"] = time.time()
    return values.get(name, defaults().get(name, 0.0))


def get_sync(name: str) -> float:
    """Sync getter — for module-level constants and sync code paths.
    Returns the in-cache value or the default; never blocks. Cache is
    populated by the async path or by warm() at startup."""
    if _CACHE["values"] is not None:
        v = _CACHE["values"].get(name)
        if v is not None:
            return v
    p = by_name(name)
    return p.default if p else 0.0


async def warm() -> None:
    """Pre-populate the cache. Safe to call from app startup."""
    values = await _load()
    _CACHE["values"] = values
    _CACHE["loaded_at"] = time.time()


async def all_current() -> dict[str, float]:
    """Snapshot of every parameter and its currently-active value.
    Used by the optimizer (to record the observation) and the
    /api/optimization/status endpoint."""
    if (
        _CACHE["values"] is None
        or time.time() - _CACHE["loaded_at"] >= _CACHE_TTL_SECONDS
    ):
        await warm()
    return dict(_CACHE["values"] or defaults())


async def adopt(values: dict[str, Any]) -> dict[str, float]:
    """Persist a new active config. Clamps every value through the
    HyperParam definition so adopted configs stay valid even when an
    out-of-range suggestion sneaks in. Returns the saved dict."""
    from app.services.hyperparameter_space import clamp_config
    from app.utils.utc import utcnow
    from sqlalchemy import select, delete
    from app.database import async_session
    from app.models import HenryStats

    cleaned = clamp_config(values)
    payload = {
        "params": cleaned,
        "adopted_at": utcnow().isoformat() + "Z",
        "source": values.get("__source__", "manual"),
    }
    async with async_session() as db:
        # Single-row upsert pattern matching memory_clusters
        await db.execute(
            delete(HenryStats).where(HenryStats.stat_type == "runtime_config")
        )
        db.add(HenryStats(
            stat_type="runtime_config",
            strategy=None,
            ticker=None,
            portfolio_id=None,
            data=payload,
            period_days=0,
            computed_at=utcnow(),
        ))
        await db.commit()
    invalidate_cache()
    return cleaned
