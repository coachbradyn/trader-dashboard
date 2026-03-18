# Portfolio Manager — Henry as Active Portfolio Manager

**Date:** 2026-03-17
**Status:** Approved

## Overview

Henry evolves from a passive trade analyst into a semi-autonomous portfolio manager. Users build portfolios with manual holdings and backtest imports, and Henry continuously analyzes incoming signals, threshold breaches, and daily reviews to recommend actions. Users approve or reject recommendations from a persistent Action Queue.

---

## Core Concepts

### Three Trigger Types

1. **Webhook-driven (SIGNAL)** — every incoming strategy signal gets evaluated against portfolio state + backtest history. Henry may recommend: take the trade, skip it, trim a position, or adjust.
2. **Threshold monitors (THRESHOLD)** — lightweight Python checks every hour during market hours. Concentration, drawdown, stop proximity, unrealized P&L extremes. No Claude call — creates actions directly with pre-templated reasoning.
3. **Scheduled deep review (SCHEDULED_REVIEW)** — once daily (configurable, e.g., 10am ET). Full Claude analysis with entire portfolio state + all backtest context. Hold time analysis, strategy benchmark comparison, rebalancing.

### Semi-Autonomous Workflow

Henry queues recommended actions. User approves or rejects from the dashboard. Actions auto-expire (4h for signal-driven, 24h for scheduled). Nothing executes without approval.

### Action Priority

Composite score: `urgency_weight x confidence`. Urgency weights:
- Threshold breach: 3x
- Signal-driven: 2x
- Scheduled review: 1x

Queue sorted by composite score descending.

---

## Data Model

### `portfolio_actions` — the Action Queue

| Column | Type | Description |
|--------|------|-------------|
| id | UUID | Primary key |
| portfolio_id | FK → portfolios | Target portfolio |
| ticker | String(10) | Stock ticker |
| direction | String(10) | long / short |
| action_type | String(20) | BUY / SELL / TRIM / ADD / CLOSE / REBALANCE |
| suggested_qty | Float | Recommended quantity |
| suggested_price | Float | Price at time of recommendation |
| current_price | Float | Market price when action was created |
| confidence | Integer | 1-10 confidence score |
| reasoning | Text | Henry's explanation (2-3 sentences) |
| trigger_type | String(20) | SIGNAL / THRESHOLD / SCHEDULED_REVIEW |
| trigger_ref | String(36), nullable | Trade ID or alert ID that caused it |
| priority_score | Float | Computed: urgency_weight x confidence |
| status | String(20) | pending / approved / rejected / expired |
| expires_at | DateTime | 4h for signal, 24h for scheduled |
| outcome_pnl | Float, nullable | Realized P&L after approval played out |
| outcome_correct | Boolean, nullable | Was the recommendation profitable |
| outcome_resolved_at | DateTime, nullable | When resulting position closed |
| created_at | DateTime | |
| resolved_at | DateTime, nullable | When approved/rejected |

### `backtest_imports` — metadata per imported CSV

| Column | Type | Description |
|--------|------|-------------|
| id | UUID | Primary key |
| strategy_name | String(50) | Parsed from filename (e.g., "HENRY") |
| strategy_version | String(20) | Parsed from filename (e.g., "v3.8") |
| exchange | String(20) | Parsed from filename (e.g., "NASDAQ") |
| ticker | String(10) | Parsed from filename (e.g., "NVDA") |
| filename | String(255) | Original filename |
| trade_count | Integer | Number of round-trip trades |
| win_rate | Float | Percentage of winning trades |
| profit_factor | Float | Gross profit / gross loss |
| avg_gain_pct | Float | Average winning trade % |
| avg_loss_pct | Float | Average losing trade % |
| max_drawdown_pct | Float | Max drawdown from equity peak |
| max_adverse_excursion_pct | Float | Average worst intra-trade drawdown |
| avg_hold_bars | Integer | Average bars in trade |
| imported_at | DateTime | |

### `backtest_trades` — individual rows from CSV

| Column | Type | Description |
|--------|------|-------------|
| id | UUID | Primary key |
| import_id | FK → backtest_imports | Parent import |
| trade_number | Integer | Trade # from CSV |
| type | String(10) | "Entry long" / "Exit long" / etc. |
| direction | String(10) | long / short |
| signal | String(50) | Entry/exit signal name (L Entry, ADX Fade, K-Reversal, Slope Flat) |
| price | Float | Trade price |
| qty | Float | Position size in shares |
| position_value | Float | Position size in dollars |
| net_pnl | Float, nullable | Net P&L in dollars (exit rows only) |
| net_pnl_pct | Float, nullable | Net P&L percentage |
| favorable_excursion | Float, nullable | Max favorable excursion $ |
| favorable_excursion_pct | Float, nullable | Max favorable excursion % |
| adverse_excursion | Float, nullable | Max adverse excursion $ |
| adverse_excursion_pct | Float, nullable | Max adverse excursion % |
| cumulative_pnl | Float, nullable | Running cumulative P&L $ |
| cumulative_pnl_pct | Float, nullable | Running cumulative P&L % |
| trade_date | DateTime | Date and time from CSV |

### `portfolio_holdings` — manually entered + webhook-linked positions

| Column | Type | Description |
|--------|------|-------------|
| id | UUID | Primary key |
| portfolio_id | FK → portfolios | Parent portfolio |
| trade_id | FK → trades, nullable | Links to webhook trade (null = manual entry) |
| ticker | String(10) | |
| direction | String(10) | long / short |
| entry_price | Float | |
| qty | Float | |
| entry_date | DateTime | |
| strategy_name | String(50) | Which strategy originated this |
| notes | Text, nullable | Optional user notes |
| is_active | Boolean | Still held or closed out |
| created_at | DateTime | |

#### Source of Truth Logic

When a webhook creates a trade, the system checks for an existing manual holding on the same ticker + direction + strategy. If found, it **links** the holding to the trade via `trade_id` instead of creating a duplicate. Henry sees one position with full provenance.

---

## API Design

### Backtest Import

```
POST   /api/portfolio-manager/import          — multipart file upload, multiple CSVs
GET    /api/portfolio-manager/imports          — list all imports with summary stats
DELETE /api/portfolio-manager/imports/{id}     — remove import and its trades
```

**Filename parsing pattern:** `{STRATEGY}_{VERSION}_{EXCHANGE}_{TICKER}_{DATE}.csv`
Example: `HENRY_v3.8_NASDAQ_NVDA_2026-03-17.csv` → strategy=HENRY, version=v3.8, exchange=NASDAQ, ticker=NVDA

**On import:**
1. Parse filename for metadata
2. Parse CSV rows, create backtest_trade records
3. Compute summary stats (win rate, profit factor, avg gain/loss, MAE, avg hold time)
4. Store in backtest_imports

### Holdings Management

```
GET    /api/portfolio-manager/holdings?portfolio_id=X   — current holdings
POST   /api/portfolio-manager/holdings                  — add holding manually
PUT    /api/portfolio-manager/holdings/{id}              — edit
DELETE /api/portfolio-manager/holdings/{id}              — remove
```

### Action Queue

```
GET    /api/portfolio-manager/actions?status=pending     — fetch actions (pending/all/approved/rejected)
POST   /api/portfolio-manager/actions/{id}/approve       — approve
POST   /api/portfolio-manager/actions/{id}/reject        — reject with optional reason
```

---

## Henry's Analysis Functions

### 1. `evaluate_signal(signal, portfolio, backtest_stats)`

**Trigger:** Every incoming webhook, called from `trade_processor.py` after processing.

**Input to Claude:**
- Incoming signal (ticker, direction, strategy, price, indicators)
- Current portfolio holdings (all positions, cash, concentration by ticker)
- Backtest stats for this strategy+ticker (win rate, avg gain, avg loss, MAE, exit signals)
- Other strategies' open positions on the same ticker

**Output:** Action recommendation (or no action) with reasoning and confidence.

**Example prompt context:** "S1 fired long NVDA at $174. Portfolio is 0% NVDA. Backtest: S1 has 100% win rate on NVDA across 9 trades, avg gain 4.07%, avg MAE -4.4%. Recommend: BUY, confidence 8/10."

### 2. `evaluate_thresholds(portfolio, prices)` — NO CLAUDE CALL

**Trigger:** Every hour during market hours (lightweight Python check).

**Checks:**
- Concentration: any ticker > 25% of portfolio value
- Drawdown: portfolio drawdown approaching max_drawdown_pct
- Stop proximity: current price within 1% of a holding's stop level
- Unrealized P&L extremes: position up/down > 2x strategy's average move

**Output:** Creates portfolio_actions directly with pre-templated reasoning. No Claude call.

### 3. `scheduled_review(portfolio, all_backtest_stats)`

**Trigger:** Once daily at configurable time (e.g., 10am ET).

**Input to Claude:**
- All holdings with current P&L
- All backtest data across every strategy and ticker
- Recent action queue history (approved/rejected/outcomes)
- Market context (SPY/VIX)

**Analysis focus:**
- Rebalancing opportunities
- Positions overstaying average hold time vs backtest benchmarks
- Strategies underperforming their backtest stats
- Overall risk assessment

---

## Action Outcome Tracking

When an approved action's resulting position closes:
1. Calculate P&L from the trade
2. Set `outcome_pnl`, `outcome_correct`, `outcome_resolved_at`
3. Over time, surface "Henry's hit rate" — segmented by confidence level, action type, and trigger type

Example insight: "Henry's BUY recommendations at confidence 8+ have a 72% success rate. TRIM recommendations are only 45% accurate."

---

## Frontend: `/portfolio-manager`

### Tab 1: Action Queue (default)

**Summary bar:** `3 pending · 12 approved today · Henry's hit rate: 71%`

**Action cards** sorted by priority score, each showing:
- Left color border: red (threshold), screener-amber (signal), gray (scheduled)
- Ticker + direction badge + action type (BUY/TRIM/CLOSE/etc.)
- Henry's reasoning (2-3 sentences)
- Confidence gauge (1-10)
- Trigger source label ("S1 fired long" / "Concentration at 32%" / "Daily review")
- Approve (green) / Reject (muted) buttons
- Expiry countdown ("expires in 3h 12m")

Expired actions in collapsible section below.

### Tab 2: Holdings

**Concentration bar** at top showing allocation by ticker.

**Position table/cards:**
- Ticker, direction, qty, entry price, current price, unrealized P&L
- Source badge: "Manual" or strategy name
- Linked trade ID if webhook-originated

**Add Holding button** → inline form: ticker, direction, entry price, qty, date, strategy dropdown.

### Tab 3: Backtest Data

**Drag-and-drop zone** for multi-file CSV upload.

**Import grid:**
- Strategy, ticker, trade count, win rate, profit factor, max drawdown
- Expandable rows for individual trade history
- Re-import / Delete per entry

---

## Design Language

Follows established project conventions:
- Fonts: Outfit (display) + JetBrains Mono (data)
- Dark theme: surface #111827
- Colors: screener-amber #fbbf24, ai-blue #6366f1, profit #22c55e, loss #ef4444
- Animation: fade-in, scale-in for action cards
- Single-file page component (established pattern)

---

## Implementation Order

### Phase 1: Data Foundation
1. Database models (portfolio_actions, backtest_imports, backtest_trades, portfolio_holdings)
2. Alembic migrations
3. CSV import endpoint with filename parsing
4. Holdings CRUD API
5. Action queue API (CRUD + approve/reject)

### Phase 2: Henry's Brain
6. `evaluate_signal()` — hook into webhook pipeline
7. `evaluate_thresholds()` — hourly Python checks
8. `scheduled_review()` — daily Claude analysis
9. Action outcome tracking on trade close
10. Source of truth linking (holdings ↔ trades)

### Phase 3: Frontend
11. `/portfolio-manager` page with three tabs
12. Action Queue tab with priority sorting and approve/reject
13. Holdings tab with manual entry form
14. Backtest Data tab with drag-and-drop import
15. Henry's hit rate summary stats
