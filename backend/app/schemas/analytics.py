from typing import Literal
from pydantic import BaseModel, Field


class MonteCarloRequest(BaseModel):
    source: Literal["live", "backtest", "combined"] = "combined"
    strategy: str | None = None
    ticker: str | None = None
    num_simulations: int = Field(default=1000, ge=100, le=10000)
    forward_trades: int = Field(default=100, ge=10, le=500)
    initial_capital: float = Field(default=10000.0, gt=0)
    position_size_pct: float = Field(default=100.0, gt=0, le=100)


class HistogramBin(BaseModel):
    bin_start: float
    bin_end: float
    label: str
    count: int


class MonteCarloSummary(BaseModel):
    median_final_equity: float
    mean_final_equity: float
    best_case_p95: float
    worst_case_p5: float
    probability_of_profit: float
    probability_of_ruin: float
    median_max_drawdown_pct: float
    worst_drawdown_p95: float
    sharpe_estimate: float
    median_return_pct: float
    mean_return_pct: float


class InputStats(BaseModel):
    total_trades_pooled: int
    live_trade_count: int
    backtest_trade_count: int
    mean_pnl_pct: float
    median_pnl_pct: float
    std_pnl_pct: float
    win_rate: float
    avg_win_pct: float
    avg_loss_pct: float
    profit_factor: float
    best_trade_pct: float
    worst_trade_pct: float
    strategies_included: list[str]
    tickers_included: list[str]


class MonteCarloResponse(BaseModel):
    percentile_bands: dict[str, list[float]]
    sample_paths: list[list[float]]
    trade_indices: list[int]
    summary: MonteCarloSummary
    equity_histogram: list[HistogramBin]
    drawdown_histogram: list[HistogramBin]
    input_stats: InputStats
