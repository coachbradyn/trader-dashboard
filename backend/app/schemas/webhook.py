from pydantic import BaseModel, field_validator


def _coerce_float(v):
    """Coerce TradingView values to float. Returns None for empty/invalid."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v) if v == v else None  # NaN check: NaN != NaN
    if isinstance(v, str):
        v = v.strip()
        if not v or v.lower() in ("nan", "null", "na", "none", ""):
            return None
        try:
            return float(v)
        except (ValueError, TypeError):
            return None
    return None


def _coerce_int(v):
    """Coerce TradingView values to int. Returns None for empty/invalid."""
    if v is None:
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, float):
        return int(v) if v == v else None
    if isinstance(v, str):
        v = v.strip()
        if not v or v.lower() in ("nan", "null", "na", "none", ""):
            return None
        try:
            return int(float(v))
        except (ValueError, TypeError):
            return None
    return None


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

    @field_validator("price", "qty", mode="before")
    @classmethod
    def coerce_required_float(cls, v):
        result = _coerce_float(v)
        if result is None:
            result = 0.0
        return result

    @field_validator("sig", "adx", "atr", "stop", "pnl_pct", mode="before")
    @classmethod
    def coerce_optional_float(cls, v):
        return _coerce_float(v)

    @field_validator("bars_in_trade", mode="before")
    @classmethod
    def coerce_optional_int(cls, v):
        return _coerce_int(v)

    @field_validator("time", mode="before")
    @classmethod
    def coerce_time(cls, v):
        return _coerce_int(v)

    @field_validator("signal", mode="before")
    @classmethod
    def normalize_signal(cls, v):
        if isinstance(v, str):
            v = v.strip().lower()
            # Map common TradingView signal synonyms
            entry_words = {"entry", "buy", "long", "open", "enter"}
            exit_words = {"exit", "sell", "close", "short", "cover"}
            if v in entry_words:
                return "entry"
            if v in exit_words:
                return "exit"
        return v

    @field_validator("dir", mode="before")
    @classmethod
    def normalize_dir(cls, v):
        if isinstance(v, str):
            return v.strip().lower()
        return v

    @field_validator("exit_reason", "tf", "profile", mode="before")
    @classmethod
    def coerce_optional_str(cls, v):
        if v is None:
            return None
        if isinstance(v, str):
            v = v.strip()
            return v if v and v.lower() not in ("nan", "null", "none") else None
        return str(v)
