from datetime import datetime, date
from pydantic import BaseModel


# ── Holdings ──────────────────────────────────────────────────────────

class HoldingCreate(BaseModel):
    portfolio_id: str
    ticker: str
    direction: str  # "long" / "short"
    entry_price: float
    qty: float
    entry_date: datetime
    strategy_name: str | None = None
    notes: str | None = None
    position_type: str = "momentum"  # momentum, accumulation, catalyst, conviction
    thesis: str | None = None
    catalyst_date: date | None = None
    catalyst_description: str | None = None
    max_allocation_pct: float | None = None
    dca_enabled: bool = False
    dca_threshold_pct: float | None = None


class HoldingUpdate(BaseModel):
    ticker: str | None = None
    direction: str | None = None
    entry_price: float | None = None
    qty: float | None = None
    entry_date: datetime | None = None
    strategy_name: str | None = None
    notes: str | None = None
    is_active: bool | None = None
    position_type: str | None = None
    thesis: str | None = None
    catalyst_date: date | None = None
    catalyst_description: str | None = None
    max_allocation_pct: float | None = None
    dca_enabled: bool | None = None
    dca_threshold_pct: float | None = None
    avg_cost: float | None = None
    total_shares: float | None = None


class HoldingResponse(BaseModel):
    id: str
    portfolio_id: str
    trade_id: str | None = None
    ticker: str
    direction: str
    entry_price: float
    qty: float
    entry_date: datetime
    strategy_name: str | None = None
    notes: str | None = None
    is_active: bool
    source: str  # "manual" or strategy name
    current_price: float | None = None
    unrealized_pnl: float | None = None
    unrealized_pnl_pct: float | None = None
    position_type: str = "momentum"
    thesis: str | None = None
    catalyst_date: date | None = None
    catalyst_description: str | None = None
    max_allocation_pct: float | None = None
    dca_enabled: bool = False
    dca_threshold_pct: float | None = None
    avg_cost: float | None = None
    total_shares: float | None = None
    created_at: datetime


# ── Action Queue ──────────────────────────────────────────────────────

class ActionResponse(BaseModel):
    id: str
    portfolio_id: str
    ticker: str
    direction: str
    action_type: str
    suggested_qty: float | None = None
    suggested_price: float | None = None
    current_price: float | None = None
    confidence: int
    reasoning: str
    trigger_type: str
    trigger_ref: str | None = None
    priority_score: float
    status: str
    expires_at: datetime | None = None
    resolved_at: datetime | None = None
    reject_reason: str | None = None
    outcome_pnl: float | None = None
    outcome_correct: bool | None = None
    created_at: datetime


class ActionReject(BaseModel):
    reason: str | None = None


class ActionStats(BaseModel):
    pending_count: int
    approved_today: int
    rejected_today: int
    total_approved: int
    hit_rate: float | None = None  # % of approved actions that were correct
    hit_rate_high_confidence: float | None = None  # hit rate for confidence >= 8


# ── Backtest Import ──────────────────────────────────────────────────

class BacktestImportResponse(BaseModel):
    id: str
    strategy_name: str
    strategy_version: str | None = None
    exchange: str | None = None
    ticker: str
    filename: str
    trade_count: int
    win_rate: float | None = None
    profit_factor: float | None = None
    avg_gain_pct: float | None = None
    avg_loss_pct: float | None = None
    max_drawdown_pct: float | None = None
    max_adverse_excursion_pct: float | None = None
    avg_hold_days: float | None = None
    total_pnl_pct: float | None = None
    imported_at: datetime


class BacktestTradeResponse(BaseModel):
    id: str
    trade_number: int
    type: str
    direction: str
    signal: str | None = None
    price: float
    qty: float | None = None
    position_value: float | None = None
    net_pnl: float | None = None
    net_pnl_pct: float | None = None
    favorable_excursion: float | None = None
    favorable_excursion_pct: float | None = None
    adverse_excursion: float | None = None
    adverse_excursion_pct: float | None = None
    cumulative_pnl: float | None = None
    cumulative_pnl_pct: float | None = None
    trade_date: datetime
