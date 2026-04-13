"""
Market Regime Context
=====================

Two scheduled jobs that snapshot the macro environment as structured
memories Henry can retrieve:

  - pre_market_job (8:30 AM ET) — overnight gaps, sector setup, calendar
  - eod_recap_job  (4:30 PM ET) — what actually happened, regime confirm

Both write a portfolio-wide memory (ticker=null) so semantic retrieval
surfaces them on any signal evaluation during the relevant window.

A fast `current_regime_classification()` helper reads a process-local
cache + HenryStats fallback, used by `_build_system_prompt` to inject
the regime label into every Claude/Gemini call without an FMP round-trip.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

from app.utils.utc import utcnow

logger = logging.getLogger(__name__)


# ─── Regime classification heuristic ────────────────────────────────────────
#
# Pure Python — no AI call. Inputs are observable market metrics; output is
# a short label the system prompt can quote. Mirrors the ladder in the
# intelligence-upgrade brief so behavior is auditable.

REGIME_LABELS = {
    "low_vol_uptrend": "low-vol uptrend, favor momentum entries",
    "volatile_uptrend": "volatile uptrend, tighten stops",
    "quiet_downtrend": "quiet downtrend, favor mean reversion",
    "high_vol_downtrend": "high-vol downtrend, reduce exposure",
    "choppy_range": "choppy / range-bound, reduce position sizing",
    "unknown": "regime undetermined (insufficient data)",
}


def classify_regime(
    spy_close: Optional[float],
    spy_20ema: Optional[float],
    vix_level: Optional[float],
    spy_adx: Optional[float] = None,
) -> tuple[str, str]:
    """
    Returns (key, human-readable label). Falls back to 'unknown' on
    missing inputs rather than guessing — Henry would rather know the
    regime is unknown than be lied to.
    """
    if spy_close is None or spy_20ema is None or vix_level is None:
        return "unknown", REGIME_LABELS["unknown"]

    # Choppy regime gates everything else — if SPY isn't trending, the
    # uptrend/downtrend split is misleading. Only invoke if ADX provided.
    if spy_adx is not None and spy_adx < 20:
        return "choppy_range", REGIME_LABELS["choppy_range"]

    above_ema = spy_close > spy_20ema
    high_vol = vix_level > 25
    low_vol = vix_level < 18

    if above_ema and low_vol:
        return "low_vol_uptrend", REGIME_LABELS["low_vol_uptrend"]
    if above_ema and high_vol:
        return "volatile_uptrend", REGIME_LABELS["volatile_uptrend"]
    if not above_ema and low_vol:
        return "quiet_downtrend", REGIME_LABELS["quiet_downtrend"]
    if not above_ema and high_vol:
        return "high_vol_downtrend", REGIME_LABELS["high_vol_downtrend"]
    # Mid-VIX, on either side — call it the trend without a vol qualifier.
    return (
        "low_vol_uptrend" if above_ema else "quiet_downtrend",
        REGIME_LABELS["low_vol_uptrend"] if above_ema else REGIME_LABELS["quiet_downtrend"],
    )


# ─── In-process cache for the prompt-injection helper ───────────────────────

_REGIME_CACHE: dict = {
    "label": None,         # human-readable string
    "key": None,           # short key (low_vol_uptrend, etc.)
    "spy_close": None,
    "spy_20ema": None,
    "vix": None,
    "spy_adx": None,
    "computed_at": 0.0,    # epoch
}
_CACHE_TTL_SECONDS = 60 * 60 * 6  # 6h — refreshed by the jobs but bounded


def _set_cache(label: str, key: str, snapshot: dict) -> None:
    _REGIME_CACHE.update({
        "label": label,
        "key": key,
        "spy_close": snapshot.get("spy_close"),
        "spy_20ema": snapshot.get("spy_20ema"),
        "vix": snapshot.get("vix"),
        "spy_adx": snapshot.get("spy_adx"),
        "computed_at": time.time(),
    })


async def current_regime_classification() -> Optional[dict]:
    """
    Fast, prompt-friendly regime snapshot. Returns dict with:
      label, key, spy_close, spy_20ema, vix, spy_adx, computed_at_iso
    or None when no regime has been computed yet (no cache + no stat row).

    Order of resolution:
      1. In-process cache (fresh)
      2. HenryStats(stat_type='market_regime') latest row
      3. None
    """
    # Cache first
    if (
        _REGIME_CACHE["label"]
        and time.time() - _REGIME_CACHE["computed_at"] < _CACHE_TTL_SECONDS
    ):
        return {
            "label": _REGIME_CACHE["label"],
            "key": _REGIME_CACHE["key"],
            "spy_close": _REGIME_CACHE["spy_close"],
            "spy_20ema": _REGIME_CACHE["spy_20ema"],
            "vix": _REGIME_CACHE["vix"],
            "spy_adx": _REGIME_CACHE["spy_adx"],
            "computed_at_iso": datetime.fromtimestamp(
                _REGIME_CACHE["computed_at"], tz=timezone.utc
            ).isoformat(),
        }

    # DB fallback
    try:
        from sqlalchemy import select
        from app.database import async_session
        from app.models import HenryStats

        async with async_session() as db:
            row = (
                await db.execute(
                    select(HenryStats)
                    .where(HenryStats.stat_type == "market_regime")
                    .order_by(HenryStats.computed_at.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            if row and row.data:
                d = row.data
                # Warm the cache so the next call is free
                _set_cache(d.get("label", ""), d.get("key", ""), d)
                return {
                    "label": d.get("label"),
                    "key": d.get("key"),
                    "spy_close": d.get("spy_close"),
                    "spy_20ema": d.get("spy_20ema"),
                    "vix": d.get("vix"),
                    "spy_adx": d.get("spy_adx"),
                    "computed_at_iso": (
                        row.computed_at.isoformat() + "Z" if row.computed_at else None
                    ),
                }
    except Exception as e:
        logger.debug(f"current_regime_classification DB fallback failed: {e}")
    return None


# ─── Data pulls ─────────────────────────────────────────────────────────────


async def _fetch_spy_20ema() -> Optional[float]:
    """SPY 20-period daily EMA via FMP technical indicator endpoint."""
    try:
        from app.services import fmp_service
        data = await fmp_service.get_technical_indicator(
            "SPY", "ema", period=20, interval="daily"
        )
        if isinstance(data, list) and data:
            # FMP returns most-recent first; field name varies by version
            row = data[0]
            return float(row.get("ema") or row.get("value") or 0.0) or None
    except Exception as e:
        logger.debug(f"SPY 20EMA fetch failed: {e}")
    return None


async def _fetch_spy_adx() -> Optional[float]:
    """SPY ADX via FMP. Used to detect choppy/range-bound markets."""
    try:
        from app.services import fmp_service
        data = await fmp_service.get_technical_indicator(
            "SPY", "adx", period=14, interval="daily"
        )
        if isinstance(data, list) and data:
            row = data[0]
            return float(row.get("adx") or row.get("value") or 0.0) or None
    except Exception as e:
        logger.debug(f"SPY ADX fetch failed: {e}")
    return None


async def _fetch_quote(ticker: str) -> Optional[dict]:
    try:
        from app.services import fmp_service
        data = await fmp_service.get_quote(ticker)
        if isinstance(data, list) and data:
            return data[0]
        if isinstance(data, dict):
            return data
    except Exception as e:
        logger.debug(f"Quote fetch failed for {ticker}: {e}")
    return None


async def _fetch_sector_top_bottom(top_n: int = 3) -> tuple[list, list]:
    """Returns (leaders, laggards) — list of (sector, change_pct) tuples."""
    try:
        from app.services import fmp_service
        data = await fmp_service.get_sector_performance()
        if isinstance(data, list) and data:
            # Normalize: rows look like {"sector": "...", "changesPercentage": ...}
            rows = []
            for r in data:
                sector = r.get("sector") or r.get("name")
                pct = r.get("changesPercentage") or r.get("change_pct") or r.get("change")
                if sector is None or pct is None:
                    continue
                try:
                    rows.append((sector, float(pct)))
                except (TypeError, ValueError):
                    continue
            rows.sort(key=lambda x: x[1], reverse=True)
            return rows[:top_n], rows[-top_n:][::-1]
    except Exception as e:
        logger.debug(f"Sector performance fetch failed: {e}")
    return [], []


async def _fetch_earnings_today_and_week() -> tuple[list[str], list[str]]:
    """Returns (today's tickers, week's tickers excluding today)."""
    try:
        from app.services import fmp_service
        today = datetime.now(tz=timezone.utc).date()
        end = today + timedelta(days=7)
        data = await fmp_service.get_earnings_calendar(
            today.isoformat(), end.isoformat()
        )
        if not isinstance(data, list):
            return [], []
        today_tickers: list[str] = []
        week_tickers: list[str] = []
        for r in data:
            t = r.get("symbol") or r.get("ticker")
            d = r.get("date")
            if not t or not d:
                continue
            if d.startswith(today.isoformat()):
                today_tickers.append(t)
            else:
                week_tickers.append(t)
        return today_tickers[:20], week_tickers[:20]
    except Exception as e:
        logger.debug(f"Earnings calendar fetch failed: {e}")
        return [], []


async def _fetch_economic_today_week() -> list[str]:
    """High-level event names for today + this week. Filter to common big-name events."""
    try:
        from app.services import fmp_service
        data = await fmp_service.get_economic_calendar()
        if not isinstance(data, list):
            return []
        today = datetime.now(tz=timezone.utc).date()
        end = today + timedelta(days=7)
        names: list[str] = []
        # Filter to events flagged high-impact when the API provides it
        big_keywords = {"FOMC", "CPI", "PCE", "Nonfarm", "Payrolls", "GDP", "Powell", "Unemployment"}
        for r in data:
            d = (r.get("date") or "")[:10]
            if not d:
                continue
            try:
                event_date = datetime.fromisoformat(d).date()
            except ValueError:
                continue
            if not (today <= event_date <= end):
                continue
            ev = r.get("event") or ""
            impact = (r.get("impact") or "").lower()
            if impact == "high" or any(k in ev for k in big_keywords):
                names.append(f"{event_date.isoformat()}: {ev}")
        return names[:8]
    except Exception as e:
        logger.debug(f"Economic calendar fetch failed: {e}")
        return []


async def _fetch_open_position_tickers() -> list[str]:
    """Tickers Henry currently has live exposure to (for context line)."""
    try:
        from sqlalchemy import select
        from app.database import async_session
        from app.models import PortfolioHolding

        async with async_session() as db:
            rows = (
                await db.execute(
                    select(PortfolioHolding.ticker)
                    .where(PortfolioHolding.is_active == True)
                )
            ).all()
            return sorted({r[0] for r in rows if r[0]})
    except Exception:
        return []


# ─── Job entrypoints ────────────────────────────────────────────────────────


async def pre_market_job() -> Optional[str]:
    """
    Run at 8:30 AM ET. Pulls macro snapshot, classifies regime, saves a
    portfolio-wide memory + a HenryStats(stat_type='market_regime') row.
    Returns the saved memory content (or None on full failure).
    """
    return await _build_and_save_regime("pre_market_context", session_label="Pre-market")


async def eod_recap_job() -> Optional[str]:
    """
    Run at 4:30 PM ET. Same snapshot but framed as a recap of the closed
    session. Updates the regime cache + persists a fresh stat row so the
    next pre-market starts with yesterday's regime as fallback.
    """
    return await _build_and_save_regime("eod_recap", session_label="EOD recap")


async def _build_and_save_regime(source_tag: str, session_label: str) -> Optional[str]:
    from app.services.ai_service import save_memory
    from sqlalchemy import select, delete, and_
    from app.database import async_session
    from app.models import HenryStats

    # Pull everything in parallel — these are all independent FMP calls.
    import asyncio

    spy_quote, qqq_quote, vix_quote, spy_20ema, spy_adx, sectors, earnings, econ, holdings = await asyncio.gather(
        _fetch_quote("SPY"),
        _fetch_quote("QQQ"),
        _fetch_quote("^VIX"),
        _fetch_spy_20ema(),
        _fetch_spy_adx(),
        _fetch_sector_top_bottom(3),
        _fetch_earnings_today_and_week(),
        _fetch_economic_today_week(),
        _fetch_open_position_tickers(),
        return_exceptions=True,
    )

    def _safe(v):
        return None if isinstance(v, Exception) else v

    spy_quote = _safe(spy_quote) or {}
    qqq_quote = _safe(qqq_quote) or {}
    vix_quote = _safe(vix_quote) or {}
    spy_20ema = _safe(spy_20ema)
    spy_adx = _safe(spy_adx)
    sectors_pair = _safe(sectors) or ([], [])
    earnings_pair = _safe(earnings) or ([], [])
    econ_events = _safe(econ) or []
    holdings_list = _safe(holdings) or []

    spy_price = spy_quote.get("price") if isinstance(spy_quote, dict) else None
    spy_change_pct = spy_quote.get("changesPercentage") if isinstance(spy_quote, dict) else None
    qqq_change_pct = qqq_quote.get("changesPercentage") if isinstance(qqq_quote, dict) else None
    vix_level = vix_quote.get("price") if isinstance(vix_quote, dict) else None
    vix_change_pct = vix_quote.get("changesPercentage") if isinstance(vix_quote, dict) else None

    leaders, laggards = sectors_pair
    earnings_today, earnings_week = earnings_pair

    regime_key, regime_label = classify_regime(
        spy_close=spy_price,
        spy_20ema=spy_20ema,
        vix_level=vix_level,
        spy_adx=spy_adx,
    )

    # ─── Compose the structured memory content ────────────────────────────
    today = datetime.now(tz=timezone.utc).date().isoformat()
    parts: list[str] = [f"{session_label} {today}:"]
    if spy_price is not None:
        parts.append(
            f"SPY ${spy_price:.2f} ({_fmt_pct(spy_change_pct)})"
            + (f", QQQ {_fmt_pct(qqq_change_pct)}" if qqq_change_pct is not None else "")
        )
    if vix_level is not None:
        parts.append(
            f"VIX {vix_level:.1f} ({_fmt_pct(vix_change_pct)})"
        )
    if spy_20ema is not None and spy_price is not None:
        rel = "above" if spy_price > spy_20ema else "below"
        parts.append(f"SPY {rel} 20EMA (${spy_20ema:.2f})")
    if spy_adx is not None:
        parts.append(f"SPY ADX {spy_adx:.1f}")

    if leaders:
        parts.append(
            "Leaders: " + ", ".join(f"{s} {_fmt_pct(p)}" for s, p in leaders)
        )
    if laggards:
        parts.append(
            "Laggards: " + ", ".join(f"{s} {_fmt_pct(p)}" for s, p in laggards)
        )
    if earnings_today:
        parts.append("Earnings today: " + ", ".join(earnings_today[:10]))
    if earnings_week:
        parts.append("Earnings this week: " + ", ".join(earnings_week[:10]))
    if econ_events:
        parts.append("Macro events: " + " | ".join(econ_events[:5]))
    if holdings_list:
        parts.append("Open exposure: " + ", ".join(holdings_list[:12]))
    parts.append(f"Regime: {regime_label}")

    content = "\n".join(parts)

    # ─── Persist as memory + stat row ─────────────────────────────────────
    try:
        await save_memory(
            content=content,
            memory_type="observation",
            ticker=None,
            strategy_id=None,
            importance=7,
            source=source_tag,
        )
    except Exception as e:
        logger.error(f"{source_tag}: save_memory failed: {e}")

    snapshot = {
        "label": regime_label,
        "key": regime_key,
        "spy_close": spy_price,
        "spy_20ema": spy_20ema,
        "vix": vix_level,
        "spy_adx": spy_adx,
        "spy_change_pct": spy_change_pct,
        "qqq_change_pct": qqq_change_pct,
        "vix_change_pct": vix_change_pct,
        "leaders": leaders,
        "laggards": laggards,
        "earnings_today": earnings_today,
        "earnings_week": earnings_week,
        "econ_events": econ_events,
        "holdings": holdings_list,
        "source": source_tag,
        "computed_at_iso": utcnow().isoformat() + "Z",
    }

    try:
        async with async_session() as db:
            await db.execute(
                delete(HenryStats).where(
                    and_(
                        HenryStats.stat_type == "market_regime",
                        HenryStats.strategy.is_(None),
                        HenryStats.ticker.is_(None),
                        HenryStats.portfolio_id.is_(None),
                    )
                )
            )
            db.add(HenryStats(
                stat_type="market_regime",
                strategy=None,
                ticker=None,
                portfolio_id=None,
                data=snapshot,
                period_days=0,
                computed_at=utcnow(),
            ))
            await db.commit()
    except Exception as e:
        logger.error(f"{source_tag}: HenryStats persist failed: {e}")

    _set_cache(regime_label, regime_key, snapshot)
    logger.info(f"{source_tag} job complete — regime: {regime_key}")
    return content


def _fmt_pct(v) -> str:
    if v is None:
        return "?"
    try:
        f = float(v)
        return f"{f:+.2f}%"
    except (TypeError, ValueError):
        return "?"
