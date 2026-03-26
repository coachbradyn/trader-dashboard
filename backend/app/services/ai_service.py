"""
Henry AI Integration Layer
==========================
Plugs into your existing FastAPI backend. Drop this in /backend/ai_service.py
and add the routes to your main app.

Requires: pip install anthropic
Set env var: ANTHROPIC_API_KEY=sk-ant-...

Four features:
  1. Nightly Trade Review  — POST /api/ai/review
  2. Morning Briefing      — GET  /api/ai/briefing
  3. Natural Language Query — POST /api/ai/query
  4. Strategy Conflict Res  — POST /api/ai/conflict (auto-triggered by webhook handler)
"""

import os
import json
from datetime import datetime, timedelta
from typing import Optional
import anthropic

# ─── CONFIG ──────────────────────────────────────────────────────────────────

MODEL = "claude-sonnet-4-5-20250514"  # Primary — best reasoning for trade analysis
MODEL_FALLBACK = "claude-3-5-sonnet-latest"  # Fallback — uses latest available Sonnet 3.5
MODEL_LAST_RESORT = "claude-3-haiku-20240307"  # Last resort — cheap and fast

try:
    CLIENT = anthropic.Anthropic()  # Reads ANTHROPIC_API_KEY from env
except Exception:
    CLIENT = None

SYSTEM_PROMPT = """You are Henry, an AI trading analyst embedded in a multi-strategy trading dashboard.
You analyze trade data from four Pine Script strategies:
  - S1 (LMA Momentum): Log-weighted moving average + Kalman filter trend following
  - S2 (Regime Trend): 200 SMA + ADX trend detection as entry signals
  - S3 (Impulse Breakout): Volume spike + candle expansion breakouts with time decay
  - S4 (Kalman Reversion): Mean reversion when price stretches from Kalman filter

You speak concisely and directly. No fluff. Use numbers to back up every claim.
When you identify a pattern, explain WHY it matters for tomorrow's trading.
Format currency as $X.XX. Format percentages as X.X%.
Use bullet points sparingly — prefer short paragraphs.
If data is insufficient to draw conclusions, say so rather than speculating."""


# ─── HELPERS ─────────────────────────────────────────────────────────────────

def _call_claude(prompt: str, max_tokens: int = 1500) -> str:
    """Single Claude API call with system prompt. Falls back to older model on BadRequest."""
    if CLIENT is None:
        return "AI analysis unavailable — ANTHROPIC_API_KEY not configured."
    import logging
    logger = logging.getLogger(__name__)

    last_error = None
    for model in [MODEL, MODEL_FALLBACK, MODEL_LAST_RESORT]:
        try:
            response = CLIENT.messages.create(
                model=model,
                max_tokens=max_tokens,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": prompt}],
                timeout=30.0,
            )
            if model != MODEL:
                logger.info(f"Used fallback model: {model}")
            return response.content[0].text
        except (anthropic.BadRequestError, anthropic.NotFoundError) as e:
            last_error = f"{type(e).__name__} ({model}): {str(e)[:200]}"
            logger.warning(f"Claude API {type(e).__name__} with model {model}: {e}")
            continue  # Try next model
        except anthropic.AuthenticationError as e:
            return f"AI analysis unavailable — invalid API key. Check ANTHROPIC_API_KEY in Railway."
        except Exception as e:
            last_error = f"{type(e).__name__} ({model}): {str(e)[:200]}"
            logger.error(f"Claude API call failed with model {model}: {e}")
            continue  # Try next model

    return f"AI analysis temporarily unavailable. Both primary and fallback models failed. {last_error or ''}"


def _format_trades_for_prompt(trades: list[dict]) -> str:
    """Convert trade records to a compact text block for the prompt."""
    if not trades:
        return "No trades recorded."
    
    lines = []
    for t in trades:
        signal_type = t.get("signal", "?")
        if signal_type == "entry":
            lines.append(
                f"  ENTRY | {t.get('trader','?')} | {t.get('dir','?').upper()} {t.get('ticker','?')} "
                f"@ ${t.get('price',0):.2f} | qty={t.get('qty',0):.1f} | "
                f"sig={t.get('sig',0):.1f} adx={t.get('adx',0):.1f} atr={t.get('atr',0):.2f} | "
                f"stop=${t.get('stop',0):.2f} | tf={t.get('tf','?')}"
            )
        elif signal_type == "exit":
            lines.append(
                f"  EXIT  | {t.get('trader','?')} | {t.get('dir','?').upper()} {t.get('ticker','?')} "
                f"@ ${t.get('price',0):.2f} | pnl={t.get('pnl_pct',0):.2f}% | "
                f"bars={t.get('bars_in_trade',0)} | reason={t.get('exit_reason','?')} | tf={t.get('tf','?')}"
            )
    return "\n".join(lines)


def _format_positions_for_prompt(positions: list[dict]) -> str:
    """Convert open positions to text block."""
    if not positions:
        return "No open positions."
    
    lines = []
    for p in positions:
        lines.append(
            f"  {p.get('trader','?')} | {p.get('dir','?').upper()} {p.get('ticker','?')} "
            f"@ ${p.get('entry_price',0):.2f} | current=${p.get('current_price',0):.2f} | "
            f"pnl={p.get('pnl_pct',0):.2f}% | bars={p.get('bars_in_trade',0)}"
        )
    return "\n".join(lines)


# ─── FEATURE 1: NIGHTLY TRADE REVIEW ────────────────────────────────────────

def nightly_review(todays_trades: list[dict], recent_history: list[dict] = None) -> str:
    """
    Analyze today's trades, spot patterns, suggest adjustments.
    
    Call this from a scheduled job (cron/celery) at market close,
    or from a manual POST /api/ai/review endpoint.
    
    Args:
        todays_trades: All webhook signals received today (entries + exits)
        recent_history: Optional last 5 days of trades for pattern context
    """
    today_text = _format_trades_for_prompt(todays_trades)
    history_text = _format_trades_for_prompt(recent_history) if recent_history else "Not provided."
    
    # Compute summary stats
    exits_today = [t for t in todays_trades if t.get("signal") == "exit"]
    wins = [t for t in exits_today if t.get("pnl_pct", 0) > 0]
    losses = [t for t in exits_today if t.get("pnl_pct", 0) <= 0]
    total_pnl = sum(t.get("pnl_pct", 0) for t in exits_today)
    
    by_strategy = {}
    for t in exits_today:
        trader = t.get("trader", "unknown")
        if trader not in by_strategy:
            by_strategy[trader] = {"wins": 0, "losses": 0, "total_pnl": 0}
        if t.get("pnl_pct", 0) > 0:
            by_strategy[trader]["wins"] += 1
        else:
            by_strategy[trader]["losses"] += 1
        by_strategy[trader]["total_pnl"] += t.get("pnl_pct", 0)
    
    strategy_summary = "\n".join(
        f"  {name}: {s['wins']}W/{s['losses']}L, net {s['total_pnl']:.2f}%"
        for name, s in by_strategy.items()
    ) or "No closed trades today."
    
    by_exit_reason = {}
    for t in exits_today:
        reason = t.get("exit_reason", "unknown")
        if reason not in by_exit_reason:
            by_exit_reason[reason] = {"count": 0, "total_pnl": 0}
        by_exit_reason[reason]["count"] += 1
        by_exit_reason[reason]["total_pnl"] += t.get("pnl_pct", 0)
    
    exit_reason_summary = "\n".join(
        f"  {reason}: {s['count']} trades, avg {s['total_pnl']/s['count']:.2f}%"
        for reason, s in by_exit_reason.items()
    ) or "No exits."
    
    prompt = f"""Analyze today's trading session. Be specific and actionable.

TODAY'S STATS:
  Total trades: {len(todays_trades)} signals ({len(exits_today)} closed)
  Wins: {len(wins)}, Losses: {len(losses)}
  Net P&L: {total_pnl:.2f}%

BY STRATEGY:
{strategy_summary}

BY EXIT REASON:
{exit_reason_summary}

TODAY'S TRADE LOG:
{today_text}

RECENT HISTORY (last 5 days):
{history_text}

Analyze:
1. Which strategies performed and which didn't — and why based on the data
2. Are there patterns in the exit reasons? Any exit type consistently unprofitable?
3. Any ticker-specific patterns (same stock getting whipsawed across strategies)?
4. One concrete adjustment to consider for tomorrow
Keep it under 300 words. Lead with the most important finding."""

    return _call_claude(prompt, max_tokens=1500)


# ─── FEATURE 2: MORNING BRIEFING (ENHANCED) ────────────────────────────────

def _format_market_intel(intel: dict) -> str:
    """Convert market intel dict into a rich text block for the prompt."""
    sections = []

    # SPY & VIX
    spy = intel.get("spy", {})
    vix = intel.get("vix", {})
    if spy or vix:
        market_lines = []
        if spy:
            market_lines.append(
                f"  SPY: ${spy.get('price', 0)} ({spy.get('change_pct', 0):+.2f}%) | "
                f"5d range: ${spy.get('5d_low', 0)}-${spy.get('5d_high', 0)} | "
                f"vol: {spy.get('volume', 0):,}"
            )
        if vix:
            market_lines.append(
                f"  VIX: {vix.get('current', 0)} ({vix.get('change', 0):+.1f} from prev) | "
                f"regime: {vix.get('regime', '?')} | 5d trend: {vix.get('5d_trend', '?')} | "
                f"week ago: {vix.get('week_ago', 0)}"
            )
        sections.append("MARKET OVERVIEW:\n" + "\n".join(market_lines))

    # Pre-market gaps
    gaps = intel.get("premarket_gaps", [])
    if gaps:
        gap_lines = [f"  {g['ticker']}: {g['gap_pct']:+.2f}% gap (prev ${g['prev_close']} → ${g['current']})" for g in gaps[:8]]
        sections.append("PRE-MARKET GAPS (held tickers):\n" + "\n".join(gap_lines))

    # Sector performance
    sectors = intel.get("sectors", [])
    if sectors:
        top3 = sectors[:3]
        bottom3 = sectors[-3:] if len(sectors) > 3 else []
        sect_lines = ["  LEADING: " + " | ".join(f"{s['sector']} {s['change_pct']:+.2f}%" for s in top3)]
        if bottom3:
            sect_lines.append("  LAGGING: " + " | ".join(f"{s['sector']} {s['change_pct']:+.2f}%" for s in bottom3))
        sections.append("SECTOR ROTATION:\n" + "\n".join(sect_lines))

    # Volume movers
    movers = intel.get("movers", {})
    gainers = movers.get("gainers", [])[:3]
    losers = movers.get("losers", [])[:3]
    if gainers or losers:
        mover_lines = []
        if gainers:
            mover_lines.append("  TOP MOVERS: " + " | ".join(f"{m['symbol']} ({m.get('change_pct', 0):+.1f}%)" for m in gainers))
        sections.append("VOLUME & MOVERS:\n" + "\n".join(mover_lines))

    # Earnings calendar
    earnings = intel.get("earnings", [])
    if earnings:
        earn_lines = [f"  ⚠ {e['ticker']} reports in {e['days_away']}d ({e['earnings_date']})" for e in earnings]
        sections.append("EARNINGS WATCH (held tickers):\n" + "\n".join(earn_lines))

    # News — portfolio-relevant
    news_portfolio = intel.get("news_portfolio", [])
    if news_portfolio:
        news_lines = [f"  [{a.get('source', '?')}] {a['headline']}" for a in news_portfolio[:6]]
        sections.append("NEWS (your tickers):\n" + "\n".join(news_lines))

    # News — general market
    news_general = intel.get("news_general", [])
    if news_general:
        # Deduplicate against portfolio news
        portfolio_headlines = {a["headline"] for a in news_portfolio}
        general_unique = [a for a in news_general if a["headline"] not in portfolio_headlines][:5]
        if general_unique:
            news_lines = [f"  [{a.get('source', '?')}] {a['headline']}" for a in general_unique]
            sections.append("MARKET NEWS:\n" + "\n".join(news_lines))

    # Position snapshots (current prices for held tickers)
    snapshots = intel.get("snapshots", {})
    if snapshots:
        snap_lines = []
        for ticker, snap in list(snapshots.items())[:15]:
            if ticker in ("SPY", "QQQ"):
                continue  # Already shown above
            snap_lines.append(
                f"  {ticker}: ${snap['price']} ({snap['change_pct']:+.2f}%) | "
                f"vol: {snap['volume']:,}"
            )
        if snap_lines:
            sections.append("HELD TICKER SNAPSHOTS:\n" + "\n".join(snap_lines))

    return "\n\n".join(sections) if sections else "Market data unavailable."


def morning_briefing(
    open_positions: list[dict],
    yesterdays_trades: list[dict],
    market_intel: dict = None,
    cumulative_stats: dict = None,
    holdings_context: str = None,
) -> str:
    """
    Generate enhanced morning briefing with full market intelligence.

    Args:
        open_positions: Currently open positions across all strategies
        yesterdays_trades: Yesterday's full trade log
        market_intel: Rich market data from gather_market_intel()
        cumulative_stats: Running totals per strategy
        holdings_context: Text summary of manual holdings
    """
    positions_text = _format_positions_for_prompt(open_positions)
    yesterday_text = _format_trades_for_prompt(yesterdays_trades)

    # Format rich market intel
    if market_intel:
        intel_text = _format_market_intel(market_intel)
    else:
        intel_text = "Market data unavailable."

    stats_text = "Not available."
    if cumulative_stats:
        stats_lines = []
        for name, s in cumulative_stats.items():
            stats_lines.append(
                f"  {name}: {s.get('total_trades',0)} trades, "
                f"{s.get('win_rate',0):.0f}% win rate, "
                f"net {s.get('total_pnl',0):.2f}%"
            )
        stats_text = "\n".join(stats_lines)

    holdings_text = holdings_context or "No manual holdings."

    prompt = f"""Generate a comprehensive morning briefing for today's trading session.
You have access to real-time market data, news, sector rotation, and portfolio context.

{intel_text}

OPEN STRATEGY POSITIONS (carrying over):
{positions_text}

MANUAL HOLDINGS:
{holdings_text}

YESTERDAY'S ACTIVITY:
{yesterday_text}

CUMULATIVE STRATEGY PERFORMANCE (30 days):
{stats_text}

Write a 5-section briefing:

1. **MARKET OVERVIEW** — What happened overnight, where are futures/indices, VIX regime, any gap risks on held tickers. Reference specific numbers.

2. **NEWS & EVENTS** — Key headlines affecting your portfolio or watchlist. Flag any earnings within the week for held tickers. Mention sector rotation (what's leading/lagging) and whether your holdings are in favorable sectors.

3. **PORTFOLIO STATUS** — Open positions P&L, manual holdings status, any positions approaching stops or with extended hold times. Total exposure and concentration risk.

4. **YESTERDAY'S TAKEAWAY** — What worked, what didn't, patterns in exit signals. One specific data-backed lesson.

5. **TODAY'S GAME PLAN** — Specific levels to watch on held tickers, which strategies are in favorable conditions given today's VIX/trend, any trades to be cautious about. If earnings are upcoming for a held ticker, flag the risk.

Be specific. Use dollar amounts and percentages. Reference actual headlines and sector data. No generic advice like "stay disciplined" — give me actionable intelligence.
Keep it under 500 words."""

    return _call_claude(prompt, max_tokens=2500)


# ─── FEATURE 3: NATURAL LANGUAGE QUERY ───────────────────────────────────────

def query_trades(
    question: str,
    all_trades: list[dict],
    open_positions: list[dict] = None
) -> str:
    """
    Answer a natural language question about trade history.
    
    The LLM receives the full trade dataset as context and answers
    analytical questions. For large histories, pre-filter or summarize
    before passing in.
    
    Args:
        question: User's natural language question
        all_trades: Trade history (entries + exits)
        open_positions: Current open positions
    """
    trades_text = _format_trades_for_prompt(all_trades)
    positions_text = _format_positions_for_prompt(open_positions) if open_positions else "None."
    
    # Pre-compute stats the model can reference
    exits = [t for t in all_trades if t.get("signal") == "exit"]
    total_trades = len(exits)
    total_pnl = sum(t.get("pnl_pct", 0) for t in exits)
    wins = len([t for t in exits if t.get("pnl_pct", 0) > 0])
    win_rate = (wins / total_trades * 100) if total_trades > 0 else 0
    
    # Per-strategy breakdown
    by_strat = {}
    for t in exits:
        trader = t.get("trader", "unknown")
        if trader not in by_strat:
            by_strat[trader] = {"count": 0, "pnl": 0, "wins": 0}
        by_strat[trader]["count"] += 1
        by_strat[trader]["pnl"] += t.get("pnl_pct", 0)
        if t.get("pnl_pct", 0) > 0:
            by_strat[trader]["wins"] += 1
    
    strat_summary = "\n".join(
        f"  {name}: {s['count']} trades, {s['wins']}/{s['count']} wins "
        f"({s['wins']/s['count']*100:.0f}%), net {s['pnl']:.2f}%"
        for name, s in by_strat.items()
    )
    
    # Per-ticker breakdown
    by_ticker = {}
    for t in exits:
        ticker = t.get("ticker", "?")
        if ticker not in by_ticker:
            by_ticker[ticker] = {"count": 0, "pnl": 0}
        by_ticker[ticker]["count"] += 1
        by_ticker[ticker]["pnl"] += t.get("pnl_pct", 0)
    
    ticker_summary = "\n".join(
        f"  {ticker}: {s['count']} trades, net {s['pnl']:.2f}%"
        for ticker, s in sorted(by_ticker.items(), key=lambda x: x[1]["pnl"], reverse=True)[:10]
    )
    
    prompt = f"""Answer this question about the trading data. Use specific numbers.

QUESTION: {question}

SUMMARY STATS:
  Total closed trades: {total_trades}
  Win rate: {win_rate:.1f}% ({wins}/{total_trades})
  Net P&L: {total_pnl:.2f}%

BY STRATEGY:
{strat_summary}

TOP TICKERS:
{ticker_summary}

CURRENT POSITIONS:
{positions_text}

FULL TRADE LOG:
{trades_text}

Answer concisely. If the data doesn't contain enough info to answer, say so.
If the question involves a comparison, use a small table.
Keep it under 200 words unless the question requires more detail."""

    return _call_claude(prompt, max_tokens=1000)


# ─── FEATURE 4: STRATEGY CONFLICT RESOLUTION ────────────────────────────────

def resolve_conflict(
    conflicting_signals: list[dict],
    recent_trades: list[dict] = None,
    market_context: dict = None
) -> dict:
    """
    When two strategies disagree on direction for the same ticker,
    analyze which signal has more merit.
    
    Call this from your webhook handler when you detect opposing signals
    within a time window.
    
    Args:
        conflicting_signals: List of 2+ signals on same ticker with different dirs
        recent_trades: Recent history for context
        market_context: Optional market data
    
    Returns:
        dict with keys: recommendation, confidence, reasoning
    """
    signals_text = "\n".join(
        f"  {s.get('trader','?')}: {s.get('dir','?').upper()} {s.get('ticker','?')} "
        f"@ ${s.get('price',0):.2f} | sig={s.get('sig',0):.1f} adx={s.get('adx',0):.1f} "
        f"atr={s.get('atr',0):.2f}"
        for s in conflicting_signals
    )
    
    history_text = _format_trades_for_prompt(recent_trades) if recent_trades else "Not available."
    
    market_text = "Not available."
    if market_context:
        parts = []
        for k, v in market_context.items():
            parts.append(f"{k}: {v}")
        market_text = " | ".join(parts)
    
    # Get recent performance per conflicting strategy on this ticker
    ticker = conflicting_signals[0].get("ticker", "?")
    traders_involved = [s.get("trader") for s in conflicting_signals]
    
    relevant_exits = [
        t for t in (recent_trades or [])
        if t.get("signal") == "exit" 
        and t.get("ticker") == ticker
        and t.get("trader") in traders_involved
    ]
    
    track_record = ""
    for trader in traders_involved:
        trader_exits = [t for t in relevant_exits if t.get("trader") == trader]
        if trader_exits:
            avg_pnl = sum(t.get("pnl_pct", 0) for t in trader_exits) / len(trader_exits)
            wins = len([t for t in trader_exits if t.get("pnl_pct", 0) > 0])
            track_record += f"  {trader} on {ticker}: {wins}/{len(trader_exits)} wins, avg {avg_pnl:.2f}%\n"
        else:
            track_record += f"  {trader} on {ticker}: no history\n"
    
    prompt = f"""Two or more strategies are giving conflicting signals on the same ticker.
Analyze which signal has more merit and recommend an action.

CONFLICTING SIGNALS:
{signals_text}

TRACK RECORD ON THIS TICKER:
{track_record if track_record else "No history available."}

RECENT TRADE HISTORY:
{history_text}

MARKET CONTEXT:
  {market_text}

Respond in EXACTLY this JSON format (no markdown, no backticks):
{{"recommendation": "LONG" or "SHORT" or "STAY_FLAT", "confidence": 1-10, "reasoning": "one paragraph max"}}"""

    raw = _call_claude(prompt, max_tokens=500)
    
    # Parse JSON response
    try:
        # Strip any markdown fencing just in case
        clean = raw.strip().replace("```json", "").replace("```", "").strip()
        result = json.loads(clean)
        return {
            "recommendation": result.get("recommendation", "STAY_FLAT"),
            "confidence": result.get("confidence", 5),
            "reasoning": result.get("reasoning", "Unable to determine."),
            "raw": raw
        }
    except json.JSONDecodeError:
        return {
            "recommendation": "STAY_FLAT",
            "confidence": 1,
            "reasoning": f"Failed to parse AI response: {raw[:200]}",
            "raw": raw
        }


# ─── FASTAPI ROUTES ─────────────────────────────────────────────────────────
# Add these to your existing FastAPI app

def register_ai_routes(app, get_trades_fn, get_positions_fn, get_market_data_fn=None):
    """
    Register AI endpoints on your existing FastAPI app.
    
    Usage in your main.py:
        from ai_service import register_ai_routes
        register_ai_routes(app, get_trades, get_positions, get_market_data)
    
    Args:
        app: Your FastAPI app instance
        get_trades_fn: async fn(days_back: int) -> list[dict]
        get_positions_fn: async fn() -> list[dict]
        get_market_data_fn: optional async fn() -> dict
    """
    from fastapi import HTTPException
    from pydantic import BaseModel
    
    class QueryRequest(BaseModel):
        question: str
    
    class ReviewRequest(BaseModel):
        days_back: int = 1
    
    @app.post("/api/ai/review")
    async def ai_review(req: ReviewRequest):
        try:
            todays_trades = await get_trades_fn(days_back=1)
            if not todays_trades:
                return {"review": "No trades recorded yet. Once your strategies start sending webhooks, trade reviews will appear here.", "trades_analyzed": 0}
            recent_history = await get_trades_fn(days_back=5) if req.days_back > 1 else None
            result = nightly_review(todays_trades, recent_history)
            return {"review": result, "trades_analyzed": len(todays_trades)}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
    
    @app.get("/api/ai/briefing")
    async def ai_briefing():
        """Return today's cached briefing, or generate if none exists."""
        import logging
        from datetime import date
        from sqlalchemy import select
        from app.database import async_session
        from app.models.market_summary import MarketSummary

        logger = logging.getLogger(__name__)

        try:
            # Check cache: has today's briefing already been generated?
            today_str = date.today().isoformat()
            async with async_session() as db:
                result = await db.execute(
                    select(MarketSummary)
                    .where(
                        MarketSummary.summary_type == "daily_briefing",
                        MarketSummary.generated_at >= datetime.utcnow().replace(hour=0, minute=0, second=0),
                    )
                    .order_by(MarketSummary.generated_at.desc())
                    .limit(1)
                )
                cached = result.scalar_one_or_none()

            if cached:
                positions = await get_positions_fn()
                return {
                    "briefing": cached.content,
                    "open_positions": len(positions),
                    "generated_at": cached.generated_at.isoformat(),
                    "cached": True,
                }

            # No cache — generate fresh briefing
            return await _generate_fresh_briefing(get_trades_fn, get_positions_fn, logger)

        except Exception as e:
            logger.error(f"Briefing failed: {e}", exc_info=True)
            return {"briefing": f"Briefing temporarily unavailable: {type(e).__name__}. Try again in a moment.", "open_positions": 0}

    @app.post("/api/ai/briefing/refresh")
    async def ai_briefing_refresh():
        """Force-regenerate today's briefing (manual refresh button)."""
        import logging
        logger = logging.getLogger(__name__)
        try:
            return await _generate_fresh_briefing(get_trades_fn, get_positions_fn, logger, force=True)
        except Exception as e:
            logger.error(f"Briefing refresh failed: {e}", exc_info=True)
            return {"briefing": f"Refresh failed: {type(e).__name__}", "open_positions": 0}

    async def _generate_fresh_briefing(get_trades_fn, get_positions_fn, logger, force=False):
        """Generate a fresh briefing with full market intelligence and cache it."""
        from sqlalchemy import select
        from app.database import async_session
        from app.models.market_summary import MarketSummary
        from app.models.portfolio_holding import PortfolioHolding
        from app.services.market_intel import gather_market_intel
        from app.services.price_service import price_service

        positions = await get_positions_fn()
        yesterdays_trades = await get_trades_fn(days_back=1)

        if not positions and not yesterdays_trades:
            return {
                "briefing": "No trading activity yet. Connect your TradingView strategies via Settings to start receiving webhooks and generating briefings.",
                "open_positions": 0,
                "cached": False,
            }

        # Collect all held tickers (strategy positions + manual holdings)
        held_tickers = list(set(
            [p.get("ticker", "") for p in positions]
        ))

        # Add manual holdings tickers
        async with async_session() as db:
            result = await db.execute(
                select(PortfolioHolding).where(PortfolioHolding.is_active == True)
            )
            holdings = result.scalars().all()
            for h in holdings:
                if h.ticker not in held_tickers:
                    held_tickers.append(h.ticker)

            # Build holdings context string
            holdings_context_lines = []
            for h in holdings:
                current_price = price_service.get_price(h.ticker) or h.entry_price
                if h.direction == "long":
                    pnl = (current_price - h.entry_price) / h.entry_price * 100
                else:
                    pnl = (h.entry_price - current_price) / h.entry_price * 100
                holdings_context_lines.append(
                    f"  {h.ticker} | {h.direction.upper()} | {h.qty} shares @ ${h.entry_price:.2f} | "
                    f"current ${current_price:.2f} | {pnl:+.2f}% | strategy: {h.strategy_name or 'manual'}"
                )
            holdings_context = "\n".join(holdings_context_lines) if holdings_context_lines else None

        # Gather all market intelligence in parallel
        logger.info(f"Gathering market intel for {len(held_tickers)} tickers: {held_tickers}")
        market_intel = await gather_market_intel(held_tickers)

        # Build cumulative stats from 30-day history
        all_trades = await get_trades_fn(days_back=30)
        exits = [t for t in all_trades if t.get("signal") == "exit"]
        cumulative = {}
        for t in exits:
            trader = t.get("trader", "unknown")
            if trader not in cumulative:
                cumulative[trader] = {"total_trades": 0, "wins": 0, "total_pnl": 0}
            cumulative[trader]["total_trades"] += 1
            if t.get("pnl_pct", 0) > 0:
                cumulative[trader]["wins"] += 1
            cumulative[trader]["total_pnl"] += t.get("pnl_pct", 0)
        for s in cumulative.values():
            s["win_rate"] = (s["wins"] / s["total_trades"] * 100) if s["total_trades"] > 0 else 0

        # Generate briefing with full context
        result = morning_briefing(
            positions,
            yesterdays_trades,
            market_intel=market_intel,
            cumulative_stats=cumulative,
            holdings_context=holdings_context,
        )

        # Cache in database
        async with async_session() as db:
            summary = MarketSummary(
                summary_type="daily_briefing",
                scope="combined",
                content=result,
                tickers_analyzed=held_tickers,
            )
            db.add(summary)
            await db.commit()

            logger.info("Daily briefing generated and cached")

        return {
            "briefing": result,
            "open_positions": len(positions),
            "generated_at": datetime.utcnow().isoformat(),
            "cached": False,
        }
    
    @app.post("/api/ai/query")
    async def ai_query(req: QueryRequest):
        try:
            all_trades = await get_trades_fn(days_back=30)
            positions = await get_positions_fn()
            if not all_trades and not positions:
                return {"answer": "No trading data available yet. Once webhooks start flowing in, I'll be able to answer questions about your trading performance.", "trades_in_context": 0}
            result = query_trades(req.question, all_trades, positions)
            return {"answer": result, "trades_in_context": len(all_trades)}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
    
    @app.post("/api/ai/conflict")
    async def ai_conflict(signals: list[dict]):
        try:
            recent = await get_trades_fn(days_back=14)
            market = await get_market_data_fn() if get_market_data_fn else None
            result = resolve_conflict(signals, recent, market)
            return result
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
