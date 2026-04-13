"""
Memory Clustering — Gaussian Mixture Model over memory embeddings.

Implements a diagonal-covariance GMM in pure numpy (no sklearn dep). Fit
periodically by the stats engine; results stored as a HenryStats row of
type "memory_clusters". Retrieval uses P(cluster | query) as a scoring
boost alongside cosine similarity.

Design choices:
  - Diagonal covariance only. Full covariance in 512-D needs 512×512 params
    per cluster — overfits badly with our memory count, and 50× slower.
  - k-means++ initialization for stable EM.
  - Adaptive cluster count: K = max(MIN_K, min(MAX_K, round(sqrt(N/2)))).
  - Floors on variance so single-member clusters don't collapse to zero.
  - All math in float32 to keep JSON payload reasonable (~4 KB per cluster).

The clustering is a ranking signal, not a correctness requirement. Any
failure path returns a no-op result — retrieval falls back to pure cosine.
"""

from __future__ import annotations

import logging
import math
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)


# ─── Config ──────────────────────────────────────────────────────────────────

MIN_MEMORIES_TO_FIT = 20        # Below this, clustering is noise — skip
MIN_K = 3
MAX_K = 15
MAX_EM_ITERS = 60
EM_TOL = 1e-3                   # Relative log-likelihood improvement to stop
VARIANCE_FLOOR = 1e-4           # Prevent zero-variance degenerate clusters
KMEANS_PP_SEED = 42             # Deterministic init for reproducible clusters


# ─── Fit: k-means++ init + diagonal GMM EM ───────────────────────────────────

def _kmeans_pp_init(X: np.ndarray, k: int, rng: np.random.Generator) -> np.ndarray:
    """k-means++ seeding. Returns (k, d) initial means."""
    n = X.shape[0]
    idx0 = int(rng.integers(0, n))
    centers = [X[idx0]]
    # Squared distance from each point to nearest chosen center
    d2 = np.sum((X - centers[0]) ** 2, axis=1)
    for _ in range(1, k):
        probs = d2 / (d2.sum() + 1e-12)
        next_idx = int(rng.choice(n, p=probs))
        centers.append(X[next_idx])
        new_d2 = np.sum((X - centers[-1]) ** 2, axis=1)
        d2 = np.minimum(d2, new_d2)
    return np.stack(centers, axis=0)


def _log_gauss_diag(X: np.ndarray, mean: np.ndarray, var: np.ndarray) -> np.ndarray:
    """Log-pdf of diagonal gaussian for each row of X. Returns (n,)."""
    # log N(x; mu, diag(sigma^2)) = -0.5 * sum( (x-mu)^2/sigma^2 + log(2*pi*sigma^2) )
    d = X.shape[1]
    diff = X - mean
    # Guard against zero variance (shouldn't happen with floor, but belt-and-suspenders)
    var_safe = np.maximum(var, VARIANCE_FLOOR)
    mahal = np.sum((diff * diff) / var_safe, axis=1)
    logdet = np.sum(np.log(2.0 * math.pi * var_safe))
    return -0.5 * (mahal + logdet)


def _fit_diag_gmm(
    X: np.ndarray, k: int, seed: int = KMEANS_PP_SEED
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, float]:
    """
    Fit a diagonal-covariance GMM via EM.
    Returns (means, vars, weights, responsibilities, final_log_likelihood).
      means:  (k, d)
      vars:   (k, d)
      weights:(k,)
      resp:   (n, k)  — posterior responsibilities P(cluster | x)
    """
    n, d = X.shape
    rng = np.random.default_rng(seed)

    # Init: k-means++ means; shared global variance; uniform weights.
    means = _kmeans_pp_init(X, k, rng)
    global_var = np.maximum(X.var(axis=0), VARIANCE_FLOOR)
    vars_ = np.tile(global_var, (k, 1))
    weights = np.full(k, 1.0 / k)

    prev_ll = -np.inf
    for it in range(MAX_EM_ITERS):
        # E-step: log responsibilities
        log_probs = np.zeros((n, k), dtype=np.float64)
        for j in range(k):
            log_probs[:, j] = np.log(weights[j] + 1e-12) + _log_gauss_diag(
                X, means[j], vars_[j]
            )
        # Stable logsumexp per row
        row_max = log_probs.max(axis=1, keepdims=True)
        ll = float(np.sum(row_max) + np.sum(np.log(np.sum(np.exp(log_probs - row_max), axis=1) + 1e-12)))
        resp = np.exp(log_probs - row_max)
        resp = resp / (resp.sum(axis=1, keepdims=True) + 1e-12)

        # M-step
        nk = resp.sum(axis=0) + 1e-12  # (k,)
        weights = nk / n
        for j in range(k):
            r = resp[:, j : j + 1]
            means[j] = (r * X).sum(axis=0) / nk[j]
            diff = X - means[j]
            vars_[j] = np.maximum(
                (r * (diff * diff)).sum(axis=0) / nk[j], VARIANCE_FLOOR
            )

        # Convergence check on relative log-likelihood improvement
        if prev_ll != -np.inf:
            rel = abs(ll - prev_ll) / (abs(prev_ll) + 1e-12)
            if rel < EM_TOL:
                logger.debug(f"GMM EM converged at iter {it} (rel Δll={rel:.2e})")
                break
        prev_ll = ll

    return means, vars_, weights, resp, prev_ll


def _choose_k(n: int) -> int:
    """Adaptive K: more data → more clusters, capped at MAX_K."""
    k = round(math.sqrt(n / 2.0))
    return max(MIN_K, min(MAX_K, int(k)))


# ─── Public API ──────────────────────────────────────────────────────────────


async def fit_memory_clusters(db) -> Optional[dict]:
    """
    Fit GMM over all current memory embeddings. Writes cluster_id to each
    memory row and upserts a HenryStats row of type "memory_clusters" with
    the cluster parameters. Returns a small summary dict (or None if no fit).

    Caller is expected to commit the session — this function flushes updates
    but does not commit, matching the pattern of other _compute_ functions
    in henry_stats_engine.
    """
    from sqlalchemy import select, update, delete, and_
    from app.models import HenryMemory, HenryStats
    from app.utils.utc import utcnow
    from app.config import get_settings

    settings = get_settings()
    if not getattr(settings, "memory_clustering_enabled", True):
        logger.info("memory_clustering_enabled=false — skipping fit")
        return None

    # Pull all embedded memories. We cluster per embedding_model so a mid-flight
    # model swap doesn't mix vectors. In practice we only have one active model
    # at a time, so we just take whichever is most common.
    result = await db.execute(
        select(HenryMemory.id, HenryMemory.embedding, HenryMemory.embedding_model)
        .where(HenryMemory.embedding.is_not(None))
    )
    rows = result.all()
    if not rows:
        logger.info("No embedded memories — skipping cluster fit")
        return None

    # Pick dominant model
    from collections import Counter
    model_counts = Counter(r.embedding_model for r in rows if r.embedding_model)
    if not model_counts:
        return None
    dominant_model, _ = model_counts.most_common(1)[0]
    rows = [r for r in rows if r.embedding_model == dominant_model]

    n = len(rows)
    if n < MIN_MEMORIES_TO_FIT:
        logger.info(f"Only {n} embedded memories (<{MIN_MEMORIES_TO_FIT}) — skipping cluster fit")
        return None

    # Build matrix. Handle ragged embeddings defensively (shouldn't happen
    # with single-model filter, but a safety net).
    dims_counter = Counter(len(r.embedding) for r in rows)
    dims, _ = dims_counter.most_common(1)[0]
    valid = [r for r in rows if len(r.embedding) == dims]
    if len(valid) < MIN_MEMORIES_TO_FIT:
        logger.warning(f"Only {len(valid)} dim-consistent memories — skipping")
        return None

    ids = [r.id for r in valid]
    X = np.array([r.embedding for r in valid], dtype=np.float64)

    # L2-normalize vectors so cosine distance ≈ Euclidean on the unit sphere.
    # This makes diagonal GMM a reasonable approximation of a von Mises-Fisher
    # mixture, which is the "proper" distribution on the sphere. Voyage
    # embeddings are already unit-norm but we re-normalize for safety.
    norms = np.linalg.norm(X, axis=1, keepdims=True)
    X = X / np.maximum(norms, 1e-12)

    k = _choose_k(n)
    logger.info(f"Fitting GMM: n={n}, dims={dims}, k={k}, model={dominant_model}")

    means, vars_, weights, resp, ll = _fit_diag_gmm(X, k)

    # Hard assignments
    assignments = resp.argmax(axis=1).tolist()
    id_to_cluster = dict(zip(ids, assignments))

    # ─── Diagnostics ──────────────────────────────────────────────────────
    # BIC = -2·logL + p·log(n), where p = free parameters.
    # For diagonal GMM: k*(2*d) + (k-1) weights.
    n_params = k * (2 * dims) + (k - 1)
    bic = -2.0 * ll + n_params * math.log(max(n, 2))

    # Per-memory silhouette (in a reduced form): distance to own cluster
    # mean vs distance to nearest other cluster mean. Cheap proxy for true
    # silhouette and useful for the viz's "how well does this memory fit"
    # coloring. Stored per-memory on the row below.
    from collections import defaultdict
    silhouettes: dict[str, float] = {}
    for i, mid in enumerate(ids):
        own = int(assignments[i])
        x = X[i]
        own_dist = float(np.sqrt(np.sum((x - means[own]) ** 2)))
        other_dists = [
            float(np.sqrt(np.sum((x - means[j]) ** 2)))
            for j in range(k)
            if j != own
        ]
        nearest_other = min(other_dists) if other_dists else own_dist + 1e-6
        denom = max(own_dist, nearest_other)
        sil = (nearest_other - own_dist) / denom if denom > 0 else 0.0
        silhouettes[mid] = float(max(-1.0, min(1.0, sil)))

    # Per-cluster prototype: the memory closest to each centroid (minimum
    # Euclidean distance since X is L2-normalized → proportional to cosine).
    prototype_ids: dict[int, str] = {}
    for j in range(k):
        members_idx = [i for i, a in enumerate(assignments) if a == j]
        if not members_idx:
            continue
        dists = [
            float(np.sqrt(np.sum((X[i] - means[j]) ** 2)))
            for i in members_idx
        ]
        proto_local = int(np.argmin(dists))
        prototype_ids[j] = ids[members_idx[proto_local]]

    # Write assignments + silhouette back. Single UPDATE per cluster with
    # IN-list is fast. Clear existing cluster_ids on rows that weren't in
    # this fit (e.g., new embeddings added between fit runs).
    by_cluster = defaultdict(list)
    for mid, cid in id_to_cluster.items():
        by_cluster[int(cid)].append(mid)

    # First: null out all cluster_ids for this model. Simpler than diffing.
    await db.execute(
        update(HenryMemory)
        .where(HenryMemory.embedding_model == dominant_model)
        .values(cluster_id=None, cluster_silhouette=None)
    )
    for cid, mids in by_cluster.items():
        await db.execute(
            update(HenryMemory)
            .where(HenryMemory.id.in_(mids))
            .values(cluster_id=cid)
        )
    # Silhouette is per-memory — update in batches by approximate quartile
    # to avoid thousands of single-row UPDATEs. 4 batches is enough
    # resolution for the viz color gradient and keeps write cost tame.
    sil_batches: dict[int, list[str]] = {0: [], 1: [], 2: [], 3: []}
    for mid, sil in silhouettes.items():
        # Quantize to 4 buckets (-1..-0.5, -0.5..0, 0..0.5, 0.5..1)
        q = int(min(3, max(0, (sil + 1.0) * 2)))
        sil_batches[q].append(mid)
    # Store the real float (not the bucket) on each row for fidelity;
    # batching by bucket is just to minimize round trips on the quantized
    # value — but we actually want the real value. Simpler: bulk update in
    # chunks of 500 with CASE expressions. At our scale, do it in a loop.
    bulk = list(silhouettes.items())
    CHUNK = 500
    for i in range(0, len(bulk), CHUNK):
        chunk = bulk[i : i + CHUNK]
        for mid, sil in chunk:
            await db.execute(
                update(HenryMemory)
                .where(HenryMemory.id == mid)
                .values(cluster_silhouette=sil)
            )

    # ─── LLM cluster labels (Gemini, ~$0.001/refit) ──────────────────────
    # Labels run after the DB writes so the fit persists even if labeling
    # fails. Each cluster gets a 3-5 word label via Gemini Flash — 30×
    # cheaper than Claude for this kind of short-output task.
    labels: dict[int, str] = {}
    try:
        labels = await _generate_cluster_labels(db, prototype_ids, assignments, ids)
    except Exception as e:
        logger.warning(f"Cluster labeling failed (fit still succeeded): {e}")

    # Build cluster metadata for HenryStats.data — cast to float32 to halve
    # payload size; retrieval converts back to float64 for scoring.
    clusters_payload = []
    for j in range(k):
        clusters_payload.append({
            "id": j,
            "weight": float(weights[j]),
            "member_count": int(np.sum(resp.argmax(axis=1) == j)),
            "centroid": means[j].astype(np.float32).tolist(),
            "variance_diag": vars_[j].astype(np.float32).tolist(),
            "prototype_memory_id": prototype_ids.get(j),
            "label": labels.get(j),
        })

    data = {
        "clusters": clusters_payload,
        "model_name": dominant_model,
        "dims": int(dims),
        "n_memories_fit": n,
        "k": int(k),
        "log_likelihood": float(ll),
        "bic": float(bic),
        "avg_silhouette": float(np.mean(list(silhouettes.values()))) if silhouettes else 0.0,
        "fit_at": utcnow().isoformat() + "Z",
    }

    # Upsert the single "memory_clusters" stat row. Clear existing, insert fresh.
    await db.execute(
        delete(HenryStats).where(
            and_(
                HenryStats.stat_type == "memory_clusters",
                HenryStats.strategy.is_(None),
                HenryStats.ticker.is_(None),
                HenryStats.portfolio_id.is_(None),
            )
        )
    )
    db.add(HenryStats(
        stat_type="memory_clusters",
        strategy=None,
        ticker=None,
        portfolio_id=None,
        data=data,
        period_days=0,
        computed_at=utcnow(),
    ))

    return {
        "n_memories_fit": n,
        "k": k,
        "model": dominant_model,
        "log_likelihood": ll,
    }


# ─── Runtime scoring (called from ai_service retrieval) ──────────────────────


class _ClusterCache:
    """Process-local cache of the latest cluster fit. 5-minute TTL."""

    def __init__(self):
        self.data: Optional[dict] = None
        self.loaded_at: float = 0.0
        self.ttl_seconds: float = 300.0

    def fresh(self) -> bool:
        import time
        return self.data is not None and (time.time() - self.loaded_at) < self.ttl_seconds

    def set(self, data: Optional[dict]):
        import time
        self.data = data
        self.loaded_at = time.time()

    def invalidate(self):
        self.data = None
        self.loaded_at = 0.0


_CACHE = _ClusterCache()


async def _load_clusters_data(db) -> Optional[dict]:
    """Fetch the latest memory_clusters HenryStats row. Cached for 5 min."""
    if _CACHE.fresh():
        return _CACHE.data

    from sqlalchemy import select
    from app.models import HenryStats
    try:
        result = await db.execute(
            select(HenryStats)
            .where(HenryStats.stat_type == "memory_clusters")
            .order_by(HenryStats.computed_at.desc())
            .limit(1)
        )
        row = result.scalar_one_or_none()
        data = row.data if row else None
    except Exception as e:
        logger.debug(f"Cluster data load failed: {e}")
        data = None

    _CACHE.set(data)
    return data


async def score_query_clusters(db, query_vec: list[float], model_name: str) -> dict[int, float]:
    """
    Return {cluster_id: P(cluster | query)} for the current fit, or {} if
    clustering is unavailable / stale / model-mismatched.

    Uses a stable softmax over log_weight + log_gauss(query | cluster).
    """
    data = await _load_clusters_data(db)
    if not data or data.get("model_name") != model_name:
        return {}

    clusters = data.get("clusters") or []
    if not clusters:
        return {}

    dims = data.get("dims")
    if dims is None or len(query_vec) != dims:
        return {}

    # Normalize query vector (same transform as fit) so cluster geometry lines up.
    q = np.asarray(query_vec, dtype=np.float64)
    q_norm = np.linalg.norm(q)
    if q_norm < 1e-12:
        return {}
    q = q / q_norm

    log_scores = np.empty(len(clusters), dtype=np.float64)
    ids = []
    for i, c in enumerate(clusters):
        ids.append(int(c["id"]))
        mean = np.asarray(c["centroid"], dtype=np.float64)
        var = np.asarray(c["variance_diag"], dtype=np.float64)
        var = np.maximum(var, VARIANCE_FLOOR)
        # Inline log N(q; mu, diag(var))
        diff = q - mean
        mahal = float(np.sum((diff * diff) / var))
        logdet = float(np.sum(np.log(2.0 * math.pi * var)))
        log_scores[i] = math.log(float(c.get("weight", 1.0)) + 1e-12) - 0.5 * (mahal + logdet)

    # Softmax → posterior
    m = float(log_scores.max())
    exps = np.exp(log_scores - m)
    probs = exps / (exps.sum() + 1e-12)
    return {ids[i]: float(probs[i]) for i in range(len(ids))}


def invalidate_cache() -> None:
    """Forces a reload on next scoring call. Invoke after manual re-fits."""
    _CACHE.invalidate()


# ─── LLM-backed cluster labeling (Gemini Flash, ~$0.001 per refit) ──────────


async def _generate_cluster_labels(
    db,
    prototype_ids: dict[int, str],
    assignments: list[int],
    memory_ids: list[str],
) -> dict[int, str]:
    """
    Ask Gemini Flash to produce 3-5 word labels per cluster based on up to
    8 memory previews. Returns {cluster_id: label}. Failures return {} — the
    fit still persists without labels, just displays as 'cluster N'.

    Gemini is ~30x cheaper than Claude for this short-output task. A full
    refit with 15 clusters costs <$0.01. Uses the existing ai_provider
    routing so fallback to Claude happens on Gemini failure.
    """
    from sqlalchemy import select
    from app.models import HenryMemory
    from app.services.ai_provider import call_ai
    from collections import defaultdict

    # Group memory IDs by cluster
    by_cluster: dict[int, list[str]] = defaultdict(list)
    for mid, cid in zip(memory_ids, assignments):
        by_cluster[int(cid)].append(mid)

    # Pull content previews (up to 8 per cluster, prioritizing the prototype)
    all_ids_needed: set[str] = set()
    for cid, mids in by_cluster.items():
        proto = prototype_ids.get(cid)
        picks = [proto] if proto else []
        # Take first 7 non-proto memories — they're already approximately
        # ordered by importance/recency from the fetch query upstream.
        for mid in mids[:8]:
            if mid not in picks:
                picks.append(mid)
            if len(picks) >= 8:
                break
        all_ids_needed.update(p for p in picks if p)
        by_cluster[cid] = [p for p in picks if p]

    if not all_ids_needed:
        return {}

    result = await db.execute(
        select(HenryMemory.id, HenryMemory.content, HenryMemory.memory_type)
        .where(HenryMemory.id.in_(all_ids_needed))
    )
    id_to_row = {r.id: r for r in result.all()}

    labels: dict[int, str] = {}

    async def label_one(cid: int, mids: list[str]) -> tuple[int, Optional[str]]:
        # Build a compact bulleted preview list (160 chars each to keep
        # input token count low — per-cluster cost is dominated by input).
        lines = []
        for mid in mids[:8]:
            row = id_to_row.get(mid)
            if not row:
                continue
            preview = (row.content or "")[:160].strip().replace("\n", " ")
            lines.append(f"- [{row.memory_type}] {preview}")
        if not lines:
            return cid, None

        prompt = (
            "The following are memories of a trading AI that clustered together in embedding space. "
            "Produce a 3-5 word label that captures their common theme. "
            "Return ONLY the label text — no quotes, no periods, no explanation.\n\n"
            + "\n".join(lines)
        )
        system = (
            "You produce extremely terse labels for clusters of trading memories. "
            "Output a single label of 3-5 words, plain text, no punctuation at end."
        )
        try:
            text = await call_ai(
                system=system,
                prompt=prompt,
                function_name="memory_extraction",  # already Gemini-routed
                max_tokens=20,
            )
            if not text:
                return cid, None
            # Strip quotes, trailing punctuation, and any accidental newlines.
            clean = text.strip().strip('"').strip("'").strip()
            # First line only
            clean = clean.split("\n", 1)[0].strip()
            # Reject obvious failure sentinels
            if not clean or len(clean) > 60:
                return cid, None
            return cid, clean
        except Exception as e:
            logger.debug(f"Cluster {cid} label failed: {e}")
            return cid, None

    # Run all clusters in parallel — capped at 15 clusters by MAX_K so
    # this is at most 15 concurrent Gemini calls. Fine.
    import asyncio
    results = await asyncio.gather(
        *(label_one(cid, mids) for cid, mids in by_cluster.items()),
        return_exceptions=True,
    )
    for r in results:
        if isinstance(r, tuple):
            cid, lbl = r
            if lbl:
                labels[cid] = lbl
    return labels
