"""
Screener webhook throughput validation — LOCAL DEV ONLY.

Sends 30 POST /api/screener/webhook requests concurrently with a valid
API key and per-request distinct indicators/timestamps (to avoid the
dedup cache). Measures total wall-clock, success count, failure count,
and p50 / p95 / max response times.

Target after Fix 1 + Fix 2 + Fix 3:
  - 30/30 successful (200)
  - p95 under 500ms
  - Total wall-clock well under TradingView's ~10s timeout

Do NOT run against production. Set WEBHOOK_URL to localhost only.

Usage:
  export WEBHOOK_URL="http://localhost:8000/api/screener/webhook"
  export TEST_API_KEY="<a valid key from your dev DB>"
  python -m scripts.test_webhook_throughput

  Optional:
    --concurrent N      (default 30)
    --ticker SYM        (default AAPL; different per iteration by --spread)
    --spread N          (default 30; cycles through N tickers to avoid dedup)

Exit codes:
  0 — all requests succeeded AND p95 < 500ms
  1 — some requests failed
  2 — all succeeded but p95 regressed above 500ms
"""

from __future__ import annotations

import argparse
import asyncio
import os
import statistics
import sys
import time
from dataclasses import dataclass
from typing import Optional

try:
    import httpx
except ImportError:
    print("httpx not installed. `pip install httpx`", file=sys.stderr)
    sys.exit(1)


# A small rotation pool so each of 30 requests uses a distinct ticker —
# sidesteps the dedup cache (fingerprint includes ticker + indicator +
# timeframe + unix_time, so distinct tickers are sufficient).
DEFAULT_TICKER_POOL = [
    "AAPL", "MSFT", "NVDA", "GOOGL", "AMZN",
    "META",  "TSLA", "AVGO", "LLY",   "JPM",
    "V",     "UNH",  "XOM",  "MA",    "HD",
    "PG",    "COST", "JNJ",  "ABBV",  "BAC",
    "WMT",   "ORCL", "MRK",  "KO",    "CVX",
    "ADBE",  "PEP",  "CRM",  "ACN",   "NFLX",
]


@dataclass
class RequestResult:
    ok: bool
    status_code: int
    elapsed_ms: float
    response_text: str
    ticker: str


async def _fire_one(
    client: httpx.AsyncClient,
    url: str,
    api_key: str,
    ticker: str,
    i: int,
    base_ts_ms: int,
) -> RequestResult:
    payload = {
        "key": api_key,
        "ticker": ticker,
        # Varying the indicator so the screener fingerprint also differs
        # when the same ticker gets reused across a very long burst.
        "indicator": f"THROUGHPUT_TEST_{i % 5}",
        "value": 100.0 + i,
        "signal": "bullish" if i % 2 == 0 else "bearish",
        # Unique timestamp per request — milliseconds
        "time": base_ts_ms + i,
        "tf": "5",
        "metadata": {"source": "throughput_test", "seq": i},
    }
    start = time.perf_counter()
    try:
        resp = await client.post(url, json=payload, timeout=15.0)
        elapsed = (time.perf_counter() - start) * 1000
        return RequestResult(
            ok=resp.status_code == 200,
            status_code=resp.status_code,
            elapsed_ms=elapsed,
            response_text=resp.text[:200],
            ticker=ticker,
        )
    except (httpx.TimeoutException, httpx.HTTPError) as e:
        elapsed = (time.perf_counter() - start) * 1000
        return RequestResult(
            ok=False,
            status_code=0,
            elapsed_ms=elapsed,
            response_text=f"{type(e).__name__}: {e}",
            ticker=ticker,
        )


async def run(
    url: str,
    api_key: str,
    concurrent: int,
    tickers: list[str],
) -> int:
    # Base unix-ms; each request adds its index so dedup never collides
    base_ts_ms = int(time.time() * 1000)
    async with httpx.AsyncClient() as client:
        wall_start = time.perf_counter()
        results = await asyncio.gather(
            *(
                _fire_one(
                    client, url, api_key,
                    tickers[i % len(tickers)], i, base_ts_ms,
                )
                for i in range(concurrent)
            ),
            return_exceptions=False,
        )
        wall_ms = (time.perf_counter() - wall_start) * 1000

    ok_count = sum(1 for r in results if r.ok)
    fail_count = concurrent - ok_count
    elapsed_list = [r.elapsed_ms for r in results]
    elapsed_list.sort()

    def _pct(p: float) -> float:
        if not elapsed_list:
            return 0.0
        k = max(0, min(len(elapsed_list) - 1, int(len(elapsed_list) * p) - 1))
        return elapsed_list[k]

    p50 = statistics.median(elapsed_list) if elapsed_list else 0.0
    p95 = _pct(0.95)
    p_max = max(elapsed_list) if elapsed_list else 0.0

    # Status-code histogram
    from collections import Counter
    by_status: Counter[int] = Counter(r.status_code for r in results)

    print("=" * 60)
    print(f"Screener webhook throughput — {concurrent} concurrent")
    print("=" * 60)
    print(f"Target URL:     {url}")
    print(f"Wall-clock:     {wall_ms:.0f}ms total")
    print(f"Successful:     {ok_count}/{concurrent}")
    print(f"Failed:         {fail_count}")
    print(f"p50 latency:    {p50:.0f}ms")
    print(f"p95 latency:    {p95:.0f}ms")
    print(f"max latency:    {p_max:.0f}ms")
    print(f"Status codes:   {dict(by_status)}")

    if fail_count:
        print()
        print("Failure samples:")
        for r in results:
            if not r.ok:
                print(f"  [{r.ticker}] {r.status_code}: {r.response_text}")
                if fail_count > 5:
                    print("  …")
                    break

    print()
    if fail_count:
        print("RESULT: FAIL — some requests did not succeed.")
        return 1
    if p95 > 500.0:
        print(f"RESULT: REGRESSION — p95 {p95:.0f}ms exceeds 500ms target.")
        return 2
    print("RESULT: PASS — all succeeded, p95 within budget.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--concurrent", type=int, default=30)
    ap.add_argument(
        "--spread",
        type=int,
        default=30,
        help="Cycle through this many distinct tickers.",
    )
    ap.add_argument(
        "--ticker",
        type=str,
        default=None,
        help="Single ticker to hammer (overrides the spread pool).",
    )
    args = ap.parse_args()

    url = os.environ.get("WEBHOOK_URL")
    if not url:
        print("WEBHOOK_URL env var required (e.g. http://localhost:8000/api/screener/webhook)", file=sys.stderr)
        return 1
    if "localhost" not in url and "127.0.0.1" not in url and "0.0.0.0" not in url:
        print(
            f"Refusing to run against non-local URL: {url}\n"
            "This script is for local dev validation only.",
            file=sys.stderr,
        )
        return 1

    api_key = os.environ.get("TEST_API_KEY")
    if not api_key:
        print("TEST_API_KEY env var required (a valid key from the dev DB)", file=sys.stderr)
        return 1

    if args.ticker:
        tickers = [args.ticker.upper()]
    else:
        tickers = DEFAULT_TICKER_POOL[: max(1, args.spread)]

    return asyncio.run(
        run(url=url, api_key=api_key, concurrent=args.concurrent, tickers=tickers)
    )


if __name__ == "__main__":
    sys.exit(main())
