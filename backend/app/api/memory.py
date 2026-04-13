"""Henry Memory Management API — CRUD for HenryMemory entries."""

import asyncio
import json
import logging
from app.utils.utc import utcnow

from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect
from pydantic import BaseModel
from sqlalchemy import select, func, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import HenryMemory

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/memory", tags=["memory"])

# Tracks the background backfill run so the frontend can show progress.
# Single-process state — fine for Railway's single web worker. If you ever
# scale out, move this into Redis or a DB row.
_backfill_state: dict = {
    "running": False,
    "started_at": None,
    "finished_at": None,
    "processed": 0,
    "updated": 0,
    "failed": 0,
    "error": None,
}


def _require_admin(secret: str) -> None:
    """Mirrors the pattern in main.py's /api/admin/seed."""
    from app.config import get_settings
    settings = get_settings()
    if not settings.admin_secret:
        raise HTTPException(
            status_code=503,
            detail="ADMIN_SECRET not configured on the server.",
        )
    if secret != settings.admin_secret:
        raise HTTPException(status_code=403, detail="Forbidden")


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

    Exceptions are caught and returned as `{available: false, reason: ...}`
    so the frontend can surface the real reason instead of a generic
    HTTP 500. Full traceback is logged server-side.
    """
    from app.services.memory_projection import compute_projection

    try:
        payload = await compute_projection(db, force=force)
    except Exception as e:
        logger.exception("memory_projection failed")
        # Heuristic: column-missing errors from pre-migration DBs are the
        # single most common cause. Surface that hint prominently.
        msg = str(e)
        hint = ""
        lower = msg.lower()
        if "cluster_id" in lower or "embedding_model" in lower or "embedding" in lower:
            hint = (
                " Likely cause: a database migration hasn't run. "
                "Check Railway logs for 'alembic upgrade head' output."
            )
        return {
            "available": False,
            "reason": f"Projection failed: {type(e).__name__}: {msg[:300]}.{hint}",
        }

    if payload is None:
        return {
            "available": False,
            "reason": (
                "Need at least 3 embedded memories of the same model. "
                "Run scripts/backfill_memory_embeddings.py if you haven't."
            ),
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


# ══════════════════════════════════════════════════════════════════════════
# CURATION ENDPOINTS — duplicate detection, orphan flagging, forget selector
# ══════════════════════════════════════════════════════════════════════════


@router.get("/curation/duplicates")
async def curation_duplicates(
    threshold: float = Query(0.92, ge=0.5, le=1.0),
    limit: int = Query(50, ge=1, le=500),
    same_cluster_only: bool = Query(
        True,
        description=(
            "When true, only compare memories within the same cluster — "
            "much faster on large stores and catches the same-topic dupes. "
            "Set false for an exhaustive cross-cluster scan (O(N²))."
        ),
    ),
    db: AsyncSession = Depends(get_db),
):
    """
    Return memory pairs whose embeddings cosine-similarity ≥ threshold.

    Sorted by similarity descending. Pairs are unordered (a, b == b, a) and
    de-duplicated. Each side of the pair returns id + content_preview +
    importance + reference_count so the UI can render a side-by-side merge
    candidate without an extra round-trip.
    """
    from app.services.embeddings import cosine_similarity
    from collections import defaultdict

    rows = list(
        (
            await db.execute(
                select(
                    HenryMemory.id,
                    HenryMemory.embedding,
                    HenryMemory.embedding_model,
                    HenryMemory.cluster_id,
                    HenryMemory.importance,
                    HenryMemory.reference_count,
                    HenryMemory.memory_type,
                    HenryMemory.ticker,
                    HenryMemory.content,
                ).where(HenryMemory.embedding.is_not(None))
            )
        ).all()
    )

    if not rows:
        return {"pairs": [], "n_compared": 0, "threshold": threshold}

    # Filter to dominant model + consistent dim — same convention as
    # clustering / projection.
    from collections import Counter
    model_counts = Counter(r.embedding_model for r in rows if r.embedding_model)
    if not model_counts:
        return {"pairs": [], "n_compared": 0, "threshold": threshold}
    dominant_model, _ = model_counts.most_common(1)[0]
    rows = [r for r in rows if r.embedding_model == dominant_model]

    dims_counter = Counter(len(r.embedding) for r in rows)
    if not dims_counter:
        return {"pairs": [], "n_compared": 0, "threshold": threshold}
    dims, _ = dims_counter.most_common(1)[0]
    rows = [r for r in rows if len(r.embedding) == dims]

    # Group by cluster if scoped, else single bucket.
    buckets: dict[object, list] = defaultdict(list)
    if same_cluster_only:
        for r in rows:
            buckets[r.cluster_id].append(r)
    else:
        buckets[None] = rows

    pairs: list[dict] = []
    n_compared = 0
    # Stop early if we hit the cap — duplicates are usually clustered, so
    # most useful pairs surface in the first few buckets.
    for bucket in buckets.values():
        if len(bucket) < 2:
            continue
        for i in range(len(bucket)):
            ri = bucket[i]
            for j in range(i + 1, len(bucket)):
                rj = bucket[j]
                n_compared += 1
                sim = cosine_similarity(ri.embedding, rj.embedding)
                if sim >= threshold:
                    # Order so the higher-importance side is "keep" by default.
                    a, b = (ri, rj) if (ri.importance or 5) >= (rj.importance or 5) else (rj, ri)
                    pairs.append({
                        "similarity": float(sim),
                        "keep": _serialize_curation_row(a),
                        "drop": _serialize_curation_row(b),
                    })
        if len(pairs) >= limit:
            break

    pairs.sort(key=lambda p: p["similarity"], reverse=True)
    return {
        "pairs": pairs[:limit],
        "n_compared": n_compared,
        "threshold": threshold,
        "same_cluster_only": same_cluster_only,
    }


def _serialize_curation_row(r) -> dict:
    """Compact memory snapshot for curation panels."""
    return {
        "id": r.id,
        "memory_type": r.memory_type,
        "ticker": r.ticker,
        "importance": int(r.importance or 5),
        "reference_count": int(r.reference_count or 0),
        "cluster_id": r.cluster_id,
        "content_preview": (r.content or "")[:200].strip(),
    }


class ConsolidatePreviewRequest(BaseModel):
    threshold: float = 0.93   # cosine — looser than dup detection's 0.92
    min_group_size: int = 2   # need at least N candidates to consolidate
    max_groups: int = 10      # cap LLM calls per preview
    same_cluster_only: bool = True


@router.post("/curation/consolidate-preview")
async def curation_consolidate_preview(
    body: ConsolidatePreviewRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Find groups of near-duplicate memories and use Gemini to draft a
    single consolidated lesson per group. Returns the proposals WITHOUT
    committing — caller reviews and posts to /admin/consolidate-commit
    to actually replace the originals.

    Gated behind a preview-then-commit flow per the user's earlier
    instruction: "I'd want to ship it gated behind a preview UI step,
    never as an unattended cron, until you've watched it merge ~10
    times and trust the LLM's groupings."

    Per group: pulls up to 8 member previews, asks Gemini to write one
    unified observation that captures their common pattern. Returns
    that draft + the member IDs so the UI can render side-by-side and
    the user can edit before commit.
    """
    from app.services.embeddings import cosine_similarity
    from app.services.ai_provider import call_ai
    from collections import Counter, defaultdict

    rows = list(
        (
            await db.execute(
                select(
                    HenryMemory.id,
                    HenryMemory.embedding,
                    HenryMemory.embedding_model,
                    HenryMemory.cluster_id,
                    HenryMemory.importance,
                    HenryMemory.reference_count,
                    HenryMemory.memory_type,
                    HenryMemory.ticker,
                    HenryMemory.strategy_id,
                    HenryMemory.content,
                ).where(HenryMemory.embedding.is_not(None))
            )
        ).all()
    )
    if not rows:
        return {"groups": [], "n_compared": 0}

    model_counts = Counter(r.embedding_model for r in rows if r.embedding_model)
    if not model_counts:
        return {"groups": [], "n_compared": 0}
    dominant_model, _ = model_counts.most_common(1)[0]
    rows = [r for r in rows if r.embedding_model == dominant_model]
    dims_counter = Counter(len(r.embedding) for r in rows)
    if not dims_counter:
        return {"groups": [], "n_compared": 0}
    dims, _ = dims_counter.most_common(1)[0]
    rows = [r for r in rows if len(r.embedding) == dims]

    # Build similarity-graph buckets via simple union-find on edges above
    # threshold. O(N²) fine at our scale; can scope to same-cluster.
    buckets: list[list] = []
    if body.same_cluster_only:
        by_cluster: dict[object, list] = defaultdict(list)
        for r in rows:
            by_cluster[r.cluster_id].append(r)
        for c_id, lst in by_cluster.items():
            if c_id is None or len(lst) < 2:
                continue
            buckets.append(lst)
    else:
        buckets.append(rows)

    n_compared = 0
    parents: dict[str, str] = {}

    def find(x: str) -> str:
        while parents.get(x, x) != x:
            parents[x] = parents.get(parents[x], parents[x])
            x = parents[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parents[ra] = rb

    id_to_row = {r.id: r for r in rows}
    for bucket in buckets:
        for i in range(len(bucket)):
            ri = bucket[i]
            for j in range(i + 1, len(bucket)):
                rj = bucket[j]
                n_compared += 1
                if cosine_similarity(ri.embedding, rj.embedding) >= body.threshold:
                    parents.setdefault(ri.id, ri.id)
                    parents.setdefault(rj.id, rj.id)
                    union(ri.id, rj.id)

    # Group by root
    groups_map: dict[str, list[str]] = defaultdict(list)
    for member_id in parents:
        groups_map[find(member_id)].append(member_id)
    groups = [
        sorted(g) for g in groups_map.values() if len(g) >= body.min_group_size
    ]
    # Sort groups by largest first, cap at max_groups
    groups.sort(key=len, reverse=True)
    groups = groups[: body.max_groups]

    if not groups:
        return {
            "groups": [],
            "n_compared": n_compared,
            "threshold": body.threshold,
        }

    # Gemini: one consolidation call per group (parallel), capped above
    import asyncio as _asyncio

    async def consolidate_one(member_ids: list[str]) -> dict:
        members = [id_to_row[mid] for mid in member_ids if mid in id_to_row]
        if not members:
            return {}
        # Pick the "anchor" member: highest importance, then most refs
        anchor = max(members, key=lambda m: (int(m.importance or 0), int(m.reference_count or 0)))
        ticker = anchor.ticker
        strategy_id = anchor.strategy_id
        memory_type = anchor.memory_type

        previews = "\n".join(
            f"- [{m.memory_type}] (imp {m.importance}, refs {m.reference_count}): "
            f"{(m.content or '')[:240].strip().replace(chr(10), ' ')}"
            for m in members
        )
        system = (
            "You consolidate near-duplicate trading memories into a single "
            "unified observation that preserves the strongest quantitative "
            "signal across them. Output ONLY the consolidated content text "
            "(1-2 sentences). No preamble, no markdown."
        )
        prompt = (
            f"Below are {len(members)} near-duplicate memories about the "
            f"same trading pattern. Write ONE unified memory that:\n"
            "- Combines the strongest evidence from all of them\n"
            "- Preserves the most specific numbers (prices, %s, ADX, VIX, "
            "win rates, sample sizes)\n"
            "- Reads as a single coherent observation, not a bulleted list\n"
            "- Stays in the same style as the originals\n\n"
            f"Memories:\n{previews}"
        )

        try:
            text = await call_ai(
                system=system,
                prompt=prompt,
                function_name="memory_extraction",  # Gemini-routed
                max_tokens=300,
            )
        except Exception as e:
            text = ""
            logger.warning(f"Consolidation Gemini call failed: {e}")

        proposed = (text or "").strip()
        # Strip surrounding markdown/quotes
        for ch in ('"', "'", "*", "`"):
            if proposed.startswith(ch):
                proposed = proposed[1:]
            if proposed.endswith(ch):
                proposed = proposed[:-1]
        proposed = proposed.strip()

        # Compute average pairwise similarity for the UI
        sims = []
        for i in range(len(members)):
            for j in range(i + 1, len(members)):
                sims.append(cosine_similarity(members[i].embedding, members[j].embedding))
        avg_sim = float(sum(sims) / len(sims)) if sims else 0.0

        return {
            "member_ids": [m.id for m in members],
            "n": len(members),
            "anchor_id": anchor.id,
            "ticker": ticker,
            "strategy_id": strategy_id,
            "memory_type": memory_type,
            "avg_similarity": round(avg_sim, 4),
            "proposed_content": proposed if proposed else None,
            "proposed_importance": min(10, max(1, int(anchor.importance or 5) + 1)),
            "members": [
                {
                    "id": m.id,
                    "importance": int(m.importance or 5),
                    "reference_count": int(m.reference_count or 0),
                    "memory_type": m.memory_type,
                    "content_preview": (m.content or "")[:200].strip(),
                }
                for m in members
            ],
        }

    consolidations = await _asyncio.gather(
        *(consolidate_one(g) for g in groups), return_exceptions=True
    )
    valid_groups = [c for c in consolidations if isinstance(c, dict) and c.get("proposed_content")]

    return {
        "groups": valid_groups,
        "n_compared": n_compared,
        "n_groups_found": len(groups),
        "n_groups_with_proposal": len(valid_groups),
        "threshold": body.threshold,
    }


class ConsolidateCommitRequest(BaseModel):
    member_ids: list[str]
    content: str
    importance: int = 7
    memory_type: str = "lesson"
    ticker: str | None = None
    strategy_id: str | None = None


@router.post("/admin/consolidate-commit")
async def admin_consolidate_commit(
    body: ConsolidateCommitRequest,
    secret: str = Query(...),
    db: AsyncSession = Depends(get_db),
):
    """
    Commit a single consolidation: create a new memory from `content`
    + `importance` + scope, then delete the original `member_ids`.

    The new memory inherits the combined reference_count of its
    members so retrieval ranking history isn't lost.
    """
    _require_admin(secret)
    from sqlalchemy import select, delete as sql_delete
    from app.services.ai_service import save_memory

    if not body.member_ids or not body.content.strip():
        return {"ok": False, "reason": "Need member_ids and non-empty content."}
    if len(body.member_ids) < 2:
        return {"ok": False, "reason": "Need ≥2 member_ids to consolidate."}

    # Sum reference counts before deletion
    members = list(
        (
            await db.execute(
                select(HenryMemory).where(HenryMemory.id.in_(body.member_ids))
            )
        ).scalars().all()
    )
    if len(members) != len(body.member_ids):
        missing = set(body.member_ids) - {m.id for m in members}
        return {
            "ok": False,
            "reason": f"Some member_ids not found: {sorted(missing)[:5]}",
        }

    combined_refs = sum(int(m.reference_count or 0) for m in members)
    importance = max(1, min(10, int(body.importance)))
    mtype = body.memory_type if body.memory_type in {
        "observation", "lesson", "decision", "preference", "strategy_note"
    } else "lesson"

    # Save the consolidated memory (gets embedded inline by save_memory)
    await save_memory(
        content=body.content.strip(),
        memory_type=mtype,
        ticker=body.ticker,
        strategy_id=body.strategy_id,
        importance=importance,
        source="auto_consolidated",
    )

    # Bump the new memory's reference count via direct SQL (save_memory
    # doesn't expose this). Match by content_hash + freshly-set source.
    from app.services.ai_service import _memory_fingerprint as _fp
    fp = _fp(body.ticker, body.strategy_id, body.content.strip())
    if combined_refs > 0:
        from sqlalchemy import update as sql_update
        await db.execute(
            sql_update(HenryMemory)
            .where(HenryMemory.content_hash == fp)
            .where(HenryMemory.source == "auto_consolidated")
            .values(reference_count=combined_refs)
        )

    # Delete the originals
    await db.execute(
        sql_delete(HenryMemory).where(HenryMemory.id.in_(body.member_ids))
    )
    await db.commit()
    return {
        "ok": True,
        "deleted": len(body.member_ids),
        "consolidated_reference_count": combined_refs,
    }


@router.get("/curation/orphans")
async def curation_orphans(
    threshold: float = Query(
        -0.05,
        ge=-1.0,
        le=1.0,
        description="Memories with cluster_silhouette < threshold are orphans.",
    ),
    limit: int = Query(100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
):
    """
    Memories whose silhouette is below threshold — i.e. they don't fit
    their assigned cluster well. Candidates for re-categorization or
    deletion.
    """
    result = await db.execute(
        select(HenryMemory)
        .where(HenryMemory.cluster_silhouette.isnot(None))
        .where(HenryMemory.cluster_silhouette < threshold)
        .order_by(HenryMemory.cluster_silhouette.asc())
        .limit(limit)
    )
    rows = list(result.scalars().all())
    return {
        "threshold": threshold,
        "count": len(rows),
        "orphans": [
            {
                "id": r.id,
                "silhouette": float(r.cluster_silhouette) if r.cluster_silhouette is not None else None,
                "cluster_id": r.cluster_id,
                "memory_type": r.memory_type,
                "ticker": r.ticker,
                "importance": int(r.importance or 5),
                "reference_count": int(r.reference_count or 0),
                "content_preview": (r.content or "")[:200].strip(),
            }
            for r in rows
        ],
    }


class ForgetCandidatesRequest(BaseModel):
    max_importance: int = 4
    max_reference_count: int = 0
    min_age_days: int = 30
    require_unvalidated: bool = True
    limit: int = 200


@router.post("/curation/forget-candidates")
async def curation_forget_candidates(
    body: ForgetCandidatesRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Preview candidates for the "forget" bulk delete: low importance, low
    reference count, old, and (optionally) not validated. Returns IDs +
    previews. The UI shows count + samples and asks for confirmation
    before invoking /admin/bulk-delete.
    """
    from datetime import timedelta

    cutoff = utcnow() - timedelta(days=max(0, body.min_age_days))
    stmt = (
        select(HenryMemory)
        .where(HenryMemory.importance <= body.max_importance)
        .where(HenryMemory.reference_count <= body.max_reference_count)
        .where(HenryMemory.created_at <= cutoff)
    )
    if body.require_unvalidated:
        # validated IS NULL OR validated = false — rejects only confirmed-correct.
        from sqlalchemy import or_ as _or
        stmt = stmt.where(
            _or(
                HenryMemory.validated.is_(None),
                HenryMemory.validated.is_(False),
            )
        )
    stmt = stmt.order_by(
        HenryMemory.importance.asc(),
        HenryMemory.reference_count.asc(),
        HenryMemory.created_at.asc(),
    ).limit(max(1, min(1000, body.limit)))

    rows = list((await db.execute(stmt)).scalars().all())
    return {
        "criteria": body.dict(),
        "count": len(rows),
        "candidates": [
            {
                "id": r.id,
                "importance": int(r.importance or 5),
                "reference_count": int(r.reference_count or 0),
                "memory_type": r.memory_type,
                "ticker": r.ticker,
                "validated": r.validated,
                "created_at": r.created_at.isoformat() + "Z" if r.created_at else None,
                "content_preview": (r.content or "")[:160].strip(),
            }
            for r in rows
        ],
    }


class BulkDeleteRequest(BaseModel):
    ids: list[str]


@router.post("/admin/bulk-delete")
async def admin_bulk_delete(
    body: BulkDeleteRequest,
    secret: str = Query(...),
    db: AsyncSession = Depends(get_db),
):
    """
    Admin-gated bulk delete by ID list. Returns how many actually got
    removed (some IDs may not exist). Caps at 1000 per call as a safety
    measure — the forget UI batches if it needs more.
    """
    _require_admin(secret)
    from sqlalchemy import delete as sql_delete

    ids = list({i for i in (body.ids or []) if i})
    if not ids:
        return {"ok": True, "deleted": 0, "requested": 0}
    if len(ids) > 1000:
        return {
            "ok": False,
            "reason": f"Refusing to bulk-delete {len(ids)} memories in one call. Cap is 1000.",
        }

    # Count first so we can report accurately even if some IDs were stale.
    found_count_result = await db.execute(
        select(func.count(HenryMemory.id)).where(HenryMemory.id.in_(ids))
    )
    found = int(found_count_result.scalar() or 0)

    await db.execute(sql_delete(HenryMemory).where(HenryMemory.id.in_(ids)))
    await db.commit()
    return {"ok": True, "deleted": found, "requested": len(ids)}


class MergeMemoryRequest(BaseModel):
    keep_id: str
    drop_id: str
    # If true, bump the kept memory's importance by min(10, kept.importance + 1)
    # to reflect the consolidation.
    bump_importance: bool = True


@router.post("/admin/merge")
async def admin_merge_memory(
    body: MergeMemoryRequest,
    secret: str = Query(...),
    db: AsyncSession = Depends(get_db),
):
    """
    Admin-gated merge of two memories: drop one, optionally bump the
    other's importance + add its reference count. Used by the duplicate
    detection panel's "Merge" action.
    """
    _require_admin(secret)
    from sqlalchemy import delete as sql_delete

    if body.keep_id == body.drop_id:
        return {"ok": False, "reason": "keep_id and drop_id are identical."}

    keep = (await db.execute(select(HenryMemory).where(HenryMemory.id == body.keep_id))).scalar_one_or_none()
    drop = (await db.execute(select(HenryMemory).where(HenryMemory.id == body.drop_id))).scalar_one_or_none()
    if not keep:
        return {"ok": False, "reason": f"keep_id {body.keep_id} not found."}
    if not drop:
        return {"ok": False, "reason": f"drop_id {body.drop_id} not found."}

    # Merge the reference counts so we don't lose the dropped memory's history.
    keep.reference_count = (keep.reference_count or 0) + (drop.reference_count or 0)
    if body.bump_importance:
        keep.importance = max(1, min(10, (keep.importance or 5) + 1))
    keep.updated_at = utcnow()

    await db.execute(sql_delete(HenryMemory).where(HenryMemory.id == body.drop_id))
    await db.commit()
    return {
        "ok": True,
        "kept": {
            "id": keep.id,
            "importance": int(keep.importance),
            "reference_count": int(keep.reference_count),
        },
        "dropped_id": body.drop_id,
    }


@router.websocket("/ws/retrieval-events")
async def ws_retrieval_events(websocket: WebSocket):
    """
    Live push channel for retrieval events. Replaces the 3s polling that
    Sprint B used to drive the 3D map's pulse animation. Frontend reuses
    GET /retrieval-events as a fallback when the socket fails to connect
    or drops.

    Protocol:
      Server → client: {"events": [{ts, function_name, query_preview,
                                     memory_ids[], scope_*}]} on every
                       new retrieval. Also sends an initial {"hello":
                       <cursor>} so the client knows to drop any
                       polling-based catch-up since the cursor.
      Client → server: nothing required. Clients that send anything are
                       ignored (we still keep them connected).

    No auth header on the socket — same posture as the polling endpoint
    (memory IDs only, no content). If your deploy needs origin gating,
    add it here.
    """
    from app.services.retrieval_events import (
        register_ws, unregister_ws, latest_ts,
    )
    await websocket.accept()
    register_ws(websocket)
    try:
        # Initial hello so the client can advance its cursor and stop
        # double-polling for events older than this.
        await websocket.send_text(json.dumps({"hello": latest_ts()}))
        # Idle loop — we just need to keep the socket open. Receive
        # raises WebSocketDisconnect when the client closes; until then,
        # all sends happen via _broadcast() in retrieval_events.
        while True:
            try:
                # Block waiting for any incoming message; we ignore
                # contents but this is the standard pattern to detect
                # disconnect cleanly.
                await websocket.receive_text()
            except WebSocketDisconnect:
                break
    except Exception as e:
        logger.debug(f"ws_retrieval_events loop ended: {e}")
    finally:
        unregister_ws(websocket)


@router.get("/retrieval-events")
async def retrieval_events(since: float = Query(0.0, ge=0.0)):
    """
    Live feed of recent memory retrievals for the 3D viz pulse animation.

    Pass `since` as the epoch-seconds cursor returned by the previous call;
    we return events with `ts > since` plus the latest cursor value so the
    client can advance. First call: pass 0 to get the buffered tail.

    No auth — these are anonymized memory IDs only, no content. Single
    process / in-memory ring buffer (RING_SIZE most recent events).
    """
    from app.services.retrieval_events import events_since, latest_ts

    events = events_since(since)
    return {
        "events": events,
        "cursor": latest_ts(),
    }


class PreviewRetrievalRequest(BaseModel):
    query: str
    top_k: int = 8
    ticker: str | None = None
    strategy_id: str | None = None


@router.post("/preview-retrieval")
async def preview_retrieval(
    body: PreviewRetrievalRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Run the same semantic + cluster ranking as the system-prompt builder,
    but return the top-K memory IDs + scores instead of injecting them
    into a Claude call. Powers the 3D viz "live query playback" input.

    Cheap: one Voyage embedding call (~$0.000005) + cosine over a 200-row
    candidate pool + softmax over cluster gaussians. No LLM token spend.
    """
    from sqlalchemy import or_ as _or
    from app.services.embeddings import (
        get_embedding_provider,
        cosine_similarity,
    )
    from app.services.memory_clustering import score_query_clusters
    from app.config import get_settings

    settings = get_settings()
    if not settings.embedding_enabled:
        return {"ok": False, "reason": "Embeddings disabled."}

    q = (body.query or "").strip()
    if not q:
        return {"ok": False, "reason": "Empty query."}

    provider = get_embedding_provider()
    if provider is None:
        return {"ok": False, "reason": "No embedding provider — set VOYAGE_API_KEY."}

    try:
        if hasattr(provider, "embed_query"):
            qvec = await provider.embed_query(q)
        else:
            qvec = await provider.embed(q)
    except Exception as e:
        return {"ok": False, "reason": f"Embedding failed: {type(e).__name__}: {str(e)[:200]}"}

    if qvec is None:
        return {"ok": False, "reason": "Embedding returned None."}

    model_name = provider.model_name

    stmt = (
        select(HenryMemory)
        .where(HenryMemory.embedding_model == model_name)
        .where(HenryMemory.embedding.is_not(None))
    )
    scope_filters = []
    if body.ticker:
        scope_filters.append(HenryMemory.ticker == body.ticker)
    if body.strategy_id:
        scope_filters.append(HenryMemory.strategy_id == body.strategy_id)
    if scope_filters:
        scope_filters.append(HenryMemory.ticker.is_(None))
        scope_filters.append(HenryMemory.strategy_id.is_(None))
        stmt = stmt.where(_or(*scope_filters))
    stmt = stmt.order_by(
        HenryMemory.importance.desc(),
        HenryMemory.updated_at.desc(),
    ).limit(200)

    candidates = list((await db.execute(stmt)).scalars().all())
    if not candidates:
        return {"ok": True, "results": [], "n_candidates": 0, "model_name": model_name}

    cluster_probs: dict[int, float] = {}
    cluster_weight = float(getattr(settings, "memory_cluster_weight", 0.3))
    if getattr(settings, "memory_clustering_enabled", True) and cluster_weight > 0:
        try:
            cluster_probs = await score_query_clusters(db, qvec, model_name)
        except Exception:
            cluster_probs = {}

    ranked = []
    for m in candidates:
        sim = cosine_similarity(qvec, m.embedding or [])
        importance_nudge = max(0, int(m.importance or 5)) / 50.0
        cluster_boost = 0.0
        if cluster_probs and m.cluster_id is not None:
            cluster_boost = cluster_weight * cluster_probs.get(int(m.cluster_id), 0.0)
        score = sim + importance_nudge + cluster_boost
        ranked.append({
            "id": m.id,
            "score": float(score),
            "similarity": float(sim),
            "cluster_boost": float(cluster_boost),
            "importance": int(m.importance or 5),
            "cluster_id": m.cluster_id,
            "memory_type": m.memory_type,
            "ticker": m.ticker,
            "content_preview": (m.content or "")[:160].strip(),
        })
    ranked.sort(key=lambda x: x["score"], reverse=True)
    top = ranked[: max(1, min(50, body.top_k))]

    # Surface in the live event buffer so the viz pulses these too.
    try:
        from app.services.retrieval_events import record_retrieval
        record_retrieval(
            memory_ids=[r["id"] for r in top],
            function_name="preview_retrieval",
            query_preview=q,
            scope_ticker=body.ticker,
            scope_strategy=body.strategy_id,
        )
    except Exception:
        pass

    return {
        "ok": True,
        "model_name": model_name,
        "n_candidates": len(candidates),
        "results": top,
    }


@router.post("/admin/ensure-schema")
async def admin_ensure_schema(
    secret: str = Query(..., description="ADMIN_SECRET"),
    db: AsyncSession = Depends(get_db),
):
    """
    Idempotently ensure the memory-related columns and indexes exist.

    This is a belt-and-suspenders recovery path for when Alembic migrations
    don't run at deploy (the railway.toml startCommand uses `|| true` on
    alembic upgrade head, so a failing migration is silent). The endpoint
    issues `ADD COLUMN IF NOT EXISTS` / `CREATE INDEX IF NOT EXISTS` so
    it's safe to run any number of times — it just reports what it did.

    After success, also bumps the alembic_version row to the latest head
    so subsequent `alembic upgrade head` invocations see a clean state
    and don't try to re-apply migrations we already satisfied here.

    Postgres-only (uses IF NOT EXISTS on ALTER TABLE). SQLite dev DBs
    don't need this — alembic works fine locally.
    """
    _require_admin(secret)

    from sqlalchemy import text

    changes: list[str] = []

    # Detect dialect — we need Postgres. On SQLite, alembic should just work.
    dialect = db.bind.dialect.name if db.bind else None
    if dialect != "postgresql":
        return {
            "ok": False,
            "reason": f"ensure-schema is Postgres-only (detected dialect: {dialect}). Run `alembic upgrade head` locally instead.",
        }

    # All statements below are idempotent.
    ddl_statements: list[tuple[str, str]] = [
        (
            "add_embedding_column",
            "ALTER TABLE henry_memory ADD COLUMN IF NOT EXISTS embedding JSON",
        ),
        (
            "add_embedding_model_column",
            "ALTER TABLE henry_memory ADD COLUMN IF NOT EXISTS embedding_model VARCHAR(50)",
        ),
        (
            "add_cluster_id_column",
            "ALTER TABLE henry_memory ADD COLUMN IF NOT EXISTS cluster_id INTEGER",
        ),
        (
            "add_cluster_silhouette_column",
            "ALTER TABLE henry_memory ADD COLUMN IF NOT EXISTS cluster_silhouette FLOAT",
        ),
        (
            "create_cluster_id_index",
            "CREATE INDEX IF NOT EXISTS ix_henry_memory_cluster_id ON henry_memory (cluster_id)",
        ),
        # Phase 3 — entry-time regime snapshot on trades. Used by
        # _compute_conditional_probability to split win rates by VIX
        # bucket / SPY-trend / SPY-ADX regime.
        (
            "add_trade_entry_vix",
            "ALTER TABLE trades ADD COLUMN IF NOT EXISTS entry_vix FLOAT",
        ),
        (
            "add_trade_entry_spy_close",
            "ALTER TABLE trades ADD COLUMN IF NOT EXISTS entry_spy_close FLOAT",
        ),
        (
            "add_trade_entry_spy_20ema",
            "ALTER TABLE trades ADD COLUMN IF NOT EXISTS entry_spy_20ema FLOAT",
        ),
        (
            "add_trade_entry_spy_adx",
            "ALTER TABLE trades ADD COLUMN IF NOT EXISTS entry_spy_adx FLOAT",
        ),
        # Phase 4 — position sizing fields on portfolio_actions.
        (
            "add_action_recommended_shares",
            "ALTER TABLE portfolio_actions ADD COLUMN IF NOT EXISTS recommended_shares FLOAT",
        ),
        (
            "add_action_recommended_dollar_amount",
            "ALTER TABLE portfolio_actions ADD COLUMN IF NOT EXISTS recommended_dollar_amount FLOAT",
        ),
        (
            "add_action_recommended_pct_of_equity",
            "ALTER TABLE portfolio_actions ADD COLUMN IF NOT EXISTS recommended_pct_of_equity FLOAT",
        ),
        (
            "add_action_sizing_method",
            "ALTER TABLE portfolio_actions ADD COLUMN IF NOT EXISTS sizing_method VARCHAR(30)",
        ),
    ]

    # Check which columns exist before attempting the DDL so we can report
    # precisely what was added versus already-present.
    existing_cols_result = await db.execute(
        text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name IN ('henry_memory', 'trades')"
        )
    )
    # Now we span two tables — namespace by table to keep counts clear.
    rows_existing = list(existing_cols_result.all())
    henry_cols_q = await db.execute(
        text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'henry_memory'"
        )
    )
    existing_cols = {row[0] for row in henry_cols_q.all()}
    trade_cols_q = await db.execute(
        text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'trades'"
        )
    )
    trade_cols = {row[0] for row in trade_cols_q.all()}

    target_cols = {"embedding", "embedding_model", "cluster_id", "cluster_silhouette"}
    target_trade_cols = {"entry_vix", "entry_spy_close", "entry_spy_20ema", "entry_spy_adx"}
    target_action_cols = {
        "recommended_shares",
        "recommended_dollar_amount",
        "recommended_pct_of_equity",
        "sizing_method",
    }

    action_cols_q = await db.execute(
        text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'portfolio_actions'"
        )
    )
    action_cols = {row[0] for row in action_cols_q.all()}

    missing_before = (
        (target_cols - existing_cols)
        | {f"trades.{c}" for c in (target_trade_cols - trade_cols)}
        | {f"portfolio_actions.{c}" for c in (target_action_cols - action_cols)}
    )

    try:
        for name, stmt in ddl_statements:
            try:
                await db.execute(text(stmt))
                changes.append(f"executed:{name}")
            except Exception as e:
                # ADD COLUMN IF NOT EXISTS is Postgres 9.6+; if an older
                # Postgres complains, fall back to a try/except around a
                # plain ADD COLUMN and swallow "already exists" errors.
                err_msg = str(e).lower()
                if "already exists" in err_msg or "duplicate" in err_msg:
                    changes.append(f"skipped:{name} (already exists)")
                else:
                    changes.append(f"failed:{name} — {str(e)[:200]}")
                    raise

        # Bump alembic_version to the latest head so future deploys don't
        # try to re-apply these migrations (and fail on already-existing
        # columns without IF NOT EXISTS).
        latest_head = "n485960718i0"
        try:
            version_result = await db.execute(
                text("SELECT version_num FROM alembic_version LIMIT 1")
            )
            current_version = version_result.scalar_one_or_none()
            if current_version != latest_head:
                if current_version is None:
                    await db.execute(
                        text("INSERT INTO alembic_version (version_num) VALUES (:v)"),
                        {"v": latest_head},
                    )
                    changes.append(f"alembic_version: set → {latest_head}")
                else:
                    await db.execute(
                        text("UPDATE alembic_version SET version_num = :v"),
                        {"v": latest_head},
                    )
                    changes.append(
                        f"alembic_version: {current_version} → {latest_head}"
                    )
            else:
                changes.append(f"alembic_version: already at {latest_head}")
        except Exception as e:
            # alembic_version table might not exist — not fatal.
            changes.append(f"alembic_version: skipped ({str(e)[:120]})")

        await db.commit()

        henry_after = await db.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'henry_memory'"
            )
        )
        henry_cols_after = {row[0] for row in henry_after.all()}
        trades_after = await db.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'trades'"
            )
        )
        trade_cols_after = {row[0] for row in trades_after.all()}
        actions_after = await db.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'portfolio_actions'"
            )
        )
        action_cols_after = {row[0] for row in actions_after.all()}
        missing_after = (
            (target_cols - henry_cols_after)
            | {f"trades.{c}" for c in (target_trade_cols - trade_cols_after)}
            | {f"portfolio_actions.{c}" for c in (target_action_cols - action_cols_after)}
        )

        return {
            "ok": len(missing_after) == 0,
            "missing_before": sorted(missing_before),
            "missing_after": sorted(missing_after),
            "changes": changes,
        }
    except Exception as e:
        logger.exception("ensure-schema failed")
        try:
            await db.rollback()
        except Exception:
            pass
        return {
            "ok": False,
            "reason": f"{type(e).__name__}: {str(e)[:300]}",
            "changes": changes,
        }


# ══════════════════════════════════════════════════════════════════════════
# ADMIN ENDPOINTS — backfill embeddings + fit clusters without shell access
# ══════════════════════════════════════════════════════════════════════════
#
# The shell-based scripts (backfill_memory_embeddings.py, fit_memory_clusters.py)
# require Railway shell access. These HTTP equivalents let the frontend
# trigger the same work over the admin-auth'd API. Gated by ADMIN_SECRET
# to match the existing /api/admin/seed pattern in main.py.


async def _run_backfill_job(batch_size: int = 32):
    """Background task: embed every memory that lacks an embedding.

    Writes progress to the module-level `_backfill_state` dict so
    /api/memory/admin/backfill-status can report it.
    """
    from app.database import async_session
    from app.services.embeddings import get_embedding_provider
    from sqlalchemy import select, update, or_

    state = _backfill_state
    state.update(
        running=True,
        started_at=utcnow().isoformat() + "Z",
        finished_at=None,
        processed=0,
        updated=0,
        failed=0,
        error=None,
    )

    try:
        provider = get_embedding_provider()
        if provider is None:
            state["error"] = "No embedding provider — is VOYAGE_API_KEY set?"
            return

        model_name = provider.model_name

        while True:
            async with async_session() as db:
                stmt = (
                    select(HenryMemory)
                    .where(
                        or_(
                            HenryMemory.embedding.is_(None),
                            HenryMemory.embedding_model != model_name,
                        )
                    )
                    .order_by(HenryMemory.created_at.asc())
                    .limit(batch_size)
                )
                rows = list((await db.execute(stmt)).scalars().all())

            if not rows:
                break

            texts = [r.content or "" for r in rows]
            valid = [(r.id, t) for r, t in zip(rows, texts) if t.strip()]
            if not valid:
                state["processed"] += len(rows)
                continue

            ids = [v[0] for v in valid]
            try:
                vectors = await provider.embed_batch([v[1] for v in valid])
            except Exception as e:
                logger.warning(f"Backfill batch embed failed: {e}")
                state["failed"] += len(valid)
                state["processed"] += len(rows)
                if state["failed"] > 3 * batch_size:
                    state["error"] = "Too many consecutive embed failures — aborting."
                    return
                continue

            async with async_session() as db:
                for mid, vec in zip(ids, vectors):
                    if vec is None:
                        state["failed"] += 1
                        continue
                    await db.execute(
                        update(HenryMemory)
                        .where(HenryMemory.id == mid)
                        .values(embedding=vec, embedding_model=model_name)
                    )
                    state["updated"] += 1
                await db.commit()

            state["processed"] += len(rows)
    except Exception as e:
        logger.exception("Admin backfill failed")
        state["error"] = f"{type(e).__name__}: {str(e)[:200]}"
    finally:
        state["running"] = False
        state["finished_at"] = utcnow().isoformat() + "Z"


@router.post("/admin/backfill-embeddings")
async def admin_backfill_embeddings(
    secret: str = Query(..., description="ADMIN_SECRET"),
    batch_size: int = Query(32, ge=1, le=128),
):
    """
    Kick off embedding backfill as a background task. Returns 202 + current
    state. Poll /api/memory/admin/backfill-status for progress.
    """
    _require_admin(secret)

    if _backfill_state["running"]:
        return {"ok": False, "reason": "Backfill already running.", "state": _backfill_state}

    asyncio.create_task(_run_backfill_job(batch_size=batch_size))
    return {"ok": True, "state": _backfill_state}


@router.get("/admin/backfill-status")
async def admin_backfill_status(secret: str = Query(...)):
    """Returns the current backfill job state."""
    _require_admin(secret)
    return _backfill_state


@router.post("/admin/relabel-clusters")
async def admin_relabel_clusters(
    secret: str = Query(...),
    db: AsyncSession = Depends(get_db),
):
    """
    Re-run only the LLM labeling pass on the latest cluster fit. Used
    when:
      - clusters were fit before the labeling code shipped (existing
        rows show as 'cluster N' instead of human labels)
      - GEMINI_API_KEY just got configured
      - prior labeling rejected too aggressively

    Does NOT refit the GMM. Mutates the existing memory_clusters
    HenryStats row's `clusters[].label` field in place. Cheap (~$0.001
    per refit, max 15 Gemini calls in parallel).
    """
    _require_admin(secret)
    from app.services.memory_clustering import (
        relabel_existing_clusters,
        invalidate_cache as invalidate_clusters,
    )
    from app.services.memory_projection import invalidate_cache as invalidate_projection

    try:
        summary = await relabel_existing_clusters(db)
        if summary is None:
            return {
                "ok": False,
                "reason": "No cluster fit found, or no embedded memories assigned to clusters. Run Fit Clusters first.",
            }
        await db.commit()
        invalidate_clusters()
        invalidate_projection()
        return {"ok": True, "summary": summary}
    except Exception as e:
        logger.exception("admin_relabel_clusters failed")
        return {"ok": False, "reason": f"{type(e).__name__}: {str(e)[:300]}"}


@router.post("/admin/fit-clusters")
async def admin_fit_clusters(
    secret: str = Query(...),
    min_memories: int = Query(
        20,
        ge=3,
        description="Skip fit if fewer than this many embedded memories exist.",
    ),
    db: AsyncSession = Depends(get_db),
):
    """
    Synchronously fit the gaussian mixture over current memory embeddings.

    Fast at our scale (<10s even for ~5k memories). Returns the fit
    summary immediately. Also invalidates the retrieval cache and the
    3D projection cache so the next query / viz reflects the new fit.
    """
    _require_admin(secret)

    import app.services.memory_clustering as mc
    from app.services.memory_clustering import fit_memory_clusters, invalidate_cache as invalidate_clusters
    from app.services.memory_projection import invalidate_cache as invalidate_projection

    if min_memories != mc.MIN_MEMORIES_TO_FIT:
        mc.MIN_MEMORIES_TO_FIT = min_memories

    try:
        summary = await fit_memory_clusters(db)
        if summary is None:
            return {
                "ok": False,
                "reason": (
                    f"Not enough embedded memories to fit (need ≥{min_memories}). "
                    "Run the backfill first."
                ),
            }
        await db.commit()
        invalidate_clusters()
        invalidate_projection()
        return {"ok": True, "summary": summary}
    except Exception as e:
        logger.exception("Admin fit-clusters failed")
        return {
            "ok": False,
            "reason": f"{type(e).__name__}: {str(e)[:300]}",
        }
