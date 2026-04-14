"""
Memory Projection — project high-dim embeddings to 3D for visualization.

Uses PCA (numpy only, no new dependency). Not as visually "clustery" as
UMAP, but honest about the true embedding geometry and free of extra
install weight. UMAP is a later swap behind the same endpoint contract
if the user wants tighter visual clusters.

Projection is cached in-process for 10 minutes. A refit of the GMM
(via henry_stats_engine) doesn't invalidate automatically — we accept
slight staleness on the viz in exchange for keeping the endpoint cheap.
"""

from __future__ import annotations

import logging
import math
import time
from typing import Optional

import numpy as np

from app.utils.utc import utcnow

logger = logging.getLogger(__name__)


_CACHE: dict = {
    "data": None,
    "expires_at": 0.0,
}
_CACHE_TTL_SECONDS = 600.0  # 10 minutes


def _pca_3d(X: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    Project rows of X (n, d) onto top-3 principal components.
    Returns (coords (n, 3), mean (d,), components (3, d)).

    Uses eigendecomposition of d×d covariance — faster than full SVD
    when d < n (typical here: d=512, n<5000).
    """
    mean = X.mean(axis=0)
    Xc = X - mean
    # Small regularization on diagonal guards against rank-deficient data.
    cov = (Xc.T @ Xc) / max(X.shape[0] - 1, 1)
    # eigh returns ascending eigenvalues. Take top 3.
    eigvals, eigvecs = np.linalg.eigh(cov)
    top_idx = np.argsort(eigvals)[::-1][:3]
    components = eigvecs[:, top_idx].T  # (3, d)
    coords = Xc @ components.T  # (n, 3)
    return coords, mean, components


def _normalize_coords(coords: np.ndarray) -> np.ndarray:
    """Center at origin and scale to roughly [-1, 1] per axis for frontend."""
    if coords.size == 0:
        return coords
    mins = coords.min(axis=0)
    maxs = coords.max(axis=0)
    span = np.maximum(maxs - mins, 1e-6)
    scaled = 2.0 * (coords - mins) / span - 1.0
    # Center each axis at its mean (avoid asymmetric bias from outliers)
    scaled = scaled - scaled.mean(axis=0)
    return scaled


async def compute_projection(db, force: bool = False) -> Optional[dict]:
    """
    Build the 3D projection payload. Returns None when unavailable (no
    embedded memories, or all memories are the same model but <3 rows).

    Payload shape — stable for the frontend:
      {
        "available": true,
        "model_name": "voyage-3-lite",
        "n_memories": 234,
        "fit_at": "2026-04-13T12:34:56Z",
        "memories": [
          {
            "id": "...",
            "x": 0.12, "y": -0.44, "z": 0.9,
            "cluster_id": 3,
            "importance": 7,
            "memory_type": "lesson",
            "ticker": "AAPL",
            "strategy_id": null,
            "validated": true,
            "content_preview": "Noted that S3 tends to fail..."
          },
          ...
        ],
        "clusters": [
          {"id": 0, "x": ..., "y": ..., "z": ..., "member_count": 23, "weight": 0.11},
          ...
        ]
      }
    """
    from sqlalchemy import select
    from app.models import HenryMemory, HenryStats
    from collections import Counter

    if not force and _CACHE["data"] is not None and time.time() < _CACHE["expires_at"]:
        return _CACHE["data"]

    # Pull all embedded memories. Only the fields we need for the payload.
    result = await db.execute(
        select(
            HenryMemory.id,
            HenryMemory.embedding,
            HenryMemory.embedding_model,
            HenryMemory.cluster_id,
            HenryMemory.cluster_id_override,  # carryover #32
            HenryMemory.cluster_silhouette,
            HenryMemory.importance,
            HenryMemory.reference_count,
            HenryMemory.retrieval_count,       # System 7 / #41
            HenryMemory.last_retrieved_at,     # System 7 / #41
            HenryMemory.memory_type,
            HenryMemory.ticker,
            HenryMemory.strategy_id,
            HenryMemory.validated,
            HenryMemory.content,
            HenryMemory.created_at,
            HenryMemory.updated_at,
        )
        .where(HenryMemory.embedding.is_not(None))
    )
    rows = result.all()
    if not rows:
        return None

    # Pick dominant model, same convention as clustering.
    model_counts = Counter(r.embedding_model for r in rows if r.embedding_model)
    if not model_counts:
        return None
    dominant_model, _ = model_counts.most_common(1)[0]
    rows = [r for r in rows if r.embedding_model == dominant_model]
    if len(rows) < 3:
        return None

    dims_counter = Counter(len(r.embedding) for r in rows)
    dims, _ = dims_counter.most_common(1)[0]
    rows = [r for r in rows if len(r.embedding) == dims]
    if len(rows) < 3:
        return None

    X = np.asarray([r.embedding for r in rows], dtype=np.float64)
    # Match the clustering pipeline: L2-normalize before projection so the
    # viz geometry lines up with GMM cluster geometry.
    norms = np.linalg.norm(X, axis=1, keepdims=True)
    X = X / np.maximum(norms, 1e-12)

    # Fetch cluster centroids (if any) so we can project them through the
    # same PCA basis and same normalization bounds as the memories. This
    # keeps centroids in-frame with the points they're meant to anchor.
    centroid_rows: list[np.ndarray] = []
    centroid_meta: list[dict] = []
    try:
        cluster_stat = (
            await db.execute(
                select(HenryStats)
                .where(HenryStats.stat_type == "memory_clusters")
                .order_by(HenryStats.computed_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if (
            cluster_stat
            and cluster_stat.data
            and cluster_stat.data.get("model_name") == dominant_model
        ):
            for c in cluster_stat.data.get("clusters", []):
                centroid = c.get("centroid")
                if centroid and len(centroid) == dims:
                    v = np.asarray(centroid, dtype=np.float64)
                    n = np.linalg.norm(v)
                    if n > 1e-12:
                        centroid_rows.append(v / n)
                        centroid_meta.append(c)
    except Exception as e:
        logger.debug(f"Cluster centroid fetch skipped: {e}")

    # Project memories + centroids together so they share PCA basis and
    # normalization box.
    if centroid_rows:
        stacked = np.vstack([X, np.asarray(centroid_rows, dtype=np.float64)])
    else:
        stacked = X
    all_coords, _, _ = _pca_3d(stacked)
    all_coords = _normalize_coords(all_coords)
    mem_coords = all_coords[: len(X)]
    cen_coords = all_coords[len(X):]

    memories_out = []
    for r, xyz in zip(rows, mem_coords):
        preview = (r.content or "")[:160].strip()
        created_iso = (
            r.created_at.isoformat() + "Z" if r.created_at else None
        )
        updated_iso = (
            r.updated_at.isoformat() + "Z" if r.updated_at else None
        )
        memories_out.append({
            "id": r.id,
            "x": float(xyz[0]),
            "y": float(xyz[1]),
            "z": float(xyz[2]),
            # Effective cluster respects manual override (carryover #32).
            "cluster_id": (
                r.cluster_id_override
                if r.cluster_id_override is not None
                else r.cluster_id
            ),
            "cluster_id_auto": r.cluster_id,
            "cluster_id_override": r.cluster_id_override,
            "silhouette": (
                float(r.cluster_silhouette)
                if r.cluster_silhouette is not None
                else None
            ),
            "importance": int(r.importance or 5),
            "reference_count": int(r.reference_count or 0),
            "retrieval_count": int(r.retrieval_count or 0),
            "last_retrieved_at": (
                r.last_retrieved_at.isoformat() + "Z"
                if r.last_retrieved_at else None
            ),
            "memory_type": r.memory_type,
            "ticker": r.ticker,
            "strategy_id": r.strategy_id,
            "validated": r.validated,
            "content_preview": preview,
            "created_at": created_iso,
            "updated_at": updated_iso,
        })

    # Pre-compute per-cluster aging stats (carryover #41). Each memory
    # contributes to its effective cluster (override or auto). Stats
    # attached to each cluster_out entry so the 3D tooltip can show
    # "hot" vs "stale" at a glance.
    from collections import defaultdict
    now_ts = utcnow()
    cluster_aging: dict[int, dict] = defaultdict(
        lambda: {
            "total": 0,
            "never_retrieved": 0,
            "decayed": 0,           # importance < 3
            "recency_days_sum": 0.0,
            "recency_days_count": 0,
            "avg_importance_sum": 0.0,
        }
    )
    for r in rows:
        cid = (
            r.cluster_id_override
            if r.cluster_id_override is not None
            else r.cluster_id
        )
        if cid is None:
            continue
        bucket = cluster_aging[int(cid)]
        bucket["total"] += 1
        bucket["avg_importance_sum"] += float(r.importance or 0.0)
        if r.importance is not None and float(r.importance) < 3.0:
            bucket["decayed"] += 1
        if r.last_retrieved_at is None:
            bucket["never_retrieved"] += 1
        else:
            delta = (now_ts - r.last_retrieved_at).total_seconds() / 86400.0
            bucket["recency_days_sum"] += delta
            bucket["recency_days_count"] += 1

    clusters_out = []
    for m, xyz in zip(centroid_meta, cen_coords):
        cid = int(m.get("id"))
        aging = cluster_aging.get(cid, {})
        total = aging.get("total", 0) or 0
        recency_n = aging.get("recency_days_count", 0) or 0
        avg_recency_days = (
            round(aging["recency_days_sum"] / recency_n, 1)
            if recency_n > 0 else None
        )
        avg_importance = (
            round(aging["avg_importance_sum"] / total, 2)
            if total > 0 else None
        )
        clusters_out.append({
            "id": cid,
            "x": float(xyz[0]),
            "y": float(xyz[1]),
            "z": float(xyz[2]),
            "member_count": int(m.get("member_count", 0)),
            "weight": float(m.get("weight", 0.0)),
            "label": m.get("label"),
            "prototype_memory_id": m.get("prototype_memory_id"),
            # Aging metrics — carryover #41. None when cluster has no
            # retrieved memories yet.
            "avg_days_since_retrieval": avg_recency_days,
            "never_retrieved_count": int(aging.get("never_retrieved", 0)),
            "decayed_count": int(aging.get("decayed", 0)),
            "avg_importance": avg_importance,
        })

    # Surface fit-quality diagnostics from the cluster_stat row so the
    # frontend quality panel has what it needs without a second round-trip.
    cluster_quality = {}
    try:
        if cluster_stat and cluster_stat.data:
            cluster_quality = {
                "k": cluster_stat.data.get("k"),
                "log_likelihood": cluster_stat.data.get("log_likelihood"),
                "bic": cluster_stat.data.get("bic"),
                "avg_silhouette": cluster_stat.data.get("avg_silhouette"),
                "n_memories_fit": cluster_stat.data.get("n_memories_fit"),
                "fit_at": cluster_stat.data.get("fit_at"),
            }
    except Exception:
        pass

    payload = {
        "available": True,
        "model_name": dominant_model,
        "n_memories": len(memories_out),
        "fit_at": utcnow().isoformat() + "Z",
        "projection_method": "pca_3d",
        "memories": memories_out,
        "clusters": clusters_out,
        "cluster_quality": cluster_quality,
    }

    _CACHE["data"] = payload
    _CACHE["expires_at"] = time.time() + _CACHE_TTL_SECONDS
    return payload


def invalidate_cache() -> None:
    _CACHE["data"] = None
    _CACHE["expires_at"] = 0.0
