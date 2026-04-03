"""
UTC datetime helper.
Returns timezone-naive UTC datetime for database compatibility.
All database columns store naive UTC timestamps.
"""
from datetime import datetime, timezone


def utcnow() -> datetime:
    """Return current UTC time as a naive datetime (no tzinfo).
    Use this for database comparisons and column defaults.
    Equivalent to the deprecated datetime.utcnow() but future-proof."""
    return datetime.now(timezone.utc).replace(tzinfo=None)
