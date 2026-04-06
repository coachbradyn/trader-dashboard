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
