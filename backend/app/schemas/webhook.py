from pydantic import BaseModel


class WebhookPayload(BaseModel):
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
