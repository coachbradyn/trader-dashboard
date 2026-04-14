"""
API Key Verification Cache
==========================

Module-level cache that maps SHA-256(raw_key) → trader.id so subsequent
requests with the same key skip the bcrypt scan entirely.

Primary motivation: the trader `api_key_hash` column stores bcrypt
hashes, so the SHA-256 "fast path" in the webhook handlers never hits.
Every request falls through to a bcrypt loop that blocks the event
loop for ~200ms per comparison. With 30 concurrent alerts, this
serializes the async loop and TradingView times out.

The fix has two independent levers; both are applied here:

1. **Cache**: after the first successful bcrypt auth for a given key,
   remember `sha256_of_raw_key → trader_id`. Later requests hit a
   single indexed SELECT by primary key (sub-5ms) and skip bcrypt.

2. **Thread pool**: bcrypt.checkpw is CPU-bound synchronous work. Run
   it via `asyncio.to_thread()` so it executes in the default executor
   (worker threads) instead of blocking the main event loop. The
   first request for a new key still takes ~200ms/comparison, but the
   OTHER requests can make progress while it runs.

TTL: 1 hour. After expiry, the next request re-authenticates and
repopulates the cache. If a key is revoked or rotated, the worst
case is one hour of stale authorization — acceptable for a <5 user
system per the brief.

Thread-safe: the dict is protected by a single asyncio.Lock for
writes; reads are lock-free (racy dict reads are safe because worst
case is a re-authentication, not incorrect authorization).
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from typing import Optional

import bcrypt

logger = logging.getLogger(__name__)

# Cache TTL — balances freshness against bcrypt work. At 1h, a rotated
# key takes up to an hour to stop working through this service; this is
# deemed acceptable per the design brief.
_CACHE_TTL_SECONDS = 3600.0

# {sha256(raw_key): (trader_id, expires_at_epoch)}
_CACHE: dict[str, tuple[str, float]] = {}
_CACHE_LOCK = asyncio.Lock()


def _sha256(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode()).hexdigest()


def get_cached_trader_id(raw_key: str) -> Optional[str]:
    """Return the cached trader_id for a raw key, or None on miss/expired.

    Lock-free read. Expired entries are cleaned opportunistically on read
    (cheaper than a dedicated janitor at our request volume)."""
    digest = _sha256(raw_key)
    hit = _CACHE.get(digest)
    if hit is None:
        return None
    trader_id, expires_at = hit
    if time.time() >= expires_at:
        # Expired — drop and miss. Write is racy but benign.
        _CACHE.pop(digest, None)
        return None
    return trader_id


async def remember(raw_key: str, trader_id: str) -> None:
    """Insert or refresh a cache entry. Takes the lock because
    dict-mutation under GIL is atomic for single ops, but we want
    ordered writes so the TTL is sensible under contention."""
    digest = _sha256(raw_key)
    expires_at = time.time() + _CACHE_TTL_SECONDS
    async with _CACHE_LOCK:
        _CACHE[digest] = (trader_id, expires_at)


async def forget(raw_key: str) -> None:
    """Explicit invalidation. Call from any admin endpoint that rotates
    or revokes a key to avoid up to 1h of stale authorization."""
    digest = _sha256(raw_key)
    async with _CACHE_LOCK:
        _CACHE.pop(digest, None)


async def bcrypt_check(raw_key: str, bcrypt_hash: str) -> bool:
    """Non-blocking bcrypt comparison. Runs in the default thread pool
    so the main event loop stays responsive while bcrypt churns.

    bcrypt.checkpw is CPU-bound C code; it releases the GIL, so
    running it in a worker thread genuinely parallelizes with other
    async work. A single comparison still takes ~200ms on typical
    hardware — the optimization here is that request #2-#N can make
    progress instead of all queuing behind request #1's bcrypt.
    """
    try:
        return await asyncio.to_thread(
            bcrypt.checkpw, raw_key.encode(), bcrypt_hash.encode()
        )
    except (ValueError, TypeError) as e:
        # Invalid hash format (e.g. non-bcrypt string stored by mistake).
        logger.debug(f"bcrypt_check invalid hash: {e}")
        return False


def stats() -> dict:
    """Diagnostics — for an admin/health endpoint to surface."""
    now = time.time()
    live = sum(1 for _, (_, exp) in _CACHE.items() if exp > now)
    return {
        "total_entries": len(_CACHE),
        "live_entries": live,
        "ttl_seconds": _CACHE_TTL_SECONDS,
    }
