from datetime import datetime
from pydantic import BaseModel

class ScreenerWebhookPayload(BaseModel):
    model_config = {"extra": "ignore"}  # Ignore extra fields from Pine Script

    key: str
    ticker: str
    indicator: str
    value: float
    signal: str = "neutral"  # "bullish" / "bearish" / "neutral"
    tf: str | None = None
    time: int | None = None  # unix timestamp ms
    metadata: dict | None = None

class AlertResponse(BaseModel):
    id: str
    ticker: str
    indicator: str
    value: float
    signal: str
    timeframe: str | None = None
    created_at: datetime

    class Config:
        from_attributes = True

class TickerAggregation(BaseModel):
    ticker: str
    alert_count: int
    latest_signal: str
    indicators: list[str]
    latest_alert_at: datetime

class ChartDataPoint(BaseModel):
    date: str
    open: float
    high: float
    low: float
    close: float
    volume: int

class ScreenerPickResponse(BaseModel):
    id: str
    picks: list[dict] | None
    market_context: dict | None
    alerts_analyzed: int
    generated_at: datetime


# ── Per-Ticker Analysis ──────────────────────────────────────

class TickerAnalysisRequest(BaseModel):
    hours: int = 24

class HistoricalMatch(BaseModel):
    pattern: str
    occurrences: int
    avg_return_pct: float
    win_rate: float
    avg_bars_held: int
    sample_dates: list[str] = []

class StrategyAlignment(BaseModel):
    strategy_name: str
    strategy_id: str
    has_active_position: bool
    position_direction: str | None = None
    latest_signal: str | None = None
    signal_agrees: bool
    notes: str

class TickerAnalysisResponse(BaseModel):
    ticker: str
    play_type: str
    direction: str
    confidence: int
    thesis: str
    entry_zone: str
    price_target: str
    stop_loss: str
    risk_reward: str

    indicators_firing: list[str]
    signal_breakdown: dict
    dominant_signal: str

    historical_matches: list[HistoricalMatch] = []
    strategy_alignment: list[StrategyAlignment] = []

    alert_timeline_summary: str
    timeframes_represented: list[str] = []

    generated_at: datetime
