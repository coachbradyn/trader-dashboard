"""
Scanner Service
===============
Automated stock scanning pipeline:
1. Screen stocks via FMP screener API (full parameter support)
2. Cascading technical rule evaluation (minimises API calls)
3. Derived-indicator evaluation (MACD, Bollinger, volume surge)
4. Enrich with cached fundamentals
5. Send shortlist to Claude for AI-powered opportunity ranking
6. Create PortfolioAction entries with action_type=OPPORTUNITY

Scanner criteria stored in henry_cache with cache_type="scanner_config".
"""

import json
from app.utils.utc import utcnow
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import async_session
from app.models.portfolio_action import PortfolioAction
from app.models.portfolio import Portfolio

logger = logging.getLogger(__name__)

# ══════════════════════════════════════════════════════════════════════
# DEFAULT SCANNER CRITERIA
# ══════════════════════════════════════════════════════════════════════

DEFAULT_SCANNER_CRITERIA = {
    # ── Screener filters (passed directly to FMP /api/v3/stock-screener) ──
    "screener": {
        "priceMoreThan": 5.0,
        "priceLessThan": None,
        "marketCapMoreThan": 500000000,
        "marketCapLessThan": None,
        "volumeMoreThan": 500000,
        "volumeLessThan": None,
        "betaMoreThan": None,
        "betaLessThan": None,
        "dividendMoreThan": None,
        "dividendLessThan": None,
        "sector": "",          # comma-separated or empty for all
        "industry": "",
        "country": "US",
        "exchange": "NYSE,NASDAQ",
        "isEtf": False,
        "isFund": False,
        "isActivelyTrading": True,
        # Wide pool → diverse candidates. FMP returns up to 500 results
        # per screener call; with the Starter plan's 300/min headroom we
        # can afford to pull the full 500 and let the momentum/technical
        # filters narrow it. Previously capped at 200, which left mid-caps
        # and small-caps under-represented.
        "limit": 500,
    },
    # ── Momentum post-filter (applied after FMP screener, before technical
    # rules). The FMP screener alone biases toward mega-caps; this filter
    # narrows the pool to actual movers by daily percent change.
    # ──────────────────────────────────────────────────────────────────────
    "momentum_filter": {
        "enabled": True,
        "min_change_pct": 0.5,
        "top_n": 60,
    },
    # ── Technical filter rules (evaluated in sequence, all must pass) ──
    "technical_rules": [
        {
            "enabled": False,
            "indicator": "rsi",
            "period": 14,
            "timeframe": "daily",
            "condition": "below",   # above | below | crosses_above | crosses_below | increasing | decreasing
            "value": 35,
            "compare_indicator": None,  # null = compare to value; or {"indicator": "sma", "period": 200}
            "label": "Oversold RSI"
        },
        {
            "enabled": False,
            "indicator": "price",
            "period": 0,
            "timeframe": "daily",
            "condition": "above",
            "value": 0,
            "compare_indicator": {"indicator": "sma", "period": 200},
            "label": "Price above SMA 200 (uptrend)"
        },
        {
            "enabled": False,
            "indicator": "adx",
            "period": 14,
            "timeframe": "daily",
            "condition": "above",
            "value": 20,
            "compare_indicator": None,
            "label": "ADX trending"
        },
        {
            "enabled": False,
            "indicator": "ema",
            "period": 9,
            "timeframe": "daily",
            "condition": "above",
            "value": 0,
            "compare_indicator": {"indicator": "ema", "period": 21},
            "label": "EMA 9 > EMA 21 (momentum)"
        },
        {
            "enabled": False,
            "indicator": "macd",
            "period": 0,
            "timeframe": "daily",
            "condition": "crosses_above",
            "value": 0,
            "compare_indicator": {"indicator": "macd_signal", "period": 0},
            "label": "MACD bullish crossover"
        },
        {
            "enabled": False,
            "indicator": "bollinger_lower",
            "period": 20,
            "timeframe": "daily",
            "condition": "below",
            "value": 0,
            "compare_indicator": None,
            "label": "Price below lower Bollinger Band"
        },
    ],
    # ── Volume filter ──
    "volume_filter": {
        "enabled": False,
        "surge_multiplier": 1.5,   # current vol must be N x average
        "avg_period": 20,
    },
    # ── Presets (stored for UI reference, not used by scanner directly) ──
    "active_preset": None,  # "momentum" | "oversold_bounce" | "breakout" | "value_catalyst" | null
}

SCANNER_CACHE_KEY = "scanner:criteria"
PROFILES_CACHE_KEY = "scanner:profiles"


# ══════════════════════════════════════════════════════════════════════
# SCAN PROFILES
# ══════════════════════════════════════════════════════════════════════

# Each profile has: name, criteria (screener + technical_rules + volume_filter),
# market_conditions (when Henry should use this profile), enabled flag.

DEFAULT_PROFILES = [
    {
        "id": "momentum",
        "name": "Momentum",
        "description": "Large cap stocks in established uptrends with room to run",
        "enabled": True,
        "market_conditions": {
            "trend": "bullish",
            "time_slots": ["morning", "midday"],
        },
        "criteria": None,
    },
    {
        "id": "oversold_bounce",
        "name": "Oversold Bounce",
        "description": "Pullback buying opportunities in confirmed uptrends",
        "enabled": True,
        "market_conditions": {
            "trend": "any",
            "time_slots": ["morning", "midday", "afternoon"],
        },
        "criteria": None,
    },
    {
        "id": "breakout",
        "name": "Breakout",
        "description": "Stocks starting new trends with volume confirmation",
        "enabled": True,
        "market_conditions": {
            "trend": "any",
            "time_slots": ["morning", "midday"],
        },
        "criteria": None,
    },
    {
        "id": "value_catalyst",
        "name": "Value + Catalyst",
        "description": "Fundamentally solid stocks approaching catalyst events",
        "enabled": True,
        "market_conditions": {
            "trend": "any",
            "time_slots": ["morning", "midday", "afternoon"],
        },
        "criteria": None,
    },
    {
        "id": "gap_breakout",
        "name": "Gap Breakout",
        "description": "Stocks gapping up >2% at open with volume — catch the continuation",
        "enabled": True,
        "market_conditions": {
            "trend": "any",
            "time_slots": ["morning"],
        },
        "criteria": None,
    },
    {
        "id": "dead_cat_bounce",
        "name": "Dead Cat Bounce / Recovery",
        "description": "Stocks down >15% in 30 days showing early recovery signals — positive LMA crossover + volume surge",
        "enabled": True,
        "market_conditions": {
            "trend": "any",
            "time_slots": ["morning", "midday", "afternoon"],
        },
        "criteria": None,
    },
    {
        "id": "mid_cap_momentum",
        "name": "Mid Cap Momentum",
        "description": "$1B–$10B mid caps with strong intraday moves — where most swing setups live",
        "enabled": True,
        "market_conditions": {
            "trend": "any",
            "time_slots": ["morning", "midday"],
        },
        "criteria": None,
    },
    {
        "id": "small_cap_momentum",
        "name": "Small Cap Momentum",
        "description": "$300M–$1B small caps with high relative volume — higher risk, higher reward",
        "enabled": True,
        "market_conditions": {
            "trend": "any",
            "time_slots": ["morning", "midday"],
        },
        "criteria": None,
    },
]


def _time_slot_now() -> str:
    """Return current market time slot: morning | midday | afternoon."""
    try:
        from zoneinfo import ZoneInfo
        now_et = datetime.now(ZoneInfo("America/New_York"))
        hour = now_et.hour
        if hour < 11:
            return "morning"
        elif hour < 14:
            return "midday"
        else:
            return "afternoon"
    except Exception:
        return "midday"


async def get_scan_profiles() -> list[dict]:
    """Get all scan profiles. Merges saved profiles with defaults."""
    try:
        from app.models.henry_cache import HenryCache
        async with async_session() as db:
            result = await db.execute(
                select(HenryCache).where(HenryCache.cache_key == PROFILES_CACHE_KEY)
            )
            entry = result.scalar_one_or_none()
            if entry and entry.content and isinstance(entry.content, list):
                profiles = entry.content
                # Ensure each profile has criteria filled in
                for p in profiles:
                    if p.get("criteria") is None:
                        preset = get_preset_criteria(p["id"])
                        if preset:
                            p["criteria"] = preset
                        else:
                            p["criteria"] = DEFAULT_SCANNER_CRITERIA.copy()
                return profiles
    except Exception as e:
        logger.debug(f"Error reading scan profiles: {e}")

    # Return defaults with criteria filled in
    profiles = []
    for p in DEFAULT_PROFILES:
        profile = {**p}
        preset = get_preset_criteria(profile["id"])
        profile["criteria"] = preset if preset else DEFAULT_SCANNER_CRITERIA.copy()
        profiles.append(profile)
    return profiles


async def save_scan_profiles(profiles: list[dict]) -> list[dict]:
    """Save all scan profiles."""
    from app.models.henry_cache import HenryCache

    try:
        async with async_session() as db:
            result = await db.execute(
                select(HenryCache).where(HenryCache.cache_key == PROFILES_CACHE_KEY)
            )
            existing = result.scalar_one_or_none()
            if existing:
                existing.content = profiles
                existing.generated_at = utcnow()
            else:
                db.add(HenryCache(
                    cache_key=PROFILES_CACHE_KEY,
                    cache_type="scanner_profiles",
                    content=profiles,
                ))
            await db.commit()
    except Exception as e:
        logger.error(f"Error saving scan profiles: {e}")

    return profiles


async def save_single_profile(profile: dict) -> list[dict]:
    """Create or update a single profile by ID."""
    profiles = await get_scan_profiles()
    profile_id = profile.get("id")

    # Update existing or append new
    found = False
    for i, p in enumerate(profiles):
        if p["id"] == profile_id:
            profiles[i] = {**p, **profile}
            found = True
            break
    if not found:
        profiles.append(profile)

    return await save_scan_profiles(profiles)


async def delete_profile(profile_id: str) -> list[dict]:
    """Delete a profile by ID. Built-in profiles can be disabled but not deleted."""
    profiles = await get_scan_profiles()
    builtin_ids = {p["id"] for p in DEFAULT_PROFILES}

    if profile_id in builtin_ids:
        # Disable instead of delete
        for p in profiles:
            if p["id"] == profile_id:
                p["enabled"] = False
                break
    else:
        profiles = [p for p in profiles if p["id"] != profile_id]

    return await save_scan_profiles(profiles)


async def select_profiles_for_now() -> list[dict]:
    """
    Select which scan profiles Henry should run right now based on:
    - Profile enabled status
    - Current time slot (morning/midday/afternoon)
    - Market conditions (VIX level, market trend)
    Returns profiles sorted by priority.
    """
    profiles = await get_scan_profiles()
    time_slot = _time_slot_now()

    # Get market conditions
    vix = None
    spy_trend = "any"
    try:
        from app.services.price_service import price_service
        vix = price_service.get_price("VIX")
        spy = price_service.get_price("SPY")

        # Determine trend from recent SPY data
        if spy:
            from app.services.fmp_service import get_technical_indicator
            sma_data = await get_technical_indicator("SPY", "sma", period=50, interval="daily")
            if sma_data and isinstance(sma_data, list) and len(sma_data) > 0:
                sma50 = sma_data[0].get("sma") or sma_data[0].get("value")
                if sma50 and spy > sma50:
                    spy_trend = "bullish"
                elif sma50:
                    spy_trend = "bearish"
    except Exception:
        pass

    selected = []
    for profile in profiles:
        if not profile.get("enabled", False):
            continue

        conditions = profile.get("market_conditions", {})

        # Check time slot
        allowed_slots = conditions.get("time_slots", ["morning", "midday", "afternoon"])
        if time_slot not in allowed_slots:
            continue

        # Check VIX
        if vix is not None:
            vix_min = conditions.get("vix_min")
            vix_max = conditions.get("vix_max")
            if vix_min is not None and vix < vix_min:
                continue
            if vix_max is not None and vix > vix_max:
                continue

        # Check trend
        required_trend = conditions.get("trend", "any")
        if required_trend != "any" and spy_trend != "any" and required_trend != spy_trend:
            continue

        selected.append(profile)

    logger.info(
        f"Profile selection: time={time_slot}, VIX={vix}, trend={spy_trend} → "
        f"{len(selected)}/{len(profiles)} profiles selected: {[p['name'] for p in selected]}"
    )
    return selected


# ══════════════════════════════════════════════════════════════════════
# CRITERIA MANAGEMENT
# ══════════════════════════════════════════════════════════════════════

async def get_scanner_criteria() -> dict:
    """Get scanner criteria from henry_cache or return defaults."""
    try:
        from app.models.henry_cache import HenryCache

        async with async_session() as db:
            result = await db.execute(
                select(HenryCache).where(HenryCache.cache_key == SCANNER_CACHE_KEY)
            )
            entry = result.scalar_one_or_none()
            if entry and entry.content:
                return entry.content
    except Exception as e:
        logger.debug(f"Error reading scanner criteria: {e}")
    return DEFAULT_SCANNER_CRITERIA.copy()


async def update_scanner_criteria(criteria: dict) -> dict:
    """Update scanner criteria in henry_cache. Merges with defaults."""
    from app.models.henry_cache import HenryCache

    merged = DEFAULT_SCANNER_CRITERIA.copy()
    # Deep merge top-level and nested dicts
    for key, value in criteria.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
            merged[key].update(value)
        else:
            merged[key] = value

    try:
        async with async_session() as db:
            result = await db.execute(
                select(HenryCache).where(HenryCache.cache_key == SCANNER_CACHE_KEY)
            )
            existing = result.scalar_one_or_none()
            if existing:
                existing.content = merged
                existing.cache_type = "scanner_config"
                existing.generated_at = utcnow()
            else:
                entry = HenryCache(
                    cache_key=SCANNER_CACHE_KEY,
                    cache_type="scanner_config",
                    content=merged,
                )
                db.add(entry)
            await db.commit()
    except Exception as e:
        logger.error(f"Error saving scanner criteria: {e}")

    return merged


# ══════════════════════════════════════════════════════════════════════
# PRESET CONFIGURATIONS
# ══════════════════════════════════════════════════════════════════════

def get_preset_criteria(preset_name: str) -> dict:
    """Return full criteria dict for a named preset."""
    presets = {
        "momentum": {
            "screener": {
                **DEFAULT_SCANNER_CRITERIA["screener"],
                "marketCapMoreThan": 10000000000,  # >10B large cap
                "volumeMoreThan": 1000000,
            },
            "technical_rules": [
                {
                    "enabled": True, "indicator": "rsi", "period": 14, "timeframe": "daily",
                    "condition": "above", "value": 40, "compare_indicator": None,
                    "label": "RSI above 40",
                },
                {
                    "enabled": True, "indicator": "rsi", "period": 14, "timeframe": "daily",
                    "condition": "below", "value": 65, "compare_indicator": None,
                    "label": "RSI below 65",
                },
                {
                    "enabled": True, "indicator": "adx", "period": 14, "timeframe": "daily",
                    "condition": "above", "value": 25, "compare_indicator": None,
                    "label": "ADX > 25 (strong trend)",
                },
                {
                    "enabled": True, "indicator": "price", "period": 0, "timeframe": "daily",
                    "condition": "above", "value": 0,
                    "compare_indicator": {"indicator": "sma", "period": 200},
                    "label": "Price above SMA 200",
                },
                {
                    "enabled": True, "indicator": "ema", "period": 9, "timeframe": "daily",
                    "condition": "above", "value": 0,
                    "compare_indicator": {"indicator": "ema", "period": 21},
                    "label": "EMA 9 > EMA 21 (momentum)",
                },
            ],
            "volume_filter": {"enabled": False, "surge_multiplier": 1.5, "avg_period": 20},
            "active_preset": "momentum",
        },
        "oversold_bounce": {
            "screener": {
                **DEFAULT_SCANNER_CRITERIA["screener"],
                "marketCapMoreThan": 2000000000,  # >2B mid+
            },
            "technical_rules": [
                {
                    "enabled": True, "indicator": "rsi", "period": 14, "timeframe": "daily",
                    "condition": "below", "value": 40, "compare_indicator": None,
                    "label": "RSI < 40 (pulling back)",
                },
                {
                    "enabled": True, "indicator": "price", "period": 0, "timeframe": "daily",
                    "condition": "above", "value": 0,
                    "compare_indicator": {"indicator": "sma", "period": 200},
                    "label": "Price above SMA 200 (uptrend)",
                },
            ],
            "volume_filter": {"enabled": False, "surge_multiplier": 1.5, "avg_period": 20},
            "active_preset": "oversold_bounce",
        },
        "breakout": {
            "screener": {
                **DEFAULT_SCANNER_CRITERIA["screener"],
                "marketCapMoreThan": 500000000,
            },
            "technical_rules": [
                {
                    "enabled": True, "indicator": "adx", "period": 14, "timeframe": "daily",
                    "condition": "above", "value": 20, "compare_indicator": None,
                    "label": "ADX > 20 (directional move)",
                },
                {
                    "enabled": True, "indicator": "price", "period": 0, "timeframe": "daily",
                    "condition": "above", "value": 0,
                    "compare_indicator": {"indicator": "ema", "period": 50},
                    "label": "Price above EMA 50",
                },
            ],
            "volume_filter": {"enabled": True, "surge_multiplier": 1.5, "avg_period": 20},
            "active_preset": "breakout",
        },
        "value_catalyst": {
            "screener": {
                **DEFAULT_SCANNER_CRITERIA["screener"],
                "marketCapMoreThan": 2000000000,
                "betaLessThan": 1.5,
            },
            "technical_rules": [
                {
                    "enabled": True, "indicator": "price", "period": 0, "timeframe": "daily",
                    "condition": "above", "value": 0,
                    "compare_indicator": {"indicator": "sma", "period": 200},
                    "label": "Price above SMA 200",
                },
            ],
            "volume_filter": {"enabled": False, "surge_multiplier": 1.5, "avg_period": 20},
            "active_preset": "value_catalyst",
        },
        "gap_breakout": {
            "screener": {
                **DEFAULT_SCANNER_CRITERIA["screener"],
                "marketCapMoreThan": 1000000000,  # >1B — liquid enough for gaps
                "volumeMoreThan": 750000,
                "limit": 80,
            },
            "technical_rules": [
                {
                    "enabled": True, "indicator": "price", "period": 0, "timeframe": "daily",
                    "condition": "above", "value": 0,
                    "compare_indicator": {"indicator": "ema", "period": 9},
                    "label": "Price above EMA 9 (short-term momentum)",
                },
                {
                    "enabled": True, "indicator": "adx", "period": 14, "timeframe": "daily",
                    "condition": "above", "value": 20, "compare_indicator": None,
                    "label": "ADX > 20 (directional move)",
                },
            ],
            "volume_filter": {"enabled": True, "surge_multiplier": 2.0, "avg_period": 20},
            "active_preset": "gap_breakout",
            # Gap detection: run_scanner checks daily change_pct > 2%
            "gap_filter": {"enabled": True, "min_gap_pct": 2.0},
        },
        "dead_cat_bounce": {
            "screener": {
                **DEFAULT_SCANNER_CRITERIA["screener"],
                "marketCapMoreThan": 500000000,
                "volumeMoreThan": 500000,
                "limit": 80,
            },
            "technical_rules": [
                {
                    "enabled": True, "indicator": "ema", "period": 9, "timeframe": "daily",
                    "condition": "above", "value": 0,
                    "compare_indicator": {"indicator": "ema", "period": 21},
                    "label": "EMA 9 > EMA 21 (short-term recovery)",
                },
                {
                    "enabled": True, "indicator": "rsi", "period": 14, "timeframe": "daily",
                    "condition": "above", "value": 25, "compare_indicator": None,
                    "label": "RSI > 25 (leaving oversold)",
                },
                {
                    "enabled": True, "indicator": "rsi", "period": 14, "timeframe": "daily",
                    "condition": "below", "value": 60, "compare_indicator": None,
                    "label": "RSI < 60 (room to run)",
                },
            ],
            "volume_filter": {"enabled": True, "surge_multiplier": 1.5, "avg_period": 20},
            "active_preset": "dead_cat_bounce",
            "drawdown_filter": {"enabled": True, "min_drawdown_pct": 10, "lookback_days": 30},
        },
        "mid_cap_momentum": {
            "screener": {
                **DEFAULT_SCANNER_CRITERIA["screener"],
                "marketCapMoreThan": 1_000_000_000,     # >$1B
                "marketCapLessThan": 10_000_000_000,    # <$10B
                "volumeMoreThan": 750_000,
                "limit": 300,  # wider net — mid caps are more numerous
            },
            "technical_rules": [
                {
                    "enabled": True, "indicator": "rsi", "period": 14, "timeframe": "daily",
                    "condition": "above", "value": 45, "compare_indicator": None,
                    "label": "RSI > 45",
                },
                {
                    "enabled": True, "indicator": "adx", "period": 14, "timeframe": "daily",
                    "condition": "above", "value": 20, "compare_indicator": None,
                    "label": "ADX > 20 (directional move)",
                },
                {
                    "enabled": True, "indicator": "ema", "period": 9, "timeframe": "daily",
                    "condition": "above", "value": 0,
                    "compare_indicator": {"indicator": "ema", "period": 21},
                    "label": "EMA 9 > EMA 21 (momentum)",
                },
            ],
            "volume_filter": {"enabled": False, "surge_multiplier": 1.5, "avg_period": 20},
            # Stronger momentum bar for mid caps — they move more
            "momentum_filter": {"enabled": True, "min_change_pct": 0.5, "top_n": 60},
            "active_preset": "mid_cap_momentum",
        },
        "small_cap_momentum": {
            "screener": {
                **DEFAULT_SCANNER_CRITERIA["screener"],
                "marketCapMoreThan": 300_000_000,       # >$300M (avoid pennies)
                "marketCapLessThan": 1_000_000_000,     # <$1B
                "priceMoreThan": 5.0,
                "volumeMoreThan": 300_000,
                "limit": 500,
            },
            "technical_rules": [
                {
                    "enabled": True, "indicator": "adx", "period": 14, "timeframe": "daily",
                    "condition": "above", "value": 25, "compare_indicator": None,
                    "label": "ADX > 25 (strong directional)",
                },
                {
                    "enabled": True, "indicator": "rsi", "period": 14, "timeframe": "daily",
                    "condition": "below", "value": 75, "compare_indicator": None,
                    "label": "RSI < 75 (not yet blow-off top)",
                },
            ],
            "volume_filter": {"enabled": True, "surge_multiplier": 1.5, "avg_period": 20},
            "momentum_filter": {"enabled": True, "min_change_pct": 1.0, "top_n": 40},
            "active_preset": "small_cap_momentum",
        },
    }
    preset = presets.get(preset_name)
    if not preset:
        logger.warning(f"Unknown preset '{preset_name}', returning defaults")
        return DEFAULT_SCANNER_CRITERIA.copy()
    return preset


# ══════════════════════════════════════════════════════════════════════
# MAIN SCANNER PIPELINE
# ══════════════════════════════════════════════════════════════════════

async def _filter_by_momentum(
    screener_results: list[dict],
    min_change_pct: float = 0.5,
    top_n: int = 60,
) -> list[dict]:
    """Enrich screener results with batch quotes and sort by daily %change.

    The FMP screener returns results sorted by market cap, so without this
    step the candidate pool is always megacaps. This function pulls live
    quotes, attaches %change, and returns the top N movers.

    If batch quotes fail entirely, returns the original list (capped to
    top_n) so the technical pipeline still has something to evaluate.
    """
    from app.services.fmp_service import get_batch_quotes

    if not screener_results:
        return []

    tickers = [s.get("symbol") for s in screener_results if s.get("symbol")]
    if not tickers:
        return []

    change_map: dict[str, float] = {}
    volume_map: dict[str, float] = {}
    CHUNK = 50
    for i in range(0, len(tickers), CHUNK):
        chunk = tickers[i:i + CHUNK]
        quotes = await get_batch_quotes(chunk)
        if not quotes or not isinstance(quotes, list):
            logger.warning(f"Momentum filter: batch quote failed for chunk {i//CHUNK+1} ({len(chunk)} tickers)")
            continue
        for q in quotes:
            sym = q.get("symbol")
            cp = q.get("changesPercentage") or q.get("changePercentage")
            if sym is not None and cp is not None:
                try:
                    change_map[sym] = float(cp)
                    if q.get("volume") is not None:
                        volume_map[sym] = float(q.get("volume") or 0)
                except (TypeError, ValueError):
                    continue

    # If batch quotes failed entirely, pass stocks through unfiltered
    # so the technical pipeline has candidates to work with.
    if not change_map:
        logger.warning(
            f"Momentum filter: no quote data at all — passing {min(len(screener_results), top_n)} "
            f"stocks through unfiltered"
        )
        return screener_results[:top_n]

    # Attach change% and sort by absolute movement
    with_change: list[dict] = []
    without_change: list[dict] = []
    for s in screener_results:
        sym = s.get("symbol")
        if not sym:
            continue
        cp = change_map.get(sym)
        if cp is None:
            without_change.append(s)
            continue
        s["_change_pct"] = cp
        s["_live_volume"] = volume_map.get(sym, s.get("volume"))
        with_change.append(s)

    # Sort movers by absolute %change descending
    with_change.sort(key=lambda x: abs(x.get("_change_pct", 0)), reverse=True)

    # Apply the min_change threshold, but guarantee a minimum pool
    above_threshold = [s for s in with_change if abs(s.get("_change_pct", 0)) >= min_change_pct]

    if len(above_threshold) >= 10:
        result = above_threshold[:top_n]
    else:
        # Not enough movers above threshold — take the top N by absolute
        # change regardless. On flat days this ensures the scanner still
        # evaluates the most active names rather than returning nothing.
        result = with_change[:top_n]
        if above_threshold:
            logger.info(
                f"Momentum filter: only {len(above_threshold)} stocks above "
                f"{min_change_pct}% — taking top {min(len(with_change), top_n)} by abs change"
            )
        else:
            logger.info(
                f"Momentum filter: 0 stocks above {min_change_pct}% threshold — "
                f"taking top {min(len(with_change), top_n)} movers regardless"
            )

    logger.info(
        f"Momentum filter: {len(screener_results)} in → {len(result)} out "
        f"(quotes: {len(change_map)}, above {min_change_pct}%: {len(above_threshold)}, "
        f"no quote: {len(without_change)})"
    )
    return result


_FMP_SCREENER_KEYS = frozenset({
    "priceMoreThan", "priceLowerThan", "priceLessThan",
    "marketCapMoreThan", "marketCapLowerThan", "marketCapLessThan",
    "volumeMoreThan", "volumeLowerThan", "volumeLessThan",
    "betaMoreThan", "betaLowerThan", "betaLessThan",
    "dividendMoreThan", "dividendLowerThan", "dividendLessThan",
    "sector", "industry", "country", "exchange",
    "isEtf", "isFund", "isActivelyTrading",
    "limit",
})


def _build_screener_params(screener_cfg: dict) -> dict:
    """Convert the screener section of criteria into FMP query params.

    Only passes keys that the FMP /stable/company-screener endpoint
    actually supports. Technical indicator keys (adx, rsi, etc.) are
    NOT screener params — they belong in technical_rules and are
    evaluated per-ticker after the screener narrows the universe.
    """
    params: dict = {}
    for key, value in screener_cfg.items():
        if key not in _FMP_SCREENER_KEYS:
            continue
        if value is None:
            continue
        if isinstance(value, bool):
            if value:
                params[key] = "true"
            continue
        if isinstance(value, str) and value == "":
            continue
        params[key] = str(value)
    return params


async def _fetch_indicator_for_ticker(
    ticker: str,
    indicator: str,
    period: int,
    timeframe: str,
) -> tuple[float | None, float | None, float | None]:
    """Fetch a single indicator and return (current, prev, 3-bars-ago).
    Returns (None, None, None) on failure."""
    from app.services.fmp_service import get_technical_indicator, get_quote

    try:
        # Special handling for 'price' indicator
        if indicator == "price":
            quote = await get_quote(ticker)
            if quote and isinstance(quote, list) and len(quote) > 0:
                price = quote[0].get("price")
                return (price, price, price)  # price doesn't have history here
            return (None, None, None)

        # Derived indicators handled separately
        if indicator in ("macd", "macd_signal", "bollinger_lower", "bollinger_upper"):
            return (None, None, None)  # handled by derived evaluator

        data = await get_technical_indicator(ticker, indicator, period=period, interval=timeframe)
        if not data or not isinstance(data, list) or len(data) < 1:
            return (None, None, None)

        # FMP returns newest first
        def _extract(entry: dict) -> float | None:
            return entry.get(indicator) or entry.get("value")

        current = _extract(data[0]) if len(data) > 0 else None
        prev = _extract(data[1]) if len(data) > 1 else None
        three_ago = _extract(data[3]) if len(data) > 3 else None
        return (current, prev, three_ago)
    except Exception as e:
        logger.warning(f"Failed to fetch {indicator}({period}) for {ticker}: {e}")
        return (None, None, None)


def _evaluate_condition(
    condition: str,
    current: float | None,
    prev: float | None,
    three_ago: float | None,
    compare_value: float | None,
) -> bool:
    """Evaluate a single condition.  Returns False if data is missing."""
    if current is None:
        return False

    target = compare_value
    if target is None:
        return False

    if condition == "above":
        return current > target
    elif condition == "below":
        return current < target
    elif condition == "crosses_above":
        if prev is None:
            return False
        return prev <= target and current > target
    elif condition == "crosses_below":
        if prev is None:
            return False
        return prev >= target and current < target
    elif condition == "increasing":
        if three_ago is None:
            return False
        return current > three_ago
    elif condition == "decreasing":
        if three_ago is None:
            return False
        return current < three_ago
    else:
        logger.warning(f"Unknown condition: {condition}")
        return False


async def _evaluate_technical_rules(
    tickers: list[str],
    rules: list[dict],
    screener_map: dict[str, dict],
) -> list[dict]:
    """Cascading technical evaluation.
    For each enabled rule, fetch the indicator only for surviving tickers.
    Returns surviving stocks with their collected indicator data attached.
    """
    from app.services.fmp_service import (
        get_technical_indicator, get_quote,
        compute_macd, compute_bollinger,
        get_api_usage,
    )
    import asyncio as _aio

    # Build candidate dicts: ticker -> collected indicator data
    candidates: dict[str, dict] = {
        t: {"ticker": t, "screener_data": screener_map.get(t, {}), "indicators": {}}
        for t in tickers
    }

    fmp_calls = 0

    async def _pace():
        """Pause briefly when nearing the per-minute rate limit so the
        rolling window slides and we don't self-throttle."""
        nonlocal fmp_calls
        if fmp_calls > 0 and fmp_calls % 50 == 0:
            usage = get_api_usage()
            rpm = usage.get("rpm", 0)
            if rpm >= 200:
                wait = 5 if rpm >= 250 else 2
                logger.info(f"Scanner pacing: {rpm} RPM, waiting {wait}s")
                await _aio.sleep(wait)

    for rule in rules:
        if not rule.get("enabled", False):
            continue

        indicator = rule["indicator"]
        period = rule.get("period", 14)
        timeframe = rule.get("timeframe", "daily")
        condition = rule["condition"]
        static_value = rule.get("value", 0)
        compare_ind = rule.get("compare_indicator")
        label = rule.get("label", "")

        surviving = list(candidates.keys())
        if not surviving:
            break

        logger.info(f"Scanner rule '{label}': evaluating {len(surviving)} candidates")

        passed_tickers: list[str] = []

        for ticker in surviving:
            try:
                # ── Get the primary indicator value ──
                if indicator in ("macd", "macd_signal"):
                    macd_data = await compute_macd(ticker, timeframe)
                    fmp_calls += 2  # ema12 + ema26
                    current = macd_data.get("macd")
                    prev = macd_data.get("prev_macd")
                    three_ago = None
                    candidates[ticker]["indicators"]["macd"] = macd_data

                    # Compare indicator for MACD is the signal line
                    if compare_ind and compare_ind.get("indicator") == "macd_signal":
                        compare_value = macd_data.get("signal")
                    else:
                        compare_value = static_value

                elif indicator == "bollinger_lower":
                    boll = await compute_bollinger(ticker, period, timeframe)
                    fmp_calls += 3  # sma + stddev + quote
                    candidates[ticker]["indicators"]["bollinger"] = boll
                    current = boll.get("price")
                    prev = None
                    three_ago = None
                    compare_value = boll.get("lower")

                elif indicator == "bollinger_upper":
                    boll = await compute_bollinger(ticker, period, timeframe)
                    fmp_calls += 3
                    candidates[ticker]["indicators"]["bollinger"] = boll
                    current = boll.get("price")
                    prev = None
                    three_ago = None
                    compare_value = boll.get("upper")

                else:
                    current, prev, three_ago = await _fetch_indicator_for_ticker(
                        ticker, indicator, period, timeframe
                    )
                    fmp_calls += 1
                    ind_key = f"{indicator}_{period}" if period else indicator
                    candidates[ticker]["indicators"][ind_key] = current

                    # ── Get the comparison value ──
                    if compare_ind:
                        comp_ind = compare_ind["indicator"]
                        comp_period = compare_ind.get("period", 0)

                        if comp_ind in ("macd", "macd_signal"):
                            macd_data = await compute_macd(ticker, timeframe)
                            fmp_calls += 2
                            candidates[ticker]["indicators"]["macd"] = macd_data
                            compare_value = macd_data.get("signal") if comp_ind == "macd_signal" else macd_data.get("macd")
                        else:
                            comp_current, _, _ = await _fetch_indicator_for_ticker(
                                ticker, comp_ind, comp_period, timeframe
                            )
                            fmp_calls += 1
                            comp_key = f"{comp_ind}_{comp_period}" if comp_period else comp_ind
                            candidates[ticker]["indicators"][comp_key] = comp_current
                            compare_value = comp_current
                    else:
                        compare_value = static_value

                await _pace()

                # ── Evaluate ──
                if _evaluate_condition(condition, current, prev, three_ago, compare_value):
                    passed_tickers.append(ticker)
                else:
                    logger.debug(f"Scanner: {ticker} failed rule '{label}' (current={current}, compare={compare_value})")

            except Exception as e:
                logger.warning(f"Scanner: error evaluating rule '{label}' for {ticker}: {e}")
                # Drop tickers that can't be evaluated — keeping them
                # defeats the purpose of the filter. If we can't confirm
                # the indicator condition holds, don't trade on it.

        # Remove tickers that didn't pass
        removed = set(candidates.keys()) - set(passed_tickers)
        for t in removed:
            del candidates[t]

        logger.info(f"Scanner rule '{label}': {len(passed_tickers)} passed, {len(removed)} filtered out")

    logger.info(f"Scanner technical evaluation used ~{fmp_calls} FMP API calls")
    return list(candidates.values())


async def run_watchlist_scan() -> list[dict]:
    """
    Priority scan: evaluate watchlist tickers against all enabled profiles.
    Runs BEFORE the full universe scan to catch opportunities on tickers
    the user is already watching. Uses the same technical rule pipeline
    but skips the FMP screener step — goes straight to rule evaluation.
    """
    from app.services.fmp_service import (
        get_technical_snapshot, get_fundamentals, get_api_usage, get_volume_surge, get_quote,
    )

    usage = get_api_usage()
    if usage.get("rpm", 0) >= usage.get("rpm_limit", 300):
        logger.warning("Watchlist scan skipped: FMP API at hard rate limit")
        return []

    # Get watchlist tickers
    try:
        from app.models.watchlist_ticker import WatchlistTicker
        async with async_session() as db:
            result = await db.execute(select(WatchlistTicker))
            wl_tickers = [w.ticker for w in result.scalars().all()]
    except Exception:
        wl_tickers = []

    if not wl_tickers:
        return []

    logger.info(f"Watchlist scan: evaluating {len(wl_tickers)} tickers")

    # Build candidates from watchlist
    candidates = []
    for ticker in wl_tickers[:25]:
        try:
            quote = await get_quote(ticker)
            price = None
            change_pct = None
            if quote and isinstance(quote, list) and quote:
                price = quote[0].get("price")
                change_pct = quote[0].get("changesPercentage")
            candidates.append({
                "ticker": ticker,
                "source": "watchlist",
                "indicators": {"price": price, "change_pct": change_pct},
                "rules_passed": [],
            })
        except Exception:
            candidates.append({
                "ticker": ticker,
                "source": "watchlist",
                "indicators": {},
                "rules_passed": [],
            })

    # Evaluate each profile's technical rules against watchlist tickers
    profiles = await select_profiles_for_now()
    all_opportunities = []

    # Build a fake screener_map from quotes for _evaluate_technical_rules
    screener_map = {}
    for cand in candidates:
        screener_map[cand["ticker"]] = cand.get("indicators", {})

    ticker_list = [c["ticker"] for c in candidates]

    for profile in profiles:
        preset_name = profile.get("id", "")
        try:
            criteria = profile.get("criteria") or get_preset_criteria(preset_name)
        except Exception:
            continue

        rules = criteria.get("technical_rules", [])
        enabled_rules = [r for r in rules if r.get("enabled")]
        if not enabled_rules:
            continue

        # Run technical rules using the existing pipeline
        try:
            profile_survivors = await _evaluate_technical_rules(
                ticker_list, rules, screener_map
            )
        except Exception as e:
            logger.debug(f"Watchlist scan profile {preset_name} failed: {e}")
            continue

        # Volume filter
        vol_filter = criteria.get("volume_filter", {})
        if vol_filter.get("enabled") and profile_survivors:
            surge_mult = vol_filter.get("surge_multiplier", 1.5)
            vol_passed = []
            for cand in profile_survivors:
                try:
                    vol_data = await get_volume_surge(cand["ticker"], vol_filter.get("avg_period", 20))
                    ratio = vol_data.get("surge_ratio")
                    if ratio is not None and ratio >= surge_mult:
                        cand["indicators"]["volume_surge"] = vol_data
                        vol_passed.append(cand)
                except Exception:
                    vol_passed.append(cand)
            profile_survivors = vol_passed

        for cand in profile_survivors:
            cand["matched_profile"] = profile.get("name", preset_name)

        all_opportunities.extend(profile_survivors)

    # Deduplicate by ticker (keep first match)
    seen = set()
    unique = []
    for opp in all_opportunities:
        if opp["ticker"] not in seen:
            seen.add(opp["ticker"])
            unique.append(opp)

    logger.info(f"Watchlist scan: {len(unique)} opportunities from {len(wl_tickers)} tickers")
    return unique


async def run_scanner(profile_criteria: dict | None = None, profile_name: str | None = None, skip_actions: bool = False) -> list[dict]:
    """
    Full scanner pipeline. Optionally accepts criteria from a specific profile.
    When skip_actions=True, returns opportunities without creating PortfolioAction records
    (used by autonomous trading which handles execution itself).
    1. Build screener params from criteria and call FMP
    2. Cascading technical rule evaluation
    3. Volume surge filter (if enabled)
    4. Enrich with cached fundamentals
    5. AI analysis
    6. Create OPPORTUNITY actions (unless skip_actions)
    """
    from app.services.fmp_service import (
        run_screener, get_technical_snapshot, get_fundamentals,
        format_fundamentals_for_prompt, get_api_usage, get_volume_surge,
    )

    # Check if FMP API is available — only bail on hard ceiling, not soft.
    # The soft limit (240/min) is a pacing hint, not a stop sign. The
    # scanner pauses between indicator batches to let the rolling window
    # slide rather than abandoning the scan entirely.
    usage = get_api_usage()
    if usage.get("rpm", 0) >= usage.get("rpm_limit", 300):
        logger.warning("Scanner skipped: FMP API at hard rate limit")
        return []

    # 1. Get criteria — use profile override if provided, else saved criteria
    if profile_criteria:
        criteria = profile_criteria
        source = f"profile:{profile_name or 'custom'}"
    else:
        criteria = await get_scanner_criteria()
        active_preset = criteria.get("active_preset")
        if active_preset:
            criteria = get_preset_criteria(active_preset)
        source = f"preset:{criteria.get('active_preset', 'default')}"

    screener_cfg = criteria.get("screener", DEFAULT_SCANNER_CRITERIA["screener"])
    logger.info(
        f"Running scanner ({source}): "
        f"price>{screener_cfg.get('priceMoreThan')}, vol>{screener_cfg.get('volumeMoreThan')}, "
        f"cap>{screener_cfg.get('marketCapMoreThan')}"
    )

    # 2. Build screener params and call FMP
    screener_params = _build_screener_params(screener_cfg)
    logger.info(f"Scanner: calling FMP screener with params: {screener_params}")
    screener_results = await run_screener(screener_params)
    if not screener_results:
        logger.warning("Scanner: FMP screener returned None/empty — check FMP_API_KEY and API status")
        return []
    if not isinstance(screener_results, list):
        logger.warning(f"Scanner: FMP screener returned unexpected type: {type(screener_results)}")
        return []

    # Diagnostics: market cap range + sector diversity of the raw pool. If
    # every result is a $500B+ megacap the pool is rotten before any rules run.
    try:
        mcaps = [float(s.get("marketCap") or 0) for s in screener_results if s.get("marketCap")]
        sectors = {s.get("sector") for s in screener_results if s.get("sector")}
        if mcaps:
            logger.info(
                f"Scanner pool: {len(screener_results)} stocks | "
                f"mcap ${min(mcaps)/1e9:.1f}B–${max(mcaps)/1e9:.1f}B | "
                f"{len(sectors)} sectors"
            )
        else:
            logger.info(f"Scanner pool: {len(screener_results)} stocks (no mcap data)")
    except Exception:
        pass

    # 2b. Momentum post-filter — the screener's default sort is mcap desc, so
    # without this step the MAG 7 pass every profile. This narrows to actual
    # movers by today's %change before burning API calls on technical rules.
    momentum_cfg = criteria.get("momentum_filter", DEFAULT_SCANNER_CRITERIA.get("momentum_filter", {}))
    if momentum_cfg.get("enabled", True):
        min_change = float(momentum_cfg.get("min_change_pct", 0.5))
        top_n = int(momentum_cfg.get("top_n", 60))
        before = len(screener_results)
        screener_results = await _filter_by_momentum(
            screener_results, min_change_pct=min_change, top_n=top_n,
        )
        logger.info(
            f"Scanner momentum filter: {before} → {len(screener_results)} "
            f"(|Δ| ≥ {min_change}%, top {top_n})"
        )
        if screener_results:
            top_preview = ", ".join(
                f"{s['symbol']}({s.get('_change_pct', 0):+.1f}%)"
                for s in screener_results[:8]
            )
            logger.info(f"Scanner top movers: {top_preview}")

    logger.info(f"Scanner: {len(screener_results)} stocks from screener")

    # Build a map for quick lookup
    screener_map: dict[str, dict] = {}
    tickers: list[str] = []
    for stock in screener_results:
        sym = stock.get("symbol")
        if sym:
            screener_map[sym] = stock
            tickers.append(sym)

    # 3. Cascading technical evaluation
    technical_rules = criteria.get("technical_rules", DEFAULT_SCANNER_CRITERIA["technical_rules"])
    enabled_rules = [r for r in technical_rules if r.get("enabled")]

    if enabled_rules:
        survivors = await _evaluate_technical_rules(tickers, technical_rules, screener_map)
    else:
        # No technical rules enabled -- pass all through with basic snapshot
        survivors = [
            {"ticker": t, "screener_data": screener_map.get(t, {}), "indicators": {}}
            for t in tickers[:20]
        ]

    logger.info(f"Scanner: {len(survivors)} stocks survived technical rules")

    if not survivors:
        return []

    # 4. Volume surge filter
    vol_filter = criteria.get("volume_filter", DEFAULT_SCANNER_CRITERIA["volume_filter"])
    if vol_filter.get("enabled"):
        surge_mult = vol_filter.get("surge_multiplier", 1.5)
        avg_period = vol_filter.get("avg_period", 20)
        vol_survivors = []
        for cand in survivors:
            ticker = cand["ticker"]
            try:
                vol_data = await get_volume_surge(ticker, avg_period)
                cand["indicators"]["volume_surge"] = vol_data
                ratio = vol_data.get("surge_ratio")
                if ratio is not None and ratio >= surge_mult:
                    vol_survivors.append(cand)
                    logger.debug(f"Scanner: {ticker} volume surge {ratio:.2f}x >= {surge_mult}x")
                else:
                    logger.debug(f"Scanner: {ticker} volume surge {ratio}x < {surge_mult}x -- filtered")
            except Exception as e:
                logger.debug(f"Scanner: volume surge check failed for {ticker}: {e}")
                vol_survivors.append(cand)  # keep on failure
        survivors = vol_survivors
        logger.info(f"Scanner: {len(survivors)} stocks survived volume filter")

    # 4b. Gap filter (for gap_breakout profile)
    gap_filter = criteria.get("gap_filter", {})
    if gap_filter.get("enabled"):
        min_gap = gap_filter.get("min_gap_pct", 2.0)
        gap_survivors = []
        for cand in survivors:
            ticker = cand["ticker"]
            try:
                from app.services.fmp_service import get_historical_daily
                hist = await get_historical_daily(ticker, days=2)
                if hist and isinstance(hist, list) and len(hist) >= 2:
                    today_open = hist[0].get("open", 0)
                    prev_close = hist[1].get("close", 0)
                    if prev_close > 0:
                        gap_pct = (today_open - prev_close) / prev_close * 100
                        cand["indicators"]["gap_pct"] = round(gap_pct, 2)
                        if gap_pct >= min_gap:
                            gap_survivors.append(cand)
                            continue
                # No gap data — skip
            except Exception:
                pass
        survivors = gap_survivors
        logger.info(f"Scanner: {len(survivors)} stocks survived gap filter (>{min_gap}%)")

    # 4c. Drawdown filter (for dead_cat_bounce profile)
    dd_filter = criteria.get("drawdown_filter", {})
    if dd_filter.get("enabled"):
        min_dd = dd_filter.get("min_drawdown_pct", 15)
        lookback = dd_filter.get("lookback_days", 30)
        dd_survivors = []
        for cand in survivors:
            ticker = cand["ticker"]
            try:
                from app.services.fmp_service import get_historical_daily
                hist = await get_historical_daily(ticker, days=lookback)
                if hist and isinstance(hist, list) and len(hist) >= 5:
                    high_30d = max(d.get("high", 0) for d in hist)
                    current = hist[0].get("close", 0)
                    if high_30d > 0 and current > 0:
                        drawdown = (high_30d - current) / high_30d * 100
                        cand["indicators"]["drawdown_30d_pct"] = round(drawdown, 2)
                        if drawdown >= min_dd:
                            dd_survivors.append(cand)
                            continue
            except Exception:
                pass
        survivors = dd_survivors
        logger.info(f"Scanner: {len(survivors)} stocks survived drawdown filter (>{min_dd}% from 30d high)")

    if not survivors:
        return []

    # Cap at 15 for AI analysis
    survivors = survivors[:15]

    # 5. Enrich with cached fundamentals and fetch price if missing
    from app.services.fmp_service import get_quote
    for cand in survivors:
        ticker = cand["ticker"]
        # Get price if not in indicators
        if "price" not in cand.get("indicators", {}):
            try:
                quote = await get_quote(ticker)
                if quote and isinstance(quote, list) and len(quote) > 0:
                    cand["price"] = quote[0].get("price")
                    cand["volume"] = quote[0].get("volume")
                    cand["change_pct"] = quote[0].get("changesPercentage")
            except Exception:
                pass
        else:
            cand["price"] = cand["indicators"].get("price")

        fund = await get_fundamentals(ticker)
        if fund:
            cand["fundamentals_summary"] = format_fundamentals_for_prompt(fund)
        else:
            cand["fundamentals_summary"] = "No cached fundamentals."

    # 6. AI analysis
    logger.info(f"Scanner: sending {len(survivors)} candidates to AI for analysis")
    opportunities = await _ai_analyze_candidates(survivors)
    logger.info(f"Scanner: AI returned {len(opportunities)} opportunities")

    if not opportunities:
        logger.info("Scanner: no opportunities after AI analysis")
        return []

    # 7. Create PortfolioAction entries (unless called from autonomous trading)
    if skip_actions:
        logger.info(f"Scanner: skip_actions=True, returning {len(opportunities)} opportunities for autonomous execution")
        return opportunities

    created_actions = await _create_opportunity_actions(opportunities)

    # Log final stats
    final_usage = get_api_usage()
    logger.info(
        f"Scanner complete: {len(created_actions)} opportunities created. "
        f"FMP calls today: {final_usage['calls_today']}/{final_usage['limit']}"
    )
    return created_actions


async def _ai_analyze_candidates(candidates: list[dict]) -> list[dict]:
    """Send candidate list to Claude for AI-powered ranking and analysis.
    Enriched with all indicator data collected during cascading evaluation."""
    try:
        from app.services.ai_service import _call_claude_async
    except ImportError:
        logger.warning("AI service not available, returning candidates as-is")
        return [
            {
                "ticker": c.get("ticker", ""),
                "direction": "long",
                "confidence": 5,
                "reasoning": _build_fallback_reasoning(c),
                "suggested_price": c.get("price"),
                "setup_type": "scanner",
            }
            for c in candidates[:5]
        ]

    # Build concise candidate summaries with all indicator data
    candidate_text = ""
    for i, c in enumerate(candidates, 1):
        sd = c.get("screener_data", {})
        indicators = c.get("indicators", {})
        price = c.get("price") or sd.get("price", "N/A")

        line = f"\n{i}. {c.get('ticker')} - ${price}"

        # Append all collected indicators
        for ind_key, ind_val in indicators.items():
            if ind_key == "volume_surge" and isinstance(ind_val, dict):
                ratio = ind_val.get("surge_ratio")
                if ratio is not None:
                    line += f" | VolSurge: {ratio:.2f}x"
            elif ind_key == "macd" and isinstance(ind_val, dict):
                m = ind_val.get("macd")
                s = ind_val.get("signal")
                h = ind_val.get("histogram")
                if m is not None:
                    line += f" | MACD: {m:.3f}"
                if s is not None:
                    line += f" Sig: {s:.3f}"
                if h is not None:
                    line += f" Hist: {h:.3f}"
            elif ind_key == "bollinger" and isinstance(ind_val, dict):
                pp = ind_val.get("price_position")
                if pp is not None:
                    line += f" | Boll%: {pp:.2f}"
            elif isinstance(ind_val, (int, float)):
                line += f" | {ind_key}: {ind_val:.2f}"

        if sd.get("marketCap"):
            mcap = sd["marketCap"]
            if mcap >= 1e12:
                line += f" | Mkt Cap: ${mcap / 1e12:.1f}T"
            elif mcap >= 1e9:
                line += f" | Mkt Cap: ${mcap / 1e9:.1f}B"
            else:
                line += f" | Mkt Cap: ${mcap / 1e6:.0f}M"

        line += f"\n   {c.get('fundamentals_summary', 'No fundamentals.')}\n"
        candidate_text += line

    prompt = f"""You are Henry, an AI trading analyst running a stock scanner. Analyze these candidates and select the best trading opportunities.

CANDIDATES:
{candidate_text}

For each opportunity worth pursuing, provide a DETAILED analysis including:
- What the company does (1 sentence)
- The technical setup: what indicators are showing and why the timing is right
- The fundamental case: valuation, analyst sentiment, catalysts
- Risk factors: what could go wrong
- Entry strategy: suggested price level and sizing approach

Respond with a JSON array of opportunities. Each object:
{{"ticker": "AAPL", "direction": "long", "confidence": 7, "reasoning": "Apple (AAPL) is a $3T consumer tech company showing a momentum pullback to EMA 21 support at $172. RSI at 48 is cooling from overbought, ADX at 28 confirms the uptrend is intact. Analysts rate it Buy with a $195 consensus target (13% upside). Earnings in 3 weeks could be a catalyst. Risk: broad tech selloff if macro deteriorates. Entry near $172 support with a stop below $168.", "suggested_price": 172.00, "setup_type": "momentum_pullback", "signal_weights": {{"technical_strength": 0.0-1.0, "fundamental_value": 0.0-1.0, "thesis_quality": 0.0-1.0, "catalyst_proximity": 0.0-1.0, "risk_reward_ratio": 0.0-1.0, "memory_alignment": 0.0-1.0, "regime_fit": 0.0-1.0, "entry_timing": 0.0-1.0}}}}

Score each signal_weights dimension 0.0-1.0 based on how strongly it supports the trade.
Return only the top 5-8 best opportunities. Empty array if nothing compelling.
No markdown, no backticks. Just the JSON array."""

    try:
        from app.services.ai_provider import call_ai
        system = "You are Henry, an AI trading analyst. Analyze stock scanner candidates and return a JSON array of the best opportunities. Return ONLY valid JSON, no markdown."
        raw = await call_ai(
            system,
            prompt,
            function_name="screener_analysis",
            max_tokens=1500,
        )

        clean = raw.strip().replace("```json", "").replace("```", "").strip()
        # Extract JSON array even if wrapped in prose
        import re as _re
        json_match = _re.search(r'\[[\s\S]*\]', clean)
        if json_match:
            result = json.loads(json_match.group())
        else:
            result = json.loads(clean)
        if not isinstance(result, list):
            result = [result]
        return result

    except json.JSONDecodeError:
        logger.warning(f"Scanner AI: failed to parse JSON response: {raw[:300] if raw else 'empty'}")
        # Fallback: return top candidates without AI ranking
        return _fallback_opportunities(candidates)
    except Exception as e:
        logger.error(f"Scanner AI analysis failed: {e}", exc_info=True)
        # Fallback: return top candidates without AI ranking
        return _fallback_opportunities(candidates)


def _fallback_opportunities(candidates: list[dict]) -> list[dict]:
    """When AI analysis fails, return top candidates with basic reasoning."""
    results = []
    for c in candidates[:5]:
        ticker = c.get("ticker", "")
        if not ticker:
            continue
        results.append({
            "ticker": ticker,
            "direction": "long",
            "confidence": 5,
            "reasoning": _build_fallback_reasoning(c),
            "suggested_price": c.get("price"),
            "setup_type": "scanner",
        })
    return results


def _build_fallback_reasoning(candidate: dict) -> str:
    """Build detailed reasoning when AI service is unavailable."""
    ticker = candidate.get("ticker", "?")
    sd = candidate.get("screener_data", {})
    indicators = candidate.get("indicators", {})
    fund = candidate.get("fundamentals_summary", "")
    price = candidate.get("price") or sd.get("price")

    parts = [f"{sd.get('companyName', ticker)} ({ticker})"]

    # Add sector/industry context
    if sd.get("sector"):
        parts.append(f"{sd['sector']}" + (f" / {sd['industry']}" if sd.get("industry") else ""))

    # Price and market cap
    if price:
        parts.append(f"trading at ${price:.2f}")
    if sd.get("marketCap"):
        mc = sd["marketCap"]
        parts.append(f"Mkt cap ${mc/1e9:.1f}B" if mc >= 1e9 else f"Mkt cap ${mc/1e6:.0f}M")

    # Technical indicators
    ind_parts = []
    for key, val in indicators.items():
        if key in ("rsi_14", "rsi") and isinstance(val, (int, float)):
            ind_parts.append(f"RSI {val:.0f}")
        elif key in ("adx_14", "adx") and isinstance(val, (int, float)):
            ind_parts.append(f"ADX {val:.0f}")
        elif "sma" in key and isinstance(val, (int, float)):
            ind_parts.append(f"SMA {key.split('_')[-1]}: ${val:.2f}")
    if ind_parts:
        parts.append(f"Technical: {', '.join(ind_parts)}")

    # Fundamentals summary
    if fund and fund != "No cached fundamentals.":
        parts.append(fund[:200])

    return ". ".join(parts)


async def _create_opportunity_actions(opportunities: list[dict]) -> list[dict]:
    """Create PortfolioAction entries with action_type=OPPORTUNITY for each opportunity."""
    if not opportunities:
        return []

    created = []
    try:
        async with async_session() as db:
            # Get a default portfolio to attach opportunities to
            result = await db.execute(
                select(Portfolio).where(Portfolio.is_active == True).limit(1)
            )
            portfolio = result.scalar_one_or_none()
            if not portfolio:
                logger.warning("Scanner: no active portfolio found for opportunities")
                return []

            for opp in opportunities:
                ticker = opp.get("ticker", "")
                if not ticker:
                    continue

                confidence = min(max(int(opp.get("confidence", 5)), 1), 10)
                expiry = utcnow() + timedelta(hours=24)

                from app.services.decision_signals import validate_signal_weights
                action = PortfolioAction(
                    portfolio_id=portfolio.id,
                    ticker=ticker,
                    direction=opp.get("direction", "long"),
                    action_type="OPPORTUNITY",
                    suggested_price=opp.get("suggested_price"),
                    current_price=opp.get("suggested_price"),
                    confidence=confidence,
                    reasoning=opp.get("reasoning", "Scanner opportunity"),
                    trigger_type="SCANNER",
                    priority_score=round(1.5 * confidence, 1),  # Scanner weight = 1.5
                    expires_at=expiry,
                    signal_weights=validate_signal_weights(opp.get("signal_weights")),
                )
                db.add(action)
                await db.flush()
                # Phase 4.5 — surface adaptive-Kelly suggested size on
                # scanner opportunities so the user has a baseline when
                # approving. Strategy_id null because scanner opps aren't
                # tied to a specific strategy — Kelly will fall back to
                # fixed % of equity in that case.
                try:
                    from app.services.position_sizing import apply_sizing_to_action
                    await apply_sizing_to_action(db, action, strategy_id=None)
                except Exception:
                    pass
                created.append({
                    "ticker": ticker,
                    "direction": opp.get("direction", "long"),
                    "confidence": confidence,
                    "reasoning": opp.get("reasoning", ""),
                    "suggested_price": opp.get("suggested_price"),
                    "setup_type": opp.get("setup_type", "unknown"),
                    "expires_at": expiry.isoformat(),
                })

            await db.commit()

    except Exception as e:
        logger.error(f"Failed to create opportunity actions: {e}")

    return created


# ══════════════════════════════════════════════════════════════════════
# RESULTS & STATS
# ══════════════════════════════════════════════════════════════════════

async def get_scanner_results(limit: int = 20) -> list[dict]:
    """Return recent OPPORTUNITY actions (pending)."""
    try:
        async with async_session() as db:
            result = await db.execute(
                select(PortfolioAction)
                .where(
                    PortfolioAction.action_type == "OPPORTUNITY",
                    PortfolioAction.trigger_type == "SCANNER",
                    PortfolioAction.status == "pending",
                )
                .order_by(PortfolioAction.created_at.desc())
                .limit(limit)
            )
            actions = result.scalars().all()

            return [
                {
                    "id": a.id,
                    "ticker": a.ticker,
                    "direction": a.direction,
                    "confidence": a.confidence,
                    "reasoning": a.reasoning,
                    "suggested_price": a.suggested_price,
                    "current_price": a.current_price,
                    "priority_score": a.priority_score,
                    "status": a.status,
                    "expires_at": a.expires_at.isoformat() if a.expires_at else None,
                    "created_at": a.created_at.isoformat(),
                }
                for a in actions
            ]
    except Exception as e:
        logger.error(f"Error fetching scanner results: {e}")
        return []


async def get_scanner_history(limit: int = 50) -> list[dict]:
    """Return past scanner results (all statuses) with outcomes."""
    try:
        async with async_session() as db:
            result = await db.execute(
                select(PortfolioAction)
                .where(
                    PortfolioAction.action_type == "OPPORTUNITY",
                    PortfolioAction.trigger_type == "SCANNER",
                )
                .order_by(PortfolioAction.created_at.desc())
                .limit(limit)
            )
            actions = result.scalars().all()

            return [
                {
                    "id": a.id,
                    "ticker": a.ticker,
                    "direction": a.direction,
                    "confidence": a.confidence,
                    "reasoning": a.reasoning,
                    "suggested_price": a.suggested_price,
                    "current_price": a.current_price,
                    "priority_score": a.priority_score,
                    "status": a.status,
                    "outcome_pnl": a.outcome_pnl,
                    "outcome_correct": a.outcome_correct,
                    "expires_at": a.expires_at.isoformat() if a.expires_at else None,
                    "created_at": a.created_at.isoformat(),
                    "resolved_at": a.resolved_at.isoformat() if a.resolved_at else None,
                }
                for a in actions
            ]
    except Exception as e:
        logger.error(f"Error fetching scanner history: {e}")
        return []


async def get_scanner_stats() -> dict:
    """Compute scanner accuracy stats from outcome tracking."""
    try:
        async with async_session() as db:
            # Total opportunities
            total_result = await db.execute(
                select(func.count(PortfolioAction.id)).where(
                    PortfolioAction.action_type == "OPPORTUNITY",
                    PortfolioAction.trigger_type == "SCANNER",
                )
            )
            total = total_result.scalar() or 0

            # Approved / acted upon
            approved_result = await db.execute(
                select(func.count(PortfolioAction.id)).where(
                    PortfolioAction.action_type == "OPPORTUNITY",
                    PortfolioAction.trigger_type == "SCANNER",
                    PortfolioAction.status == "approved",
                )
            )
            approved = approved_result.scalar() or 0

            # With outcomes
            outcome_result = await db.execute(
                select(PortfolioAction).where(
                    PortfolioAction.action_type == "OPPORTUNITY",
                    PortfolioAction.trigger_type == "SCANNER",
                    PortfolioAction.outcome_correct.isnot(None),
                )
            )
            outcomes = outcome_result.scalars().all()

            correct = sum(1 for o in outcomes if o.outcome_correct)
            total_outcomes = len(outcomes)
            avg_pnl = (
                sum(o.outcome_pnl or 0 for o in outcomes) / total_outcomes
                if total_outcomes > 0 else 0
            )
            accuracy = (correct / total_outcomes * 100) if total_outcomes > 0 else 0

            # Pending (not expired)
            pending_result = await db.execute(
                select(func.count(PortfolioAction.id)).where(
                    PortfolioAction.action_type == "OPPORTUNITY",
                    PortfolioAction.trigger_type == "SCANNER",
                    PortfolioAction.status == "pending",
                    PortfolioAction.expires_at > utcnow(),
                )
            )
            pending = pending_result.scalar() or 0

            # Expired
            expired_result = await db.execute(
                select(func.count(PortfolioAction.id)).where(
                    PortfolioAction.action_type == "OPPORTUNITY",
                    PortfolioAction.trigger_type == "SCANNER",
                    PortfolioAction.status == "expired",
                )
            )
            expired = expired_result.scalar() or 0

            # By confidence level
            confidence_breakdown = {}
            if outcomes:
                for o in outcomes:
                    conf = o.confidence
                    if conf not in confidence_breakdown:
                        confidence_breakdown[conf] = {"total": 0, "correct": 0}
                    confidence_breakdown[conf]["total"] += 1
                    if o.outcome_correct:
                        confidence_breakdown[conf]["correct"] += 1

            return {
                "total_opportunities": total,
                "approved": approved,
                "pending_active": pending,
                "expired": expired,
                "outcomes_tracked": total_outcomes,
                "correct": correct,
                "accuracy_pct": round(accuracy, 1),
                "avg_pnl_pct": round(avg_pnl, 2),
                "by_confidence": confidence_breakdown,
            }

    except Exception as e:
        logger.error(f"Error computing scanner stats: {e}")
        return {
            "total_opportunities": 0,
            "error": str(e),
        }
