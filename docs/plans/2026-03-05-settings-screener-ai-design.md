# Settings, Screener & AI Summaries — Design Document

**Date**: 2026-03-05
**Status**: Approved

---

## Overview

Three major additions to the trader dashboard:

1. **Settings page** (`/settings`) — Portfolio management (create, configure, archive) and strategy/trader management (auto-registration via allowlisted keys, rename, rotate keys)
2. **Screener page** (`/screener`) — Real-time indicator alert cards that dynamically grow with alert volume, daily candlestick charts via yfinance, and Claude-powered trade ideas with price targets
3. **Scheduled AI summaries** — Morning and nightly Claude-generated market summaries combining portfolio positions, screener signals, and trade picks

---

## 1. Data Model Changes

### New: `IndicatorAlert`

Stores incoming screener webhook signals. High-volume table.

| Column | Type | Notes |
|---|---|---|
| id | UUID | Primary key |
| ticker | String(20) | Indexed |
| indicator | String(50) | e.g. "RSI", "MACD_CROSS", "VOL_SPIKE" |
| value | Float | The indicator reading |
| signal | String(20) | "bullish" / "bearish" / "neutral" |
| timeframe | String(10) | "5m", "1H", "1D" etc. |
| metadata | JSON | Flexible extra data from webhook |
| created_at | DateTime | Indexed, auto-set |

### New: `MarketSummary`

Stores scheduled Claude-generated summaries.

| Column | Type | Notes |
|---|---|---|
| id | UUID | Primary key |
| summary_type | String(20) | "morning" / "nightly" / "alert_digest" |
| scope | String(20) | "portfolio" / "screener" / "combined" |
| content | Text | Markdown from Claude |
| tickers_analyzed | JSON | List of tickers covered |
| generated_at | DateTime | Indexed |

### New: `ScreenerAnalysis`

Stores Claude's trade ideas generated from screener signals.

| Column | Type | Notes |
|---|---|---|
| id | UUID | Primary key |
| picks | JSON | Array of trade ideas with entry/target/stop |
| market_context | JSON | Sector heat, catalysts, noise ratio |
| alerts_analyzed | Integer | How many alerts fed the analysis |
| generated_at | DateTime | Indexed |

### New: `AllowlistedKey`

Pre-generated API keys waiting for first webhook claim.

| Column | Type | Notes |
|---|---|---|
| id | UUID | Primary key |
| api_key_hash | String(64) | Hashed key |
| label | String(100) | Optional user note ("Henry v4 test") |
| claimed_by | FK -> Trader | Null until first webhook |
| created_at | DateTime | Auto-set |

### Modified: `Portfolio` — new columns

| Column | Type | Notes |
|---|---|---|
| max_pct_per_trade | Float | Nullable, e.g. 0.05 = 5% |
| max_open_positions | Integer | Nullable |
| max_drawdown_pct | Float | Nullable, risk kill-switch |
| status | String(20) | "active" / "archived", default "active" |

---

## 2. API Layer

### Screener Endpoints

| Method | Route | Purpose |
|---|---|---|
| `POST` | `/api/screener/webhook` | Receive indicator alerts |
| `GET` | `/api/screener/alerts` | List alerts with filters (ticker, indicator, timeframe, last N hours) |
| `GET` | `/api/screener/tickers` | Aggregated view — unique tickers with alert counts and latest signals |
| `GET` | `/api/screener/chart/{ticker}` | Daily OHLCV via yfinance, cached 15 min, with alert markers |

**Screener Webhook Payload:**

```json
{
  "key": "abc123",
  "ticker": "AAPL",
  "indicator": "RSI",
  "value": 72.5,
  "signal": "bearish",
  "tf": "1D",
  "time": 1709654400000
}
```

### Settings/Management Endpoints

| Method | Route | Purpose |
|---|---|---|
| `POST` | `/api/portfolios` | Create portfolio with full config |
| `PUT` | `/api/portfolios/{id}` | Update settings, strategy assignments |
| `PATCH` | `/api/portfolios/{id}/archive` | Archive a portfolio |
| `POST` | `/api/keys/generate` | Create allowlisted API key |
| `DELETE` | `/api/keys/{id}` | Revoke an unclaimed key |
| `PUT` | `/api/traders/{slug}` | Rename trader, edit description |

### Market Summary Endpoints

| Method | Route | Purpose |
|---|---|---|
| `GET` | `/api/ai/summaries` | List recent summaries |
| `POST` | `/api/ai/summaries/generate` | Force manual generation |

---

## 3. Webhook Flow Changes

### Trade webhook (`POST /api/webhook`) — updated key resolution:

1. Webhook arrives with `key` in payload
2. Key lookup:
   - Known trader -> proceed as normal
   - Unknown key, matches **AllowlistedKey** -> auto-create Trader, link key, mark as claimed. Trader gets generated slug + "Unnamed Strategy" display name. Proceed with trade processing.
   - Unknown key, no allowlist match -> reject 401
3. Trade processing continues (PortfolioStrategy routing, conflict detection)

### Screener webhook (`POST /api/screener/webhook`):

1. Webhook arrives with `key` + indicator data
2. Same key validation (known trader OR allowlisted key)
3. Create `IndicatorAlert` record
4. If alert count for that ticker crosses threshold in last hour (3+ alerts), trigger async Claude analysis refresh for that ticker
5. Return 200 with alert ID

---

## 4. Settings Page (`/settings`)

Two-tab layout: **Portfolios** | **Strategies**

### Portfolios Tab

Two-panel layout — portfolio list (left), detail editor (right).

**Portfolio List:**
- Cards: name, status badge (active/archived), equity, strategy count
- Active first, archived grayed out at bottom
- "+ New Portfolio" button at top

**Detail Panel — Create/Edit Form (3 sections):**

**General:**
- Name (text input)
- Description (textarea, optional)
- Initial Capital (currency input)

**Risk & Sizing:**
- Max % per trade — slider + numeric input
- Max open positions — number input
- Max drawdown % — slider + numeric input (kill-switch: stops accepting trades when hit, warning banner on dashboard)

**Strategy Assignments:**
- All available traders shown as rows
- Each row: strategy name, assign/unassign toggle, 3-way segmented control (All Trades | Long Only | Short Only)
- Unassigned strategies dimmed but visible

**Archive:** Button at bottom, confirmation dialog. Moves to collapsed "Archived" section, read-only.

### Strategies Tab

Same two-panel layout.

**Strategy List:**
- Cards: display name (or "Unnamed Strategy"), trader_id slug, portfolio count, trade count
- Unclaimed allowlisted keys: dashed-border cards with key icon + label
- "+ Generate Key" button — creates key, shows once in copy-to-clipboard modal

**Detail Panel — Claimed Trader:**

**Identity:**
- Display Name (editable)
- Description (editable)
- Trader ID slug (read-only, monospace)

**API Key Status:**
- Status badge: "Active" green
- Key created date
- Last webhook received (timestamp + relative)
- "Rotate Key" button — new key shown once, old key invalidated after 24h grace period

**Portfolio Links (read-only):**
- List of portfolios this strategy feeds, with direction filter shown
- Links to portfolio in Portfolios tab

**Detail Panel — Unclaimed Key:**
- Label, created date, status "Waiting for first webhook"
- "Revoke" button

---

## 5. Screener Page (`/screener`)

Top-level nav link. Three zones stacked vertically.

### Filter Bar (top)

Horizontal strip: time range (1H, 4H, 12H, 24H, 7D), indicator multi-select, signal filter (All, Bullish, Bearish), ticker search. Filters update grid in real-time.

### Claude's Take — Hero Section

**Top Picks Panel (2-4 trade ideas):**
- Ticker + direction badge (LONG/SHORT)
- Entry zone, price target, stop loss
- Confidence gauge (reusing AI design system)
- One-line thesis ("3 bullish indicators converging + earnings catalyst Thursday")
- Supporting indicator badges
- Ranked by conviction score (indicator volume, alignment, event relevance)

**Market Context Strip:**
- Sector heat ("Tech names clustering bullish")
- Upcoming catalysts ("FOMC Wednesday, AAPL earnings Thursday")
- Noise vs. signal ratio ("12 tickers alerting, 3 worth watching")

**Generation:** Auto-refreshes every 30 min. Claude receives: 24h alerts, clustering data, chart data for hottest tickers, current portfolio positions. Stored in `ScreenerAnalysis` for instant load.

### Card Grid (main area)

Dynamic card sizing based on alert count:

- **1-2 alerts**: Small card — ticker, latest indicator + value, signal badge, 30-day sparkline
- **3-5 alerts**: Medium card — grows taller, all active indicators stacked, taller sparkline
- **6+ alerts**: Large card — double-width in grid, full indicator list, larger sparkline, subtle pulse glow

Cards sort by alert count descending. New alerts trigger scale-up animation. Signal badges: green (bullish), red (bearish), gray (neutral). Each indicator row: name, value, timeframe, relative time.

### Expanded Chart View (on card click)

Card expands inline (pushes others down) or slide-over panel:
- Full daily candlestick chart (yfinance, last 60 days)
- Alert markers overlaid as vertical lines/dots with indicator labels
- Complete alert history table below chart
- Close button collapses back

---

## 6. Scheduled AI Summaries

**Scheduler:** APScheduler with CronTrigger, runs inside FastAPI process.

### Morning Summary (9:30 AM ET)

**Claude receives:**
- Overnight price moves for all portfolio tickers
- Pre-market screener alerts
- Previous day's open positions and P&L
- Upcoming catalysts/events

**Generates:**
- Portfolio outlook ("3 positions gapping up, 1 risk flag on TSLA")
- Screener digest ("7 new alerts overnight, NVDA and AMD clustering bullish")
- Today's focus list — top 3 tickers with reasoning
- Risk callouts ("FOMC at 2pm — consider tightening stops")

### Nightly Summary (4:15 PM ET)

**Claude receives:**
- Day's closed trades with P&L
- Portfolio performance vs. morning expectations
- Full day's screener alert history
- How morning picks performed

**Generates:**
- Performance recap ("2W 1L today, +$340 across portfolios")
- Pick scorecard ("Morning AAPL long hit target, TSLA stop triggered")
- Screener patterns ("Tech indicators cooling off, energy heating up")
- Tomorrow setup ("3 tickers worth watching into tomorrow")

**Where summaries appear:**
- `/ai` page — enhanced Morning Briefing component with screener + pick data
- Accessible via `GET /api/ai/summaries`
- Graceful fallback if Claude API unreachable ("Summary unavailable" + retry button)

---

## 7. Frontend Aesthetic Directive

All new UI must avoid generic AI aesthetics. Requirements:

- **Typography**: Distinctive font choices — no Inter, Roboto, Arial, or system defaults
- **Color & Theme**: Cohesive dark aesthetic with dominant colors and sharp accents via CSS variables. Draw from IDE/terminal culture
- **Motion**: Orchestrated page load with staggered reveals. CSS-first animations. High-impact moments over scattered micro-interactions
- **Backgrounds**: Atmosphere and depth — layered gradients, geometric patterns, contextual effects. No flat solid backgrounds
- **Cards**: Screener cards must feel alive — pulse, grow, and breathe based on alert activity
- **Charts**: Candlestick charts should feel native to the terminal aesthetic, not like a generic charting widget

---

## 8. Technical Dependencies

### Backend
- `yfinance` — daily OHLCV chart data
- `apscheduler` — scheduled summary generation
- `anthropic` — Claude API (already installed)

### Frontend
- Existing shadcn/ui component library (already migrated)
- Chart library for candlesticks (lightweight-charts by TradingView, or extend existing Recharts)
- Existing AI design system (gradient borders, terminal viewport, confidence gauges)

---

## 9. Implementation Order

1. **Backend models + migrations** — New tables, Portfolio column additions
2. **Settings API + page** — Portfolio CRUD, strategy management, allowlisted keys
3. **Webhook flow update** — Allowlisted key auto-registration
4. **Screener backend** — Indicator alert webhook, aggregation endpoints, yfinance chart endpoint
5. **Screener frontend** — Card grid with dynamic sizing, filter bar, chart expansion
6. **Claude screener integration** — Trade idea generation, "Claude's Take" hero section
7. **Scheduled summaries** — APScheduler setup, morning/nightly jobs, enhanced briefing component
