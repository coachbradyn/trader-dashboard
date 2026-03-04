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
