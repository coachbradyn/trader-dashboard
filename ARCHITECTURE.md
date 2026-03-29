# Trader Dashboard — Full Architecture Reference

Generated 2026-03-29. This document describes every table, endpoint, service, and subsystem in the Trader Dashboard project.

---

## Section 1: Project Overview

### What It Does
The Trader Dashboard ("Henry AI Trader") is a full-stack system for managing, analyzing, and executing trades across multiple Pine Script strategies. It receives real-time webhook signals from TradingView, routes them to portfolios, runs AI analysis via an embedded AI assistant named "Henry," and optionally executes orders through Alpaca brokerage accounts.

### Who It's For
A single trader running multiple algorithmic strategies on TradingView who wants:
- Centralized trade tracking across strategies
- AI-powered trade analysis, conflict resolution, and morning briefings
- Portfolio management with manual holdings, backtest imports, and position archetypes
- A screener/watchlist with per-ticker AI analysis
- Optional paper/live execution through Alpaca

### Tech Stack

**Backend** (deployed on Railway):
- Python 3.12+ / FastAPI
- SQLAlchemy 2.0 (async, with asyncpg for PostgreSQL)
- Alembic for migrations (with runtime `_ensure_schema` fallback)
- Anthropic Claude (Sonnet 4.5 primary, Sonnet 4.6 fallback, Haiku 4.5 last resort)
- Google Gemini 2.0 Flash (high-volume, lower-stakes AI calls)
- yfinance (chart data, sector ETFs, VIX, earnings calendar)
- Alpaca Market Data API (prices, news) + Alpaca Trading API (order execution)
- APScheduler (background jobs)

**Frontend** (Vercel-ready):
- Next.js 14 (App Router)
- React 18, TypeScript
- Tailwind CSS with custom dark theme
- shadcn/ui component library
- Recharts for charts
- Fonts: Outfit (display) + JetBrains Mono (data/terminal)

### Environment Variables

| Variable | Required | Description |
|---|---|---|
| `DATABASE_URL` | Yes | PostgreSQL connection string |
| `ADMIN_SECRET` | Yes | Secret for `/api/admin/seed` endpoint |
| `ALLOWED_ORIGINS` | Yes | Comma-separated CORS origins |
| `ANTHROPIC_API_KEY` | Yes | Claude API key |
| `GEMINI_API_KEY` | No | Google Gemini API key (enables dual-provider routing) |
| `ALPACA_API_KEY` | Yes | Alpaca Data API key (prices, news) |
| `ALPACA_SECRET_KEY` | Yes | Alpaca Data API secret |
| `ALPACA_BASE_URL` | No | Default: `https://data.alpaca.markets` |
| `AI_ROUTING_MODE` | No | `dual` (default), `claude_only`, or `gemini_only` |
| `PRICE_POLL_INTERVAL_MARKET` | No | Seconds between price polls during market hours (default: 15) |
| `PRICE_POLL_INTERVAL_CLOSED` | No | Seconds between price polls outside market hours (default: 60) |
| `NEXT_PUBLIC_API_URL` | Yes (frontend) | Backend API URL, e.g. `https://your-app.railway.app/api` |

---

## Section 2: Database Schema

### Table: `traders`
Represents a strategy/bot identity. Each Pine Script strategy has its own trader row.

| Column | Type | Default | Description |
|---|---|---|---|
| `id` | VARCHAR(36) PK | uuid4 | Internal UUID |
| `trader_id` | VARCHAR(50) UNIQUE | — | Slug used in webhooks, e.g. `henry-v36` |
| `display_name` | VARCHAR(100) | — | Human-readable name |
| `strategy_name` | VARCHAR(100) | NULL | Strategy identifier |
| `description` | TEXT | NULL | Short description |
| `strategy_description` | TEXT | NULL | Rich description for Henry's AI prompts (philosophy, entry/exit logic, ideal conditions, weaknesses) |
| `api_key_hash` | VARCHAR(255) | — | bcrypt hash of the API key used in webhooks |
| `is_active` | BOOLEAN | TRUE | Whether this strategy is active |
| `created_at` | TIMESTAMP | utcnow | Creation time |
| `last_webhook_at` | TIMESTAMP | NULL | Last webhook received |

**Indexes:** `trader_id` (unique index)
**Relationships:** `trades` (1:M), `portfolio_strategies` (1:M)

### Table: `trades`
Individual trade entries and exits, created by webhook signals.

| Column | Type | Default | Description |
|---|---|---|---|
| `id` | VARCHAR(36) PK | uuid4 | Trade UUID |
| `trader_id` | VARCHAR(36) FK | — | References `traders.id` |
| `ticker` | VARCHAR(20) | — | Stock symbol |
| `direction` | VARCHAR(5) | — | `long` or `short` |
| `entry_price` | FLOAT | — | Price at entry |
| `qty` | FLOAT | — | Share quantity |
| `entry_signal_strength` | FLOAT | NULL | Signal strength from webhook |
| `entry_adx` | FLOAT | NULL | ADX value at entry |
| `entry_atr` | FLOAT | NULL | ATR value at entry |
| `stop_price` | FLOAT | NULL | Stop loss price |
| `timeframe` | VARCHAR(10) | NULL | Chart timeframe |
| `entry_time` | TIMESTAMP | — | When the entry occurred |
| `exit_price` | FLOAT | NULL | Price at exit (NULL while open) |
| `exit_reason` | VARCHAR(50) | NULL | Why the trade was closed |
| `exit_time` | TIMESTAMP | NULL | When the exit occurred |
| `bars_in_trade` | INTEGER | NULL | Number of bars held |
| `pnl_dollars` | FLOAT | NULL | Dollar P&L (computed on exit) |
| `pnl_percent` | FLOAT | NULL | Percentage P&L (computed on exit) |
| `status` | VARCHAR(10) | `open` | `open` or `closed` |
| `is_simulated` | BOOLEAN | FALSE | TRUE for AI portfolio paper trades |
| `raw_entry_payload` | JSON | NULL | Full webhook JSON for entry |
| `raw_exit_payload` | JSON | NULL | Full webhook JSON for exit |
| `created_at` | TIMESTAMP | utcnow | Row creation time |

**Indexes:** `trader_id`, `status`
**Relationships:** `trader` (M:1), `portfolio_trades` (1:M)

### Table: `portfolios`
A portfolio groups trades and tracks capital.

| Column | Type | Default | Description |
|---|---|---|---|
| `id` | VARCHAR(36) PK | uuid4 | Portfolio UUID |
| `name` | VARCHAR(100) UNIQUE | — | Portfolio name |
| `description` | TEXT | NULL | Description |
| `initial_capital` | FLOAT | 10000.0 | Starting capital |
| `cash` | FLOAT | 10000.0 | Current cash balance |
| `is_active` | BOOLEAN | TRUE | Whether portfolio is active |
| `max_pct_per_trade` | FLOAT | NULL | Max percentage of capital per trade |
| `max_open_positions` | INTEGER | NULL | Max concurrent positions |
| `max_drawdown_pct` | FLOAT | NULL | Max allowed drawdown |
| `is_ai_managed` | BOOLEAN | FALSE | TRUE for Henry's AI paper portfolio |
| `status` | VARCHAR(20) | `active` | `active` or `archived` |
| `execution_mode` | VARCHAR(10) | `local` | `local`, `paper`, or `live` |
| `alpaca_api_key` | VARCHAR(255) | NULL | Per-portfolio Alpaca API key |
| `alpaca_secret_key` | VARCHAR(255) | NULL | Per-portfolio Alpaca secret key |
| `max_order_amount` | FLOAT | 1000.0 | Safety rail: max dollar value per order |
| `created_at` | TIMESTAMP | utcnow | Creation time |

**Relationships:** `strategies` (1:M), `portfolio_trades` (1:M), `snapshots` (1:M), `daily_stats` (1:M)

### Table: `portfolio_strategies`
Junction table linking portfolios to traders (strategies).

| Column | Type | Default | Description |
|---|---|---|---|
| `id` | VARCHAR(36) PK | uuid4 | Row UUID |
| `portfolio_id` | VARCHAR(36) FK | — | References `portfolios.id` |
| `trader_id` | VARCHAR(36) FK | — | References `traders.id` |
| `direction_filter` | VARCHAR(10) | NULL | NULL = all, `long` = longs only, `short` = shorts only |

**Relationships:** `portfolio` (M:1), `trader` (M:1)

### Table: `portfolio_trades`
Junction table linking trades to portfolios.

| Column | Type | Default | Description |
|---|---|---|---|
| `id` | VARCHAR(36) PK | uuid4 | Row UUID |
| `portfolio_id` | VARCHAR(36) FK | — | References `portfolios.id` |
| `trade_id` | VARCHAR(36) FK | — | References `trades.id` |

**Indexes:** `portfolio_id`, `trade_id`

### Table: `portfolio_snapshots`
Point-in-time equity snapshots for a portfolio, taken on each trade exit.

| Column | Type | Default | Description |
|---|---|---|---|
| `id` | VARCHAR(36) PK | uuid4 | Row UUID |
| `portfolio_id` | VARCHAR(36) FK | — | References `portfolios.id` |
| `equity` | FLOAT | — | Total equity at snapshot time |
| `cash` | FLOAT | — | Cash at snapshot time |
| `unrealized_pnl` | FLOAT | 0.0 | Unrealized P&L |
| `open_positions` | INTEGER | 0 | Number of open positions |
| `drawdown_pct` | FLOAT | 0.0 | Drawdown from peak |
| `peak_equity` | FLOAT | 0.0 | Peak equity seen so far |
| `snapshot_time` | TIMESTAMP | utcnow | When snapshot was taken |

**Indexes:** `portfolio_id`

### Table: `daily_stats`
One row per portfolio per trading day, for daily P&L tracking.

| Column | Type | Default | Description |
|---|---|---|---|
| `id` | VARCHAR(36) PK | uuid4 | Row UUID |
| `portfolio_id` | VARCHAR(36) FK | — | References `portfolios.id` |
| `date` | DATE | — | Trading date |
| `starting_equity` | FLOAT | 0.0 | Equity at start of day |
| `ending_equity` | FLOAT | 0.0 | Equity at end of day |
| `daily_pnl` | FLOAT | 0.0 | Day's P&L in dollars |
| `daily_pnl_pct` | FLOAT | 0.0 | Day's P&L as percentage |
| `trades_opened` | INTEGER | 0 | Trades opened today |
| `trades_closed` | INTEGER | 0 | Trades closed today |
| `wins` | INTEGER | 0 | Winning trades today |
| `losses` | INTEGER | 0 | Losing trades today |
| `gross_profit` | FLOAT | 0.0 | Sum of winning trade P&L |
| `gross_loss` | FLOAT | 0.0 | Sum of losing trade P&L |
| `max_drawdown_pct` | FLOAT | 0.0 | Max intraday drawdown |

**Constraints:** UNIQUE(`portfolio_id`, `date`)
**Indexes:** `portfolio_id`

### Table: `portfolio_holdings`
Manual holdings (or trade-linked holdings) in a portfolio. Supports position archetypes.

| Column | Type | Default | Description |
|---|---|---|---|
| `id` | VARCHAR(36) PK | uuid4 | Row UUID |
| `portfolio_id` | VARCHAR(36) FK | — | References `portfolios.id` |
| `trade_id` | VARCHAR(36) FK | NULL | References `trades.id`, NULL for manual entries |
| `ticker` | VARCHAR(10) | — | Stock symbol |
| `direction` | VARCHAR(10) | — | `long` or `short` |
| `entry_price` | FLOAT | — | Entry price |
| `qty` | FLOAT | — | Share quantity |
| `entry_date` | TIMESTAMP | — | When position was opened |
| `strategy_name` | VARCHAR(50) | NULL | Which strategy originated this |
| `notes` | TEXT | NULL | User notes |
| `is_active` | BOOLEAN | TRUE | Whether holding is still open |
| `position_type` | VARCHAR(20) | `momentum` | Archetype: `momentum`, `accumulation`, `catalyst`, `conviction` |
| `thesis` | TEXT | NULL | User's thesis for the position |
| `catalyst_date` | DATE | NULL | Expected catalyst date (for catalyst type) |
| `catalyst_description` | VARCHAR(200) | NULL | What the catalyst is |
| `max_allocation_pct` | FLOAT | NULL | Max portfolio allocation for this position |
| `dca_enabled` | BOOLEAN | FALSE | Whether DCA is enabled |
| `dca_threshold_pct` | FLOAT | NULL | DCA trigger threshold (% below avg cost) |
| `avg_cost` | FLOAT | NULL | Average cost basis (for DCA tracking) |
| `total_shares` | FLOAT | NULL | Total shares including DCA adds |
| `created_at` | TIMESTAMP | utcnow | Row creation time |

**Indexes:** `portfolio_id`, `trade_id`, `ticker`, `is_active`

### Table: `portfolio_actions`
Henry's recommended actions (BUY/SELL/TRIM/ADD/CLOSE/REBALANCE/DCA) for portfolios.

| Column | Type | Default | Description |
|---|---|---|---|
| `id` | VARCHAR(36) PK | uuid4 | Row UUID |
| `portfolio_id` | VARCHAR(36) FK | — | References `portfolios.id` |
| `ticker` | VARCHAR(10) | — | Stock symbol |
| `direction` | VARCHAR(10) | — | `long` or `short` |
| `action_type` | VARCHAR(20) | — | `BUY`, `SELL`, `TRIM`, `ADD`, `CLOSE`, `REBALANCE`, `DCA` |
| `suggested_qty` | FLOAT | NULL | Suggested share quantity |
| `suggested_price` | FLOAT | NULL | Suggested price |
| `current_price` | FLOAT | NULL | Price at action creation |
| `confidence` | INTEGER | 5 | Henry's confidence (1-10) |
| `reasoning` | TEXT | — | Henry's reasoning |
| `trigger_type` | VARCHAR(20) | — | `SIGNAL`, `THRESHOLD`, `SCHEDULED_REVIEW` |
| `trigger_ref` | VARCHAR(36) | NULL | trade_id or alert_id that triggered this |
| `priority_score` | FLOAT | 0.0 | Urgency weight x confidence |
| `status` | VARCHAR(20) | `pending` | `pending`, `approved`, `rejected`, `expired` |
| `expires_at` | TIMESTAMP | NULL | When this action expires |
| `resolved_at` | TIMESTAMP | NULL | When action was resolved |
| `reject_reason` | TEXT | NULL | Why it was rejected |
| `outcome_pnl` | FLOAT | NULL | Actual P&L if trade was taken |
| `outcome_correct` | BOOLEAN | NULL | Whether the action was correct |
| `outcome_resolved_at` | TIMESTAMP | NULL | When outcome was tracked |
| `created_at` | TIMESTAMP | utcnow | Row creation time |

**Indexes:** `portfolio_id`, `trigger_type`, `status`, `priority_score`

### Table: `backtest_imports`
Imported TradingView backtest CSV summary records.

| Column | Type | Default | Description |
|---|---|---|---|
| `id` | VARCHAR(36) PK | uuid4 | Row UUID |
| `strategy_name` | VARCHAR(50) | — | Parsed from filename |
| `strategy_version` | VARCHAR(20) | NULL | e.g. `v3.8` |
| `exchange` | VARCHAR(20) | NULL | e.g. `NASDAQ` |
| `ticker` | VARCHAR(10) | — | Stock symbol |
| `filename` | VARCHAR(255) | — | Original filename |
| `trade_count` | INTEGER | 0 | Number of trades in import |
| `win_rate` | FLOAT | NULL | Win rate percentage |
| `profit_factor` | FLOAT | NULL | Gross profit / gross loss |
| `avg_gain_pct` | FLOAT | NULL | Average winning trade % |
| `avg_loss_pct` | FLOAT | NULL | Average losing trade % |
| `max_drawdown_pct` | FLOAT | NULL | Max drawdown % |
| `max_adverse_excursion_pct` | FLOAT | NULL | Worst MAE % |
| `avg_hold_days` | FLOAT | NULL | Average holding period |
| `total_pnl_pct` | FLOAT | NULL | Total cumulative P&L % |
| `imported_at` | TIMESTAMP | utcnow | Import time |

**Indexes:** `strategy_name`, `ticker`
**Relationships:** `trades` (1:M, cascade delete)

### Table: `backtest_trades`
Individual trade rows from imported TradingView backtests.

| Column | Type | Default | Description |
|---|---|---|---|
| `id` | VARCHAR(36) PK | uuid4 | Row UUID |
| `import_id` | VARCHAR(36) FK | — | References `backtest_imports.id` |
| `trade_number` | INTEGER | — | Trade sequence number |
| `type` | VARCHAR(20) | — | `Entry long`, `Exit long`, etc. |
| `direction` | VARCHAR(10) | — | `long` or `short` |
| `signal` | VARCHAR(50) | NULL | Entry/exit signal name |
| `price` | FLOAT | — | Trade price |
| `qty` | FLOAT | NULL | Share quantity |
| `position_value` | FLOAT | NULL | Total position value |
| `net_pnl` | FLOAT | NULL | Net P&L in dollars |
| `net_pnl_pct` | FLOAT | NULL | Net P&L percentage |
| `favorable_excursion` | FLOAT | NULL | Max favorable excursion $ |
| `favorable_excursion_pct` | FLOAT | NULL | Max favorable excursion % |
| `adverse_excursion` | FLOAT | NULL | Max adverse excursion $ |
| `adverse_excursion_pct` | FLOAT | NULL | Max adverse excursion % |
| `cumulative_pnl` | FLOAT | NULL | Running cumulative P&L $ |
| `cumulative_pnl_pct` | FLOAT | NULL | Running cumulative P&L % |
| `trade_date` | TIMESTAMP | — | Date/time of the trade |

**Indexes:** `import_id`

### Table: `henry_memory`
Henry's long-term memory — observations, lessons, and strategy notes.

| Column | Type | Default | Description |
|---|---|---|---|
| `id` | VARCHAR(36) PK | uuid4 | Row UUID |
| `memory_type` | VARCHAR(30) | — | `observation`, `lesson`, `preference`, `strategy_note`, `decision` |
| `strategy_id` | VARCHAR(50) | NULL | Which strategy this relates to (NULL = general) |
| `ticker` | VARCHAR(10) | NULL | Which ticker (NULL = general) |
| `content` | TEXT | — | The memory content |
| `importance` | INTEGER | 5 | Importance score (1-10). Memories with importance >= 6 are included in prompts |
| `reference_count` | INTEGER | 0 | How many times referenced in analysis |
| `validated` | BOOLEAN | NULL | NULL = not validated, TRUE = confirmed, FALSE = invalidated |
| `source` | VARCHAR(30) | `system` | `briefing`, `signal_eval`, `scheduled_review`, `user`, `outcome_tracking`, `thesis_generator` |
| `created_at` | TIMESTAMP | utcnow | Creation time |
| `updated_at` | TIMESTAMP | utcnow | Last update time (auto-updates) |

**Indexes:** `memory_type`, `strategy_id`, `ticker`

### Table: `henry_context`
Henry's short/medium-term context notes — recommendations, outcomes, observations that expire.

| Column | Type | Default | Description |
|---|---|---|---|
| `id` | VARCHAR(36) PK | uuid4 | Row UUID |
| `ticker` | VARCHAR(20) | NULL | Related ticker |
| `strategy` | VARCHAR(50) | NULL | Related strategy |
| `portfolio_id` | VARCHAR(36) FK | NULL | References `portfolios.id` |
| `context_type` | VARCHAR(30) | — | `recommendation`, `outcome`, `observation`, `pattern`, `portfolio_note`, `user_decision` |
| `content` | TEXT | — | The context note |
| `confidence` | INTEGER | NULL | Confidence level (1-10) |
| `action_id` | VARCHAR(36) FK | NULL | References `portfolio_actions.id` |
| `trade_id` | VARCHAR(36) FK | NULL | References `trades.id` |
| `created_at` | TIMESTAMP | utcnow | Creation time |
| `expires_at` | TIMESTAMP | NULL | When this context expires |

**Indexes:** `ticker`, `strategy`, `context_type`, `created_at`, composite(`ticker`, `strategy`, `created_at`)

### Table: `henry_stats`
Pre-computed analytics injected into Henry's prompts. Updated by background jobs.

| Column | Type | Default | Description |
|---|---|---|---|
| `id` | VARCHAR(36) PK | uuid4 | Row UUID |
| `stat_type` | VARCHAR(50) | — | `strategy_performance`, `ticker_performance`, `strategy_correlation`, `exit_reason_analysis`, `henry_hit_rate`, `hold_time_analysis`, `portfolio_risk`, `screener_accuracy` |
| `ticker` | VARCHAR(20) | NULL | Related ticker |
| `strategy` | VARCHAR(50) | NULL | Related strategy |
| `portfolio_id` | VARCHAR(36) FK | NULL | References `portfolios.id` |
| `data` | JSON | — | The computed analytics blob |
| `period_days` | INTEGER | 30 | Lookback period |
| `computed_at` | TIMESTAMP | utcnow | When last computed |

**Indexes:** `stat_type`, `ticker`, `strategy`, `computed_at`, composite(`stat_type`, `ticker`, `strategy`)

### Table: `henry_cache`
Caches AI analysis results (ticker analysis, signal evaluations, reviews, thesis, config).

| Column | Type | Default | Description |
|---|---|---|---|
| `id` | VARCHAR(36) PK | uuid4 | Row UUID |
| `cache_key` | VARCHAR(200) UNIQUE | — | Cache key, e.g. `ticker_analysis:NVDA`, `thesis:AAPL`, `ai_trading_config` |
| `cache_type` | VARCHAR(50) | — | `ticker_analysis`, `signal_eval`, `review`, `bull_bear_thesis`, `config` |
| `content` | JSON | — | Cached content |
| `ticker` | VARCHAR(20) | NULL | Related ticker |
| `strategy` | VARCHAR(50) | NULL | Related strategy |
| `is_stale` | BOOLEAN | FALSE | Whether cache needs refresh |
| `generated_at` | TIMESTAMP | utcnow | When generated |
| `data_hash` | VARCHAR(64) | NULL | Hash of input data, used to detect changes |

**Indexes:** `cache_key` (unique), `cache_type`, `ticker`

### Table: `watchlist_tickers`
User's watchlist of tickers to monitor.

| Column | Type | Default | Description |
|---|---|---|---|
| `id` | VARCHAR(36) PK | uuid4 | Row UUID |
| `ticker` | VARCHAR(20) UNIQUE | — | Stock symbol |
| `notes` | TEXT | NULL | User notes |
| `is_active` | BOOLEAN | TRUE | FALSE = soft-deleted |
| `created_at` | TIMESTAMP | utcnow | When added |
| `removed_at` | TIMESTAMP | NULL | When soft-deleted |

**Indexes:** `ticker` (unique), `is_active`

### Table: `watchlist_summaries`
Cached AI summaries per watchlist ticker. Auto-regenerated when stale.

| Column | Type | Default | Description |
|---|---|---|---|
| `id` | VARCHAR(36) PK | uuid4 | Row UUID |
| `ticker` | VARCHAR(20) UNIQUE | — | Stock symbol |
| `summary` | TEXT | — | AI-generated summary |
| `alert_count_at_generation` | INTEGER | 0 | Alert count when summary was generated (for staleness detection) |
| `generated_at` | TIMESTAMP | utcnow | When generated |

**Indexes:** `ticker` (unique)

### Table: `indicator_alerts`
Screener webhook alerts — each row is one indicator firing on one ticker.

| Column | Type | Default | Description |
|---|---|---|---|
| `id` | VARCHAR(36) PK | uuid4 | Row UUID |
| `ticker` | VARCHAR(20) | — | Stock symbol |
| `indicator` | VARCHAR(50) | — | Indicator name (e.g. `RSI`, `MACD`, `VWAP`) |
| `value` | FLOAT | — | Indicator value |
| `signal` | VARCHAR(20) | — | `bullish`, `bearish`, `neutral` |
| `timeframe` | VARCHAR(10) | NULL | Chart timeframe |
| `metadata_extra` | JSON | NULL | Additional metadata from webhook |
| `created_at` | TIMESTAMP | utcnow | Alert time |

**Indexes:** `ticker`, `indicator`, `created_at`

### Table: `market_summaries`
Morning and nightly AI-generated market summaries.

| Column | Type | Default | Description |
|---|---|---|---|
| `id` | VARCHAR(36) PK | uuid4 | Row UUID |
| `summary_type` | VARCHAR(20) | — | `morning`, `nightly`, `alert_digest` |
| `scope` | VARCHAR(20) | — | `portfolio`, `screener`, `combined` |
| `content` | TEXT | — | The summary text |
| `tickers_analyzed` | JSON | NULL | List of tickers analyzed |
| `generated_at` | TIMESTAMP | utcnow | When generated |

**Indexes:** `summary_type`, `generated_at`

### Table: `screener_analyses`
Periodic AI analyses of screener alerts, producing trade ideas.

| Column | Type | Default | Description |
|---|---|---|---|
| `id` | VARCHAR(36) PK | uuid4 | Row UUID |
| `picks` | JSON | NULL | Array of trade ideas |
| `market_context` | JSON | NULL | Sector heat, catalysts, noise ratio |
| `alerts_analyzed` | INTEGER | 0 | How many alerts were analyzed |
| `generated_at` | TIMESTAMP | utcnow | When generated |

**Indexes:** `generated_at`

### Table: `conflict_resolutions`
When two strategies have opposing positions on the same ticker, Henry resolves the conflict.

| Column | Type | Default | Description |
|---|---|---|---|
| `id` | VARCHAR(36) PK | uuid4 | Row UUID |
| `ticker` | VARCHAR(20) | — | Stock symbol |
| `strategies` | TEXT | — | JSON string of strategy names involved |
| `recommendation` | VARCHAR(20) | — | `LONG`, `SHORT`, `STAY_FLAT` |
| `confidence` | INTEGER | — | Confidence level (1-10) |
| `reasoning` | TEXT | — | Henry's reasoning |
| `signals` | JSON | NULL | Raw conflicting signal data |
| `created_at` | TIMESTAMP | utcnow | When resolved |

**Indexes:** `ticker`, `created_at`

### Table: `allowlisted_keys`
Pre-generated API keys that can be claimed by new strategies on first webhook.

| Column | Type | Default | Description |
|---|---|---|---|
| `id` | VARCHAR(36) PK | uuid4 | Row UUID |
| `api_key_hash` | VARCHAR(255) | — | bcrypt hash of the API key |
| `label` | VARCHAR(100) | NULL | Human-readable label |
| `claimed_by_id` | VARCHAR(36) FK | NULL | References `traders.id` (set when key is claimed) |
| `created_at` | TIMESTAMP | utcnow | When created |

### Table: `news_cache`
Cached news articles from Alpaca News API.

| Column | Type | Default | Description |
|---|---|---|---|
| `id` | VARCHAR(36) PK | uuid4 | Row UUID |
| `alpaca_id` | VARCHAR(50) UNIQUE | — | Alpaca's article ID |
| `headline` | TEXT | — | Article headline |
| `summary` | TEXT | NULL | Article summary |
| `source` | VARCHAR(100) | NULL | News source |
| `tickers` | JSON | NULL | List of related ticker symbols |
| `published_at` | TIMESTAMP | NULL | When article was published |
| `url` | VARCHAR(500) | NULL | Article URL |
| `sentiment_score` | FLOAT | NULL | Keyword-based sentiment score (-1.0 to 1.0) |
| `fetched_at` | TIMESTAMP | utcnow | When cached |

**Indexes:** `alpaca_id` (unique), `published_at`, `fetched_at`

### Table: `ai_usage`
Tracks every AI API call for cost monitoring and analytics.

| Column | Type | Default | Description |
|---|---|---|---|
| `id` | VARCHAR(36) PK | uuid4 | Row UUID |
| `provider` | VARCHAR(20) | — | `claude` or `gemini` |
| `function_name` | VARCHAR(50) | — | Which function made the call |
| `model` | VARCHAR(100) | NULL | Exact model used |
| `input_tokens` | INTEGER | NULL | Input token count |
| `output_tokens` | INTEGER | NULL | Output token count |
| `latency_ms` | INTEGER | NULL | Response latency in ms |
| `was_fallback` | BOOLEAN | FALSE | Whether this was a fallback call |
| `created_at` | TIMESTAMP | utcnow | When the call was made |

**Indexes:** `created_at`, `provider`

### Alembic Migrations
Located in `backend/alembic/versions/`:
1. `3939ba0e0346_initial.py` — Base schema (traders, trades, portfolios, portfolio_strategies, portfolio_trades, portfolio_snapshots, daily_stats)
2. `394815f1ac84_add_conflict_resolutions_table.py`
3. `a1b2c3d4e5f6_add_screener_settings_tables.py` — indicator_alerts, market_summaries, screener_analyses, allowlisted_keys
4. `b2c3d4e5f607_add_portfolio_manager_tables.py` — portfolio_actions, backtest_imports, backtest_trades, portfolio_holdings
5. `c3d4e5f60718_add_henry_memory_and_strategy_desc.py` — henry_memory table, strategy_description column
6. `d4e5f6071829_add_henry_context_and_henry_stats.py` — henry_context, henry_stats tables
7. `e5f6071829a1_add_news_cache_table.py`
8. `f607182930a2_add_ai_usage_table.py`
9. `g718293041b3_add_position_archetypes.py` — Position archetype columns on portfolio_holdings

---

## Section 3: API Reference

### Webhooks Router (`/api`)

#### `POST /api/webhook`
Receive a trade signal from TradingView Pine Script.
- **Request:** `WebhookPayload` — `key`, `trader`, `signal` (entry/exit), `dir`, `ticker`, `price`, `qty`, `sig`, `adx`, `atr`, `stop`, `exit_reason`, `pnl_pct`, `bars_in_trade`, `tf`, `time`
- **Response:** `{ status, trade_id, signal, ticker, direction }`
- **Calls:** `trade_processor.process_webhook()`, `_check_for_conflicts()`, `ai_portfolio.evaluate_signal_for_ai_portfolio()`, `watchlist_ai.check_and_regenerate_if_stale()`, `henry_cache.invalidate_by_ticker()`

### Trades Router (`/api`)

#### `GET /api/trades`
List trades with optional filters.
- **Params:** `trader_id`, `portfolio_id`, `status`, `limit` (max 200), `offset`
- **Response:** `TradeResponse[]` — id, trader_id, trader_name, ticker, direction, entry/exit data, pnl, status

### Traders Router (`/api`)

#### `GET /api/traders`
List all active traders/strategies.
- **Response:** `TraderResponse[]` — id, trader_id, display_name, strategy_name, description, portfolios list

#### `GET /api/traders/{trader_slug}`
Get single trader by slug.
- **Response:** `TraderResponse`

### Portfolios Router (`/api`)

#### `GET /api/portfolios`
List active portfolios with equity calculations.
- **Response:** `PortfolioResponse[]` — combines snapshot data + holdings data for equity/return calculation

#### `GET /api/portfolios/{portfolio_id}`
Single portfolio detail.

#### `GET /api/portfolios/{portfolio_id}/positions`
Open positions — combines webhook trades + manual holdings.
- **Response:** `PositionResponse[]` with current_price from price_service

#### `GET /api/portfolios/{portfolio_id}/performance`
Calculated performance metrics.
- **Response:** `PerformanceResponse` — total_trades, wins, losses, win_rate, profit_factor, avg_win, avg_loss, total_pnl, total_return_pct, max_drawdown_pct, sharpe_ratio, current_streak

#### `GET /api/portfolios/{portfolio_id}/equity-history`
Equity snapshots for charting.
- **Response:** `EquityPoint[]` — time, equity, drawdown_pct

#### `GET /api/portfolios/{portfolio_id}/daily-stats`
Daily P&L breakdown.
- **Response:** `DailyStatsResponse[]`

#### `POST /api/portfolios/{portfolio_id}/deposit`
Add cash to portfolio.
- **Body:** `{ amount: number }`

#### `POST /api/portfolios/{portfolio_id}/withdraw`
Remove cash from portfolio.
- **Body:** `{ amount: number }`

### Leaderboard Router (`/api`)

#### `GET /api/leaderboard`
Ranked portfolio performance comparison.
- **Params:** `sort_by` (enum: total_return_pct, win_rate, profit_factor, sharpe_ratio, total_trades)
- **Response:** Sorted array with rank, portfolio_name, metrics
- **Excludes:** AI-managed portfolios

### Settings Router (`/api/settings`)

#### `GET /api/settings/portfolios`
All portfolios with strategy assignments.

#### `POST /api/settings/portfolios`
Create a new portfolio.
- **Body:** `PortfolioCreate` — name, description, initial_capital, risk limits

#### `PUT /api/settings/portfolios/{portfolio_id}`
Update portfolio settings and strategy assignments.
- **Body:** `PortfolioFullUpdate` — portfolio fields + strategies array

#### `PATCH /api/settings/portfolios/{portfolio_id}/archive`
Archive (soft-delete) a portfolio.

#### `DELETE /api/settings/portfolios/{portfolio_id}`
Permanently delete portfolio and all related data (trades, snapshots, holdings, actions, strategies).

#### `GET /api/settings/traders`
List all traders with trade counts and portfolio assignments.

#### `PUT /api/settings/traders/{trader_slug}`
Update trader display_name, strategy_name, description.

#### `POST /api/settings/traders/{trader_slug}/rotate-key`
Generate a new API key for a trader. Returns the raw key (shown once).

#### `DELETE /api/settings/traders/{trader_slug}`
Permanently delete a trader and all their trades.

#### `GET /api/settings/keys`
List allowlisted API keys.

#### `POST /api/settings/keys/generate`
Generate a new allowlisted key (optionally with a label).

#### `DELETE /api/settings/keys/{key_id}`
Revoke an unclaimed allowlisted key.

### Screener Router (`/api/screener`)

#### `POST /api/screener/webhook`
Receive an indicator alert from TradingView.
- **Body:** `ScreenerWebhookPayload` — key, ticker, indicator, value, signal, tf, time, metadata
- **Auth:** Same key verification as trade webhook (checks traders, then allowlisted keys)

#### `GET /api/screener/alerts`
List recent alerts.
- **Params:** `ticker`, `indicator`, `signal`, `hours` (default 24), `limit` (default 200)

#### `GET /api/screener/tickers`
Aggregated view: alerts grouped by ticker with counts and indicator lists.
- **Params:** `hours` (default 24)

#### `GET /api/screener/chart/{ticker}`
Daily OHLCV chart data.
- **Params:** `days` (default 60)
- **Calls:** `chart_service.get_daily_chart()` (yfinance, cached 15 min)

#### `GET /api/screener/analysis/latest`
Latest screener AI analysis (trade ideas + market context).

#### `POST /api/screener/analyze/{ticker}`
Trigger per-ticker Claude analysis with alert data, chart, positions, and trade history.
- **Params:** `force_refresh` (boolean)
- **Body:** `TickerAnalysisRequest` (hours)
- **Response:** `TickerAnalysisResponse` — play_type, direction, confidence, thesis, entry_zone, price_target, stop_loss, risk_reward, indicators_firing, historical_matches, strategy_alignment

### Watchlist Router (`/api/watchlist`)

#### `GET /api/watchlist`
All active watchlist tickers with latest signals, strategy positions, consensus, cached summary, signal events, and trade events.

#### `POST /api/watchlist`
Add tickers to watchlist.
- **Body:** `{ tickers: string[], notes?: string }`

#### `DELETE /api/watchlist/{ticker}`
Soft-delete a ticker from watchlist.

#### `GET /api/watchlist/{ticker}/detail`
Expanded view: all signals, positions, trade history, consensus, summary.

#### `POST /api/watchlist/{ticker}/refresh-summary`
Trigger background AI summary regeneration.

#### `POST /api/watchlist/sync`
Sync all tickers from active holdings and open trades to watchlist.

#### `GET /api/watchlist/strategies/list`
List all active strategies from the traders table.

### Portfolio Manager Router (`/api/portfolio-manager`)

#### `GET /api/portfolio-manager/{portfolio_id}/holdings`
List holdings for a portfolio.

#### `POST /api/portfolio-manager/holdings`
Create a manual holding.
- **Body:** `HoldingCreate` — portfolio_id, ticker, direction, entry_price, qty, entry_date, position_type, thesis, catalyst fields, DCA fields

#### `PUT /api/portfolio-manager/holdings/{holding_id}`
Update a holding (price, qty, notes, archetype fields).

#### `DELETE /api/portfolio-manager/holdings/{holding_id}`
Delete a holding.

#### `GET /api/portfolio-manager/{portfolio_id}/actions`
List recommended actions for a portfolio.
- **Params:** `status` filter, `limit`

#### `POST /api/portfolio-manager/actions/{action_id}/approve`
Approve a recommended action.

#### `POST /api/portfolio-manager/actions/{action_id}/reject`
Reject a recommended action.
- **Body:** `ActionReject` — reason

#### `GET /api/portfolio-manager/{portfolio_id}/action-stats`
Action queue statistics (pending, approved, rejected, hit rate).

#### `POST /api/portfolio-manager/import-backtest`
Upload a TradingView CSV backtest.
- **Multipart:** `file` (CSV), optional `strategy_name`, `ticker`

#### `GET /api/portfolio-manager/backtests`
List all backtest imports.

#### `GET /api/portfolio-manager/backtests/{import_id}/trades`
Get trades from a specific backtest import.

#### `DELETE /api/portfolio-manager/backtests/{import_id}`
Delete a backtest import and its trades.

#### `POST /api/portfolio-manager/import-brokerage`
Import brokerage trade history CSV (auto-detects format).

#### `POST /api/portfolio-manager/import-brokerage/commit`
Commit a previewed brokerage import, creating/updating holdings.

### Analytics Router (`/api/analytics`)

#### `POST /api/analytics/monte-carlo`
Run Monte Carlo simulation using historical trade P&L data.
- **Body:** `MonteCarloRequest` — source (live/backtest/combined), strategy, ticker, num_simulations, forward_trades, initial_capital, position_size_pct
- **Response:** `MonteCarloResponse` — percentile_bands, sample_paths, summary (median/mean equity, P5/P95, probability of profit/ruin, drawdown), equity/drawdown histograms, input_stats, optional buyhold comparison

#### `POST /api/analytics/monte-carlo/buyhold`
Standalone buy-and-hold Monte Carlo for a specific ticker.
- **Params:** ticker, num_simulations, forward_steps, initial_capital, history_days

### News Router (`/api`)

#### `GET /api/news`
Get cached news articles.
- **Params:** `ticker`, `limit`, `hours`

#### `GET /api/news/ticker/{ticker}`
Get news, sentiment, and company info for a ticker.
- **Calls:** `news_service.get_ticker_headlines()`, `news_service.get_news_sentiment()`, `get_company_description()` (all in parallel)

#### `GET /api/news/ticker/{ticker}/thesis`
Get cached bull/bear thesis for a ticker.

#### `POST /api/news/ticker/{ticker}/thesis`
Generate a bull/bear thesis using AI (Gemini). Caches the result.

### AI Portfolio Router (`/api/ai-portfolio`)

#### `POST /api/ai-portfolio/create`
Create Henry's AI-managed paper portfolio.
- **Body:** name, initial_capital, risk limits

#### `POST /api/ai-portfolio/reset`
Reset the AI portfolio (delete all trades, reset equity).

#### `GET /api/ai-portfolio/status`
AI portfolio status — equity, positions, return.

#### `GET /api/ai-portfolio/compare`
Side-by-side comparison: AI portfolio vs real portfolios.

#### `GET /api/ai-portfolio/equity-history`
Equity snapshots for charting.

#### `GET /api/ai-portfolio/decisions`
Henry's decision log — taken vs skipped signals with reasoning.
- **Params:** `filter` (all/taken/skipped), `limit`

#### `GET /api/ai-portfolio/holdings`
AI portfolio open positions with P&L and Henry's reasoning.

#### `POST /api/ai-portfolio/chat`
Ask Henry about his AI portfolio decisions.
- **Body:** `{ question: string }`

#### `GET /api/ai-portfolio/config`
Get Henry's AI trading decision framework config.

#### `PUT /api/ai-portfolio/config`
Update config (min_confidence, allocation percentages, min_adx, require_stop, reward_risk_ratio).

### Execution Router (`/api/execution`)

#### `POST /api/execution/test-connection`
Test Alpaca credentials for a portfolio.
- **Body:** `{ portfolio_id: string }`

#### `POST /api/execution/order`
Submit an order (routes through local/paper/live based on portfolio mode).
- **Body:** `{ portfolio_id, ticker, qty, side }`
- **Safety:** Checks `max_order_amount`

#### `GET /api/execution/order/{order_id}`
Check order fill status.
- **Params:** `portfolio_id`

#### `GET /api/execution/positions`
Get Alpaca account positions for reconciliation.
- **Params:** `portfolio_id`

#### `POST /api/execution/sync`
Sync Alpaca positions to portfolio holdings.

#### `POST /api/execution/kill-switch`
Emergency: set ALL portfolios to `execution_mode='local'`.

### AI Endpoints (registered directly on app in `main.py`)

#### `GET /api/ai/briefing`
Get morning briefing (cached or fresh).

#### `POST /api/ai/briefing/refresh`
Force-refresh the morning briefing.

#### `POST /api/ai/review`
Nightly trade review.
- **Body:** `{ days_back: number }`

#### `POST /api/ai/query`
Ask Henry a question.
- **Body:** `{ question, portfolio_id? }`

#### `GET /api/ai/conflicts`
Get recent conflict resolutions.
- **Params:** `days_back`, `limit`

#### `GET /api/ai/usage`
AI usage analytics — calls, tokens, costs, breakdown.
- **Params:** `days`, `provider`

#### `GET /api/ai/summaries`
Get recent market summaries (morning/nightly).

#### `POST /api/ai/summaries/generate`
Force-trigger summary generation.

### Other Endpoints

#### `GET /api/health`
Health check. Returns `{ status: "ok" }`.

#### `GET /api/prices`
Return the current price cache.

#### `GET /api/debug/ai`
Test Claude API connectivity, list available models.

#### `POST /api/admin/seed`
One-time database seeding (creates default trader + portfolios).
- **Params:** `secret` (must match `ADMIN_SECRET`)

---

## Section 4: Services Architecture

### `ai_service.py`
Henry's core AI brain. Key functions:
- `_build_system_prompt(ticker, strategy, scope)` — Assembles dynamic system prompt from: base prompt + active strategy descriptions + high-importance memories + prior context notes + track record (hit rate) + strategy stats + recent market headlines
- `_call_claude_async(prompt, max_tokens, ticker, strategy, scope, function_name)` — Async wrapper that builds system prompt and routes through dual provider
- `_call_claude(prompt, max_tokens, system_override)` — Synchronous fallback with model cascade
- `save_memory(content, memory_type, strategy_id, ticker, importance, source)` — Save to henry_memory
- `save_context(content, context_type, ticker, strategy, portfolio_id, confidence, action_id, trade_id, expires_days)` — Save to henry_context
- `extract_and_save_memories(analysis_text, source)` — Post-analysis: ask AI to extract key observations, save as memories
- `_extract_and_save_context(analysis_text, context_type, ...)` — Post-analysis: extract conclusions as context
- `nightly_review(todays_trades, recent_history)` — Nightly trade review
- `morning_briefing(todays_trades, current_positions, market_data)` — Morning briefing with market intel
- `ask_henry(question, context_trades, open_positions, market_data, portfolio_id)` — Natural language Q&A
- `resolve_conflict(conflicting_signals, recent_trades)` — Strategy conflict resolution
- `register_ai_routes(app, get_trades, get_positions, get_market_data)` — Registers AI endpoints on the FastAPI app

### `ai_provider.py`
Dual AI provider routing system.
- `call_ai(system, prompt, function_name, max_tokens, question_text)` — Routes to Claude or Gemini based on function_name
- Provider routing rules:
  - **Claude (high-stakes):** signal_evaluation, scheduled_review, conflict_resolution, ai_portfolio_decision
  - **Gemini (high-volume):** morning_briefing, watchlist_summary, ask_henry, screener_analysis, trade_review, memory_extraction
- Escalation: ask_henry escalates to Claude if question contains decision keywords (should, recommend, buy, sell, trade, position, allocate, rebalance, trim, close)
- Fallback: If Gemini fails, automatically falls back to Claude
- Model cascade: Claude tries Sonnet 4.5 -> Sonnet 4.6 -> Haiku 4.5
- Gemini: Uses `gemini-2.0-flash`
- All calls logged to `ai_usage` table asynchronously

### `trade_processor.py`
Processes incoming webhooks into trades.
- `process_webhook(payload, db)` — Master entry point
  - Authenticates via trader API key or allowlisted key (auto-creates trader on first use)
  - On entry: creates Trade, links to portfolios via direction filter, deducts cash from portfolio
  - On exit: finds matching open trade (by trader + direction + ticker), calculates P&L, credits cash back, takes portfolio snapshots
  - Auto-adds ticker to watchlist
  - Fires portfolio manager hooks (link_trade_to_holding, evaluate_signal, track_action_outcome)
  - Saves outcome context to henry_context

### `price_service.py`
Background price poller (singleton).
- Polls Alpaca snapshots API every 15s during market hours, 60s outside
- In-memory cache: `{ ticker: { price, timestamp } }`
- Market hours: Mon-Fri 9:30 AM - 4:00 PM ET
- Tickers added dynamically as trades/holdings appear

### `portfolio_analysis.py`
Henry's portfolio management brain. Three tiers:
- `evaluate_signal(trade, db)` — On every webhook. Claude call. Checks backtest data, holdings, generates BUY/SELL/TRIM/ADD/DCA actions
- `evaluate_thresholds(db)` — Hourly. Pure Python, no Claude. Checks price thresholds, DCA triggers, drawdown limits, catalyst dates
- `scheduled_review(db)` — Daily at 10 AM ET. Full Claude analysis of all portfolio holdings
- `link_trade_to_holding(trade, db)` — Links incoming trades to existing holdings
- `track_action_outcome(trade, db)` — On trade exit, tracks whether Henry's prior recommendation was correct

### `ai_portfolio.py`
Manages Henry's AI paper portfolio.
- `evaluate_signal_for_ai_portfolio(trade, trader, payload)` — On each webhook signal, Henry decides BUY or SKIP with confidence and reasoning
- `process_exit_for_ai_portfolio(trade, trader)` — Closes matching simulated trade
- `create_ai_portfolio(name, initial_capital, ...)` — Creates the AI portfolio
- `reset_ai_portfolio(db)` — Resets all trades and equity
- `scheduled_ai_portfolio_review()` — Daily review of open positions
- Config stored in `henry_cache` with key `ai_trading_config`

### `screener_ai.py`
Analyzes indicator alerts and generates trade ideas.
- `analyze_screener_signals(alerts, ticker_aggregations, chart_data, portfolio_positions)` — Batch analysis producing picks + market context
- `analyze_single_ticker(ticker, alerts, chart_data, portfolio_positions, trade_history)` — Per-ticker deep analysis
- `generate_market_summary(summary_type, portfolio_data, screener_data, picks_data)` — Morning/nightly summaries
- `refresh_strategies_cache()` — Loads strategy descriptions from DB on startup

### `watchlist_ai.py`
Per-ticker AI summaries for the watchlist.
- `generate_watchlist_summary(ticker)` — Gathers signals, positions, trade history, backtest stats, Henry's prior notes; calls AI; upserts summary
- `check_and_regenerate_if_stale(ticker)` — Checks staleness criteria (>2 new alerts, >4 hours old, new trades) and triggers regen

### `market_intel.py`
Gathers rich market context for briefings.
- `gather_market_intel(held_tickers)` — Parallel fetch of: Alpaca news (portfolio + general), market snapshots, top movers, pre-market gaps (yfinance), sector ETF performance (yfinance), earnings calendar (yfinance), VIX context, SPY context
- External APIs: Alpaca News v1beta1, Alpaca Snapshots v2, yfinance

### `chart_service.py`
Daily OHLCV chart data via yfinance.
- `get_daily_chart(ticker, days)` — Cached 15 minutes in memory
- Returns: `[{ date, open, high, low, close, volume }]`

### `news_service.py`
News fetching, caching, and sentiment analysis.
- Fetches from Alpaca News API, caches in `news_cache` table
- Keyword-based sentiment scoring (-1.0 to 1.0)
- Company description caching via yfinance (24h in-memory TTL)

### `alpaca_service.py`
Order execution via Alpaca Trading API.
- `test_connection(api_key, secret_key, paper)` — Test credentials
- `submit_order(api_key, secret_key, paper, ticker, qty, side)` — Market order
- `get_order_status(api_key, secret_key, paper, order_id)` — Check fill
- `get_positions(api_key, secret_key, paper)` — Current positions
- All calls run in thread pool (alpaca-py is sync)

### `scheduler.py`
APScheduler background jobs (see Section 10).

---

## Section 5: AI System (Henry)

### System Prompt Structure
The system prompt is assembled dynamically by `_build_system_prompt()`:

1. **Base prompt** — Henry's persona, tone, formatting rules, position archetype evaluation rules
2. **Strategy descriptions** — Pulled from `traders.strategy_description` (or `description`) for all active strategies
3. **Memory log** — Top 20 memories with `importance >= 6`, sorted by importance desc then updated_at desc. Each memory's `reference_count` is incremented when included
4. **Prior context notes** — HenryContext entries that haven't expired. Scoped to ticker/strategy when in signal evaluation mode
5. **Track record** — `henry_hit_rate` stat: overall accuracy, high-confidence accuracy, mid-confidence accuracy
6. **Strategy stats** — `strategy_performance` stats: win rate, profit factor, trade count, avg hold bars, current streak per strategy
7. **Recent market headlines** — Latest 5 headlines from `news_cache`

### Provider Routing

| Function | Provider | Rationale |
|---|---|---|
| signal_evaluation | Claude | High-stakes: affects portfolio actions |
| scheduled_review | Claude | Deep analysis, needs reasoning |
| conflict_resolution | Claude | High-stakes decision |
| ai_portfolio_decision | Claude | Paper money but needs consistency |
| morning_briefing | Gemini | High-volume, less critical |
| watchlist_summary | Gemini | High-volume summaries |
| ask_henry | Gemini (escalates to Claude) | Escalates on decision keywords |
| screener_analysis | Gemini | Batch analysis |
| trade_review | Gemini | Nightly review |
| memory_extraction | Gemini | Post-processing, low stakes |

### Memory System

**HenryMemory** (long-term):
- Types: observation, lesson, preference, strategy_note, decision
- Importance 1-10 (only >= 6 included in prompts)
- Validated flag tracks whether observation was confirmed by outcomes
- Source tracks origin (briefing, signal_eval, scheduled_review, user, outcome_tracking)
- Extracted automatically after each AI analysis via `extract_and_save_memories()`

**HenryContext** (medium-term):
- Types: recommendation, outcome, observation, pattern, portfolio_note, user_decision
- Has optional expiry (expires_at)
- Linked to specific actions/trades via FKs
- Scoped by ticker/strategy/portfolio
- Cleaned up daily: expired entries deleted, non-outcome entries > 90 days deleted

**HenryStats** (pre-computed):
- Computed by background job every 2h during market hours
- Types: strategy_performance, ticker_performance, strategy_correlation, exit_reason_analysis, henry_hit_rate, hold_time_analysis, portfolio_risk, screener_accuracy
- JSON blob with computed data, period_days for lookback

### Cost Tracking
Every AI call is logged to `ai_usage` table with provider, function, model, tokens, latency, fallback flag.
Estimated costs displayed in `/api/ai/usage`: Claude ~$3/M input + $15/M output, Gemini ~$0.10/M input + $0.40/M output.

---

## Section 6: Frontend Architecture

### Page Routes

| Route | Page | Description |
|---|---|---|
| `/` | Leaderboard | Redirects to or renders the home/leaderboard page |
| `/ai` | AI Dashboard | Morning briefing, trade review, AskHenry, conflict log tabs |
| `/portfolios` | Portfolio List | All active portfolios with equity, return, open positions |
| `/portfolios/[portfolioId]` | Portfolio Detail | Equity chart, positions, performance, daily stats, holdings, action queue |
| `/screener` | Watchlist/Screener | Heatmap card grid of watchlist tickers with signals, consensus, per-ticker AI analysis |
| `/ai-portfolio` | AI Portfolio | Henry's paper portfolio: status, comparison, decision log, holdings, chat |
| `/portfolio-manager` | Portfolio Manager | Holdings CRUD, backtest imports, brokerage CSV imports |
| `/monte-carlo` | Monte Carlo | Monte Carlo simulation with parameter controls |
| `/settings` | Settings | Portfolio CRUD, strategy management, API key management, execution mode config |

### Key Components

- `MorningBriefing.tsx` — Renders the morning briefing with refresh button
- `AskHenry.tsx` — Chat interface for natural language queries
- `TradeReview.tsx` — Nightly trade review with days-back selector
- `ConflictLog.tsx` — Table of strategy conflict resolutions

### Data Fetching Patterns
- `api.ts` exports an `api` object with typed fetch wrappers
- All calls go through `fetchApi<T>()` which adds Content-Type header and throws on non-OK
- Components use `useEffect` + `useState` for data fetching (no React Query)
- Some pages use polling intervals for live data

### Design System
- **Dark theme only:** surface `#111827`, screener-amber `#fbbf24`, ai-blue `#6366f1`, profit `#22c55e`, loss `#ef4444`
- **Fonts:** Outfit (display) + JetBrains Mono (data/terminal), loaded via Google Fonts
- **Animations:** fade-in, scale-in, gauge-fill, heat-glow-warm/hot, breathe, slide-up-panel
- **Single-file pattern:** Each page component is kept in one file (no component extraction)

---

## Section 7: Webhook Formats

### Strategy Webhook (Trade Signals)

**Endpoint:** `POST /api/webhook`

```json
{
  "key": "your-api-key",
  "trader": "henry-v36",
  "signal": "entry",
  "dir": "long",
  "ticker": "NVDA",
  "price": 850.25,
  "qty": 10.0,
  "sig": 8.5,
  "adx": 32.1,
  "atr": 12.5,
  "stop": 838.00,
  "tf": "4h",
  "time": 1711900800000
}
```

**Entry fields:**
| Field | Type | Required | Description |
|---|---|---|---|
| `key` | string | Yes | API key (verified against trader's bcrypt hash) |
| `trader` | string | Yes | Trader slug (e.g. `henry-v36`) |
| `signal` | string | Yes | `entry` or `exit` |
| `dir` | string | Yes | `long` or `short` |
| `ticker` | string | Yes | Stock symbol |
| `price` | float | Yes | Execution price |
| `qty` | float | No | Share quantity (default 0.0) |
| `sig` | float | No | Signal strength |
| `adx` | float | No | ADX value |
| `atr` | float | No | ATR value |
| `stop` | float | No | Stop loss price |
| `tf` | string | No | Timeframe (e.g. `4h`, `1D`) |
| `time` | int | No | Unix timestamp in milliseconds |
| `profile` | string | No | Ignored |

**Exit-specific fields:**
| Field | Type | Description |
|---|---|---|
| `exit_reason` | string | Why the trade was closed |
| `pnl_pct` | float | P&L percentage |
| `bars_in_trade` | int | Number of bars held |

**Validation:** `time` coerced from string to int. Extra fields ignored (`model_config = {"extra": "ignore"}`).

### Screener Webhook (Indicator Alerts)

**Endpoint:** `POST /api/screener/webhook`

```json
{
  "key": "your-api-key",
  "ticker": "NASDAQ:NVDA",
  "indicator": "RSI",
  "value": 28.5,
  "signal": "bullish",
  "tf": "240",
  "time": 1711900800000,
  "metadata": { "period": 14 }
}
```

| Field | Type | Required | Description |
|---|---|---|---|
| `key` | string | Yes | API key |
| `ticker` | string | Yes | Exchange prefix stripped automatically (NASDAQ:NVDA -> NVDA) |
| `indicator` | string | No | Indicator name (default `UNKNOWN`), uppercased |
| `value` | float | No | Indicator value (handles empty strings and NaN) |
| `signal` | string | No | Normalized: bullish/bearish/neutral. Maps: bull, long, buy, up, 1, true -> bullish |
| `tf` | string | No | Normalized: 240 -> 4h, 60 -> 1h, D -> 1D, etc. |
| `time` | int | No | Unix timestamp ms |
| `metadata` | dict | No | Extra metadata stored in `metadata_extra` |

---

## Section 8: Position Archetypes

Holdings in `portfolio_holdings` are tagged with a `position_type` that controls how Henry evaluates them:

### Momentum (default)
- Standard technical trade
- Evaluated on signal strength, momentum indicators, trend
- Henry recommends SELL when signals reverse
- No special protections

### Accumulation
- Position being intentionally built over time
- User provides a `thesis` explaining the long-term view
- Henry recommends DCA on dips to the `dca_threshold_pct`
- Henry does NOT recommend selling on price weakness
- Always references the user's thesis in analysis

### Catalyst
- Held for a specific upcoming event
- `catalyst_date` and `catalyst_description` are set
- Henry does NOT recommend selling before the catalyst date
- Flags when catalyst is approaching
- After catalyst passes, suggests the user update the holding

### Conviction
- Long-term hold with high conviction
- Only flagged on extreme drawdowns (>40%) or direct thesis invalidation
- Normal volatility is NOT treated as a problem

### DCA Logic
- Holdings with `dca_enabled = True` and `dca_threshold_pct` set
- When price drops below avg_cost by the threshold percentage, Henry recommends a DCA action
- `avg_cost` and `total_shares` track the blended position
- DCA actions are created as `action_type = "DCA"` in portfolio_actions

---

## Section 9: Execution System

### Modes
Each portfolio has an `execution_mode`:

| Mode | Behavior |
|---|---|
| `local` | Orders only update holdings in the DB. No external broker calls. |
| `paper` | Orders sent to Alpaca Paper Trading API. Updates holdings on fill. |
| `live` | Orders sent to Alpaca Live Trading API. Updates holdings on fill. |

### Per-Portfolio Alpaca Credentials
Each portfolio stores its own `alpaca_api_key` and `alpaca_secret_key`. This allows different portfolios to use different Alpaca accounts (e.g. one paper, one live).

### Order Flow
1. `POST /api/execution/order` receives `{ portfolio_id, ticker, qty, side }`
2. If mode is `local`: directly update holdings via `_update_holding_local()`
3. If mode is `paper` or `live`:
   a. Check `max_order_amount` safety rail
   b. Submit market order via `alpaca_service.submit_order()`
   c. Poll for fill (up to 5 seconds, 0.5s intervals)
   d. On fill, update holdings in DB

### Safety Rails
- `max_order_amount`: Maximum dollar value per order. If `current_price * qty > max_order_amount`, the order is rejected.
- Kill switch: `POST /api/execution/kill-switch` immediately sets ALL portfolios with paper/live mode to `local` mode. No confirmation required.
- Position sync: `POST /api/execution/sync` reconciles Alpaca positions with DB holdings.

---

## Section 10: Background Jobs

All jobs managed by APScheduler. Times in US Eastern.

| Job | Schedule | Description |
|---|---|---|
| Morning Summary | 9:30 AM ET daily | Generates morning briefing from open positions, yesterday's trades, recent screener alerts. Invalidates stale ticker_analysis and signal_eval caches. |
| Nightly Summary | 4:15 PM ET daily | Generates nightly review from today's closed trades, alerts, and morning picks scorecard. |
| Screener Refresh | Every 30 minutes | Analyzes last 24h of indicator alerts, generates trade ideas + market context. |
| Portfolio Thresholds | Hourly, M-F 10AM-3PM ET | Pure Python: checks price thresholds, DCA triggers, drawdown limits, catalyst dates. No AI call. |
| Daily Portfolio Review | 10:00 AM ET, M-F | Full Claude analysis of all portfolio holdings. Invalidates cached reviews. |
| Henry Stats | Every 2h, M-F 10AM-4PM ET | Computes strategy_performance, henry_hit_rate, and other pre-computed analytics. |
| AI Portfolio Review | 2:30 PM ET, M-F | Daily review of Henry's AI paper portfolio positions. |
| Context Cleanup | Midnight ET daily | Deletes expired HenryContext rows. Deletes non-outcome rows > 90 days old. Cleans up henry_cache entries > 7 days old. |

### Startup Tasks (in `lifespan()`)
1. `_ensure_schema()` — Creates missing tables/columns as fallback for Alembic
2. Start price poller background task
3. Start APScheduler
4. `refresh_strategies_cache()` — Load strategy descriptions for screener AI
5. `load_ai_config_from_db()` — Load AI trading config from henry_cache
6. `_sync_holdings_to_watchlist()` — Ensure all tickers from holdings/trades are on watchlist

---

## Section 11: Code Audit Findings

### CRITICAL

#### C1: Alpaca Credentials Stored in Plaintext
- **File:** `backend/app/models/portfolio.py`, lines 25-26
- **Description:** `alpaca_api_key` and `alpaca_secret_key` are stored as plain VARCHAR(255) in the database. No encryption at rest. Anyone with DB access can read all brokerage credentials.
- **Fix:** Encrypt credentials before storing (e.g. Fernet symmetric encryption with a server-side key). Decrypt on read.

#### C2: No Authentication on Any Endpoint
- **File:** `backend/app/main.py`, all API routes
- **Description:** No authentication middleware. All endpoints (settings, execution, kill-switch, admin/seed, ai/usage, debug/ai) are publicly accessible. The only auth is on webhook endpoints (API key in payload body).
- **Fix:** Add bearer token or session-based auth middleware. At minimum, protect settings, execution, admin, and AI config endpoints.

#### C3: Kill Switch Has No Auth
- **File:** `backend/app/api/execution.py`, line 252
- **Description:** `POST /api/execution/kill-switch` immediately disables all live/paper trading with no authentication. Anyone can call it.
- **Fix:** Add authentication and/or require confirmation parameter.

### HIGH

#### H1: `_ensure_schema()` Duplicates Alembic Migrations
- **File:** `backend/app/main.py`, lines 26-226
- **Description:** Over 200 lines of raw SQL DDL that recreates tables and adds columns as a fallback for failed Alembic migrations. This creates a maintenance burden — every schema change must be implemented in both Alembic AND `_ensure_schema()`. The two can drift.
- **Fix:** Fix Alembic deployment so it runs reliably. Remove `_ensure_schema()` once migrations are stable.

#### H2: Synchronous Claude Calls Block Event Loop
- **File:** `backend/app/services/ai_provider.py`, lines 102-141
- **Description:** `_call_claude()` uses the synchronous `anthropic.Anthropic()` client with `client.messages.create()`. Although called from an async function, the synchronous call blocks the event loop thread. In contrast, `_call_gemini()` correctly uses `asyncio.to_thread()`.
- **Fix:** Either use `anthropic.AsyncAnthropic()` or wrap the sync call in `asyncio.to_thread()`.

#### H3: `screener_ai.py` Creates Its Own Anthropic Client
- **File:** `backend/app/services/screener_ai.py`, lines 17-22
- **Description:** Despite the dual-provider system in `ai_provider.py`, `screener_ai.py` creates its own `anthropic.Anthropic()` client at module level. Some functions in this file call through `ai_provider.call_ai()` while others use the local client directly, bypassing provider routing and usage tracking.
- **Fix:** Remove the local CLIENT and route all calls through `ai_provider.call_ai()`.

#### H4: `watchlist_ai.py` Creates Its Own Anthropic Client
- **File:** `backend/app/services/watchlist_ai.py`, lines 27-34
- **Description:** Same issue as H3. Has its own `CLIENT = anthropic.Anthropic()` but actually calls through `ai_provider.call_ai()` in `generate_watchlist_summary()`. The local CLIENT and MODEL constants are unused dead code.
- **Fix:** Remove unused CLIENT, MODEL, MODEL_FALLBACK, MODEL_LAST_RESORT constants.

#### H5: N+1 Query in Watchlist GET
- **File:** `backend/app/api/watchlist.py`, lines 228-289
- **Description:** `GET /api/watchlist` iterates all active watchlist tickers and for EACH ticker runs 5+ separate DB queries (signals, positions, consensus, summary, last_alert_at, signal_events, trade_events). With 20 watchlist tickers, that's 100+ queries per request.
- **Fix:** Batch-load signals and positions for all tickers in 2-3 queries, then assemble in Python.

#### H6: No Rate Limiting on AI Endpoints
- **File:** `backend/app/services/ai_service.py`, `backend/app/api/news.py`
- **Description:** No rate limiting on `/api/ai/query`, `/api/ai/review`, `/api/ai/briefing/refresh`, `/api/news/ticker/{ticker}/thesis`. A malicious or buggy client could rack up significant AI API costs.
- **Fix:** Add rate limiting middleware (e.g. slowapi).

### MEDIUM

#### M1: `PortfolioHolding` Has No `source` Column
- **File:** `backend/app/models/portfolio_holding.py`
- **Description:** The `HoldingResponse` schema has a `source` field (line 57), but the `PortfolioHolding` model has no `source` column. The portfolio_manager code works around this by computing source from `trade_id` presence, but the execution sync code (line 240) tries to set `source="alpaca_sync"` which would fail.
- **Fix:** Add a `source` column to the model, or remove it from the schema and stop setting it in execution code.

#### M2: datetime.utcnow() Deprecation
- **File:** All model files and service files
- **Description:** `datetime.utcnow()` is deprecated since Python 3.12. Should use `datetime.now(timezone.utc)`.
- **Fix:** Replace all `datetime.utcnow()` with `datetime.now(datetime.timezone.utc)`.

#### M3: Unbounded Queries in Multiple Endpoints
- **Files:**
  - `backend/app/api/watchlist.py` line 478: `select(WatchlistTicker)` with no limit
  - `backend/app/api/settings.py` line 337: `select(AllowlistedKey)` with no limit
  - `backend/app/main.py` line 259: `select(WatchlistTicker)` with no limit in startup sync
- **Description:** Several queries load all rows from tables without pagination or limit. Fine with small datasets but could be problematic at scale.
- **Fix:** Add reasonable limits or pagination.

#### M4: `HenryCache.content` Type Mismatch
- **File:** `backend/app/models/henry_cache.py` line 14, `backend/app/api/news.py` lines 93-96
- **Description:** `HenryCache.content` is typed as `Mapped[dict]` (JSON), but the thesis endpoint stores `json.dumps(thesis_data)` (a string) and then on read does `json.loads(cached.content) if isinstance(cached.content, str)`. This suggests the column sometimes contains a string and sometimes a dict.
- **Fix:** Always store as dict (not json.dumps'd string). Remove the isinstance check.

#### M5: AI Service Still Has Hardcoded Model Constants
- **File:** `backend/app/services/ai_service.py`, lines 25-27
- **Description:** `MODEL`, `MODEL_FALLBACK`, `MODEL_LAST_RESORT` constants are defined but duplicated in `ai_provider.py` (lines 110-114). The sync `_call_claude()` function uses the ai_service constants; the async path uses ai_provider's constants. They happen to match but could drift.
- **Fix:** Centralize model list in one place (ai_provider) and import from there.

#### M6: Portfolio Delete Does Not Clean Up henry_context/henry_stats
- **File:** `backend/app/api/settings.py`, lines 151-217
- **Description:** When deleting a portfolio, `henry_context` and `henry_stats` rows with matching `portfolio_id` are not deleted.
- **Fix:** Add cleanup for henry_context and henry_stats in the delete handler.

#### M7: `get_ai_usage()` Loads All Rows Into Memory
- **File:** `backend/app/main.py`, lines 490-558
- **Description:** The usage analytics endpoint loads ALL usage rows for the period into Python memory, then aggregates manually. Should use SQL aggregation for large datasets.
- **Fix:** Use SQL `GROUP BY` with `func.count()`, `func.sum()` for aggregation.

#### M8: Backtest Filename Parser Edge Cases
- **File:** `backend/app/api/portfolio_manager.py`, lines 31-115
- **Description:** The filename parser handles many formats but has edge cases: tickers that match exchange names (e.g. stock symbol "NYSE" would be misidentified), and multi-word strategy names with underscores could be misparsed.
- **Fix:** Consider adding manual override parameters (already partially supported via `strategy_name` and `ticker` form fields).

### LOW

#### L1: `generated_at` Reference Error in Thesis Endpoint
- **File:** `backend/app/api/news.py`, line 222
- **Description:** `datetime.utcnow().isoformat() if 'datetime' in dir() else None` — This condition checks `'datetime' in dir()` which is checking local scope. The `datetime` import is at file level and won't be in `dir()` inside the try block. The condition will always be False.
- **Fix:** Remove the condition, just use `datetime.utcnow().isoformat()`.

#### L2: Unused Import in `ai_service.py`
- **File:** `backend/app/services/ai_service.py`, line 18
- **Description:** `from typing import Optional` is imported but Python 3.10+ uses `X | None` syntax throughout the codebase.
- **Fix:** Remove unused import.

#### L3: Price Service Silently Fails
- **File:** `backend/app/services/price_service.py`, line 69
- **Description:** `_fetch_prices()` catches all exceptions silently. If the Alpaca API key is wrong or the API is down, there's no logging, and prices will be stale indefinitely.
- **Fix:** Add logging on exception.

#### L4: Inconsistent PortfolioSettingsResponse
- **File:** `backend/app/schemas/settings.py`, lines 48-63
- **Description:** `PortfolioSettingsResponse` has `execution_mode` and `has_alpaca_credentials` fields, but the `list_portfolios` endpoint never populates them (the response construction at lines 44-56 doesn't set these fields).
- **Fix:** Set `execution_mode` and `has_alpaca_credentials` in the list endpoint.

#### L5: `ai_service.py` Has Unused `_call_claude` Sync Function
- **File:** `backend/app/services/ai_service.py`, lines 266-299
- **Description:** The synchronous `_call_claude()` function is still present but all current callers use `_call_claude_async()`. The sync function duplicates the model cascade logic.
- **Fix:** Remove if no longer called by any code path.

#### L6: Stale `SYSTEM_PROMPT` Assignment
- **File:** `backend/app/services/ai_service.py`, line 261
- **Description:** `SYSTEM_PROMPT = BASE_SYSTEM_PROMPT` — This module-level assignment provides a static fallback for sync calls, but doesn't include any dynamic context. It's only used by the sync `_call_claude()` which may itself be dead code.
- **Fix:** Remove alongside L5 if sync path is dead.

#### L7: Portfolio Equity Calculation May Double-Count
- **File:** `backend/app/api/portfolios.py`, lines 84-96
- **Description:** Equity = `cash + holdings_market_value + (snap_equity - initial_capital)`. If holdings came from webhook trades that are also tracked in snapshots, there could be overlap. The code tries to avoid this but the logic is complex and fragile.
- **Fix:** Document the invariants clearly. Consider unifying the two tracking paths.

#### L8: `select(Trader)` in Screener Webhook Loads All Traders
- **File:** `backend/app/api/screener.py`, lines 35-36
- **Description:** `select(Trader)` with no filter loads all traders, then iterates to check API key. With many traders, this is inefficient.
- **Fix:** Since bcrypt verification requires the hash, consider indexing by a key prefix or caching.
