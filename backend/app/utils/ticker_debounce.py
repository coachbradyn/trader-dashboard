"""
Per-Ticker Background Work Debounce
====================================

Collapses bursts of webhook-triggered background work on the same
ticker into a single execution, delayed until the burst subsides.

Problem it solves: when TradingView fires 30 alerts in the same
minute, each screener webhook fires three fire-and-forget background
tasks (cache invalidation, watchlist staleness check, confluence
memory). That's 90 concurrent background tasks, each holding a DB
connection + potentially an embedding API call. The event loop stays
up (the webhook already returned 200) but the DB pool thrashes and
Voyage / Gemini get hammered.

Solution: per-ticker debounce. When a webhook arrives, schedule a
single post-processing task for that ticker with a 5s delay. If
another alert for the same ticker arrives during those 5s, the timer
is reset. The callback only runs once the burst is over, per ticker.

Upshot: 15 NVDA alerts + 15 AAPL alerts → 2 post-processing runs
total, not 30.

Design notes:
- Module-level dict of {ticker: (TimerHandle, awaitable_factory)}.
- asyncio.get_running_loop().call_later() returns a TimerHandle that
  can be .cancel()'d. We schedule a wrapper that calls create_task()
  on the user's async factory when the timer fires.
- No external dependency. No persistence — crash loses pending work,
  but that's acceptable: the worst case is one cycle of missed cache
  invalidation + confluence computation that the next alert will
  re-trigger. The alert data itself is already committed.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable

logger = logging.getLogger(__name__)

DEFAULT_DEBOUNCE_SECONDS = 5.0

# {normalized_ticker: asyncio.TimerHandle}
_TIMERS: dict[str, asyncio.TimerHandle] = {}


def schedule(
    ticker: str,
    factory: Callable[[], Awaitable[None]],
    delay: float = DEFAULT_DEBOUNCE_SECONDS,
) -> None:
    """Schedule (or reset) the debounce timer for `ticker`.

    `factory` is a zero-arg callable that returns a fresh awaitable
    every time the timer fires. This indirection means we don't bind
    any stale DB session or payload into the deferred work — the
    factory creates a fresh session when it runs.

    Safe to call from inside an async handler; the timer runs on the
    same loop. No-op if no running loop (e.g. called from a sync path).
    """
    norm = (ticker or "").upper()
    if not norm:
        return

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        logger.debug("ticker_debounce.schedule called with no running loop")
        return

    # Cancel any pending timer for this ticker so the burst gets
    # collapsed. Safe to call .cancel() even after the timer fired —
    # it's a no-op in that case.
    existing = _TIMERS.pop(norm, None)
    if existing is not None:
        existing.cancel()

    def _fire() -> None:
        # Remove our entry first so a new alert during the factory's
        # execution creates a fresh timer rather than mis-cancelling
        # the one currently running.
        _TIMERS.pop(norm, None)
        try:
            loop.create_task(_run(norm, factory))
        except Exception as e:
            logger.warning(f"ticker_debounce._fire({norm}) failed: {e}")

    handle = loop.call_later(delay, _fire)
    _TIMERS[norm] = handle


async def _run(
    ticker: str, factory: Callable[[], Awaitable[None]]
) -> None:
    """Run the user's factory-produced awaitable with broad error
    capture so a single failure doesn't prevent future debounced runs."""
    try:
        await factory()
    except Exception as e:
        logger.warning(f"ticker_debounce background task for {ticker} failed: {e}")


def pending_count() -> int:
    """Observability — how many tickers currently have timers armed."""
    return len(_TIMERS)


def cancel_all() -> None:
    """Shutdown-time helper. Cancels every pending timer without
    running its work. Safe to call at any time."""
    for handle in list(_TIMERS.values()):
        handle.cancel()
    _TIMERS.clear()
