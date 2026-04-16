"""
Market Intel (Gemini-grounded)
==============================
Single endpoint that consolidates the three home-page cards that used to
limp along on stale regex / missing cache:

    • Sector Analysis — per-sector score + sentiment + leaders
    • News / Macro     — today's macro headlines with impact framing
    • The Play         — top actionable trade, sourced from Henry's
                         pending action queue when available, otherwise
                         Gemini's current-conditions pick

One Gemini 2.0 Flash call with Google Search grounding backs all three.
Cached in henry_cache for `TTL_MIN` minutes so the home page doesn't
re-issue the grounded call on every poll.

Design: keep the Gemini contract strict (single JSON object) and
exhaustively fall back to safe defaults so a model hiccup doesn't blank
the dashboard.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any

from app.utils.utc import utcnow

logger = logging.getLogger(__name__)

CACHE_KEY = "market_intel_v1"
CACHE_TYPE = "market_intel"
TTL_MIN = 15


SECTORS = [
    "Technology", "Energy", "Healthcare", "Financials",
    "Consumer Discretionary", "Consumer Staples", "Industrials",
    "Materials", "Utilities", "Real Estate", "Communication",
]


_SYSTEM_PROMPT = """You are a disciplined macro/equity analyst generating a
compact market-intel JSON blob for a trading dashboard. Use Google Search
grounding to reference TODAY's actual market conditions — quote real
sector moves, index levels, and fresh headlines. Do not hallucinate.

Output ONLY a single JSON object. No prose, no markdown fences.

Schema (every field required; use null / empty list when truly unknown):
{
  "sectors": [
    { "name": "Technology", "score": -1 | 0 | 1,
      "sentiment": "bearish" | "neutral" | "bullish",
      "summary": "one sentence — mention XL? ticker move and driver",
      "leaders": ["NVDA", "AMD"]  // up to 3 leading tickers
    },
    ... exactly these 11 sectors, same order:
    Technology, Energy, Healthcare, Financials, Consumer Discretionary,
    Consumer Staples, Industrials, Materials, Utilities, Real Estate,
    Communication
  ],
  "macro": [
    { "headline": "fed meeting ...",
      "summary": "one sentence",
      "impact": "bullish" | "bearish" | "neutral",
      "url": "https://..."  // link if known, else empty string
    },
    ... 4 to 6 items, real current headlines only
  ],
  "play": {
    "ticker": "NVDA",
    "direction": "long" | "short",
    "rationale": "2 sentences — macro + micro thesis",
    "confidence": 1-10
  }
}

Rules:
- Score the sector off its SPDR ETF's daily move plus any material
  headline ("XLK +1.2% on semis rallying" ⇒ +1; "XLE -0.8% as crude
  slides" ⇒ -1; flat/mixed ⇒ 0).
- `leaders` for each sector should be the top 1-3 component tickers
  driving today's move.
- `macro` should be the 4-6 highest-impact headlines today across rates,
  Fed/FOMC, earnings, geopolitics, or commodities.
- `play` should be a directional idea a swing trader could act on now.
  Use real tickers and a plausible thesis anchored in today's data.
"""


def _default_payload() -> dict:
    """Shape used when Gemini fails — keeps the frontend rendering."""
    return {
        "sectors": [
            {"name": s, "score": 0, "sentiment": "neutral",
             "summary": "", "leaders": []}
            for s in SECTORS
        ],
        "macro": [],
        "play": None,
        "generated_at": utcnow().isoformat(),
        "source": "fallback",
    }


def _coerce(raw: dict) -> dict:
    """Validate and normalize a Gemini JSON response into the shape the
    frontend consumes. Missing sectors are filled with neutral placeholders
    so the card always renders all 11 rows in a predictable order."""
    out: dict = {"generated_at": utcnow().isoformat(), "source": "gemini"}

    # Sectors — enforce full set in canonical order
    by_name: dict[str, dict] = {}
    for s in (raw.get("sectors") or []):
        name = (s.get("name") or "").strip()
        if not name:
            continue
        # Accept either the canonical name or a close variant
        for canonical in SECTORS:
            if name.lower() == canonical.lower() or name.lower() in canonical.lower():
                by_name[canonical] = {
                    "name": canonical,
                    "score": max(-1, min(1, int(s.get("score") or 0))),
                    "sentiment": s.get("sentiment") or "neutral",
                    "summary": (s.get("summary") or "").strip()[:200],
                    "leaders": [
                        t.upper().strip() for t in (s.get("leaders") or [])
                        if isinstance(t, str)
                    ][:3],
                }
                break
    out["sectors"] = [
        by_name.get(n, {"name": n, "score": 0, "sentiment": "neutral",
                        "summary": "", "leaders": []})
        for n in SECTORS
    ]

    # Macro
    out["macro"] = []
    for m in (raw.get("macro") or [])[:8]:
        if not isinstance(m, dict):
            continue
        headline = (m.get("headline") or "").strip()
        if not headline:
            continue
        out["macro"].append({
            "headline": headline[:180],
            "summary": (m.get("summary") or "").strip()[:300],
            "impact": m.get("impact") if m.get("impact") in ("bullish", "bearish", "neutral") else "neutral",
            "url": (m.get("url") or "").strip(),
        })

    # Play
    p = raw.get("play")
    if isinstance(p, dict) and p.get("ticker"):
        dirn = p.get("direction")
        if dirn not in ("long", "short"):
            dirn = "long"
        try:
            conf = max(1, min(10, int(p.get("confidence") or 5)))
        except (TypeError, ValueError):
            conf = 5
        out["play"] = {
            "ticker": p["ticker"].upper().strip(),
            "direction": dirn,
            "rationale": (p.get("rationale") or "").strip()[:400],
            "confidence": conf,
            "source": "gemini",
        }
    else:
        out["play"] = None

    return out


async def _resolve_play_from_actions(out: dict, db) -> dict:
    """Prefer a real pending PortfolioAction as The Play when one exists.
    A Henry-generated BUY/ADD/OPPORTUNITY with real sizing beats Gemini's
    current-conditions pick. Falls back to whatever Gemini provided.
    """
    try:
        from sqlalchemy import select
        from app.models import PortfolioAction

        result = await db.execute(
            select(PortfolioAction)
            .where(
                PortfolioAction.status == "pending",
                PortfolioAction.action_type.in_(("BUY", "ADD", "OPPORTUNITY")),
            )
            .order_by(
                PortfolioAction.confidence.desc(),
                PortfolioAction.created_at.desc(),
            )
            .limit(1)
        )
        top = result.scalar_one_or_none()
        if top:
            out["play"] = {
                "ticker": top.ticker,
                "direction": top.direction or "long",
                "rationale": (top.reasoning or "").strip()[:400],
                "confidence": int(top.confidence or 5),
                "source": "henry_action",
                "action_id": top.id,
                "current_price": top.current_price,
                "suggested_price": top.suggested_price,
            }
    except Exception as e:
        logger.debug(f"play fallback from actions skipped: {e}")
    return out


async def _call_gemini_for_intel() -> dict | None:
    """Direct call — bypasses ai_provider.call_ai because we want a strict
    JSON response and the provider's prompt caching and usage-logging
    aren't useful here. Uses the same configurable model list as the rest
    of the stack, with Google Search grounding forced on."""
    from app.config import get_settings
    settings = get_settings()
    if not settings.gemini_api_key:
        logger.warning("market-intel: GEMINI_API_KEY empty; skipping Gemini")
        return None

    try:
        from google import genai
    except ImportError:
        logger.warning("market-intel: google-genai not installed")
        return None

    models = [settings.gemini_model] + [
        m.strip() for m in (settings.gemini_fallback_models or "").split(",") if m.strip()
    ]
    seen: set[str] = set()
    models = [m for m in models if not (m in seen or seen.add(m))]

    prompt = (
        "Generate the market-intel JSON blob for TODAY. "
        f"Today is {utcnow().strftime('%A %B %d %Y')} UTC. "
        "Reference real sector ETF moves and current headlines via search."
    )

    for model_name in models:
        try:
            def _sync(mn=model_name):
                client = genai.Client(api_key=settings.gemini_api_key)
                resp = client.models.generate_content(
                    model=mn,
                    contents=prompt,
                    config=genai.types.GenerateContentConfig(
                        system_instruction=_SYSTEM_PROMPT,
                        max_output_tokens=2000,
                        temperature=0.4,
                        tools=[genai.types.Tool(
                            google_search=genai.types.GoogleSearch()
                        )],
                    ),
                )
                return resp.text

            text = await asyncio.wait_for(asyncio.to_thread(_sync), timeout=60.0)
            if not text:
                continue
            # Strip markdown fences if the model added them
            clean = text.strip().replace("```json", "").replace("```", "").strip()
            match = re.search(r"\{[\s\S]*\}", clean)
            if not match:
                logger.warning(f"market-intel {model_name}: no JSON object found")
                continue
            data = json.loads(match.group(0))
            return data
        except asyncio.TimeoutError:
            logger.error(f"market-intel {model_name} timed out")
            continue
        except Exception as e:
            logger.warning(f"market-intel {model_name} failed: {str(e)[:200]}")
            continue

    return None


async def _fmp_sector_fallback() -> list[dict] | None:
    """Build a sector list from FMP's historical-sector-performance-snapshot
    when Gemini is unavailable. This keeps the Sector Analysis card
    populated with REAL data (just without Gemini's commentary) instead
    of showing 11 flat neutral rows until Gemini recovers.

    Returns None when FMP also fails — the caller falls back to the
    empty default payload in that case.
    """
    try:
        from app.services.fmp_service import get_sector_performance
        data = await get_sector_performance()
        if not data or not isinstance(data, list):
            return None
        # FMP returns items like:
        #   {"sector": "Technology", "changesPercentage": "+1.23%", ...}
        # Normalize against our canonical sector list.
        by_sector: dict[str, float] = {}
        for row in data:
            if not isinstance(row, dict):
                continue
            name = (row.get("sector") or row.get("name") or "").strip()
            change = row.get("changesPercentage") or row.get("averageChange") or 0
            if isinstance(change, str):
                try:
                    change = float(change.replace("%", "").replace("+", "").strip())
                except ValueError:
                    change = 0.0
            try:
                change = float(change)
            except (TypeError, ValueError):
                change = 0.0
            by_sector[name] = change

        out = []
        for canonical in SECTORS:
            # Best-effort fuzzy match — FMP sometimes emits "Consumer
            # Cyclical" instead of "Consumer Discretionary" etc.
            pct = by_sector.get(canonical)
            if pct is None:
                for k, v in by_sector.items():
                    if canonical.split()[0].lower() in k.lower():
                        pct = v
                        break
            pct = float(pct or 0.0)
            score = 1 if pct >= 0.4 else -1 if pct <= -0.4 else 0
            sentiment = "bullish" if score > 0 else "bearish" if score < 0 else "neutral"
            out.append({
                "name": canonical,
                "score": score,
                "sentiment": sentiment,
                "summary": f"{canonical} sector {pct:+.2f}% today.",
                "leaders": [],
            })
        return out
    except Exception as e:
        logger.debug(f"FMP sector fallback failed: {e}")
        return None


async def get_market_intel(force_refresh: bool = False) -> dict:
    """Return cached intel or generate fresh. Always returns a valid
    payload — failure modes reduce to the default shape rather than
    raising, so the frontend never crashes on a dead upstream.

    IMPORTANT: fallback payloads are NOT cached. A single Gemini
    transient error used to poison the 15-minute cache with empty
    sectors + no macro headlines, which left the home page showing
    "Awaiting Gemini refresh" long after Gemini recovered. Only
    genuine Gemini (or coerced) responses get persisted.
    """
    from app.database import async_session
    from app.services.henry_cache import get_cached, set_cached

    async with async_session() as db:
        if not force_refresh:
            cached = await get_cached(
                db, CACHE_KEY, max_age_hours=TTL_MIN / 60.0
            )
            if cached:
                # Still resolve play from live actions so the card tracks
                # Henry's queue even when the Gemini blob is cached.
                return await _resolve_play_from_actions(cached, db)

        raw = await _call_gemini_for_intel()
        is_real = raw is not None
        if raw is None:
            payload = _default_payload()
            # Upgrade the sector list with real FMP snapshot data when
            # Gemini is down — Gemini was the only sector source, and a
            # flat "all neutral" card is worse than no card.
            fmp_sectors = await _fmp_sector_fallback()
            if fmp_sectors:
                payload["sectors"] = fmp_sectors
                payload["source"] = "fmp_fallback"
        else:
            try:
                payload = _coerce(raw)
            except Exception as e:
                logger.error(f"market-intel coerce failed: {e}")
                payload = _default_payload()
                is_real = False

        # Only cache real Gemini responses. Fallback payloads expire
        # immediately so the next request retries the grounded call.
        if is_real:
            try:
                await set_cached(db, CACHE_KEY, CACHE_TYPE, payload)
                await db.commit()
            except Exception as e:
                logger.debug(f"market-intel cache write failed: {e}")

        return await _resolve_play_from_actions(payload, db)
