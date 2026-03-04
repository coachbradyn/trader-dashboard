from datetime import datetime
from pydantic import BaseModel


class TradeResponse(BaseModel):
    id: str
    trader_id: str
    trader_name: str | None = None
    ticker: str
    direction: str
    entry_price: float
    qty: float
    entry_signal_strength: float | None = None
    entry_adx: float | None = None
    stop_price: float | None = None
    timeframe: str | None = None
    entry_time: datetime
    exit_price: float | None = None
    exit_reason: str | None = None
    exit_time: datetime | None = None
    bars_in_trade: int | None = None
    pnl_dollars: float | None = None
    pnl_percent: float | None = None
    status: str

    class Config:
        from_attributes = True


class TradeListParams(BaseModel):
    trader_id: str | None = None
    portfolio_id: str | None = None
    status: str | None = None
    limit: int = 50
    offset: int = 0
