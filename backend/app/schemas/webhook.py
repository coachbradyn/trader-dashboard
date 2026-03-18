from pydantic import BaseModel, field_validator


class WebhookPayload(BaseModel):
    model_config = {"extra": "ignore"}  # Ignore extra fields from Pine Script

    key: str
    trader: str  # trader_id slug
    signal: str  # "entry" or "exit"
    dir: str  # "long" or "short"
    ticker: str
    price: float
    qty: float = 0.0
    sig: float | None = None  # signal strength
    adx: float | None = None
    atr: float | None = None
    stop: float | None = None
    exit_reason: str | None = None
    pnl_pct: float | None = None
    bars_in_trade: int | None = None
    tf: str | None = None  # timeframe
    time: int | None = None  # unix timestamp ms
    profile: str | None = None

    @field_validator("time", mode="before")
    @classmethod
    def coerce_time(cls, v):
        if v is None:
            return None
        if isinstance(v, str):
            try:
                return int(float(v))
            except (ValueError, TypeError):
                return None
        return v
