"""
Retrieval Event Ring Buffer
===========================

Records the IDs of memories pulled by recent retrievals so the 3D viz
can pulse them in real time. Single-process, in-memory — sized to the
last RING_SIZE events. No persistence: this is a live-feed signal,
not an audit log (the AIUsage table covers audit).

Recording is fire-and-forget; readers poll
GET /api/memory/retrieval-events?since=<iso> for events newer than a
client-tracked cursor.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import deque
from typing import Any, Optional

logger = logging.getLogger(__name__)


RING_SIZE = 200  # ~hours of activity even on busy days; cheap RAM-wise

# ─── WebSocket subscriber registry ──────────────────────────────────────────
# Single-process registry of currently-connected clients receiving live
# retrieval-event pushes (Phase 5b — replaces the 3s frontend polling).
# Each entry is the WebSocket object; we use a set for O(1) add/remove.
# Type hinted as `Any` to avoid importing FastAPI types here — keeps this
# module dependency-light and import-cheap.

_WS_CLIENTS: set[Any] = set()


class _RetrievalEventBuffer:
    def __init__(self, maxlen: int = RING_SIZE):
        # deque is thread-safe for append/popleft (atomic), which is enough
        # for our usage (single producer per request, many readers polling).
        self._events: deque[dict] = deque(maxlen=maxlen)

    def record(
        self,
        memory_ids: list[str],
        function_name: str,
        query_preview: str,
        scope_ticker: Optional[str] = None,
        scope_strategy: Optional[str] = None,
    ) -> None:
        """Append one retrieval. Silently no-ops if no memories surfaced."""
        if not memory_ids:
            return
        self._events.append({
            "ts": time.time(),
            "function_name": function_name,
            # Truncate the preview hard — this is a live UX signal, not a log
            "query_preview": (query_preview or "")[:120].strip(),
            "memory_ids": list(memory_ids),
            "scope_ticker": scope_ticker,
            "scope_strategy": scope_strategy,
        })

    def since(self, ts_seconds: float) -> list[dict]:
        """Return events strictly newer than ts_seconds (epoch). Bounded by ring size."""
        # Snapshot to a list so iteration isn't mutated mid-flight.
        snap = list(self._events)
        return [e for e in snap if e["ts"] > ts_seconds]

    def latest_ts(self) -> float:
        if not self._events:
            return 0.0
        return float(self._events[-1]["ts"])

    def clear(self) -> None:
        self._events.clear()


_BUFFER = _RetrievalEventBuffer()


def record_retrieval(
    memory_ids: list[str],
    function_name: str,
    query_preview: str,
    scope_ticker: Optional[str] = None,
    scope_strategy: Optional[str] = None,
) -> None:
    """Convenience wrapper. Wrapped in try/except — never let event recording
    break a real retrieval. Also pushes the event to any connected
    WebSocket subscribers (fire-and-forget via the running loop)."""
    try:
        _BUFFER.record(
            memory_ids=memory_ids,
            function_name=function_name,
            query_preview=query_preview,
            scope_ticker=scope_ticker,
            scope_strategy=scope_strategy,
        )
    except Exception as e:
        logger.debug(f"record_retrieval failed (non-fatal): {e}")
        return

    # Live broadcast — Phase 5b. Schedule on the running loop so we don't
    # block this synchronous helper. Polling fallback in the frontend
    # handles the case where no clients are connected (no-op here).
    if not _WS_CLIENTS:
        return
    if not memory_ids:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # No running loop — caller is in a sync context with no event
        # loop. Skip the live push; clients can poll for the missed event.
        return

    event_payload = {
        "ts": time.time(),
        "function_name": function_name,
        "query_preview": (query_preview or "")[:120].strip(),
        "memory_ids": list(memory_ids),
        "scope_ticker": scope_ticker,
        "scope_strategy": scope_strategy,
    }
    loop.create_task(_broadcast(event_payload))


async def _broadcast(event: dict) -> None:
    """Fan event out to every connected WebSocket. Drops dead sockets
    silently — they're cleaned up here rather than in the route."""
    if not _WS_CLIENTS:
        return
    msg = json.dumps({"events": [event]})
    dead: list[Any] = []
    # Snapshot the set so concurrent register/unregister doesn't mutate
    # mid-iteration.
    for ws in list(_WS_CLIENTS):
        try:
            await ws.send_text(msg)
        except Exception:
            dead.append(ws)
    for ws in dead:
        _WS_CLIENTS.discard(ws)


def register_ws(ws: Any) -> None:
    """Add a WebSocket to the broadcast set. Called from the route handler
    after accept(). Idempotent."""
    _WS_CLIENTS.add(ws)


def unregister_ws(ws: Any) -> None:
    """Remove a WebSocket. Safe to call multiple times."""
    _WS_CLIENTS.discard(ws)


def n_subscribers() -> int:
    """For diagnostics (could be exposed via /memory/embeddings/health later)."""
    return len(_WS_CLIENTS)


def events_since(ts_seconds: float) -> list[dict]:
    return _BUFFER.since(ts_seconds)


def latest_ts() -> float:
    return _BUFFER.latest_ts()
