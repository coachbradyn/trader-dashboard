"""Henry Memory Management API — CRUD for HenryMemory entries."""

import logging
from app.utils.utc import utcnow

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select, func, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import HenryMemory

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/memory", tags=["memory"])


class MemoryUpdate(BaseModel):
    importance: int | None = None
    content: str | None = None


@router.get("")
async def list_memories(
    memory_type: str | None = None,
    source: str | None = None,
    ticker: str | None = None,
    strategy_id: str | None = None,
    min_importance: int = 0,
    limit: int = Query(50, le=200),
    offset: int = Query(0),
    db: AsyncSession = Depends(get_db),
):
    """List memories with optional filters."""
    query = select(HenryMemory).where(HenryMemory.importance >= min_importance)

    if memory_type:
        query = query.where(HenryMemory.memory_type == memory_type)
    if source:
        query = query.where(HenryMemory.source == source)
    if ticker:
        query = query.where(HenryMemory.ticker == ticker.upper())
    if strategy_id:
        query = query.where(HenryMemory.strategy_id == strategy_id)

    query = query.order_by(desc(HenryMemory.importance), desc(HenryMemory.updated_at))
    query = query.offset(offset).limit(limit)

    result = await db.execute(query)
    memories = result.scalars().all()

    return [
        {
            "id": m.id,
            "memory_type": m.memory_type,
            "strategy_id": m.strategy_id,
            "ticker": m.ticker,
            "content": m.content,
            "importance": m.importance,
            "reference_count": m.reference_count,
            "validated": m.validated,
            "source": m.source,
            "created_at": m.created_at.isoformat() + "Z" if m.created_at else None,
            "updated_at": m.updated_at.isoformat() + "Z" if m.updated_at else None,
        }
        for m in memories
    ]


@router.get("/stats")
async def memory_stats(db: AsyncSession = Depends(get_db)):
    """Aggregate counts by type and source."""
    type_result = await db.execute(
        select(HenryMemory.memory_type, func.count(HenryMemory.id))
        .group_by(HenryMemory.memory_type)
    )
    source_result = await db.execute(
        select(HenryMemory.source, func.count(HenryMemory.id))
        .group_by(HenryMemory.source)
    )
    total_result = await db.execute(select(func.count(HenryMemory.id)))

    return {
        "total": total_result.scalar() or 0,
        "by_type": {row[0]: row[1] for row in type_result.all()},
        "by_source": {row[0]: row[1] for row in source_result.all()},
    }


@router.get("/embeddings/health")
async def embeddings_health(db: AsyncSession = Depends(get_db)):
    """
    Report embedding coverage. Drives the Phase 3 viz and also answers
    "is the backfill done?" without needing a psql shell.
    """
    total = (await db.execute(select(func.count(HenryMemory.id)))).scalar() or 0
    with_emb = (
        await db.execute(
            select(func.count(HenryMemory.id)).where(HenryMemory.embedding.is_not(None))
        )
    ).scalar() or 0
    model_dist_result = await db.execute(
        select(HenryMemory.embedding_model, func.count(HenryMemory.id))
        .where(HenryMemory.embedding_model.is_not(None))
        .group_by(HenryMemory.embedding_model)
    )
    cluster_dist_result = await db.execute(
        select(HenryMemory.cluster_id, func.count(HenryMemory.id))
        .where(HenryMemory.cluster_id.is_not(None))
        .group_by(HenryMemory.cluster_id)
    )
    return {
        "total": total,
        "with_embedding": with_emb,
        "without_embedding": total - with_emb,
        "coverage_pct": round(with_emb / total * 100, 1) if total else 0.0,
        "model_distribution": {row[0]: row[1] for row in model_dist_result.all()},
        "cluster_distribution": {int(row[0]): row[1] for row in cluster_dist_result.all()},
    }


@router.get("/embeddings/projection")
async def embeddings_projection(
    force: bool = Query(False, description="Bypass cache and recompute."),
    db: AsyncSession = Depends(get_db),
):
    """
    3D projection of memory embeddings for the visualization tab.

    Returns per-memory (x, y, z) coords in [-1, 1]³ plus cluster centroids
    in the same frame. Projection uses PCA on L2-normalized embeddings —
    matches the clustering pipeline so centroids land where their members
    cluster visually.

    Cached in-process for 10 minutes. Pass `force=true` to refresh
    immediately (e.g., right after running the backfill / re-fit).
    """
    from app.services.memory_projection import compute_projection

    payload = await compute_projection(db, force=force)
    if payload is None:
        return {
            "available": False,
            "reason": "Need at least 3 embedded memories of the same model. Run the backfill if you haven't.",
        }
    return payload


@router.get("/clusters")
async def memory_clusters(
    include_centroid: bool = Query(
        False,
        description="If true, include the 512-dim centroid and variance vectors. Adds ~40KB to response.",
    ),
    db: AsyncSession = Depends(get_db),
):
    """
    Return the current gaussian mixture over memory embeddings.

    Used by:
      - Phase 3 3D viz (centroids = cluster anchors, member_count = size)
      - Debugging retrieval ("which cluster owns this ticker's memories?")
      - Admin panels

    By default centroids are omitted to keep the response small. Pass
    `include_centroid=true` only when you need the raw vectors.
    """
    from app.models import HenryStats

    stats_row = (
        await db.execute(
            select(HenryStats)
            .where(HenryStats.stat_type == "memory_clusters")
            .order_by(HenryStats.computed_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()

    if not stats_row or not stats_row.data:
        return {
            "available": False,
            "reason": "Clustering has not run yet. Wait for the next stats-engine cycle or run scripts/fit_memory_clusters.py manually.",
        }

    data = stats_row.data
    clusters_out = []
    for c in data.get("clusters", []):
        entry = {
            "id": c.get("id"),
            "weight": c.get("weight"),
            "member_count": c.get("member_count"),
        }
        if include_centroid:
            entry["centroid"] = c.get("centroid")
            entry["variance_diag"] = c.get("variance_diag")
        clusters_out.append(entry)

    return {
        "available": True,
        "fit_at": data.get("fit_at"),
        "model_name": data.get("model_name"),
        "dims": data.get("dims"),
        "n_memories_fit": data.get("n_memories_fit"),
        "k": data.get("k"),
        "log_likelihood": data.get("log_likelihood"),
        "clusters": clusters_out,
    }


@router.put("/{memory_id}")
async def update_memory(memory_id: str, body: MemoryUpdate, db: AsyncSession = Depends(get_db)):
    """Update a memory's importance or content."""
    result = await db.execute(select(HenryMemory).where(HenryMemory.id == memory_id))
    memory = result.scalar_one_or_none()
    if not memory:
        raise HTTPException(404, "Memory not found")

    if body.importance is not None:
        memory.importance = max(1, min(10, body.importance))
    if body.content is not None:
        memory.content = body.content
    memory.updated_at = utcnow()

    await db.commit()
    return {"id": memory.id, "importance": memory.importance, "updated": True}


@router.delete("/{memory_id}")
async def delete_memory(memory_id: str, db: AsyncSession = Depends(get_db)):
    """Delete a memory entry."""
    result = await db.execute(select(HenryMemory).where(HenryMemory.id == memory_id))
    memory = result.scalar_one_or_none()
    if not memory:
        raise HTTPException(404, "Memory not found")

    await db.delete(memory)
    await db.commit()
    return {"deleted": memory_id}
