from datetime import datetime
from pydantic import BaseModel


class PortfolioResponse(BaseModel):
    id: str
    name: str
    description: str | None = None
    initial_capital: float
    cash: float
    equity: float = 0.0
    unrealized_pnl: float = 0.0
    total_return_pct: float = 0.0
    open_positions: int = 0
    is_active: bool
    created_at: datetime

    class Config:
        from_attributes = True


class PositionResponse(BaseModel):
    trade_id: str
    ticker: str
    direction: str
    entry_price: float
    qty: float
    stop_price: float | None = None
    entry_time: datetime
    current_price: float | None = None
    unrealized_pnl: float | None = None
    unrealized_pnl_pct: float | None = None


class PerformanceResponse(BaseModel):
    portfolio_id: str
    portfolio_name: str
    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    avg_win: float = 0.0
    avg_loss: float = 0.0
    total_pnl: float = 0.0
    total_return_pct: float = 0.0
    max_drawdown_pct: float = 0.0
    sharpe_ratio: float = 0.0
    current_streak: int = 0  # positive = win streak, negative = loss streak


class EquityPoint(BaseModel):
    time: datetime
    equity: float
    drawdown_pct: float = 0.0


class DailyStatsResponse(BaseModel):
    date: str
    daily_pnl: float
    daily_pnl_pct: float
    trades_closed: int
    wins: int
    losses: int
    ending_equity: float
