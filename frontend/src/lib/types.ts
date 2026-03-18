export interface Portfolio {
  id: string;
  name: string;
  description: string;
  initial_capital: number;
  cash: number;
  equity: number;
  unrealized_pnl: number;
  total_return_pct: number;
  open_positions: number;
  is_active: boolean;
  created_at: string;
}

export interface Position {
  trade_id: string;
  ticker: string;
  direction: "long" | "short";
  entry_price: number;
  qty: number;
  stop_price: number | null;
  entry_time: string;
  current_price: number | null;
  unrealized_pnl: number | null;
  unrealized_pnl_pct: number | null;
}

export interface Performance {
  total_return_pct: number;
  win_rate: number;
  profit_factor: number;
  sharpe_ratio: number;
  max_drawdown_pct: number;
  total_trades: number;
  winning_trades: number;
  losing_trades: number;
  avg_win_pct: number;
  avg_loss_pct: number;
  total_pnl: number;
  best_trade_pct: number;
  worst_trade_pct: number;
  current_streak: number;
}

export interface Trade {
  id: string;
  trader_id: string;
  trader_name: string;
  ticker: string;
  direction: "long" | "short";
  entry_price: number;
  qty: number;
  entry_signal_strength: number | null;
  entry_adx: number | null;
  stop_price: number | null;
  timeframe: string | null;
  entry_time: string;
  exit_price: number | null;
  exit_reason: string | null;
  exit_time: string | null;
  bars_in_trade: number | null;
  pnl_dollars: number | null;
  pnl_percent: number | null;
  status: "open" | "closed";
}

export interface Trader {
  id: string;
  trader_id: string;
  display_name: string;
  strategy_name: string;
  description: string;
  is_active: boolean;
  created_at: string;
  portfolios: string[];
}

export interface EquityPoint {
  timestamp: string;
  equity: number;
  drawdown_pct: number;
}

export interface DailyStats {
  date: string;
  starting_equity: number;
  ending_equity: number;
  daily_pnl: number;
  daily_pnl_pct: number;
  trades_opened: number;
  trades_closed: number;
  wins: number;
  losses: number;
  max_drawdown_pct: number;
}

export interface LeaderboardEntry {
  rank: number;
  portfolio_id: string;
  portfolio_name: string;
  description: string;
  total_return_pct: number;
  win_rate: number;
  profit_factor: number;
  sharpe_ratio: number;
  total_trades: number;
  max_drawdown_pct: number;
  total_pnl: number;
  current_streak: number;
}

export interface PriceCache {
  [ticker: string]: {
    price: number;
    timestamp: string;
  };
}

// ── AI Types ──────────────────────────────────────────────────

export interface BriefingResponse {
  briefing: string;
  open_positions: number;
}

export interface ReviewResponse {
  review: string;
  trades_analyzed: number;
}

export interface QueryResponse {
  answer: string;
  trades_in_context: number;
}

export interface ConflictResolution {
  id: string;
  ticker: string;
  strategies: string[];
  recommendation: "LONG" | "SHORT" | "STAY_FLAT";
  confidence: number;
  reasoning: string;
  signals: Array<{
    trader: string;
    dir: string;
    ticker: string;
    price: number;
    sig: number;
    adx: number;
    atr: number;
  }>;
  created_at: string;
}

export interface QueryHistoryItem {
  id: string;
  question: string;
  answer: string;
  timestamp: Date;
}

// ── Settings Types ──────────────────────────────────────────
export interface PortfolioSettings {
  id: string;
  name: string;
  description: string | null;
  initial_capital: number;
  cash: number;
  status: "active" | "archived";
  max_pct_per_trade: number | null;
  max_open_positions: number | null;
  max_drawdown_pct: number | null;
  created_at: string;
  strategies: Array<{
    trader_id: string;
    trader_slug: string;
    display_name: string;
    direction_filter: string | null;
  }>;
}

export interface TraderSettings {
  id: string;
  trader_id: string;
  display_name: string;
  strategy_name: string | null;
  description: string | null;
  is_active: boolean;
  created_at: string;
  last_webhook_at: string | null;
  portfolio_count: number;
  trade_count: number;
  portfolios: Array<{
    portfolio_id: string;
    portfolio_name: string;
    direction_filter: string | null;
  }>;
}

export interface AllowlistedKey {
  id: string;
  label: string | null;
  claimed_by_id: string | null;
  created_at: string;
}

// ── Screener Types ──────────────────────────────────────────
export interface IndicatorAlert {
  id: string;
  ticker: string;
  indicator: string;
  value: number;
  signal: "bullish" | "bearish" | "neutral";
  timeframe: string | null;
  created_at: string;
}

export interface TickerAggregation {
  ticker: string;
  alert_count: number;
  latest_signal: string;
  indicators: string[];
  latest_alert_at: string;
  alerts: Array<{
    id: string;
    indicator: string;
    value: number;
    signal: string;
    timeframe: string | null;
    created_at: string;
  }>;
}

export interface ChartDataPoint {
  date: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

export interface ScreenerAnalysis {
  id: string;
  picks: Array<{
    ticker: string;
    direction: "LONG" | "SHORT";
    entry_zone: string;
    price_target: string;
    stop_loss: string;
    confidence: number;
    thesis: string;
    indicators: string[];
  }> | null;
  market_context: {
    sector_heat: string;
    catalysts: string;
    noise_ratio: string;
  } | null;
  alerts_analyzed: number;
  generated_at: string;
}

export interface MarketSummary {
  id: string;
  summary_type: "morning" | "nightly" | "alert_digest";
  scope: string;
  content: string;
  tickers_analyzed: string[] | null;
  generated_at: string;
}

// ── Portfolio Manager Types ─────────────────────────────────

export interface PortfolioHolding {
  id: string;
  portfolio_id: string;
  trade_id: string | null;
  ticker: string;
  direction: "long" | "short";
  entry_price: number;
  qty: number;
  entry_date: string;
  strategy_name: string | null;
  notes: string | null;
  is_active: boolean;
  source: string;
  current_price: number | null;
  unrealized_pnl: number | null;
  unrealized_pnl_pct: number | null;
  created_at: string;
}

export interface PortfolioAction {
  id: string;
  portfolio_id: string;
  ticker: string;
  direction: string;
  action_type: "BUY" | "SELL" | "TRIM" | "ADD" | "CLOSE" | "REBALANCE";
  suggested_qty: number | null;
  suggested_price: number | null;
  current_price: number | null;
  confidence: number;
  reasoning: string;
  trigger_type: "SIGNAL" | "THRESHOLD" | "SCHEDULED_REVIEW";
  trigger_ref: string | null;
  priority_score: number;
  status: "pending" | "approved" | "rejected" | "expired";
  expires_at: string | null;
  resolved_at: string | null;
  reject_reason: string | null;
  outcome_pnl: number | null;
  outcome_correct: boolean | null;
  created_at: string;
}

export interface ActionStats {
  pending_count: number;
  approved_today: number;
  rejected_today: number;
  total_approved: number;
  hit_rate: number | null;
  hit_rate_high_confidence: number | null;
}

export interface BacktestImportData {
  id: string;
  strategy_name: string;
  strategy_version: string | null;
  exchange: string | null;
  ticker: string;
  filename: string;
  trade_count: number;
  win_rate: number | null;
  profit_factor: number | null;
  avg_gain_pct: number | null;
  avg_loss_pct: number | null;
  max_drawdown_pct: number | null;
  max_adverse_excursion_pct: number | null;
  avg_hold_days: number | null;
  total_pnl_pct: number | null;
  imported_at: string;
}

export interface BacktestTradeData {
  id: string;
  trade_number: number;
  type: string;
  direction: string;
  signal: string | null;
  price: number;
  qty: number | null;
  position_value: number | null;
  net_pnl: number | null;
  net_pnl_pct: number | null;
  favorable_excursion: number | null;
  favorable_excursion_pct: number | null;
  adverse_excursion: number | null;
  adverse_excursion_pct: number | null;
  cumulative_pnl: number | null;
  cumulative_pnl_pct: number | null;
  trade_date: string;
}

// ── Per-Ticker Analysis Types ────────────────────────────────

export interface HistoricalMatch {
  pattern: string;
  occurrences: number;
  avg_return_pct: number;
  win_rate: number;
  avg_bars_held: number;
  sample_dates: string[];
}

export interface StrategyAlignment {
  strategy_name: string;
  strategy_id: string;
  has_active_position: boolean;
  position_direction: string | null;
  latest_signal: string | null;
  signal_agrees: boolean;
  notes: string;
}

export interface TickerAnalysis {
  ticker: string;
  play_type: "DAILY" | "WEEKLY";
  direction: "LONG" | "SHORT";
  confidence: number;
  thesis: string;
  entry_zone: string;
  price_target: string;
  stop_loss: string;
  risk_reward: string;

  indicators_firing: string[];
  signal_breakdown: { bullish: number; bearish: number; neutral: number };
  dominant_signal: "bullish" | "bearish";

  historical_matches: HistoricalMatch[];
  strategy_alignment: StrategyAlignment[];

  alert_timeline_summary: string;
  timeframes_represented: string[];

  generated_at: string;
}
