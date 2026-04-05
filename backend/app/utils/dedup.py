"""In-memory idempotency cache using LRU eviction + TTL expiry.

Prevents duplicate webhook processing without external dependencies.
Fingerprint = SHA-256 of key fields; TTL = 120s; max 2000 entries.
"""

import hashlib
import time
from collections import OrderedDict

_CACHE_TTL = 120  # seconds
_CACHE_MAX = 2000

# OrderedDict used as an LRU: most-recently-checked entries move to the end.
# Values are insertion timestamps for TTL enforcement.
_cache: OrderedDict[str, float] = OrderedDict()


def _evict_expired() -> None:
    """Remove entries older than TTL from the front of the cache."""
    now = time.monotonic()
    while _cache:
        key, ts = next(iter(_cache.items()))
        if now - ts > _CACHE_TTL:
            _cache.popitem(last=False)
        else:
            break


def make_webhook_fingerprint(
    trader: str,
    ticker: str,
    signal: str,
    direction: str,
    price: float,
    unix_time: float,
) -> str:
    """SHA-256 fingerprint for a trade webhook (time rounded to the second)."""
    rounded_time = int(unix_time)
    raw = f"{trader}|{ticker}|{signal}|{direction}|{price}|{rounded_time}"
    return hashlib.sha256(raw.encode()).hexdigest()


def make_screener_fingerprint(
    trader: str,
    ticker: str,
    indicator: str,
    signal: str,
    timeframe: str,
    unix_time: float,
) -> str:
    """SHA-256 fingerprint for a screener webhook."""
    rounded_time = int(unix_time)
    raw = f"{trader}|{ticker}|{indicator}|{signal}|{timeframe}|{rounded_time}"
    return hashlib.sha256(raw.encode()).hexdigest()


def is_duplicate_webhook(fingerprint: str) -> bool:
    """Return True if this fingerprint was already seen within the TTL window.

    Also performs lazy eviction of expired entries and LRU eviction when the
    cache exceeds its maximum size.
    """
    _evict_expired()

    if fingerprint in _cache:
        # Move to end (most recently seen)
        _cache.move_to_end(fingerprint)
        return True

    # New entry — insert and enforce max size via LRU eviction
    _cache[fingerprint] = time.monotonic()
    while len(_cache) > _CACHE_MAX:
        _cache.popitem(last=False)

    return False
