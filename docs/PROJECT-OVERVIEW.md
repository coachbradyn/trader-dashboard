# Henry AI Trader — Project Overview

**Full-stack AI-powered trading dashboard** that connects to TradingView Pine Script strategies, tracks live trades across multiple portfolios, and uses Claude AI ("Henry") as a semi-autonomous portfolio manager.

**Stack:** Next.js 14 + FastAPI + PostgreSQL + Claude AI (Sonnet)
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
Henry's intelligence hub with four sections:
- **Morning Briefing** — daily market analysis, strategy performance summary, open position review, and actionable recommendations
- **Trade Review** — Claude analyzes recent closed trades, identifies patterns, evaluates strategy performance
- **Ask Henry** — natural language query interface. Ask anything about your trades, strategies, or market conditions
- **Conflict Log** — when strategies disagree (e.g., S1 says long NVDA, S4 says short), Henry resolves the conflict with a recommendation and confidence score

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

Henry is Claude Sonnet integrated as a trading analyst and portfolio manager. He has access to:
- All trade history (entries, exits, P&L, signal metadata)
- All open positions with real-time P&L
- Backtest performance data per strategy per ticker
- Market context (SPY, VIX)

### AI Capabilities

| Feature | Trigger | Claude Call? |
|---------|---------|-------------|
| Morning Briefing | Daily / on-demand | Yes — full portfolio analysis |
| Trade Review | On-demand | Yes — analyzes recent closed trades |
| Ask Henry | User query | Yes — natural language Q&A |
| Conflict Resolution | Auto on opposing signals | Yes — recommends LONG/SHORT/STAY_FLAT |
| Signal Evaluation | Every webhook | Yes — evaluates signal vs portfolio + backtest |
| Threshold Monitoring | Hourly during market | No — pure Python math |
| Scheduled Review | Once daily | Yes — deep portfolio analysis |
| Screener Analysis | Per-ticker on demand | Yes — trade ideas with targets |

### Action Outcome Tracking
When an approved action's resulting position closes, the system tracks whether Henry's recommendation was correct. Over time this builds a meta-performance record:
- Hit rate by confidence level
- Hit rate by action type (BUY vs TRIM vs CLOSE)
- Hit rate by trigger type (signal vs threshold vs scheduled)

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
- **AI:** Anthropic Claude API (Sonnet 4.5 primary, 3.5 Sonnet fallback)
- **Market Data:** yfinance (chart data), Alpaca API (credentials configured)
- **Background:** asyncio tasks for price polling (15s market hours, 60s closed), APScheduler for daily summaries
- **Auth:** API key hashing (SHA-256) for webhook authentication

### Deployment
- **Frontend:** Vercel (henrytrader.xyz)
- **Backend:** Railway (auto-deploy from GitHub push)
- **Database:** Railway PostgreSQL

### API Endpoints (47 total)

| Group | Prefix | Endpoints | Purpose |
|-------|--------|-----------|---------|
| Webhooks | `/api/webhook` | 1 | Strategy trade ingestion |
| Trades | `/api/trades` | 1 | Trade history queries |
| Traders | `/api/traders` | 2 | Strategy management |
| Portfolios | `/api/portfolios` | 6 | Portfolio data + metrics |
| Leaderboard | `/api/leaderboard` | 1 | Strategy rankings |
| Settings | `/api/settings` | 8 | Portfolio/trader/key CRUD |
| Screener | `/api/screener` | 6 | Indicator alerts + analysis |
| Portfolio Manager | `/api/portfolio-manager` | 12 | Holdings, actions, backtests |
| AI | `/api/ai` | 7 | Briefing, review, query, conflicts |
| System | `/api/health`, etc. | 3 | Health, prices, debug |

### Database (18 models)

| Category | Tables |
|----------|--------|
| Core Trading | portfolios, traders, trades, portfolio_trades |
| Portfolio Mgmt | portfolio_strategies, portfolio_holdings, portfolio_actions, portfolio_snapshots |
| Performance | daily_stats |
| Screener | indicator_alerts, screener_analyses |
| Backtest | backtest_imports, backtest_trades |
| AI | conflict_resolutions, market_summaries |
| Security | allowlisted_keys |

---

## Key Design Decisions

1. **Semi-autonomous, not autonomous** — Henry recommends, user approves. Nothing executes without explicit approval.
2. **Two-tier monitoring** — hourly Python threshold checks (no AI cost) + daily deep Claude review (comprehensive but expensive). Keeps API costs minimal.
3. **Source of truth linking** — manual holdings link to webhook trades via `trade_id` so Henry knows "you entered this via the dashboard" vs "the strategy entered this automatically."
4. **Extra webhook fields ignored** — Pine Script can send `win_pct`, `total_trades`, `profit_factor`, etc. alongside required fields. Backend uses `extra="ignore"` so new Pine Script versions never break the webhook.
5. **Performance calc excludes deployed capital** — adding a manual holding doesn't inflate return %. Only actual price movement since entry counts as performance.
6. **Single-file page components** — established codebase pattern. Each page is self-contained for simplicity.

---

## Environment Variables

### Backend (Railway)

| Variable | Required | Description |
|----------|----------|-------------|
| `DATABASE_URL` | Yes | PostgreSQL connection string |
| `ANTHROPIC_API_KEY` | Yes | Claude API key (`sk-ant-...`) |
| `ADMIN_SECRET` | Yes | Protects seed endpoint |
| `ALLOWED_ORIGINS` | Yes | Comma-separated CORS origins (e.g., `https://henrytrader.xyz,http://localhost:3000`) |
| `ALPACA_API_KEY` | No | Alpaca market data API key |
| `ALPACA_SECRET_KEY` | No | Alpaca secret key |

### Frontend (Vercel / `.env.local`)

| Variable | Required | Description |
|----------|----------|-------------|
| `NEXT_PUBLIC_API_URL` | Yes | Backend API URL (e.g., `https://trader-dashboard-production-02bd.up.railway.app/api`) |
