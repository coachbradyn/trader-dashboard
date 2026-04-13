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

### Phase 2 — Gaussian cluster scoring
- Periodic GMM (gaussian mixture model) fit over memory embeddings
  per cluster in `henry_stats_engine.py`.
- Retrieval score = `similarity × P(memory | cluster) × importance_weight`.
- Subsumes the hardcoded confidence buckets in
  `henry_stats_engine.py:186-188`.

### Phase 3 — 3D embedding visualization
- UMAP projection → 3D coords per memory.
- Endpoint: `GET /api/memory/embeddings/projection`.
- Frontend: react-three-fiber component. Nodes sized by `importance`,
  colored by `memory_type`, hover for content preview. Optional
  animate-on-retrieval to show which memories got pulled for a query.

### Phase 4 — pgvector migration
- Only when memory count >10k or in-Python ranking latency becomes
  measurable. Alembic migration: enable extension, add
  `embedding_vec VECTOR(512)` column, populate from JSON, drop JSON
  column, add ivfflat index.
