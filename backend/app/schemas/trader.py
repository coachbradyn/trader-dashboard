from datetime import datetime
from pydantic import BaseModel


class TraderResponse(BaseModel):
    id: str
    trader_id: str
    display_name: str
    strategy_name: str | None = None
    description: str | None = None
    is_active: bool
    created_at: datetime
    portfolios: list[str] = []  # portfolio names this trader feeds into

    class Config:
        from_attributes = True
