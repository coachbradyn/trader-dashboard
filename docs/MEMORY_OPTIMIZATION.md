# Memory System Optimization — Phase 1

**Branch:** `claude/optimize-memory-gaussian-KQ7oi`
**Goal:** Reduce Claude API token burn by replacing broad memory injection
with semantic top-K retrieval and enabling Anthropic prompt caching.

This document enumerates every change landing in this commit and explains the
reasoning. Phase 2 (gaussian cluster scoring) and Phase 3 (3D embedding
visualization) are scoped as follow-ups.

---

## Problem

`_build_system_prompt()` in `backend/app/services/ai_service.py` was injecting
up to **20 memories** (`importance >= 6`, sorted by importance+recency) into
every system prompt — regardless of whether those memories were relevant to
the current ticker, strategy, or user query. Every AI call paid the token
cost for the full set, and the same base prompt was re-sent uncached on
every request.

At ~300 chars per memory × 20 memories = ~1,500 tokens of memory context
alone, per call. Multiply by hundreds of calls/day across signal evaluations,
briefings, and ask-henry traffic, and the math dominates the credit bill.

---

## What changed

### 1. New: `EmbeddingProvider` abstraction
**File:** `backend/app/services/embeddings.py` *(new)*

- `EmbeddingProvider` Protocol with `embed()` / `embed_batch()` methods.
- `VoyageProvider` — async implementation using Voyage AI.
  - Default model: `voyage-3-lite` (512-dim, cheapest).
  - Supports `voyage-3` (1024-dim) and `voyage-3-large` via config.
  - Uses `input_type="query"` for search queries and `"document"` for
    stored memories — Voyage's recommended practice.
  - All failures swallowed → returns `None`. Embeddings are a ranking
    signal, not a correctness requirement.
- `get_embedding_provider()` factory — cached, returns `None` when
  `VOYAGE_API_KEY` is unset or `embedding_enabled=False`.
- `cosine_similarity()` helper for in-Python ranking.

**Why Voyage over local:** Embedding cost is negligible (~$0.02/1M tokens on
voyage-3-lite) and API latency is a non-issue for write-path embedding and
single-query search. Local model was evaluated and rejected — RAM footprint
on Railway containers and cold-start cost outweigh the tiny cost savings.
The abstraction makes local a drop-in swap later if privacy needs change.

### 2. `henry_memory` schema: new columns
**Files:**
- `backend/app/models/henry_memory.py` — model update
- `backend/alembic/versions/j041526374e6_add_memory_embeddings.py` — migration *(new)*

Added:
- `embedding` (`JSON`, nullable) — the vector as a float list.
- `embedding_model` (`String(50)`, nullable) — which model produced the
  vector. Vectors from different models are **not** comparable, so
  retrieval filters to the current model before ranking.

**Why JSON not pgvector:** Keeps the migration portable across Railway
Postgres and local sqlite dev DBs. For <10k memories, in-Python cosine
similarity is sub-10ms. A follow-up migration can port to pgvector +
ivfflat index once scale demands it.

### 3. `save_memory()` — auto-embed on write
**File:** `backend/app/services/ai_service.py`

Every new memory is embedded inline before insert. If the embedder fails or
is unconfigured, the memory is still saved (just without a vector) and
retrieval falls back to importance ordering for it.

### 4. `_build_system_prompt()` — top-K semantic retrieval
**File:** `backend/app/services/ai_service.py`

New optional parameter: `query_text: str = None`.

Retrieval now runs in two modes:

1. **Semantic (preferred)** — when `query_text` is provided and embeddings
   are available:
   - Embed the query with `input_type="query"`.
   - Pull a scope-filtered candidate pool (up to 200) where
     `embedding_model` matches current provider and `embedding IS NOT NULL`.
   - Rank by `cosine_similarity + (importance / 50)` — a minor importance
     nudge lets a wildly relevant low-importance memory beat a middling
     high-importance one.
   - Return top `MEMORY_TOP_K` (default **8**).
2. **Fallback** — scope-filtered, `importance >= 6`, limited to
   `MEMORY_TOP_K_FALLBACK` (default **10**). This is what callers get when
   `query_text` isn't threaded through yet, and is still a tighter filter
   than the previous `limit=20` broad scan.

Scope filter rule: when `ticker` or `strategy` is set, candidates must
match OR have null scope (so portfolio-wide lessons still surface on
ticker-specific calls).

**Expected token reduction on memory block:** ~50% from the cap alone
(20→10 fallback, 20→8 semantic), plus further gains from relevance
(smaller K is viable when memories are actually relevant).

### 5. Anthropic prompt caching (`cache_control`)
**File:** `backend/app/services/ai_provider.py`

The system prompt is now wrapped as a single cached content block with
`cache_control: {type: "ephemeral"}`. After the first call, repeated
prefixes are billed at ~10% of standard input token cost for a 5-minute
window. Disabled when `web_search=True` (web search forces tool-call
reshaping that can defeat the cache).

Toggle: `PROMPT_CACHE_ENABLED=false` to disable.

### 6. Config surface
**File:** `backend/app/config.py`

New settings:

| Key | Default | Purpose |
|---|---|---|
| `VOYAGE_API_KEY` | `""` | Voyage AI key. When blank, embeddings are disabled and retrieval falls back to importance ordering. |
| `EMBEDDING_MODEL` | `voyage-3-lite` | Voyage model. Change to `voyage-3` for higher quality (2× dims, still cheap). |
| `EMBEDDING_ENABLED` | `true` | Kill switch. Set false to bypass embedding even if key is set. |
| `MEMORY_TOP_K` | `8` | Max memories injected via semantic retrieval. |
| `MEMORY_TOP_K_FALLBACK` | `10` | Max memories when no `query_text` is available. |
| `PROMPT_CACHE_ENABLED` | `true` | Toggle Anthropic cache_control. |

### 7. Dependency
**File:** `backend/requirements.txt`

Added `voyageai>=0.3.0`.

---

## Deployment checklist (Railway)

1. Set `VOYAGE_API_KEY` in Railway env vars. Get one at
   <https://dash.voyageai.com/>. Free tier covers initial usage.
2. Deploy — `pip install -r requirements.txt` will pick up `voyageai`.
3. Run migrations: `alembic upgrade head` (adds `embedding` +
   `embedding_model` columns; safe if re-run).
4. **Backfill existing memories** — new memories get embeddings on write,
   but pre-existing rows have `embedding=NULL` and will only surface
   through the fallback path. A backfill script is left as a follow-up
   (see Phase 2 below). In the meantime, they still work via importance
   ordering — nothing breaks.

---

## What to monitor

- **`AIUsage` table** — `input_tokens` per `function_name` before/after
  deploy. Expect a drop on calls that inject memories.
- **Anthropic dashboard** — look for `cache_read_input_tokens` and
  `cache_creation_input_tokens`. Cache reads are billed at 10% of base.
- **Voyage spend** — should be rounding-error but worth confirming.
- **Log lines** — `voyageai` warnings indicate embed failures. Memories
  still save without embeddings, but semantic retrieval can't rank them.

---

## Rollback

All three levers are independently toggleable:

- `EMBEDDING_ENABLED=false` → skips embedding on write and semantic
  retrieval on read. System returns to importance-ordered fallback.
- `PROMPT_CACHE_ENABLED=false` → disables cache_control, reverts to
  plain-string system prompt.
- Migration downgrade: `alembic downgrade -1` drops the two new columns.

---

## Follow-ups (not in this commit)

### Phase 1.5 — tighter integration *(landed)*
- ✅ `_call_claude_async` defaults `query_text=prompt` so every caller that
  routes through it (signal evaluation, scheduled review, AI portfolio,
  autonomous trading, api/ai_portfolio) hits the semantic path automatically.
- ✅ `query_trades` (ask_henry) passes `query_text=question` — the raw user
  question is a cleaner retrieval signal than the wrapping prompt template.
- ✅ Every direct `_build_system_prompt(...)` callsite now passes a
  `query_text` (nightly_review, morning_briefing, resolve_conflict,
  price_target analysis).
- ✅ Backfill script at `backend/scripts/backfill_memory_embeddings.py`.
  Run with `python -m scripts.backfill_memory_embeddings` from backend/.
  Idempotent, batched, resumable.

### Phase 1.6 — observability (not yet)
- Add `/api/memory/embeddings/health` endpoint: reports `total`,
  `with_embedding`, `model_distribution`.

### Phase 2 — Gaussian cluster scoring *(landed)*

Retrieval now blends cosine similarity with a gaussian mixture model
(GMM) posterior over the query — memories in the same gaussian
"neighborhood" as the query get boosted. Fully backwards-compatible: when
clustering hasn't run yet, retrieval degrades to pure cosine + importance.

**New files:**
- `backend/app/services/memory_clustering.py` — pure-numpy diagonal GMM
  (k-means++ init, EM iterations, variance floors). No sklearn dependency.
  Includes `fit_memory_clusters()` (write path) and
  `score_query_clusters()` (read path, 5-min process-local cache).
- `backend/scripts/fit_memory_clusters.py` — on-demand CLI fit. Run after
  backfill or model swaps.
- `backend/alembic/versions/k152637485f7_add_memory_cluster_id.py` —
  adds `cluster_id` column + index.

**Modified:**
- `henry_memory.cluster_id` — nullable `Integer`, populated on fit.
- `henry_stats_engine.compute_all_stats()` — runs
  `_compute_memory_clusters` every Nth cycle (default 3). Heavy enough
  that per-run is wasteful; cheap enough that once every ~6h is fine.
- `_build_system_prompt` retrieval — score becomes
  `cosine_sim + importance_nudge + cluster_weight × P(cluster | query)`.
  Unclustered memories → 0 cluster bump (no penalty).
- `/api/memory/clusters` — returns current GMM (k, weights, member
  counts, optionally centroids). Used by the Phase 3 viz and for debug.
- `/api/memory/embeddings/health` — reports coverage + cluster
  distribution. Quick way to check "is the backfill done, are clusters
  fit yet."

**Algorithm choices:**
- **Diagonal covariance only.** Full covariance in 512-D is 262K params
  per cluster — overfits badly at our memory count (~100–5K) and is 50×
  slower. Diagonal keeps the model well-conditioned.
- **L2-normalize embeddings before fit.** Voyage vectors are already
  unit-norm but we re-normalize defensively. This makes diagonal GMM a
  reasonable approximation of a von Mises-Fisher mixture (the "proper"
  distribution on the sphere).
- **Adaptive K:** `K = clamp(3, round(sqrt(N / 2)), 15)`. 100 memories →
  7 clusters. 1000 → 15 (capped). Prevents over-splitting at low N.
- **k-means++ seeding + deterministic RNG** — same fit every time,
  modulo data changes.
- **Variance floor at 1e-4** — prevents single-member clusters from
  collapsing to zero variance.
- **Stable logsumexp** everywhere E-step touches — no overflow for
  extreme log-probabilities.

**Config knobs:**

| Key | Default | Purpose |
|---|---|---|
| `MEMORY_CLUSTERING_ENABLED` | `true` | Master switch. |
| `MEMORY_CLUSTER_WEIGHT` | `0.3` | Score weight of P(cluster \| query). Raise to make cluster membership matter more; 0 disables boost without disabling fits. |
| `MEMORY_CLUSTER_FIT_EVERY_N_RUNS` | `3` | Fit frequency (stats engine runs every ~2h, so default = every ~6h). |

**Subsuming the hardcoded confidence buckets** (noted at the start of
Phase 2 planning) is *not* part of this commit. Those buckets
(`henry_stats_engine.py:186-188`) operate on `PortfolioAction.confidence`
outcomes, not memory embeddings. Replacing them with a gaussian is a
separate, smaller change — deferred to avoid conflating two unrelated
analyses in one PR.

**When to run `fit_memory_clusters.py` manually:**
- Right after `backfill_memory_embeddings.py` — otherwise your first fit
  only sees newly-written memories.
- After switching `EMBEDDING_MODEL` — old-model vectors aren't comparable
  to new-model vectors, so existing clusters are invalid.

### Phase 3 — 3D embedding visualization *(landed)*

Interactive 3D map of Henry's memory embeddings, accessible as the new
"3D Map" tab on the Henry page.

**Backend:**
- `backend/app/services/memory_projection.py` *(new)* — PCA from 512-D
  (or 1024-D) to 3-D using pure numpy (`eigh` on the d×d covariance,
  faster than full SVD for our shape). L2-normalizes embeddings before
  projection so the viz geometry matches the clustering pipeline.
  Projects memories and cluster centroids together so they share the
  same PCA basis + normalization box. Cached in-process for 10 minutes.
- `backend/app/api/memory.py` — new endpoint
  `GET /api/memory/embeddings/projection?force=false`. Returns
  `{memories: [{id,x,y,z,cluster_id,importance,memory_type,ticker,...}],
   clusters: [{id,x,y,z,member_count,weight}]}`.

**Frontend:**
- `frontend/src/components/ai/MemoryMap3D.tsx` *(new)* — react-three-fiber
  scene. Each memory is a sphere colored by `cluster_id`, sized by
  `importance` (1→10 maps to radius 0.008→0.028). Cluster centroids
  render as translucent spheres sized by `sqrt(weight)` so a 4×-bigger
  cluster doesn't render 4× as large. Deterministic 15-color palette
  keyed by `cluster_id % 15` — same cluster always gets the same color
  across refits. Unclustered memories render gray.
- Interactions: `OrbitControls` from `@react-three/drei` for drag-to-
  rotate, scroll-zoom, right-click pan. Hover pulses the node up 60%
  and shows a tooltip with memory type, ticker, strategy, importance,
  and content preview (first 160 chars).
- Legend: all active clusters with member counts.
- Lazy-loaded via Next `dynamic({ ssr: false })` so three.js isn't
  shipped to the Chat/Activity tabs.
- Deps added to `frontend/package.json`: `three`, `@react-three/fiber`,
  `@react-three/drei`, `@types/three`.

**Why PCA, not UMAP:**
- Zero new Python dependencies (UMAP needs `umap-learn` + numba + llvmlite
  — ~200 MB).
- Honest projection: 3 axes that maximize variance in the original space.
- UMAP produces visually tighter clusters but can also invent structure
  that doesn't exist. PCA won't lie to you.
- If you want UMAP later, swap `_pca_3d` in `memory_projection.py`
  without changing the endpoint contract.

**How to use:**
1. After a fresh deploy, wait for or manually run
   `python -m scripts.fit_memory_clusters` so clusters exist.
2. Open Henry page → 3D Map tab.
3. Drag to rotate. Hover any node to see its content.
4. Click "Refresh projection" after running a backfill or re-fit to
   bypass the 10-minute cache.

### Phase 4 — pgvector migration
- Only when memory count >10k or in-Python ranking latency becomes
  measurable. Alembic migration: enable extension, add
  `embedding_vec VECTOR(512)` column, populate from JSON, drop JSON
  column, add ivfflat index.
