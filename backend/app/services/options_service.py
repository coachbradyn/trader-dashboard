"""
Options Service
===============
Fetches options chain data from Alpaca, computes Greeks as a fallback when
the upstream doesn't provide them, and provides a thin in-memory TTL cache
so option chain pulls don't become the bottleneck on a ticker page.

This is the data layer — no strategy decisions, no order submission. See
`options_strategy.py` for selection and `alpaca_service.py` for order
execution.

Design notes:
  * Greeks are computed via the Black–Scholes model with `math.erf` as the
    CDF primitive (avoids a scipy dependency).  Alpaca's options data
    endpoints return Greeks when available on your data subscription; we
    fall back to BS only when a field is missing.
  * Caching is per-process in-memory, not Redis.  Options chains are
    user-facing and time-sensitive (2-min TTL), so keeping it simple and
    local is fine.  If we later want shared caching across gunicorn
    workers we can swap in henry_cache like other services.
  * Credentials come from the global ALPACA env vars.  Options data does
    not need per-portfolio keys — only order execution does.
"""
from __future__ import annotations

import asyncio
import logging
import math
import time
from dataclasses import dataclass, field
from datetime import date as date_type, datetime, timezone

from app.config import get_settings
from app.utils.utc import utcnow


logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════
# Black-Scholes Greeks (fallback when the upstream is missing them)
# ══════════════════════════════════════════════════════════════════════

def _norm_cdf(x: float) -> float:
    """Standard-normal cumulative distribution using math.erf."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_pdf(x: float) -> float:
    """Standard-normal probability density."""
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def calculate_greeks(
    option_type: str,
    spot_price: float,
    strike: float,
    time_to_expiry_days: float,
    implied_volatility: float,
    risk_free_rate: float = 0.045,
    dividend_yield: float = 0.0,
) -> dict:
    """Black-Scholes Greeks for a European call or put.

    Returns a dict with: delta, gamma, theta (per-day), vega (per 1-vol-point),
    rho (per 1-rate-point).  All in per-share units — multiply by 100 for
    per-contract impact.

    Called only as a fallback when the upstream chain doesn't include Greeks.
    Live option pricing should always prefer the market-derived IV; we pass
    it in as `implied_volatility`.
    """
    if spot_price <= 0 or strike <= 0 or implied_volatility <= 0 or time_to_expiry_days <= 0:
        return {"delta": None, "gamma": None, "theta": None, "vega": None, "rho": None}

    T = time_to_expiry_days / 365.0
    sig = implied_volatility
    S = spot_price
    K = strike
    r = risk_free_rate
    q = dividend_yield

    sqrtT = math.sqrt(T)
    d1 = (math.log(S / K) + (r - q + 0.5 * sig * sig) * T) / (sig * sqrtT)
    d2 = d1 - sig * sqrtT

    pdf_d1 = _norm_pdf(d1)
    cdf_d1 = _norm_cdf(d1)
    cdf_d2 = _norm_cdf(d2)

    if option_type.lower() == "call":
        delta = math.exp(-q * T) * cdf_d1
        theta_yr = (
            -(S * math.exp(-q * T) * pdf_d1 * sig) / (2 * sqrtT)
            - r * K * math.exp(-r * T) * cdf_d2
            + q * S * math.exp(-q * T) * cdf_d1
        )
        rho = K * T * math.exp(-r * T) * cdf_d2 / 100.0
    else:
        delta = -math.exp(-q * T) * (1 - cdf_d1)
        theta_yr = (
            -(S * math.exp(-q * T) * pdf_d1 * sig) / (2 * sqrtT)
            + r * K * math.exp(-r * T) * (1 - cdf_d2)
            - q * S * math.exp(-q * T) * (1 - cdf_d1)
        )
        rho = -K * T * math.exp(-r * T) * (1 - cdf_d2) / 100.0

    # gamma and vega are option-type independent
    gamma = math.exp(-q * T) * pdf_d1 / (S * sig * sqrtT)
    vega = S * math.exp(-q * T) * pdf_d1 * sqrtT / 100.0  # per 1% vol point
    theta_day = theta_yr / 365.0  # per calendar day

    return {
        "delta": round(delta, 4),
        "gamma": round(gamma, 6),
        "theta": round(theta_day, 4),
        "vega": round(vega, 4),
        "rho": round(rho, 4),
    }


# ══════════════════════════════════════════════════════════════════════
# In-memory TTL cache (per-process — good enough for user-triggered fetches)
# ══════════════════════════════════════════════════════════════════════

@dataclass
class _CacheEntry:
    value: object
    expires_at: float


_cache: dict[str, _CacheEntry] = {}
_cache_lock = asyncio.Lock()

_TTL_CHAIN = 120       # 2 min — chain prices move intraday
_TTL_EXPIRATIONS = 3600  # 1 hr
_TTL_QUOTE = 0          # never cache — order decisions need fresh price


async def _cache_get(key: str):
    async with _cache_lock:
        entry = _cache.get(key)
        if entry and entry.expires_at > time.time():
            return entry.value
        if entry:
            del _cache[key]
        return None


async def _cache_set(key: str, value, ttl: int):
    if ttl <= 0:
        return
    async with _cache_lock:
        _cache[key] = _CacheEntry(value=value, expires_at=time.time() + ttl)


# ══════════════════════════════════════════════════════════════════════
# Alpaca options data client
# ══════════════════════════════════════════════════════════════════════

def _get_option_data_client():
    """Returns an alpaca-py OptionHistoricalDataClient or None if the
    package isn't installed / keys aren't configured.

    Alpaca's options data uses the same global keys as equity data.
    """
    settings = get_settings()
    if not settings.alpaca_api_key or not settings.alpaca_secret_key:
        return None
    try:
        from alpaca.data.historical.option import OptionHistoricalDataClient
        return OptionHistoricalDataClient(
            settings.alpaca_api_key,
            settings.alpaca_secret_key,
        )
    except ImportError:
        logger.warning("alpaca-py options data client not available in this version")
        return None
    except Exception as e:
        logger.warning(f"Failed to init OptionHistoricalDataClient: {e}")
        return None


def _parse_occ_symbol(sym: str) -> dict | None:
    """Parse an OCC option symbol like 'NVDA240419C00900000' into components.

    Format: ROOT + YYMMDD + (C|P) + strike*1000 zero-padded to 8 digits.
    Root can be up to 6 chars. Returns None if it doesn't match the pattern.
    """
    if not sym or len(sym) < 15:
        return None
    # Work backwards: last 8 chars = strike, before that C/P, before that YYMMDD
    try:
        strike_str = sym[-8:]
        cp = sym[-9]
        yymmdd = sym[-15:-9]
        root = sym[:-15]
        if cp not in ("C", "P"):
            return None
        strike = int(strike_str) / 1000.0
        year = 2000 + int(yymmdd[:2])
        month = int(yymmdd[2:4])
        day = int(yymmdd[4:6])
        return {
            "root": root,
            "expiration": date_type(year, month, day),
            "option_type": "call" if cp == "C" else "put",
            "strike": strike,
        }
    except (ValueError, TypeError):
        return None


# ══════════════════════════════════════════════════════════════════════
# Public API
# ══════════════════════════════════════════════════════════════════════

async def get_expirations(ticker: str) -> list[str]:
    """List available expiration dates for a ticker, sorted ascending.

    Returns ISO date strings. Cached for 1 hour.  If alpaca-py or keys
    are missing, returns [] (the caller should surface "no options data
    available" rather than crashing).
    """
    ticker = ticker.upper().strip()
    ck = f"opt:exp:{ticker}"
    cached = await _cache_get(ck)
    if cached is not None:
        return cached

    client = _get_option_data_client()
    if not client:
        return []

    def _fetch():
        try:
            from alpaca.data.requests import OptionChainRequest
            # We don't know what expirations exist yet; fetch a thin chain
            # and pull the unique expirations from the response.
            req = OptionChainRequest(underlying_symbol=ticker)
            chain = client.get_option_chain(req)
            exps: set[date_type] = set()
            # alpaca-py returns a mapping { option_symbol: snapshot }
            for sym in chain.keys() if hasattr(chain, "keys") else []:
                parsed = _parse_occ_symbol(sym)
                if parsed:
                    exps.add(parsed["expiration"])
            return sorted(d.isoformat() for d in exps)
        except Exception as e:
            logger.warning(f"get_expirations({ticker}) failed: {e}")
            return []

    result = await asyncio.to_thread(_fetch)
    await _cache_set(ck, result, _TTL_EXPIRATIONS)
    return result


async def get_options_chain(
    ticker: str,
    expiration_date: str | None = None,
    max_expirations: int = 4,
) -> dict:
    """Fetch the options chain for a ticker.

    If expiration_date (ISO 'YYYY-MM-DD') is given, return only that expiry.
    Otherwise return the nearest `max_expirations` expirations.

    Returns: {
        "ticker": str,
        "underlying_price": float | None,
        "expirations": [iso_date, ...],
        "by_expiration": {
            iso_date: {
                "calls": [ { option_symbol, strike, bid, ask, last, volume,
                             open_interest, iv, delta, gamma, theta, vega } ],
                "puts":  [ ... ],
            }
        }
    }
    Cached for 2 minutes (the caller is the ticker page — users refresh
    often, we don't want to hammer Alpaca).
    """
    ticker = ticker.upper().strip()
    ck = f"opt:chain:{ticker}:{expiration_date or 'all'}:{max_expirations}"
    cached = await _cache_get(ck)
    if cached is not None:
        return cached

    client = _get_option_data_client()
    if not client:
        empty = {
            "ticker": ticker,
            "underlying_price": None,
            "expirations": [],
            "by_expiration": {},
            "note": "Options data unavailable (Alpaca credentials or alpaca-py options not configured).",
        }
        return empty

    # Fetch the underlying price for Greek-fallback computations
    underlying_price: float | None = None
    try:
        from app.services.price_service import price_service
        underlying_price = price_service.get_price(ticker)
    except Exception:
        underlying_price = None

    def _fetch():
        try:
            from alpaca.data.requests import OptionChainRequest
            req_kwargs = {"underlying_symbol": ticker}
            if expiration_date:
                try:
                    req_kwargs["expiration_date"] = datetime.strptime(
                        expiration_date, "%Y-%m-%d"
                    ).date()
                except ValueError:
                    pass
            req = OptionChainRequest(**req_kwargs)
            return client.get_option_chain(req)
        except Exception as e:
            logger.warning(f"get_options_chain({ticker}) failed: {e}")
            return None

    raw = await asyncio.to_thread(_fetch)
    if not raw:
        result = {
            "ticker": ticker,
            "underlying_price": underlying_price,
            "expirations": [],
            "by_expiration": {},
            "note": "No chain data returned from Alpaca.",
        }
        await _cache_set(ck, result, _TTL_CHAIN)
        return result

    # Normalise alpaca-py's response. Its shape varies by SDK version —
    # handle both `{sym: snapshot}` mappings and list-style responses.
    items = []
    try:
        items = list(raw.items()) if hasattr(raw, "items") else [
            (getattr(s, "symbol", None), s) for s in raw
        ]
    except Exception:
        items = []

    by_exp: dict[str, dict] = {}
    today = date_type.today()

    for sym, snap in items:
        if not sym:
            continue
        parsed = _parse_occ_symbol(sym)
        if not parsed:
            continue
        exp_iso = parsed["expiration"].isoformat()

        # Extract fields defensively — the snapshot object has quote/greeks
        # attributes but field names vary between SDK versions.
        def _attr(obj, *names):
            for n in names:
                v = getattr(obj, n, None)
                if v is None and isinstance(obj, dict):
                    v = obj.get(n)
                if v is not None:
                    return v
            return None

        quote = _attr(snap, "latest_quote", "quote")
        trade = _attr(snap, "latest_trade", "trade")
        greeks = _attr(snap, "greeks")

        bid = _attr(quote, "bid_price", "bp") if quote else None
        ask = _attr(quote, "ask_price", "ap") if quote else None
        last = _attr(trade, "price", "p") if trade else None
        volume = _attr(snap, "volume", "day_volume")
        oi = _attr(snap, "open_interest", "oi")
        iv = _attr(snap, "implied_volatility", "iv")

        delta = _attr(greeks, "delta") if greeks else None
        gamma = _attr(greeks, "gamma") if greeks else None
        theta = _attr(greeks, "theta") if greeks else None
        vega = _attr(greeks, "vega") if greeks else None

        # Greek fallback when the API didn't send them
        if (
            delta is None and underlying_price
            and iv is not None
            and iv > 0
        ):
            dte = max((parsed["expiration"] - today).days, 0)
            if dte > 0:
                g = calculate_greeks(
                    option_type=parsed["option_type"],
                    spot_price=underlying_price,
                    strike=parsed["strike"],
                    time_to_expiry_days=dte,
                    implied_volatility=float(iv),
                )
                delta, gamma, theta, vega = g["delta"], g["gamma"], g["theta"], g["vega"]

        row = {
            "option_symbol": sym,
            "strike": parsed["strike"],
            "bid": float(bid) if bid is not None else None,
            "ask": float(ask) if ask is not None else None,
            "last": float(last) if last is not None else None,
            "volume": int(volume) if volume is not None else None,
            "open_interest": int(oi) if oi is not None else None,
            "iv": float(iv) if iv is not None else None,
            "delta": float(delta) if delta is not None else None,
            "gamma": float(gamma) if gamma is not None else None,
            "theta": float(theta) if theta is not None else None,
            "vega": float(vega) if vega is not None else None,
        }

        bucket = by_exp.setdefault(exp_iso, {"calls": [], "puts": []})
        (bucket["calls"] if parsed["option_type"] == "call" else bucket["puts"]).append(row)

    # Sort strikes within each expiration and optionally limit to nearest N
    expirations_sorted = sorted(by_exp.keys())
    if expiration_date is None and max_expirations > 0:
        expirations_sorted = expirations_sorted[:max_expirations]
        by_exp = {k: by_exp[k] for k in expirations_sorted if k in by_exp}

    for exp_iso, bucket in by_exp.items():
        bucket["calls"].sort(key=lambda r: r["strike"])
        bucket["puts"].sort(key=lambda r: r["strike"])

    result = {
        "ticker": ticker,
        "underlying_price": underlying_price,
        "expirations": expirations_sorted,
        "by_expiration": by_exp,
    }
    await _cache_set(ck, result, _TTL_CHAIN)
    return result


async def get_option_quote(option_symbol: str) -> dict | None:
    """Fetch a live quote for a single option contract by OCC symbol.
    No cache — always returns fresh data since quotes feed order decisions.
    """
    option_symbol = option_symbol.upper().strip()
    parsed = _parse_occ_symbol(option_symbol)
    if not parsed:
        return None

    client = _get_option_data_client()
    if not client:
        return None

    def _fetch():
        try:
            from alpaca.data.requests import OptionLatestQuoteRequest
            req = OptionLatestQuoteRequest(symbol_or_symbols=[option_symbol])
            res = client.get_option_latest_quote(req)
            # res is dict-like keyed by symbol
            snap = res.get(option_symbol) if hasattr(res, "get") else None
            if snap is None:
                return None
            bid = getattr(snap, "bid_price", None) or getattr(snap, "bp", None)
            ask = getattr(snap, "ask_price", None) or getattr(snap, "ap", None)
            return {
                "option_symbol": option_symbol,
                "bid": float(bid) if bid is not None else None,
                "ask": float(ask) if ask is not None else None,
                "mid": (float(bid) + float(ask)) / 2.0 if bid is not None and ask is not None else None,
                "timestamp": utcnow().isoformat(),
            }
        except Exception as e:
            logger.warning(f"get_option_quote({option_symbol}) failed: {e}")
            return None

    return await asyncio.to_thread(_fetch)


async def update_positions_live_data(options_trades: list) -> int:
    """For a list of open OptionsTrade rows, fetch the current quote and
    Greeks (computing Greeks via BS fallback as needed) and update
    current_premium + greeks_current. Returns the number of rows actually
    updated. Called by the price poller.

    Takes a pre-loaded list rather than a query so it can run inside or
    outside a transaction at the caller's discretion.
    """
    if not options_trades:
        return 0

    client = _get_option_data_client()
    if not client:
        return 0

    symbols = [t.option_symbol for t in options_trades if t.option_symbol]
    if not symbols:
        return 0

    # Batch quote in one call
    quotes: dict[str, dict] = {}

    def _fetch_batch():
        try:
            from alpaca.data.requests import OptionLatestQuoteRequest
            req = OptionLatestQuoteRequest(symbol_or_symbols=symbols)
            res = client.get_option_latest_quote(req)
            out: dict[str, dict] = {}
            items = list(res.items()) if hasattr(res, "items") else []
            for sym, snap in items:
                bid = getattr(snap, "bid_price", None)
                ask = getattr(snap, "ask_price", None)
                if bid is None or ask is None:
                    continue
                out[sym] = {"bid": float(bid), "ask": float(ask)}
            return out
        except Exception as e:
            logger.warning(f"update_positions_live_data batch quote failed: {e}")
            return {}

    quotes = await asyncio.to_thread(_fetch_batch)

    # Underlying prices for Greek recompute — pull once per unique ticker
    underlyings: dict[str, float | None] = {}
    try:
        from app.services.price_service import price_service
        for t in options_trades:
            if t.ticker not in underlyings:
                underlyings[t.ticker] = price_service.get_price(t.ticker)
    except Exception:
        underlyings = {}

    updated = 0
    today = date_type.today()
    for t in options_trades:
        q = quotes.get(t.option_symbol)
        if not q:
            continue
        mid = (q["bid"] + q["ask"]) / 2.0
        t.current_premium = mid
        dte = max((t.expiration - today).days, 0)
        iv = (t.iv_at_entry or 0.3) if (t.iv_at_entry and t.iv_at_entry > 0) else 0.3
        spot = underlyings.get(t.ticker)
        if spot and dte > 0:
            try:
                t.greeks_current = calculate_greeks(
                    option_type=t.option_type,
                    spot_price=spot,
                    strike=t.strike,
                    time_to_expiry_days=dte,
                    implied_volatility=iv,
                )
            except Exception:
                pass
        updated += 1
    return updated
