# Henry AI Trader — Project Overview

**Full-stack AI-powered trading dashboard** that connects to TradingView Pine Script strategies, tracks live trades across multiple portfolios, and uses Claude AI ("Henry") as a semi-autonomous portfolio manager with a long-term memory system backed by Voyage embeddings and gaussian mixture retrieval.

**Stack:** Next.js 14 + FastAPI + PostgreSQL + Claude (Anthropic) + Gemini (Google) + Voyage AI embeddings
**Live:** [henrytrader.xyz](https://henrytrader.xyz) (frontend) · Railway (backend)

---

## What It Does

Henry AI Trader is a personal trading command center. Four Pine Script strategies run on TradingView and send trade signals via webhooks. The dashboard tracks every entry and exit, calculates portfolio performance in real-time, and uses Claude AI to analyze trades, resolve strategy conflicts, and recommend portfolio actions.

The system operates in three layers:

1. **Signal Ingestion** — TradingView webhooks fire on strategy entries/exits and indicator alerts. The backend validates, processes, and stores them.
2. **Portfolio Intelligence** — Real-time P&L tracking, equity curves, performance metrics (win rate, Sharpe, drawdown), and strategy leaderboards.
3. **AI Portfolio Management** — Henry (Claude) reviews trades, generates morning briefings, resolves multi-strategy conflicts, and recommends portfolio actions that the user approves or rejects.

---

## Pages

### Leaderboard (`/leaderboard`)
Real-time strategy performance rankings. Sortable by total return %, win rate, profit factor, Sharpe ratio, or trade count. Shows which strategies are outperforming across all portfolios.

### Live Feed (`/feed`)
Streaming trade activity across all strategies and portfolios. Auto-refreshes every 5 seconds. Shows entries, exits, P&L, and signal metadata (ADX, ATR, signal strength).

### Portfolios (`/portfolios`)
Card grid of all active portfolios with summary metrics: equity, unrealized P&L, open positions, total return %. Click into any portfolio for the detail view.

### Portfolio Detail (`/portfolios/[id]`)
Deep dive into a single portfolio:
- **Performance grid** — total return, win rate, profit factor, max drawdown, Sharpe ratio, current streak
- **Equity curve chart** — historical equity over time (Recharts)
- **Daily stats** — per-day P&L, trade count, drawdown
- **Positions** — open positions from both webhook trades and manual holdings
- **Trades** — full trade history with entry/exit details

### AI Analysis (`/ai`)
Legacy intelligence hub — Morning Briefing, Trade Review, Ask Henry, Conflict Log. Superseded by the dedicated Henry page below but still functional.

### Henry (`/henry`)
Dedicated AI page with five tabs:
- **Chat** — persistent conversation with Henry. Uses `call_ai(function_name="ask_henry")` with decision-keyword escalation to Claude.
- **Activity** — stream of Henry's recent AI calls (briefings, reviews, signal evaluations) with token usage and model routing.
- **Decisions** — action history with outcome tracking. Surfaces Henry's hit rate by confidence bucket.
- **Memory** — CRUD over `henry_memory`. Filter by type, ticker, strategy, importance. Edit/delete individual memories.
- **3D Map** — interactive 3D visualization of Henry's memory embeddings (PCA projection, react-three-fiber). Spheres colored by gaussian cluster, sized by importance. Translucent orbs mark cluster centroids. Admin panel for first-time setup (ensure-schema → backfill embeddings → fit clusters) without shell access.

### Screener (`/screener`)
Indicator-driven heatmap card grid. Pine Script indicators send alerts via webhook (separate from strategy trades). The screener aggregates signals by ticker, showing:
- Heat-intensity card sizing based on alert volume
- Sparkline price charts per ticker
- Indicator breakdown (which indicators fired, bullish/bearish)
- Click any card for Claude-generated per-ticker analysis with trade ideas, entry/stop levels, and confidence scores

### Portfolio Manager (`/portfolio-manager`)
Henry as a semi-autonomous portfolio manager. Three tabs:

**Action Queue (default)** — Henry's recommended trades and portfolio adjustments. Each action card shows:
- Action type (BUY, SELL, TRIM, ADD, CLOSE, REBALANCE)
- Henry's reasoning (2-3 sentences backed by data)
- Confidence score (1-10)
- Trigger source — what prompted the recommendation (new signal, threshold breach, or daily review)
- Approve / Reject buttons
- Expiry countdown (4h for signal-driven, 24h for scheduled)
- Visual priority: red border = threshold breach, amber = signal-driven, gray = scheduled

Summary bar: `3 pending · 12 approved today · Henry's hit rate: 71%`

**Holdings** — Manually entered positions. Add your current holdings (ticker, direction, entry price, qty, date, strategy). Holdings link to webhook trades when a strategy fires on the same position, solving the source-of-truth problem. Concentration bar shows portfolio allocation by ticker.

**Backtest Data** — Drag-and-drop CSV import for TradingView backtest exports. Filename auto-parsed (`HENRY_v3.8_NASDAQ_NVDA_2026-03-17.csv` → strategy=HENRY, ticker=NVDA). Computes and displays:
- Win rate, profit factor, avg gain/loss
- Max drawdown, max adverse excursion
- Average hold time, total P&L
- Individual trade history (expandable)

Henry uses backtest data to make informed recommendations: *"S1 has a 100% win rate on NVDA across 9 trades with avg gain of 4.07%. Recommend: BUY, confidence 8/10."*

### Settings (`/settings`)
- **Portfolios tab** — create, edit, archive portfolios. Set initial capital, risk limits (max % per trade, max positions, max drawdown)
- **Traders tab** — manage strategies. Each trader is a Pine Script strategy instance with a unique API key for webhook authentication
- **API Keys tab** — generate and manage allowlisted API keys for webhook endpoints

### Login (`/login`)
Authentication with animated liquid gradient background.

---

## Pine Script Integration

### 4 Strategies
| ID | Name | Description |
|----|------|-------------|
| S1 | LMA Momentum | Log-weighted moving average + Kalman filter trend following |
| S2 | Regime Trend | 200 SMA + ADX trend detection as entry signals |
| S3 | Impulse Breakout | Volume spike + candle expansion breakouts with time decay |
| S4 | Kalman Reversion | Mean reversion when price stretches from Kalman filter |

### Webhook Flow
1. Pine Script strategy fires entry/exit → TradingView sends webhook to `POST /api/webhook`
2. Backend validates API key, creates Trade record, updates portfolio snapshots
3. If opposing signals detected across strategies on the same ticker → auto-triggers Claude conflict resolution
4. Portfolio Manager evaluates the signal against holdings + backtest data → may create a recommended action

### Indicator Alerts
Pine Script indicators (separate from strategies) send alerts to `POST /api/screener/webhook`. These populate the Screener heatmap — no trades are created, just signal data for Henry to analyze.

---

## Henry (AI Service)

Henry is a dual-LLM system (Claude + Gemini) integrated as a trading analyst and portfolio manager. Routing is per-function: high-stakes work goes to Claude, high-volume lower-stakes work goes to Gemini, with automatic fallback to Claude on Gemini failure.

Henry has access to:
- All trade history (entries, exits, P&L, signal metadata)
- All open positions with real-time P&L
- Backtest performance data per strategy per ticker
- Market context (SPY, VIX)
- Recent market headlines (news cache)
- Ticker fundamentals (FMP-backed)
- **Long-term memory** — observations, lessons, strategy notes, preferences, and decisions retrieved semantically (see Memory System below)
- **Pre-computed stats** — hit rate, strategy performance, exit reason analysis, hold-time distributions, portfolio concentration, strategy correlation (injected into every prompt)

### AI Capabilities

| Feature | Trigger | Provider | Notes |
|---------|---------|----------|-------|
| Morning Briefing | Daily / on-demand | Gemini → Claude fallback | Web search enabled |
| Trade Review | On-demand | Gemini | Analyzes recent closed trades |
| Ask Henry | User query | Gemini (escalates to Claude on decision keywords) | Natural language Q&A |
| Conflict Resolution | Auto on opposing signals | Claude | Recommends LONG/SHORT/STAY_FLAT |
| Signal Evaluation | Every webhook | Claude | Evaluates signal vs portfolio + backtest |
| AI Portfolio Decision | Scheduled / event-driven | Claude | Autonomous trading candidate generation |
| Scheduled Review | Once daily | Claude | Deep portfolio analysis |
| Screener Analysis | Per-ticker on demand | Gemini | Trade ideas with targets |
| Watchlist Summary | On demand | Gemini | Short-form catalyst + momentum summary |
| Memory Extraction | After every AI call | Gemini | Extracts observations → saves as HenryMemory |
| Threshold Monitoring | Hourly during market | None (pure Python) | No AI cost |

### Prompt Caching & Cost Controls
- **Anthropic cache_control** — system prompt (base + strategies + retrieved memories + stats) is wrapped in an ephemeral cache block. After the first call within 5 minutes, repeated prefixes are billed at ~10% of standard input token cost. Disabled when web search is on (tool-call reshaping defeats the cache).
- **Top-K semantic memory retrieval** — instead of stuffing all high-importance memories, only the top 8 by cosine similarity to the query are injected. Fallback path (when no query text) is top 10 by importance with scope filtering, replacing the prior top-20 broad scan.
- **Gemini routing** — high-volume functions offload to Gemini Flash to conserve Claude credits. Falls back to Claude transparently on Gemini failure.

### Action Outcome Tracking
When an approved action's resulting position closes, the system tracks whether Henry's recommendation was correct. Over time this builds a meta-performance record:
- Hit rate by confidence level
- Hit rate by action type (BUY vs TRIM vs CLOSE)
- Hit rate by trigger type (signal vs threshold vs scheduled)

---

## Memory System

Henry maintains long-term memory across three complementary layers, each populated and retrieved automatically without user intervention.

### Memory Types (`henry_memory` table)
- **observation** — a pattern Henry noticed ("S3 tends to fail on NVDA during high VIX")
- **lesson** — a data-backed conclusion from trade outcomes
- **preference** — user-stated preference ("I prefer to cut losers fast")
- **strategy_note** — strategy-specific insight
- **decision** — record of a specific decision and its reasoning

Each memory has: `content`, `importance` (1-10), `reference_count`, `validated` (bool/null), `source`, scope (`ticker`, `strategy_id`), and a content-hash fingerprint for dedup.

### Voyage Embeddings (Phase 1)
- Every memory written via `save_memory()` is embedded using **Voyage AI** (`voyage-3-lite`, 512-D by default; swap to `voyage-3` for 1024-D via `EMBEDDING_MODEL` env).
- `EmbeddingProvider` protocol in `app/services/embeddings.py` isolates the backend — swapping to a local model later (sentence-transformers, BGE) is a one-file change.
- Failures are non-fatal: memory is still saved without a vector, and retrieval falls back to importance ordering.

### Gaussian Mixture Clustering (Phase 2)
- **Pure numpy diagonal GMM** (k-means++ init, EM with stable logsumexp, variance floors). No sklearn dependency.
- Fit by `henry_stats_engine._compute_memory_clusters` every Nth orchestrator cycle (default 3, i.e. ~every 6h).
- Adaptive K: `clamp(3, round(√(N/2)), 15)`. Diagonal covariance only (full covariance in 512-D is 262K params per cluster — overfits badly at our scale).
- L2-normalizes embeddings before fit so the diagonal GMM approximates a von Mises-Fisher mixture on the unit sphere.
- Cluster metadata stored as `HenryStats(stat_type="memory_clusters")` — centroids, diagonal variance, weights, member counts.

### Retrieval Scoring
At prompt-build time, memories are ranked by:
```
score = cosine_similarity(query, memory)          # in [-1, 1]
      + importance_nudge (importance / 50)         # small tiebreaker, 0-0.2
      + cluster_weight * P(cluster | query)        # gaussian posterior, 0-cluster_weight
```
`P(cluster | query)` is computed via stable softmax over `log(weight_k) + log N(query; μ_k, diag(σ²_k))`. Unclustered memories contribute 0 cluster boost (no penalty). Candidate pool is scope-filtered (ticker / strategy / null-scoped) and capped at 200 before ranking.

### 3D Visualization (Phase 3)
- `GET /api/memory/embeddings/projection` returns per-memory 3D coords via **PCA** on L2-normalized embeddings (numpy `eigh` on the d×d covariance). Cluster centroids projected in the same basis + normalization box so they land where their members visually cluster.
- 10-minute in-process cache.
- Frontend uses `@react-three/fiber` + `@react-three/drei` — lazy-loaded via `next/dynamic({ ssr: false })`.

### Admin Endpoints (no shell required)
- `POST /api/memory/admin/ensure-schema` — idempotent `ALTER TABLE ADD COLUMN IF NOT EXISTS` recovery path when alembic migrations don't land at deploy
- `POST /api/memory/admin/backfill-embeddings` — background task that embeds every memory lacking a vector; progress via…
- `GET /api/memory/admin/backfill-status` — live counts (processed, updated, failed)
- `POST /api/memory/admin/fit-clusters` — synchronous GMM refit + cache invalidation

### CLI Scripts (for environments with shell access)
- `scripts/backfill_memory_embeddings.py` — batched, idempotent, resumable backfill
- `scripts/fit_memory_clusters.py` — on-demand clustering run

### Observability
- `GET /api/memory/embeddings/health` — total / embedded / coverage% / model distribution / cluster distribution
- `GET /api/memory/clusters` — current GMM (k, weights, member counts, optionally centroids)
- `AIUsage` table — per-call input/output tokens, provider, model, latency, fallback flag — tracks whether token reduction is working

---

## Architecture

### Frontend
- **Framework:** Next.js 14 (App Router) + React 18 + TypeScript
- **Styling:** Tailwind CSS + custom design system
- **Charts:** Recharts
- **Fonts:** Outfit (display) + JetBrains Mono (data/terminal)
- **Theme:** Dark only — surface `#111827`, amber `#fbbf24`, ai-blue `#6366f1`, profit `#22c55e`, loss `#ef4444`
- **Animations:** fade-in, scale-in, gauge-fill, heat-glow, breathe, slide-up-panel
- **Pattern:** All page components in single files (no component extraction)

### Backend
- **Framework:** FastAPI (async)
- **Database:** PostgreSQL + SQLAlchemy (async) + Alembic migrations
- **AI — Generation:** Anthropic Claude (Sonnet 4.5 primary, Sonnet 4.6 fallback, Haiku 4.5 last resort) + Google Gemini (2.0 Flash). Dual-routing via `app/services/ai_provider.py`.
- **AI — Embeddings:** Voyage AI (`voyage-3-lite` default, configurable). `EmbeddingProvider` abstraction in `app/services/embeddings.py`.
- **AI — Prompt Caching:** Anthropic `cache_control: ephemeral` on system prompt (disabled during web search).
- **AI — Memory:** Semantic top-K retrieval + gaussian mixture cluster scoring (pure numpy, no sklearn).
- **Market Data:** yfinance (chart data), Alpaca API (credentials configured), Financial Modeling Prep (fundamentals via `fmp_cache`)
- **News:** News cache table populated for prompt context + recent headlines.
- **Background:** asyncio tasks for price polling (15s market hours, 60s closed), APScheduler for daily summaries and the Henry stats engine (runs every 2h during market hours)
- **Auth:** API key hashing (SHA-256) for webhook authentication; `ADMIN_SECRET` for admin endpoints (seed, memory schema/backfill/fit)

### Deployment
- **Frontend:** Vercel (henrytrader.xyz)
- **Backend:** Railway (auto-deploy from GitHub push)
- **Database:** Railway PostgreSQL

### API Endpoints

| Group | Prefix | Purpose |
|-------|--------|---------|
| Webhooks | `/api/webhook` | Strategy trade ingestion |
| Trades | `/api/trades` | Trade history queries |
| Traders | `/api/traders` | Strategy management |
| Portfolios | `/api/portfolios` | Portfolio data + metrics |
| Leaderboard | `/api/leaderboard` | Strategy rankings |
| Settings | `/api/settings` | Portfolio/trader/key CRUD |
| Screener | `/api/screener` | Indicator alerts + analysis |
| Portfolio Manager | `/api/portfolio-manager` | Holdings, actions, backtests |
| AI | `/api/ai` | Briefing, review, query, conflicts, fundamentals |
| AI Portfolio | `/api/ai-portfolio` | Autonomous trading candidates + ask |
| Memory | `/api/memory` | Memory CRUD, embeddings health/projection, clusters, admin |
| Watchlist | `/api/watchlist` | Watchlist tickers + Gemini-backed summaries |
| News | `/api/news` | Recent market headlines cache |
| Analytics | `/api/analytics` | AI usage stats, token accounting |
| Execution | `/api/execution` | Trade execution helpers |
| FMP Scanner | `/api/scanner` | Financial Modeling Prep fundamental scans |
| System | `/api/health`, etc. | Health, prices, debug |

**Memory-specific endpoints** (under `/api/memory`):
- `GET /memory` — list memories with filters
- `GET /memory/stats` — aggregate counts by type/source
- `GET /memory/embeddings/health` — coverage + cluster distribution
- `GET /memory/embeddings/projection?force=false` — 3D PCA projection
- `GET /memory/clusters?include_centroid=false` — current GMM metadata
- `PUT /memory/{id}` — update importance/content
- `DELETE /memory/{id}` — delete memory
- `POST /memory/admin/ensure-schema` — idempotent DDL recovery (Postgres only)
- `POST /memory/admin/backfill-embeddings` — kick off background embed job
- `GET /memory/admin/backfill-status` — live backfill progress
- `POST /memory/admin/fit-clusters` — synchronous GMM refit

### Database Models

| Category | Tables |
|----------|--------|
| Core Trading | portfolios, traders, trades, portfolio_trades, webhook_inbox |
| Portfolio Mgmt | portfolio_strategies, portfolio_holdings, portfolio_actions, portfolio_snapshots |
| Performance | daily_stats |
| Screener | indicator_alerts, screener_analyses |
| Backtest | backtest_imports, backtest_trades |
| Henry AI — Conversation | henry_cache, henry_context |
| Henry AI — Memory | **henry_memory** (content + `embedding` JSON + `embedding_model` + `cluster_id`) |
| Henry AI — Stats | **henry_stats** (stat_type rows: `strategy_performance`, `exit_reason_analysis`, `henry_hit_rate`, `hold_time_analysis`, `portfolio_risk`, `strategy_correlation`, `memory_clusters`) |
| AI Observability | ai_usage (provider, model, tokens, latency, fallback flag) |
| Market Data | news_cache, ticker_fundamentals, fmp_cache |
| Watchlist | watchlist_ticker, watchlist_summary |
| Legacy AI | conflict_resolutions, market_summaries |
| Security | allowlisted_keys |

---

## Key Design Decisions

1. **Semi-autonomous, not autonomous** — Henry recommends, user approves. Nothing executes without explicit approval.
2. **Two-tier monitoring** — hourly Python threshold checks (no AI cost) + daily deep Claude review (comprehensive but expensive). Keeps API costs minimal.
3. **Source of truth linking** — manual holdings link to webhook trades via `trade_id` so Henry knows "you entered this via the dashboard" vs "the strategy entered this automatically."
4. **Extra webhook fields ignored** — Pine Script can send `win_pct`, `total_trades`, `profit_factor`, etc. alongside required fields. Backend uses `extra="ignore"` so new Pine Script versions never break the webhook.
5. **Performance calc excludes deployed capital** — adding a manual holding doesn't inflate return %. Only actual price movement since entry counts as performance.
6. **Single-file page components** — established codebase pattern. Each page is self-contained for simplicity.
7. **Dual LLM routing over single-provider** — Claude for high-stakes decisions, Gemini for high-volume lower-stakes work. Gemini failure transparently falls back to Claude. Cuts Claude token burn ~3-5× on briefings, reviews, and general Q&A.
8. **Semantic memory retrieval over importance-only** — the old "top 20 memories by importance" scan was token-heavy and context-blind. Voyage embeddings + cosine similarity + gaussian cluster posterior = top 8 actually-relevant memories per call. Backwards-compatible: when embeddings are absent, importance ordering still works.
9. **Pure-numpy GMM over sklearn** — avoids a ~30 MB dependency (and scipy transitively). Diagonal covariance + k-means++ init + stable EM is ~100 lines and well-suited to our scale (<10k memories).
10. **PCA over UMAP for the 3D viz** — zero new Python deps (UMAP pulls in numba + llvmlite, ~200 MB on the Railway container). PCA is honest about true geometry; UMAP can invent structure. Swappable behind the same endpoint contract.
11. **JSON embedding column over pgvector** — keeps the migration portable (Railway Postgres + local SQLite dev). In-Python cosine is sub-10ms for <10k memories. pgvector migration is a future Phase 4 when scale demands it.
12. **Admin endpoints for no-shell environments** — `/api/memory/admin/ensure-schema` provides an idempotent DDL recovery path when Alembic migrations don't run at deploy (Railway's `startCommand` uses `alembic upgrade head || true`, which silently swallows migration failures). Users without Railway shell access can trigger schema/backfill/fit from the frontend admin panel.

---

## Environment Variables

### Backend (Railway)

**Required**

| Variable | Description |
|----------|-------------|
| `DATABASE_URL` | PostgreSQL connection string (Railway auto-provides) |
| `ANTHROPIC_API_KEY` | Claude API key (`sk-ant-...`) |
| `ADMIN_SECRET` | Protects `/api/admin/seed` and all `/api/memory/admin/*` endpoints |
| `ALLOWED_ORIGINS` | Comma-separated CORS origins (e.g., `https://henrytrader.xyz,http://localhost:3000`) |
| `DASHBOARD_API_KEY` | Frontend-to-backend auth header (`x-api-key`) |

**Recommended (enables memory system + Gemini routing)**

| Variable | Default | Description |
|----------|---------|-------------|
| `VOYAGE_API_KEY` | — | Voyage AI key. When blank, embeddings disabled and memory retrieval falls back to importance ordering. |
| `GEMINI_API_KEY` | — | Google Gemini key. When blank, all AI calls go to Claude. |
| `AI_ROUTING_MODE` | `dual` | `dual` · `claude_only` · `gemini_only` |
| `EMBEDDING_MODEL` | `voyage-3-lite` | `voyage-3-lite` (512-D) · `voyage-3` (1024-D) · `voyage-3-large` |
| `EMBEDDING_ENABLED` | `true` | Kill switch for the embedding pipeline |
| `PROMPT_CACHE_ENABLED` | `true` | Toggle Anthropic `cache_control` on system prompt |
| `MEMORY_TOP_K` | `8` | Max memories injected via semantic retrieval |
| `MEMORY_TOP_K_FALLBACK` | `10` | Max memories when no `query_text` (importance ordering) |
| `MEMORY_CLUSTERING_ENABLED` | `true` | Master switch for GMM fit + retrieval blending |
| `MEMORY_CLUSTER_WEIGHT` | `0.3` | Weight of P(cluster \| query) in retrieval score |
| `MEMORY_CLUSTER_FIT_EVERY_N_RUNS` | `3` | Fit frequency (stats engine runs every ~2h; default ≈ every 6h) |

**Optional data providers**

| Variable | Description |
|----------|-------------|
| `ALPACA_API_KEY` · `ALPACA_SECRET_KEY` | Alpaca market data |
| `FMP_API_KEY` | Financial Modeling Prep (fundamentals, scanner) |

### Frontend (Vercel / `.env.local`)

| Variable | Required | Description |
|----------|----------|-------------|
| `NEXT_PUBLIC_API_URL` | Yes | Backend API URL (e.g., `https://trader-dashboard-production-02bd.up.railway.app/api`) |
| `NEXT_PUBLIC_API_KEY` | Yes | Must match backend `DASHBOARD_API_KEY` |

---

## Operational Runbook

### First-time memory system setup (no shell)
1. Set `VOYAGE_API_KEY` + `ADMIN_SECRET` in Railway env vars. Deploy.
2. Open Henry page → **3D Map** tab.
3. Click **0. Ensure schema** (adds `embedding`, `embedding_model`, `cluster_id` columns if the alembic migration didn't run).
4. Click **1. Backfill embeddings** (background task; watch live progress).
5. Click **2. Fit clusters** (synchronous, <10s).
6. 3D map auto-reloads with populated projection.

### First-time memory system setup (with shell)
```bash
cd /app
python -m scripts.backfill_memory_embeddings   # embed all null-embedding memories
python -m scripts.fit_memory_clusters          # run GMM on the embeddings
```

### Monitoring token spend
- **`AIUsage` table** — `input_tokens` / `output_tokens` per `function_name` per call
- **Anthropic dashboard** — look for `cache_read_input_tokens` (billed at 10%) to confirm prompt caching is landing
- **`GET /api/memory/embeddings/health`** — confirm coverage is growing

### Rollback levers
- `EMBEDDING_ENABLED=false` — skips embedding on write + semantic retrieval on read; falls back to importance ordering
- `MEMORY_CLUSTERING_ENABLED=false` — skips GMM fit + retrieval cluster boost
- `PROMPT_CACHE_ENABLED=false` — reverts to plain-string system prompt
- `AI_ROUTING_MODE=claude_only` — bypass Gemini routing
- `alembic downgrade -1` (twice) — drops `cluster_id` then `embedding`/`embedding_model` columns
