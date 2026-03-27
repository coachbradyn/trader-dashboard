from datetime import datetime
from pydantic import BaseModel, field_validator


class ScreenerWebhookPayload(BaseModel):
    model_config = {"extra": "ignore"}  # Ignore extra fields from Pine Script

    key: str
    ticker: str
    indicator: str = "UNKNOWN"
    value: float | None = None  # Optional — not every indicator has a numeric value
    signal: str = "neutral"  # "bullish" / "bearish" / "neutral"
    tf: str | None = None
    time: int | None = None  # unix timestamp ms
    metadata: dict | None = None

    # --- TradingView sends "NASDAQ:NVDA", we just want "NVDA" ---
    @field_validator("ticker", mode="before")
    @classmethod
    def strip_exchange_prefix(cls, v):
        if isinstance(v, str) and ":" in v:
            return v.split(":")[-1].strip().upper()
        return v.strip().upper() if isinstance(v, str) else v

    # --- value might come as a string, or {{close}} might resolve to "" ---
    @field_validator("value", mode="before")
    @classmethod
    def coerce_value(cls, v):
        if v is None or v == "" or v == "NaN":
            return None
        if isinstance(v, str):
            try:
                return float(v.replace(",", ""))
            except (ValueError, TypeError):
                return None
        return v

    # --- time might be a string from TradingView ---
    @field_validator("time", mode="before")
    @classmethod
    def coerce_time(cls, v):
        if v is None or v == "":
            return None
        if isinstance(v, str):
            try:
                return int(float(v))
            except (ValueError, TypeError):
                return None
        return v

    # --- signal: normalize common variations ---
    @field_validator("signal", mode="before")
    @classmethod
    def normalize_signal(cls, v):
        if not isinstance(v, str):
            return "neutral"
        v = v.strip().lower()
        # Map common variations
        bull_words = {"bullish", "bull", "long", "buy", "up", "1", "true"}
        bear_words = {"bearish", "bear", "short", "sell", "down", "-1", "false"}
        if v in bull_words:
            return "bullish"
        if v in bear_words:
            return "bearish"
        return v or "neutral"

    # --- tf: normalize timeframe strings ---
    @field_validator("tf", mode="before")
    @classmethod
    def normalize_timeframe(cls, v):
        if v is None or v == "":
            return None
        v = str(v).strip()
        # TradingView sends "240" for 4h, "60" for 1h, etc.
        tf_map = {
            "1": "1m", "3": "3m", "5": "5m", "15": "15m", "30": "30m",
            "45": "45m", "60": "1h", "120": "2h", "180": "3h", "240": "4h",
            "D": "1D", "1D": "1D", "W": "1W", "1W": "1W", "M": "1M", "1M": "1M",
        }
        return tf_map.get(v, v)

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
