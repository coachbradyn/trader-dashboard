from datetime import datetime
from pydantic import BaseModel

class PortfolioCreate(BaseModel):
    name: str
    description: str | None = None
    initial_capital: float = 10000.0
    max_pct_per_trade: float | None = None
    max_open_positions: int | None = None
    max_drawdown_pct: float | None = None

class PortfolioUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    max_pct_per_trade: float | None = None
    max_open_positions: int | None = None
    max_drawdown_pct: float | None = None
    execution_mode: str | None = None
    alpaca_api_key: str | None = None
    alpaca_secret_key: str | None = None
    max_order_amount: float | None = None
    ai_evaluation_enabled: bool | None = None

class StrategyAssignment(BaseModel):
    trader_id: str  # Trader UUID
    direction_filter: str | None = None  # null / "long" / "short"

class PortfolioFullUpdate(BaseModel):
    portfolio: PortfolioUpdate | None = None
    strategies: list[StrategyAssignment] | None = None

class TraderUpdate(BaseModel):
    display_name: str | None = None
    strategy_name: str | None = None
    description: str | None = None

class AllowlistedKeyCreate(BaseModel):
    label: str | None = None

class AllowlistedKeyResponse(BaseModel):
    id: str
    label: str | None
    claimed_by_id: str | None
    created_at: datetime

    class Config:
        from_attributes = True

class PortfolioSettingsResponse(BaseModel):
    id: str
    name: str
    description: str | None = None
    initial_capital: float
    cash: float
    status: str
    max_pct_per_trade: float | None = None
    max_open_positions: int | None = None
    max_drawdown_pct: float | None = None
    execution_mode: str = "local"
    max_order_amount: float | None = None
    has_alpaca_credentials: bool = False
    alpaca_key_preview: str | None = None
    ai_evaluation_enabled: bool = False
    created_at: datetime
    strategies: list[dict] = []  # [{trader_id, trader_slug, display_name, direction_filter}]

class TraderSettingsResponse(BaseModel):
    id: str
    trader_id: str
    display_name: str
    strategy_name: str | None = None
    description: str | None = None
    is_active: bool
    created_at: datetime
    last_webhook_at: datetime | None = None
    portfolio_count: int = 0
    trade_count: int = 0
    portfolios: list[dict] = []  # [{portfolio_id, portfolio_name, direction_filter}]
