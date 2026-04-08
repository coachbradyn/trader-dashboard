"""
UTC datetime helper + safe math utilities.
"""
from datetime import datetime, timezone


def utcnow() -> datetime:
    """Return current UTC time as a naive datetime (no tzinfo).
    Use this for database comparisons and column defaults.
    Equivalent to the deprecated datetime.utcnow() but future-proof."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def safe_pnl_pct(current_price: float, entry_price: float, direction: str = "long") -> float:
    """Calculate P&L percentage safely — returns 0.0 if entry_price is 0 or None."""
    if not entry_price or entry_price <= 0:
        return 0.0
    if direction == "long":
        return (current_price - entry_price) / entry_price * 100
    else:
        return (entry_price - current_price) / entry_price * 100
