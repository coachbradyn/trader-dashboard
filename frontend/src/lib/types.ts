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
  execution_mode?: string;  // "local" | "paper" | "live"
  max_order_amount?: number;
  has_alpaca_credentials?: boolean;
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
  portfolio_id: string;
  portfolio_name: string;
  total_trades: number;
  wins: number;
  losses: number;
  win_rate: number;
  profit_factor: number;
  avg_win: number;
  avg_loss: number;
  total_pnl: number;
  total_return_pct: number;
  max_drawdown_pct: number;
  sharpe_ratio: number;
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
  time: string;
  equity: number;
  drawdown_pct: number;
}

export interface DailyStats {
  date: string;
  daily_pnl: number;
  daily_pnl_pct: number;
  trades_closed: number;
  wins: number;
  losses: number;
  ending_equity: number;
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
  generated_at?: string;
  cached?: boolean;
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
  execution_mode?: string;  // "local" | "paper" | "live"
  max_order_amount?: number | null;
  has_alpaca_credentials?: boolean;
  alpaca_key_preview?: string | null;
  created_at: string;
  strategies: Array<{
    trader_id: string;
    trader_slug: string;
    display_name: string;
    direction_filter: string | null;
  }>;
}

export interface AlpacaConnectionTest {
  status: string;
  account_id?: string;
  buying_power?: number;
  equity?: number;
  cash?: number;
  portfolio_value?: number;
  paper?: boolean;
  message?: string;
}

export interface OrderResult {
  status: string;
  order_id?: string;
  symbol?: string;
  qty?: string;
  side?: string;
  filled_price?: number;
  filled_qty?: number;
  paper?: boolean;
  message?: string;
  holding_updated?: boolean;
  mode?: string;
  fill?: {
    status?: string;
    filled_price?: number;
    filled_qty?: number;
    filled_at?: string;
  };
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
  position_type?: string;  // "momentum" | "accumulation" | "catalyst" | "conviction"
  thesis?: string | null;
  catalyst_date?: string | null;
  catalyst_description?: string | null;
  max_allocation_pct?: number | null;
  dca_enabled?: boolean;
  dca_threshold_pct?: number | null;
  avg_cost?: number | null;
  total_shares?: number | null;
  created_at: string;
}

export interface PortfolioAction {
  id: string;
  portfolio_id: string;
  ticker: string;
  direction: string;
  action_type: "BUY" | "SELL" | "TRIM" | "ADD" | "CLOSE" | "REBALANCE" | "DCA";
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


// ── Monte Carlo Types ──────────────────────────────────────────────

export interface MonteCarloRequest {
  source: "live" | "backtest" | "combined";
  strategy?: string;
  ticker?: string;
  num_simulations: number;
  forward_trades: number;
  initial_capital: number;
  position_size_pct: number;
}

export interface HistogramBin {
  bin_start: number;
  bin_end: number;
  label: string;
  count: number;
}

export interface MonteCarloSummary {
  median_final_equity: number;
  mean_final_equity: number;
  best_case_p95: number;
  worst_case_p5: number;
  probability_of_profit: number;
  probability_of_ruin: number;
  median_max_drawdown_pct: number;
  worst_drawdown_p95: number;
  sharpe_estimate: number;
  median_return_pct: number;
  mean_return_pct: number;
}

export interface MonteCarloInputStats {
  total_trades_pooled: number;
  live_trade_count: number;
  backtest_trade_count: number;
  mean_pnl_pct: number;
  median_pnl_pct: number;
  std_pnl_pct: number;
  win_rate: number;
  avg_win_pct: number;
  avg_loss_pct: number;
  profit_factor: number;
  best_trade_pct: number;
  worst_trade_pct: number;
  strategies_included: string[];
  tickers_included: string[];
}

export interface BuyHoldMCSummary {
  median_final_equity: number;
  mean_final_equity: number;
  best_case_p95: number;
  worst_case_p5: number;
  probability_of_profit: number;
  median_max_drawdown_pct: number;
  median_return_pct: number;
  mean_return_pct: number;
}

export interface BuyHoldMCResult {
  percentile_bands: Record<string, number[]>;
  sample_paths: number[][];
  summary: BuyHoldMCSummary;
  input_stats: {
    trading_days_used: number;
    mean_daily_return_pct: number;
    std_daily_return_pct: number;
    annualized_return_pct: number;
    annualized_volatility_pct: number;
  };
}

export interface MonteCarloResponse {
  percentile_bands: Record<string, number[]>;
  sample_paths: number[][];
  trade_indices: number[];
  summary: MonteCarloSummary;
  equity_histogram: HistogramBin[];
  drawdown_histogram: HistogramBin[];
  input_stats: MonteCarloInputStats;
  buyhold?: BuyHoldMCResult;
}

// ── Watchlist Types ──────────────────────────────────────────────

export interface WatchlistSignal {
  indicator: string;
  value: number;
  signal: string;
  timeframe: string | null;
  created_at: string;
}

export interface WatchlistStrategyPosition {
  strategy_name: string;
  strategy_id: string;
  direction: string;
  entry_price: number;
  current_price: number | null;
  pnl_pct: number | null;
}

export interface WatchlistConsensus {
  direction: "bullish" | "bearish" | "mixed" | "no_data";
  bullish_count: number;
  bearish_count: number;
  total_signals: number;
}

export interface WatchlistCachedSummary {
  summary: string;
  generated_at: string;
  is_stale: boolean;
}

export interface WatchlistSignalEvent {
  date: string;
  signal: string;
}

export interface WatchlistTradeEvent {
  date: string;
  direction: string;
  status: string;
}

export interface WatchlistTickerData {
  id: string;
  ticker: string;
  notes: string | null;
  created_at: string;
  latest_signals: WatchlistSignal[];
  strategy_positions: WatchlistStrategyPosition[];
  consensus: WatchlistConsensus;
  cached_summary: WatchlistCachedSummary | null;
  last_alert_at: string | null;
  signal_events: WatchlistSignalEvent[];
  trade_events: WatchlistTradeEvent[];
}

export interface WatchlistTickerDetail {
  ticker: string;
  all_signals: Array<{
    id: string;
    indicator: string;
    value: number;
    signal: string;
    timeframe: string | null;
    created_at: string;
  }>;
  latest_signals: WatchlistSignal[];
  strategy_positions: WatchlistStrategyPosition[];
  trade_history: Array<{
    strategy_name: string;
    strategy_id: string;
    direction: string;
    entry_price: number;
    exit_price: number | null;
    pnl_pct: number;
    exit_reason: string | null;
    entry_time: string | null;
    exit_time: string | null;
  }>;
  consensus: WatchlistConsensus;
  cached_summary: WatchlistCachedSummary | null;
}

export interface StrategyInfo {
  id: string;
  trader_id: string;
  display_name: string;
  strategy_name: string | null;
  description: string | null;
}

// ── AI Portfolio Types ──────────────────────────────────────────────

export interface AIPortfolioStatus {
  exists: boolean;
  id?: string;
  name?: string;
  equity?: number;
  cash?: number;
  initial_capital?: number;
  return_pct?: number;
  open_positions?: number;
  total_trades?: number;
  created_at?: string;
}

export interface AIPortfolioMetrics {
  name: string;
  equity: number;
  total_return_pct: number;
  win_rate: number;
  profit_factor: number;
  max_drawdown_pct: number;
  total_trades: number;
}

export interface AIPortfolioComparison {
  ai_portfolio: AIPortfolioMetrics;
  real_portfolios: Array<{
    id: string;
    name: string;
    total_return_pct: number;
    win_rate: number;
    profit_factor: number;
    max_drawdown_pct: number;
    total_trades: number;
    total_pnl: number;
  }>;
  decision_stats: {
    total_signals: number;
    acted_on: number;
    acted_on_pct: number;
    skipped: number;
    avg_confidence_taken: number;
    avg_confidence_skipped: number;
  };
}

export interface AIPortfolioDecision {
  id: string;
  ticker: string;
  direction: string;
  action_type: string;
  confidence: number;
  reasoning: string;
  status: string;
  outcome: { pnl_pct: number; pnl_dollars: number; correct: boolean } | null;
  created_at: string;
}

export interface AIPortfolioHolding {
  trade_id: string;
  ticker: string;
  direction: string;
  strategy: string;
  strategy_id: string;
  entry_price: number;
  current_price: number;
  qty: number;
  pnl_pct: number;
  pnl_dollars: number;
  hold_hours: number;
  entry_time: string;
  reasoning: string | null;
  confidence: number | null;
}

// ── Brokerage CSV Import Types ─────────────────────────────────

export interface ImportedTrade {
  date: string;
  ticker: string;
  action: "buy" | "sell";
  qty: number;
  price: number;
  amount: number;
}

export interface ImportPreview {
  status: "ready" | "needs_mapping";
  brokerage?: string;
  trades?: ImportedTrade[];
  summary?: {
    total_trades: number;
    buys: number;
    sells: number;
    tickers: string[];
    date_range: string;
  };
  headers?: string[];
  sample_rows?: Record<string, string>[];
}

export interface ImportResult {
  imported: number;
  holdings_created: number;
  holdings_updated: number;
  holdings_closed: number;
}

// AI Review
export interface ReviewResponse {
  review: string;
  trades_analyzed: number;
}

// News
export interface NewsArticle {
  id: string;
  headline: string;
  summary: string | null;
  source: string;
  tickers: string[];
  published_at: string;
  url: string | null;
  sentiment_score: number | null;
}

export interface CompanyInfo {
  name: string;
  sector: string | null;
  industry: string | null;
  market_cap: number | null;
  description: string | null;
  high_52w: number | null;
  low_52w: number | null;
}

export interface NewsSentiment {
  score: number;
  label: string;
  article_count: number;
}

export interface TickerNewsResponse {
  ticker: string;
  company: CompanyInfo | null;
  sentiment: NewsSentiment;
  headlines: NewsArticle[];
}

// ── Henry Context / Stats Types ─────────────────────────────────

export interface HenryContextEntry {
  id: string;
  ticker: string | null;
  strategy: string | null;
  context_type: string;
  content: string;
  confidence: number | null;
  created_at: string;
  expires_at: string | null;
}

export interface HenryStatsEntry {
  id: string;
  stat_type: string;
  ticker: string | null;
  strategy: string | null;
  data: Record<string, unknown>;
  period_days: number;
  computed_at: string;
}

// ── Scanner Types ──────────────────────────────────────────────

export interface ScannerOpportunity {
  id: string;
  ticker: string;
  direction: string;
  action_type: string;
  confidence: number;
  reasoning: string;
  suggested_price: number | null;
  current_price: number | null;
  trigger_type: string;
  status: string;
  expires_at: string | null;
  created_at: string;
  thesis?: string;
  entry_level?: number;
  stop_level?: number;
  target_level?: number;
  position_archetype?: string;
}

export interface ScanProfile {
  id: string;
  name: string;
  description: string;
  enabled: boolean;
  market_conditions: {
    vix_min?: number;
    vix_max?: number;
    trend?: string;  // "bullish" | "bearish" | "any"
    time_slots?: string[];  // "morning" | "midday" | "afternoon"
  };
  criteria: Record<string, unknown>;
}

export interface ScannerStats {
  total_opportunities: number;
  approved: number;
  profitable: number;
  hit_rate: number;
  avg_confidence: number;
}

export interface FmpUsage {
  calls_today: number;
  limit: number;
  remaining: number;
  throttled: boolean;
}

export interface TickerFundamentals {
  ticker: string;
  company_name: string | null;
  sector: string | null;
  industry: string | null;
  market_cap: number | null;
  description: string | null;
  company_description: string | null;
  earnings_date: string | null;
  earnings_time: string | null;
  analyst_target_low: number | null;
  analyst_target_high: number | null;
  analyst_target_consensus: number | null;
  analyst_rating: string | null;
  analyst_count: number | null;
  eps_estimate_current: number | null;
  eps_actual_last: number | null;
  eps_surprise_last: number | null;
  pe_ratio: number | null;
  forward_pe: number | null;
  beta: number | null;
  profit_margin: number | null;
  roe: number | null;
  debt_to_equity: number | null;
  dcf_value: number | null;
  dcf_diff_pct: number | null;
  dividend_yield: number | null;
  short_interest_pct: number | null;
  insider_net_90d: number | null;
  institutional_ownership_pct: number | null;
  updated_at: string;
}
