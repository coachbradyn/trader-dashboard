"""
Options Strategy Selector
=========================
Pure scoring functions for options strategies.  Given structured inputs
(direction, confidence, win_rate, VIX, IV rank, chain data, etc.) each
function returns a (score, recommendation) pair.  Scores are in [0.0, 1.0].

No database access, no network calls — all computation.  The higher-level
`select_options_strategy` coroutine (Step 2B) composes these with data
sources.

Chain data shape (from options_service.get_options_chain):
    {
        "ticker": str,
        "underlying_price": float | None,
        "expirations": [iso_date, ...],
        "by_expiration": {
            iso_date: {
                "calls": [ { option_symbol, strike, bid, ask, last, volume,
                             open_interest, iv, delta, gamma, theta, vega }, ... ],
                "puts":  [ ... ]
            }
        }
    }
"""
from __future__ import annotations

import logging
import math
from datetime import date as date_type
from typing import Any

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════════

def _dte(exp_iso: str) -> int:
    """Days to expiration from today (UTC/local date)."""
    try:
        y, m, d = exp_iso.split("-")
        return max((date_type(int(y), int(m), int(d)) - date_type.today()).days, 0)
    except Exception:
        return 0


def _pick_expiry(by_expiration: dict, target_dte: int) -> str | None:
    """Return the iso_date whose DTE is closest to target_dte. None if empty."""
    if not by_expiration:
        return None
    exps = list(by_expiration.keys())
    return min(exps, key=lambda e: abs(_dte(e) - target_dte))


def _mid(row: dict) -> float | None:
    """Mid-price from bid/ask, falling back to last if one side is missing."""
    bid, ask, last = row.get("bid"), row.get("ask"), row.get("last")
    if bid is not None and ask is not None and bid > 0 and ask > 0:
        return (bid + ask) / 2.0
    return last


def _first_otm_call(rows: list[dict], spot: float, offset: int = 1) -> dict | None:
    """Return the `offset`-th OTM call (call strike > spot). Rows are sorted ascending."""
    otm = [r for r in rows if r.get("strike") is not None and r["strike"] > spot]
    if not otm:
        return None
    idx = min(max(offset - 1, 0), len(otm) - 1)
    return otm[idx]


def _first_otm_put(rows: list[dict], spot: float, offset: int = 1) -> dict | None:
    """Return the `offset`-th OTM put (put strike < spot). Rows are sorted ascending,
    so we want the ones ending just below spot — take them from the right."""
    otm = [r for r in rows if r.get("strike") is not None and r["strike"] < spot]
    if not otm:
        return None
    # Closest OTM put to spot is the last row that's still < spot.
    idx = max(len(otm) - offset, 0)
    return otm[idx]


def _nearest_strike(rows: list[dict], target: float) -> dict | None:
    rows = [r for r in rows if r.get("strike") is not None]
    if not rows:
        return None
    return min(rows, key=lambda r: abs(r["strike"] - target))


def _greeks(row: dict) -> dict:
    return {
        "delta": row.get("delta"),
        "gamma": row.get("gamma"),
        "theta": row.get("theta"),
        "vega": row.get("vega"),
    }


def _sum_greeks(legs: list[tuple[dict, int]]) -> dict:
    """Sum Greeks across legs. Each leg is (row, signed_qty) where signed_qty
    is positive for long, negative for short."""
    out = {"delta": 0.0, "gamma": 0.0, "theta": 0.0, "vega": 0.0}
    any_set = {k: False for k in out}
    for row, qty in legs:
        for k in out:
            v = row.get(k)
            if v is None:
                continue
            out[k] += float(v) * qty
            any_set[k] = True
    return {k: (round(v, 4) if any_set[k] else None) for k, v in out.items()}


def _clamp01(x: float) -> float:
    if x < 0.0:
        return 0.0
    if x > 1.0:
        return 1.0
    return x


# ══════════════════════════════════════════════════════════════════════
# Scoring functions
# ══════════════════════════════════════════════════════════════════════

def score_long_option(
    direction: str,
    confidence: float,
    win_rate: float,
    vix: float,
    iv_rank: float,
    avg_hold_days: float,
    spot_price: float,
    chain_data: dict,
) -> tuple[float, dict | None]:
    """Score a long call or long put.

    Rejected if confidence < 7, win_rate < 0.70, or vix > 25 (long premium
    gets crushed in IV expansion when vol is already elevated).

    Score = confidence/10 * win_rate * (1 - iv_rank/100)
    """
    if confidence < 7 or win_rate < 0.70 or vix > 25:
        return 0.0, None
    if not spot_price or spot_price <= 0:
        return 0.0, None

    by_exp = (chain_data or {}).get("by_expiration") or {}
    if not by_exp:
        return 0.0, None

    target_dte = max(21, int(round(avg_hold_days * 3))) if avg_hold_days else 21
    exp = _pick_expiry(by_exp, target_dte)
    if not exp:
        return 0.0, None

    bucket = by_exp[exp]
    is_call = direction.lower() in ("bullish", "long", "up", "call", "buy")
    rows = bucket["calls"] if is_call else bucket["puts"]
    row = _first_otm_call(rows, spot_price, offset=1) if is_call else _first_otm_put(rows, spot_price, offset=1)
    if not row:
        return 0.0, None

    premium = _mid(row)
    if premium is None or premium <= 0:
        return 0.0, None

    score = _clamp01((confidence / 10.0) * win_rate * (1.0 - iv_rank / 100.0))

    strategy_type = "long_call" if is_call else "long_put"
    strike = row["strike"]
    breakeven = (strike + premium) if is_call else (strike - premium)
    max_loss = premium * 100.0  # per contract

    rec = {
        "score": round(score, 4),
        "strategy_type": strategy_type,
        "expiration": exp,
        "dte": _dte(exp),
        "legs": [{
            "action": "buy",
            "type": "call" if is_call else "put",
            "strike": strike,
            "option_symbol": row.get("option_symbol"),
            "premium": premium,
            "quantity": 1,
        }],
        "net_debit": premium,
        "max_risk": max_loss,
        "max_reward": None,  # unlimited (call) / strike-premium (put)
        "breakeven": round(breakeven, 2),
        "greeks": _greeks(row),
    }
    return score, rec


def score_vertical_spread(
    direction: str,
    confidence: float,
    win_rate: float,
    vix: float,
    iv_rank: float,
    expected_move_pct: float,
    spot_price: float,
    chain_data: dict,
) -> tuple[float, dict | None]:
    """Score a vertical debit spread (bull call or bear put).

    Rejected if confidence < 6 or vix < 20 (spreads shine when IV is elevated
    so the short leg subsidises the long).

    Score = confidence/10 * win_rate * iv_rank/100
    """
    if confidence < 6 or vix < 20:
        return 0.0, None
    if not spot_price or spot_price <= 0:
        return 0.0, None
    if not expected_move_pct or expected_move_pct <= 0:
        return 0.0, None

    by_exp = (chain_data or {}).get("by_expiration") or {}
    exp = _pick_expiry(by_exp, 35)
    if not exp:
        return 0.0, None

    bucket = by_exp[exp]
    is_call = direction.lower() in ("bullish", "long", "up", "call", "buy")
    rows = bucket["calls"] if is_call else bucket["puts"]
    if not rows:
        return 0.0, None

    # Long leg: ATM (for call spread) or first ITM — roughly nearest to spot
    long_row = _nearest_strike(rows, spot_price)
    if not long_row:
        return 0.0, None

    # Short leg target
    if is_call:
        short_target = spot_price * (1.0 + expected_move_pct)
    else:
        short_target = spot_price * (1.0 - expected_move_pct)
    short_row = _nearest_strike(rows, short_target)
    if not short_row or short_row["strike"] == long_row["strike"]:
        return 0.0, None

    # Direction sanity: for bull call, short strike > long strike; bear put, short < long
    if is_call and short_row["strike"] <= long_row["strike"]:
        return 0.0, None
    if (not is_call) and short_row["strike"] >= long_row["strike"]:
        return 0.0, None

    long_prem = _mid(long_row)
    short_prem = _mid(short_row)
    if long_prem is None or short_prem is None or long_prem <= 0 or short_prem <= 0:
        return 0.0, None

    net_debit = long_prem - short_prem
    if net_debit <= 0:
        return 0.0, None

    width = abs(short_row["strike"] - long_row["strike"])
    max_profit = (width - net_debit) * 100.0
    max_loss = net_debit * 100.0
    breakeven = (long_row["strike"] + net_debit) if is_call else (long_row["strike"] - net_debit)

    score = _clamp01((confidence / 10.0) * win_rate * (iv_rank / 100.0))

    strategy_type = "bull_call_spread" if is_call else "bear_put_spread"
    net_greeks = _sum_greeks([(long_row, 1), (short_row, -1)])

    rec = {
        "score": round(score, 4),
        "strategy_type": strategy_type,
        "expiration": exp,
        "dte": _dte(exp),
        "legs": [
            {
                "action": "buy",
                "type": "call" if is_call else "put",
                "strike": long_row["strike"],
                "option_symbol": long_row.get("option_symbol"),
                "premium": long_prem,
                "quantity": 1,
            },
            {
                "action": "sell",
                "type": "call" if is_call else "put",
                "strike": short_row["strike"],
                "option_symbol": short_row.get("option_symbol"),
                "premium": short_prem,
                "quantity": 1,
            },
        ],
        "net_debit": round(net_debit, 4),
        "max_risk": round(max_loss, 2),
        "max_reward": round(max_profit, 2),
        "breakeven": round(breakeven, 2),
        "width": width,
        "greeks": net_greeks,
    }
    return score, rec


def score_covered_call(
    spot_price: float,
    shares_held: int,
    confidence: float,
    iv_rank: float,
    chain_data: dict,
    atr: float | None = None,
) -> tuple[float, dict | None]:
    """Score a covered call.

    Rejected if shares_held < 100 or confidence > 6 (too bullish — don't cap
    the upside).  Requires at least one owned contract's worth of equity.

    Strike: 1-2 stdev OTM (use ATR if available, else 3%).
    Expiry: ~21 DTE.
    Score = theta * DTE * (1 - |delta|)  where |delta| ≈ prob ITM.
    """
    if shares_held < 100 or confidence > 6:
        return 0.0, None
    if not spot_price or spot_price <= 0:
        return 0.0, None

    by_exp = (chain_data or {}).get("by_expiration") or {}
    exp = _pick_expiry(by_exp, 21)
    if not exp:
        return 0.0, None

    bucket = by_exp[exp]
    calls = bucket.get("calls") or []
    if not calls:
        return 0.0, None

    # OTM distance: ATR if provided, else 3% of spot
    otm_offset = atr if (atr and atr > 0) else (spot_price * 0.03)
    target_strike = spot_price + otm_offset
    row = _nearest_strike([r for r in calls if r["strike"] > spot_price], target_strike)
    if not row:
        # Fall back to first OTM call
        row = _first_otm_call(calls, spot_price, offset=1)
    if not row:
        return 0.0, None

    premium = _mid(row)
    if premium is None or premium <= 0:
        return 0.0, None

    contracts = shares_held // 100
    dte = _dte(exp)
    theta = row.get("theta")  # per-day, negative for long option / we're short it
    delta = row.get("delta")

    if theta is None or delta is None:
        return 0.0, None

    prob_itm = min(abs(float(delta)), 1.0)
    # theta is negative for the option buyer; as the writer, time decay is
    # a positive force. Score off |theta|.
    raw_score = abs(float(theta)) * max(dte, 1) * (1.0 - prob_itm)
    # Normalise — theta is a small number; at reasonable values (theta=0.05,
    # dte=21, prob=0.3) this is ~0.735. Clamp to [0, 1].
    score = _clamp01(raw_score)

    premium_collected = premium * 100.0 * contracts
    max_loss = None  # unlimited (stock drops), but covered — downside is equity's

    # For covered calls, Greeks reported are for the short option position
    net_greeks = _sum_greeks([(row, -contracts)])

    rec = {
        "score": round(score, 4),
        "strategy_type": "covered_call",
        "expiration": exp,
        "dte": dte,
        "legs": [{
            "action": "sell",
            "type": "call",
            "strike": row["strike"],
            "option_symbol": row.get("option_symbol"),
            "premium": premium,
            "quantity": contracts,
        }],
        "contracts": contracts,
        "shares_covered": contracts * 100,
        "premium_collected": round(premium_collected, 2),
        "max_risk": max_loss,
        "max_reward": round(premium_collected + (row["strike"] - spot_price) * 100.0 * contracts, 2),
        "breakeven": round(spot_price - premium, 2),
        "greeks": net_greeks,
    }
    return score, rec


def score_iron_condor(
    confidence: float,
    vix: float,
    iv_rank: float,
    expected_range_pct: float,
    spot_price: float,
    chain_data: dict,
    spread_width_strikes: int = 2,
) -> tuple[float, dict | None]:
    """Score an iron condor (short strangle with wings).

    Rejected if confidence > 5 (needs a neutral view) or vix < 20 (need
    premium to sell).

    Score = (1 - confidence/10) * iv_rank/100
    """
    if confidence > 5 or vix < 20:
        return 0.0, None
    if not spot_price or spot_price <= 0:
        return 0.0, None
    if not expected_range_pct or expected_range_pct <= 0:
        return 0.0, None

    by_exp = (chain_data or {}).get("by_expiration") or {}
    exp = _pick_expiry(by_exp, 35)
    if not exp:
        return 0.0, None

    bucket = by_exp[exp]
    calls = [r for r in (bucket.get("calls") or []) if r.get("strike") is not None]
    puts = [r for r in (bucket.get("puts") or []) if r.get("strike") is not None]
    if len(calls) < spread_width_strikes + 1 or len(puts) < spread_width_strikes + 1:
        return 0.0, None

    # Short strikes: at the edges of the expected range
    short_call_target = spot_price * (1.0 + expected_range_pct)
    short_put_target = spot_price * (1.0 - expected_range_pct)

    short_call = _nearest_strike(calls, short_call_target)
    short_put = _nearest_strike(puts, short_put_target)
    if not short_call or not short_put:
        return 0.0, None

    # Long wings: `spread_width_strikes` further OTM
    calls_sorted = sorted(calls, key=lambda r: r["strike"])
    puts_sorted = sorted(puts, key=lambda r: r["strike"])

    try:
        sc_idx = next(i for i, r in enumerate(calls_sorted) if r["strike"] == short_call["strike"])
        sp_idx = next(i for i, r in enumerate(puts_sorted) if r["strike"] == short_put["strike"])
    except StopIteration:
        return 0.0, None

    lc_idx = sc_idx + spread_width_strikes
    lp_idx = sp_idx - spread_width_strikes
    if lc_idx >= len(calls_sorted) or lp_idx < 0:
        return 0.0, None

    long_call = calls_sorted[lc_idx]
    long_put = puts_sorted[lp_idx]

    # Sanity: long wings must be further OTM than short
    if long_call["strike"] <= short_call["strike"]:
        return 0.0, None
    if long_put["strike"] >= short_put["strike"]:
        return 0.0, None

    sc_prem = _mid(short_call)
    lc_prem = _mid(long_call)
    sp_prem = _mid(short_put)
    lp_prem = _mid(long_put)
    if any(p is None or p <= 0 for p in (sc_prem, lc_prem, sp_prem, lp_prem)):
        return 0.0, None

    net_credit = (sc_prem - lc_prem) + (sp_prem - lp_prem)
    if net_credit <= 0:
        return 0.0, None

    call_width = long_call["strike"] - short_call["strike"]
    put_width = short_put["strike"] - long_put["strike"]
    max_width = max(call_width, put_width)
    max_loss = (max_width - net_credit) * 100.0
    max_profit = net_credit * 100.0

    be_upper = short_call["strike"] + net_credit
    be_lower = short_put["strike"] - net_credit

    score = _clamp01((1.0 - confidence / 10.0) * (iv_rank / 100.0))

    net_greeks = _sum_greeks([
        (short_call, -1), (long_call, 1),
        (short_put, -1), (long_put, 1),
    ])

    rec = {
        "score": round(score, 4),
        "strategy_type": "iron_condor",
        "expiration": exp,
        "dte": _dte(exp),
        "legs": [
            {"action": "sell", "type": "call", "strike": short_call["strike"],
             "option_symbol": short_call.get("option_symbol"), "premium": sc_prem, "quantity": 1},
            {"action": "buy",  "type": "call", "strike": long_call["strike"],
             "option_symbol": long_call.get("option_symbol"),  "premium": lc_prem, "quantity": 1},
            {"action": "sell", "type": "put",  "strike": short_put["strike"],
             "option_symbol": short_put.get("option_symbol"),  "premium": sp_prem, "quantity": 1},
            {"action": "buy",  "type": "put",  "strike": long_put["strike"],
             "option_symbol": long_put.get("option_symbol"),   "premium": lp_prem, "quantity": 1},
        ],
        "net_credit": round(net_credit, 4),
        "max_risk": round(max_loss, 2),
        "max_reward": round(max_profit, 2),
        "breakevens": [round(be_lower, 2), round(be_upper, 2)],
        "greeks": net_greeks,
    }
    return score, rec


# ══════════════════════════════════════════════════════════════════════
# Level filtering + selection
# ══════════════════════════════════════════════════════════════════════

# Minimum options level required for each strategy type.
# Level 1: covered calls only (safest — collateralised).
# Level 2: long options.
# Level 3: spreads and multi-leg.
STRATEGY_MIN_LEVEL: dict[str, int] = {
    "covered_call":     1,
    "long_call":        2,
    "long_put":         2,
    "bull_call_spread": 3,
    "bear_put_spread": 3,
    "iron_condor":      3,
}

MIN_SCORE_THRESHOLD = 0.5


def select_best_strategy(
    portfolio_options_level: int,
    scores_list: list[tuple[float, dict | None]],
) -> dict | None:
    """Pick the highest-scoring strategy that:
      1. Is allowed by the portfolio's options level.
      2. Scores at or above MIN_SCORE_THRESHOLD.

    Returns the recommendation dict (with score embedded), or None if nothing
    qualifies — in which case the caller should fall back to equity.
    """
    if not scores_list or portfolio_options_level <= 0:
        return None

    best: dict | None = None
    best_score = -1.0

    for score, rec in scores_list:
        if rec is None:
            continue
        if score < MIN_SCORE_THRESHOLD:
            continue
        min_level = STRATEGY_MIN_LEVEL.get(rec.get("strategy_type", ""), 99)
        if min_level > portfolio_options_level:
            continue
        if score > best_score:
            best = rec
            best_score = score

    return best


# ══════════════════════════════════════════════════════════════════════
# High-level async selector (composes data sources + scoring)
# ══════════════════════════════════════════════════════════════════════

async def _get_current_vix() -> float | None:
    """Best-effort VIX lookup. Falls back through:
      1. market_regime.current_regime_classification() (cached regime snapshot)
      2. price_service.get_price('^VIX')  -- some feeds use this symbol
    Returns None if both fail; callers should treat unknown VIX as
    "don't take the options trade".
    """
    try:
        from app.services.market_regime import current_regime_classification
        regime = await current_regime_classification()
        if regime and regime.get("vix") is not None:
            return float(regime["vix"])
    except Exception as e:
        logger.debug(f"regime VIX lookup failed: {e}")

    try:
        from app.services.price_service import price_service
        for sym in ("^VIX", "VIX", "VIXY"):
            p = price_service.get_price(sym)
            if p:
                return float(p)
    except Exception as e:
        logger.debug(f"price_service VIX lookup failed: {e}")

    return None


async def _get_iv_rank(ticker: str) -> float:
    """Return the current IV rank for a ticker as a 0-100 number.

    Alpaca's options API doesn't expose historical IV rank directly. We
    approximate using the ATM IV from today's chain — this is a point-in-time
    read, not a true "rank vs 52w range". If that lookup fails, return 50
    (neutral) so scoring functions can still run; they'll just weight
    strategies evenly on the IV dimension.
    """
    try:
        from app.services.options_service import get_options_chain
        chain = await get_options_chain(ticker, max_expirations=1)
        by_exp = chain.get("by_expiration") or {}
        spot = chain.get("underlying_price")
        if not by_exp or not spot:
            return 50.0
        first = next(iter(by_exp.values()))
        calls = first.get("calls") or []
        atm = _nearest_strike(calls, spot)
        if atm and atm.get("iv"):
            iv = float(atm["iv"])
            # Map raw IV to a 0-100 rank: 10% IV → 0, 80% IV → 100. Clamp.
            rank = (iv - 0.10) / (0.80 - 0.10) * 100.0
            return max(0.0, min(100.0, rank))
    except Exception as e:
        logger.debug(f"iv_rank lookup for {ticker} failed: {e}")
    return 50.0


async def _get_shares_held(portfolio_id: str, ticker: str, session) -> int:
    """Sum of active long shares in this portfolio for this ticker."""
    try:
        from sqlalchemy import select
        from app.models.portfolio_holding import PortfolioHolding
        result = await session.execute(
            select(PortfolioHolding).where(
                PortfolioHolding.portfolio_id == portfolio_id,
                PortfolioHolding.ticker == ticker.upper(),
                PortfolioHolding.is_active == True,  # noqa: E712
                PortfolioHolding.direction == "long",
            )
        )
        holdings = list(result.scalars().all())
        return int(sum(h.qty for h in holdings if h.qty))
    except Exception as e:
        logger.debug(f"shares_held lookup failed: {e}")
        return 0


async def _get_prob_table_entry(ticker: str, session) -> dict | None:
    """Load the conditional_probability HenryStats row for this ticker.

    Returns a dict with keys: win_rate (0-1), expected_move_pct (0-1),
    avg_hold_days (float). None if no entry exists (insufficient data).

    If multiple strategies have data for this ticker, we combine their
    unconditional stats (weighted by n). The selector operates at the
    ticker level — it's Henry who picks the strategy and our job is to
    say "options are/aren't appropriate for this ticker in this regime".
    """
    try:
        from sqlalchemy import select
        from app.models import HenryStats
        result = await session.execute(
            select(HenryStats).where(
                HenryStats.stat_type == "conditional_probability",
                HenryStats.ticker == ticker.upper(),
            )
        )
        rows = list(result.scalars().all())
        if not rows:
            return None

        total_n = 0
        weighted_win_rate = 0.0
        bars_sum = 0.0
        bars_n = 0
        abs_move_sum = 0.0
        abs_move_n = 0
        for r in rows:
            unc = (r.data or {}).get("unconditional") or {}
            n = int(unc.get("n") or 0)
            if n <= 0:
                continue
            total_n += n
            weighted_win_rate += (unc.get("win_rate") or 0.0) * n
            if unc.get("avg_bars_in_trade"):
                bars_sum += unc["avg_bars_in_trade"] * n
                bars_n += n
            # expected_move_pct: use max(|avg_gain_pct|, |avg_loss_pct|) as
            # a rough expected move magnitude.
            ag = abs(unc.get("avg_gain_pct") or 0.0)
            al = abs(unc.get("avg_loss_pct") or 0.0)
            if ag or al:
                abs_move_sum += max(ag, al) * n
                abs_move_n += n

        if total_n == 0:
            return None

        win_rate_pct = weighted_win_rate / total_n  # 0-100
        avg_hold_days = (bars_sum / bars_n) if bars_n else 5.0
        expected_move_pct = (abs_move_sum / abs_move_n) / 100.0 if abs_move_n else 0.03

        return {
            "win_rate": win_rate_pct / 100.0,        # 0-1
            "expected_move_pct": expected_move_pct,  # 0-1 (e.g. 0.05 = 5%)
            "avg_hold_days": avg_hold_days,
            "n": total_n,
        }
    except Exception as e:
        logger.debug(f"prob table lookup for {ticker} failed: {e}")
        return None


async def select_options_strategy(
    ticker: str,
    direction: str,
    confidence: float,
    portfolio_id: str,
    session,
) -> dict | None:
    """Compose all inputs, score every candidate strategy, and return the
    best (or None, meaning "use equity").

    This is the single entry point that higher layers (ai_service,
    autonomous executor) call. Options are always optional — any data
    shortage returns None and the caller falls back to equity.
    """
    ticker = (ticker or "").upper().strip()
    if not ticker:
        return None

    # ── Portfolio gate ─────────────────────────────────────────────────
    try:
        from sqlalchemy import select
        from app.models import Portfolio
        result = await session.execute(
            select(Portfolio).where(Portfolio.id == portfolio_id)
        )
        portfolio = result.scalar_one_or_none()
    except Exception as e:
        logger.debug(f"select_options_strategy: portfolio load failed: {e}")
        return None

    if portfolio is None:
        logger.debug(f"select_options_strategy: portfolio {portfolio_id} not found")
        return None

    options_level = int(getattr(portfolio, "options_level", 0) or 0)
    if options_level <= 0:
        logger.debug(
            f"select_options_strategy({ticker}): options disabled on "
            f"portfolio {portfolio_id}"
        )
        return None

    # ── Probability table (required) ──────────────────────────────────
    prob = await _get_prob_table_entry(ticker, session)
    if prob is None:
        logger.info(
            f"select_options_strategy({ticker}): no prob table entry, "
            f"defaulting to equity"
        )
        return None

    # ── VIX (required — rejecting silently is cheap) ──────────────────
    vix = await _get_current_vix()
    if vix is None:
        logger.info(
            f"select_options_strategy({ticker}): VIX unavailable, "
            f"defaulting to equity"
        )
        return None

    # ── Chain + spot ──────────────────────────────────────────────────
    try:
        from app.services.options_service import get_options_chain
        chain = await get_options_chain(ticker, max_expirations=4)
    except Exception as e:
        logger.info(f"select_options_strategy({ticker}): chain fetch failed: {e}")
        return None

    spot = chain.get("underlying_price")
    if not spot or not chain.get("by_expiration"):
        logger.info(f"select_options_strategy({ticker}): no usable chain, equity")
        return None

    # ── IV rank (soft — defaults to 50) ───────────────────────────────
    iv_rank = await _get_iv_rank(ticker)

    # ── Shares held (covered-call eligibility) ────────────────────────
    shares_held = await _get_shares_held(portfolio_id, ticker, session)

    # ── Score all strategies ──────────────────────────────────────────
    scores: list[tuple[float, dict | None]] = []

    scores.append(score_long_option(
        direction=direction,
        confidence=confidence,
        win_rate=prob["win_rate"],
        vix=vix,
        iv_rank=iv_rank,
        avg_hold_days=prob["avg_hold_days"],
        spot_price=spot,
        chain_data=chain,
    ))

    scores.append(score_vertical_spread(
        direction=direction,
        confidence=confidence,
        win_rate=prob["win_rate"],
        vix=vix,
        iv_rank=iv_rank,
        expected_move_pct=prob["expected_move_pct"],
        spot_price=spot,
        chain_data=chain,
    ))

    scores.append(score_covered_call(
        spot_price=spot,
        shares_held=shares_held,
        confidence=confidence,
        iv_rank=iv_rank,
        chain_data=chain,
    ))

    scores.append(score_iron_condor(
        confidence=confidence,
        vix=vix,
        iv_rank=iv_rank,
        expected_range_pct=prob["expected_move_pct"],
        spot_price=spot,
        chain_data=chain,
    ))

    # ── Log every candidate, then select ─────────────────────────────
    scored_summary = ", ".join(
        f"{(r['strategy_type'] if r else 'n/a')}={s:.3f}"
        for s, r in scores
    )
    winner = select_best_strategy(options_level, scores)
    if winner is None:
        logger.info(
            f"select_options_strategy({ticker}) level={options_level} "
            f"vix={vix:.1f} iv_rank={iv_rank:.0f} -> equity "
            f"(scores: {scored_summary})"
        )
        return None

    logger.info(
        f"select_options_strategy({ticker}) level={options_level} "
        f"vix={vix:.1f} iv_rank={iv_rank:.0f} -> "
        f"{winner['strategy_type']} score={winner['score']:.3f} "
        f"(scores: {scored_summary})"
    )
    return winner
