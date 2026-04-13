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

import logging
import time
from collections import deque
from typing import Optional

logger = logging.getLogger(__name__)


RING_SIZE = 200  # ~hours of activity even on busy days; cheap RAM-wise


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
    break a real retrieval."""
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


def events_since(ts_seconds: float) -> list[dict]:
    return _BUFFER.since(ts_seconds)


def latest_ts() -> float:
    return _BUFFER.latest_ts()
