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

MODEL = "claude-sonnet-4-5-20250929"  # Primary — best balance of speed + reasoning
MODEL_FALLBACK = "claude-sonnet-4-6"  # Fallback — latest Sonnet
MODEL_LAST_RESORT = "claude-haiku-4-5-20251001"  # Last resort — cheap and fast

try:
    CLIENT = anthropic.Anthropic()  # Reads ANTHROPIC_API_KEY from env
except Exception:
    CLIENT = None

BASE_SYSTEM_PROMPT = """You are Henry, an AI trading analyst and portfolio manager embedded in a multi-strategy trading dashboard.

You are objective and data-driven. You analyze each strategy on its own merits against its stated description and goals — not with bias toward any particular approach. When a strategy underperforms its own benchmarks, you say so. When it outperforms, you acknowledge it.

You speak concisely and directly. No fluff. Use numbers to back up every claim.
When you identify a pattern, explain WHY it matters for tomorrow's trading.
Format currency as $X.XX. Format percentages as X.X%.
Use bullet points sparingly — prefer short paragraphs.
If data is insufficient to draw conclusions, say so rather than speculating.

You maintain a memory of past decisions and their outcomes. When you reference a past observation or lesson, cite it. When you notice a new pattern, flag it as something to remember.

Positions are tagged with types that determine how you evaluate them:
- MOMENTUM: Evaluate on technical signals and momentum. Recommend sell when signals reverse.
- ACCUMULATION: Being intentionally built over time. Recommend DCA on dips to the threshold. Do NOT recommend selling on price weakness. Reference the user's thesis.
- CATALYST: Held for a specific upcoming event. Do NOT recommend selling before the catalyst date. Flag when catalyst is approaching. If catalyst has passed, suggest the user update the holding.
- CONVICTION: Long-term hold. Only flag extreme drawdowns (>40%) or direct thesis invalidation. Do not treat normal volatility as a problem.
Always reference the user's stated thesis when analyzing non-momentum positions."""


WEB_SEARCH_GUIDANCE = """
You have access to web search. Use it when you lack critical context about a stock — for example, upcoming catalysts, recent earnings results, FDA decisions, analyst actions, or why a stock is moving significantly. Do not search for basic price data (you already have that). Search for the WHY behind moves and the WHAT's COMING that your existing data doesn't cover. When you find important information through search, highlight it in your analysis so the user knows you researched it."""


async def _build_system_prompt(ticker: str = None, strategy: str = None, scope: str = "general", enable_web_search: bool = False) -> str:
    """
    Build a dynamic system prompt that includes strategy descriptions,
    memories, prior context notes, track record, and strategy stats.
    """
    import logging
    logger = logging.getLogger(__name__)
    from app.database import async_session
    from app.models import Trader
    from sqlalchemy import select

    sections = [BASE_SYSTEM_PROMPT]

    # Pull strategy descriptions dynamically — separate session to isolate errors
    try:
        async with async_session() as db:
            result = await db.execute(
                select(Trader).where(Trader.is_active == True)
            )
            traders = result.scalars().all()

            if traders:
                strat_lines = []
                for t in traders:
                    # Safely access strategy_description — column may not exist yet
                    desc = getattr(t, "strategy_description", None) or t.description or "No description provided."
                    strat_lines.append(f"  - {t.trader_id} ({t.display_name}): {desc}")
                sections.append(
                    "STRATEGIES YOU ANALYZE:\n" + "\n".join(strat_lines)
                )
    except Exception:
        pass  # Strategy query failed — continue without it

    # Pull memories in a separate session so strategy failure doesn't block this
    try:
        from app.models import HenryMemory
        async with async_session() as db:
            result = await db.execute(
                select(HenryMemory)
                .where(HenryMemory.importance >= 6)
                .order_by(HenryMemory.importance.desc(), HenryMemory.updated_at.desc())
                .limit(20)
            )
            memories = result.scalars().all()

            if memories:
                mem_lines = []
                for m in memories:
                    prefix = f"[{m.memory_type.upper()}]"
                    scope = ""
                    if m.strategy_id:
                        scope += f" ({m.strategy_id})"
                    if m.ticker:
                        scope += f" [{m.ticker}]"
                    validated = " ✓" if m.validated else (" ✗" if m.validated is False else "")
                    mem_lines.append(f"  {prefix}{scope}{validated}: {m.content}")

                    # Increment reference count
                    m.reference_count += 1

                sections.append(
                    "YOUR MEMORY LOG (past observations & lessons — reference these in analysis):\n"
                    + "\n".join(mem_lines)
                )

                await db.commit()

    except Exception:
        pass  # Memory table may not exist yet — continue without it

    # Pull prior context notes (HenryContext) — separate session
    try:
        from app.models import HenryContext
        async with async_session() as db:
            query = (
                select(HenryContext)
                .where(
                    (HenryContext.expires_at.is_(None)) | (HenryContext.expires_at > datetime.utcnow())
                )
                .order_by(HenryContext.created_at.desc())
            )

            if scope == "signal" and (ticker or strategy):
                from sqlalchemy import or_
                filters = []
                if ticker:
                    filters.append(HenryContext.ticker == ticker)
                if strategy:
                    filters.append(HenryContext.strategy == strategy)
                query = query.where(or_(*filters))
                query = query.limit(10)
            else:
                query = query.limit(15)

            result = await db.execute(query)
            contexts = result.scalars().all()

            if contexts:
                ctx_lines = []
                for c in contexts:
                    prefix = f"[{c.context_type.upper()}]"
                    scope_tag = ""
                    if c.ticker:
                        scope_tag += f" [{c.ticker}]"
                    if c.strategy:
                        scope_tag += f" ({c.strategy})"
                    conf = f" conf {c.confidence}/10" if c.confidence else ""
                    ctx_lines.append(f"  {prefix}{scope_tag}{conf}: {c.content}")

                sections.append(
                    "YOUR PRIOR NOTES (past recommendations, outcomes, observations):\n"
                    + "\n".join(ctx_lines)
                )

    except Exception:
        pass  # HenryContext table may not exist yet

    # Pull track record (HenryStats — henry_hit_rate) — separate session
    try:
        from app.models import HenryStats
        async with async_session() as db:
            result = await db.execute(
                select(HenryStats)
                .where(HenryStats.stat_type == "henry_hit_rate")
                .order_by(HenryStats.computed_at.desc())
                .limit(1)
            )
            hit_rate_stat = result.scalar_one_or_none()

            if hit_rate_stat and hit_rate_stat.data:
                d = hit_rate_stat.data
                overall = f"Overall: {d.get('overall_pct', '?')}% ({d.get('total_outcomes', 0)} outcomes)"
                high = f"High conf (7-10): {d.get('high_conf_pct', '?')}%" if d.get('high_conf_pct') is not None else ""
                mid = f"Mid (4-6): {d.get('mid_conf_pct', '?')}%" if d.get('mid_conf_pct') is not None else ""
                parts = [overall]
                if high:
                    parts.append(high)
                if mid:
                    parts.append(mid)
                sections.append("YOUR TRACK RECORD:\n  " + " | ".join(parts))

    except Exception:
        pass

    # Pull strategy stats (HenryStats — strategy_performance) — separate session
    try:
        from app.models import HenryStats as HenryStats2
        async with async_session() as db:
            query = (
                select(HenryStats2)
                .where(HenryStats2.stat_type == "strategy_performance")
                .order_by(HenryStats2.computed_at.desc())
            )
            if scope == "signal" and strategy:
                query = query.where(HenryStats2.strategy == strategy)

            query = query.limit(10)
            result = await db.execute(query)
            stats = result.scalars().all()

            if stats:
                # Deduplicate by strategy (keep most recent)
                seen = set()
                stat_lines = []
                for s in stats:
                    if s.strategy in seen:
                        continue
                    seen.add(s.strategy)
                    d = s.data
                    line = (
                        f"  {s.strategy}: {d.get('win_rate', '?')}% WR, "
                        f"PF {d.get('profit_factor', '?')}, "
                        f"{d.get('trade_count', '?')} trades, "
                        f"avg {d.get('avg_hold_bars', '?')} bars"
                    )
                    streak = d.get('current_streak')
                    if streak:
                        line += f", streak {streak}"
                    stat_lines.append(line)

                sections.append("STRATEGY STATS (30d):\n" + "\n".join(stat_lines))

    except Exception:
        pass

    # Pull recent market headlines from news_cache — separate session
    try:
        from app.services.news_service import news_service
        recent_headlines = await news_service.get_recent_headlines_for_prompt(limit=5)
        if recent_headlines:
            headline_lines = []
            for h in recent_headlines:
                date_str = h.get("published_at", "")
                if date_str:
                    # Shorten to just date + time
                    date_str = date_str[:16].replace("T", " ")
                headline_lines.append(f"  - [{date_str}] {h['headline']}")
            sections.append(
                "RECENT MARKET HEADLINES:\n" + "\n".join(headline_lines)
            )
    except Exception:
        pass  # news_cache table may not exist yet

    # Pull fundamentals data for the specific ticker — separate session
    if ticker:
        try:
            from app.services.fmp_service import get_fundamentals, format_fundamentals_for_prompt
            fund = await get_fundamentals(ticker)
            if fund:
                fund_text = format_fundamentals_for_prompt(fund)
                if fund_text:
                    sections.append(f"FUNDAMENTALS ({ticker}):\n  {fund_text}")
        except Exception:
            pass  # ticker_fundamentals table may not exist yet

        # Pull research notes from henry_context for this ticker
        try:
            from app.models import HenryContext
            async with async_session() as db:
                research_result = await db.execute(
                    select(HenryContext)
                    .where(
                        HenryContext.ticker == ticker,
                        HenryContext.context_type == "research",
                        (HenryContext.expires_at.is_(None)) | (HenryContext.expires_at > datetime.utcnow()),
                    )
                    .order_by(HenryContext.created_at.desc())
                    .limit(5)
                )
                research_notes = research_result.scalars().all()
                if research_notes:
                    research_lines = [f"  - {r.content}" for r in research_notes]
                    sections.append(f"RESEARCH NOTES ({ticker}):\n" + "\n".join(research_lines))
        except Exception:
            pass

    # Add web search guidance if enabled
    if enable_web_search:
        sections.append(WEB_SEARCH_GUIDANCE.strip())

    return "\n\n".join(sections)


# Synchronous wrapper for backward compatibility
SYSTEM_PROMPT = BASE_SYSTEM_PROMPT  # Fallback for sync calls


# ─── HELPERS ─────────────────────────────────────────────────────────────────

def _call_claude(prompt: str, max_tokens: int = 1500, system_override: str = None) -> str:
    """Single Claude API call with system prompt. Falls back to older model on BadRequest."""
    if CLIENT is None:
        return "AI analysis unavailable — ANTHROPIC_API_KEY not configured."
    import logging
    logger = logging.getLogger(__name__)

    system = system_override or SYSTEM_PROMPT

    last_error = None
    for model in [MODEL, MODEL_FALLBACK, MODEL_LAST_RESORT]:
        try:
            response = CLIENT.messages.create(
                model=model,
                max_tokens=max_tokens,
                system=system,
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


async def _call_claude_async(prompt: str, max_tokens: int = 1500, ticker: str = None, strategy: str = None, scope: str = "general", function_name: str = "general", enable_web_search: bool = False) -> str:
    """Async wrapper that builds the dynamic system prompt and routes through the dual AI provider."""
    from app.services.ai_provider import call_ai
    system = await _build_system_prompt(ticker=ticker, strategy=strategy, scope=scope)
    return await call_ai(system, prompt, function_name=function_name, max_tokens=max_tokens, enable_web_search=enable_web_search)


async def save_memory(
    content: str,
    memory_type: str = "observation",
    strategy_id: str = None,
    ticker: str = None,
    importance: int = 5,
    source: str = "system",
) -> None:
    """Save a memory entry for Henry to reference in future analysis."""
    try:
        from app.database import async_session
        from app.models import HenryMemory

        async with async_session() as db:
            memory = HenryMemory(
                memory_type=memory_type,
                strategy_id=strategy_id,
                ticker=ticker,
                content=content,
                importance=importance,
                source=source,
            )
            db.add(memory)
            await db.commit()
    except Exception:
        pass  # Non-blocking


async def save_context(
    content: str,
    context_type: str,  # recommendation | outcome | observation | pattern | portfolio_note | user_decision
    ticker: str = None,
    strategy: str = None,
    portfolio_id: str = None,
    confidence: int = None,
    action_id: str = None,
    trade_id: str = None,
    expires_days: int = None,
) -> None:
    """Save a context entry for Henry to reference in future prompts."""
    try:
        from app.database import async_session
        from app.models import HenryContext

        expires_at = None
        if expires_days:
            expires_at = datetime.utcnow() + timedelta(days=expires_days)

        async with async_session() as db:
            ctx = HenryContext(
                content=content,
                context_type=context_type,
                ticker=ticker,
                strategy=strategy,
                portfolio_id=portfolio_id,
                confidence=confidence,
                action_id=action_id,
                trade_id=trade_id,
                expires_at=expires_at,
            )
            db.add(ctx)
            await db.commit()
    except Exception:
        pass  # Non-blocking


async def _extract_and_save_context(
    analysis_text: str,
    context_type: str = "observation",
    ticker: str = None,
    strategy: str = None,
    portfolio_id: str = None,
    expires_days: int = 14,
) -> None:
    """
    After Henry generates analysis, ask AI to extract key conclusions
    and save them as HenryContext entries. Mirrors extract_and_save_memories().
    """
    try:
        from app.services.ai_provider import call_ai
        system = "Extract 1-2 key one-sentence conclusions from this trading analysis. Return a JSON array of objects with keys: content (1 sentence), ticker (null or ticker symbol), strategy (null or strategy slug), confidence (1-10 or null)."
        raw = await call_ai(system, analysis_text, function_name="memory_extraction", max_tokens=400)

        raw = raw.strip().replace("```json", "").replace("```", "").strip()
        items = json.loads(raw)

        if isinstance(items, list):
            for item in items[:2]:
                await save_context(
                    content=item.get("content", ""),
                    context_type=context_type,
                    ticker=item.get("ticker") or ticker,
                    strategy=item.get("strategy") or strategy,
                    portfolio_id=portfolio_id,
                    confidence=item.get("confidence"),
                    expires_days=expires_days,
                )
    except Exception:
        pass  # Non-blocking


async def extract_and_save_memories(analysis_text: str, source: str = "briefing") -> None:
    """
    After Henry generates analysis, ask AI to extract key observations
    worth remembering for future analysis.
    """
    try:
        from app.services.ai_provider import call_ai
        system = "Extract 1-3 key observations from this trading analysis that would be useful to remember for future decisions. Return a JSON array of objects with keys: content (1 sentence), memory_type (observation|lesson|strategy_note), strategy_id (null or strategy slug), ticker (null or ticker symbol), importance (1-10)."
        raw = await call_ai(system, analysis_text, function_name="memory_extraction", max_tokens=500)

        raw = raw.strip().replace("```json", "").replace("```", "").strip()
        memories = json.loads(raw)

        if isinstance(memories, list):
            for m in memories[:3]:  # Cap at 3 memories per analysis
                await save_memory(
                    content=m.get("content", ""),
                    memory_type=m.get("memory_type", "observation"),
                    strategy_id=m.get("strategy_id"),
                    ticker=m.get("ticker"),
                    importance=m.get("importance", 5),
                    source=source,
                )
    except Exception:
        pass  # Non-blocking


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

async def nightly_review(todays_trades: list[dict], recent_history: list[dict] = None) -> str:
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

    import asyncio
    from app.services.ai_provider import call_ai
    system = await _build_system_prompt()
    return await call_ai(system, prompt, function_name="trade_review", max_tokens=1500)


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


async def morning_briefing(
    open_positions: list[dict],
    yesterdays_trades: list[dict],
    market_intel: dict = None,
    cumulative_stats: dict = None,
    holdings_context: str = None,
) -> str:
    """
    Generate enhanced morning briefing with full market intelligence.
    Now async — builds dynamic system prompt with strategy descriptions + memory.
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
Reference your memory log where relevant — if you've seen patterns before on these tickers or strategies, call them out.

{intel_text}

OPEN STRATEGY POSITIONS (carrying over):
{positions_text}

MANUAL HOLDINGS:
{holdings_text}

YESTERDAY'S ACTIVITY:
{yesterday_text}

CUMULATIVE STRATEGY PERFORMANCE (30 days):
{stats_text}

Write a 6-section briefing. ALL recommendations must be specific to the holdings listed above. Do not give advice about stocks not in the portfolio.

1. **MARKET OVERVIEW** — What happened overnight, where are futures/indices, VIX regime, any gap risks on held tickers. Reference specific numbers.

2. **NEWS & EVENTS** — Key headlines affecting your portfolio or watchlist. Flag any earnings within the week for held tickers. Mention sector rotation (what's leading/lagging) and whether your holdings are in favorable sectors.

3. **PORTFOLIO STATUS** — For EACH holding: current P&L, key levels, and a brief assessment. Respect position types: don't recommend selling accumulation or catalyst positions on price weakness. Reference the user's thesis for non-momentum positions. Show total exposure and concentration risk.

4. **PRICE TARGETS** — For each held ticker, provide:
   - Entry target: a price level where adding to the position makes sense (or "fully allocated" if at max)
   - Exit/trim target: a price level to consider taking profits or reducing exposure
   - Stop level: where the thesis breaks and the position should be reconsidered
   Base these on technical levels, backtest data, and the position's archetype.

5. **YESTERDAY'S TAKEAWAY** — What worked, what didn't, patterns in exit signals. One specific data-backed lesson.

6. **TODAY'S GAME PLAN** — Specific levels to watch on held tickers, which strategies are in favorable conditions given today's VIX/trend, any trades to be cautious about.

Be specific. Use dollar amounts and percentages. Reference actual headlines and sector data. No generic advice like "stay disciplined" — give me actionable intelligence.
Keep it under 600 words."""

    # Use async call that includes strategy descriptions + memory
    from app.services.ai_provider import call_ai
    system = await _build_system_prompt(scope="briefing")
    result = await call_ai(system, prompt, function_name="morning_briefing", max_tokens=2500)

    # Extract and save key observations from the briefing (non-blocking, cheap)
    import asyncio
    asyncio.create_task(extract_and_save_memories(result, source="briefing"))

    # Extract and save context notes from the briefing (non-blocking)
    asyncio.create_task(_extract_and_save_context(result, context_type="observation", expires_days=14))

    return result


# ─── FEATURE 3: NATURAL LANGUAGE QUERY ───────────────────────────────────────

async def query_trades(
    question: str,
    all_trades: list[dict],
    open_positions: list[dict] = None,
    holdings_context: str = None,
) -> str:
    """
    Answer a natural language question about trade history and portfolio.

    The LLM receives the full trade dataset plus manual holdings as context
    and answers analytical questions.

    Args:
        question: User's natural language question
        all_trades: Trade history (entries + exits)
        open_positions: Current open positions from strategies
        holdings_context: Text summary of manual portfolio holdings
    """
    trades_text = _format_trades_for_prompt(all_trades)
    positions_text = _format_positions_for_prompt(open_positions) if open_positions else "None."
    holdings_text = holdings_context or "No manual holdings."
    
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

CURRENT STRATEGY POSITIONS:
{positions_text}

MANUAL PORTFOLIO HOLDINGS:
{holdings_text}

FULL TRADE LOG:
{trades_text}

Consider BOTH strategy positions AND manual holdings when answering portfolio questions.
Answer concisely. If the data doesn't contain enough info to answer, say so.
If the question involves a comparison, use a small table.
Keep it under 200 words unless the question requires more detail."""

    from app.services.ai_provider import call_ai
    system = await _build_system_prompt(enable_web_search=True)
    return await call_ai(system, prompt, function_name="ask_henry", max_tokens=1000, question_text=question, enable_web_search=True)


# ─── FEATURE 4: STRATEGY CONFLICT RESOLUTION ────────────────────────────────

async def resolve_conflict(
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

    from app.services.ai_provider import call_ai
    system = await _build_system_prompt(ticker=ticker, scope="signal", enable_web_search=True)
    raw = await call_ai(system, prompt, function_name="conflict_resolution", max_tokens=500, enable_web_search=True)

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
        portfolio_id: str | None = None  # Scope advice to a specific portfolio
    
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
            # Check cache: has today's briefing already been generated? (US Eastern)
            from zoneinfo import ZoneInfo
            now_et = datetime.now(ZoneInfo("America/New_York"))
            today_start_et = now_et.replace(hour=0, minute=0, second=0, microsecond=0)
            # Convert to UTC for DB query
            today_start_utc = today_start_et.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)
            async with async_session() as db:
                result = await db.execute(
                    select(MarketSummary)
                    .where(
                        MarketSummary.summary_type == "daily_briefing",
                        MarketSummary.generated_at >= today_start_utc,
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

            # Add position archetype context for ALL holdings with thesis or non-momentum type
            position_context_lines = []
            for h in holdings:
                pos_type = getattr(h, "position_type", None) or "momentum"
                thesis = getattr(h, "thesis", None)
                # Include if non-momentum OR if has a thesis (even momentum with thesis)
                if pos_type != "momentum" or thesis:
                    ctx = f"  [{pos_type.upper()}] {h.ticker}"
                    if thesis:
                        ctx += f" — Thesis: {thesis}"
                    cat_date = getattr(h, "catalyst_date", None)
                    if cat_date:
                        from datetime import date as date_type
                        days_until = (cat_date - date_type.today()).days
                        cat_desc = getattr(h, "catalyst_description", None) or "event"
                        ctx += f" | Catalyst: {cat_desc} in {days_until} days ({cat_date})"
                    max_alloc = getattr(h, "max_allocation_pct", None)
                    if max_alloc:
                        ctx += f" | Max alloc: {max_alloc}%"
                    dca_on = getattr(h, "dca_enabled", False)
                    if dca_on:
                        dca_thresh = getattr(h, "dca_threshold_pct", None) or 0
                        ctx += f" | DCA enabled (threshold: {dca_thresh}%)"
                    avg_c = getattr(h, "avg_cost", None)
                    if avg_c:
                        total_sh = getattr(h, "total_shares", None) or 0
                        ctx += f" | Avg cost: ${avg_c:.2f}, {total_sh:.4f} shares"
                    position_context_lines.append(ctx)

            if position_context_lines:
                holdings_context = (holdings_context or "") + "\n\nPOSITION CONTEXT (non-momentum):\n" + "\n".join(position_context_lines)

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

        # Generate briefing with full context (now async — includes strategy descriptions + memory)
        result = await morning_briefing(
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
            from sqlalchemy import select as sa_select
            from app.database import async_session
            from app.models.portfolio_holding import PortfolioHolding
            from app.models.portfolio import Portfolio
            from app.services.price_service import price_service

            all_trades = await get_trades_fn(days_back=30)
            positions = await get_positions_fn()

            # Fetch holdings — scoped to specific portfolio if provided
            holdings_context = None
            portfolio_name = None
            try:
                async with async_session() as db:
                    # Get portfolio name if scoped
                    if req.portfolio_id:
                        port_result = await db.execute(
                            sa_select(Portfolio).where(Portfolio.id == req.portfolio_id)
                        )
                        portfolio = port_result.scalar_one_or_none()
                        if portfolio:
                            portfolio_name = portfolio.name

                    query = sa_select(PortfolioHolding).where(PortfolioHolding.is_active == True)
                    if req.portfolio_id:
                        query = query.where(PortfolioHolding.portfolio_id == req.portfolio_id)

                    result = await db.execute(query)
                    holdings = result.scalars().all()
                    if holdings:
                        lines = []
                        total_value = 0.0
                        total_cost = 0.0
                        for h in holdings:
                            cp = price_service.get_price(h.ticker) or h.entry_price
                            cost = h.entry_price * h.qty
                            value = cp * h.qty
                            total_cost += cost
                            total_value += value
                            if h.direction == "long":
                                pnl = (cp - h.entry_price) / h.entry_price * 100
                            else:
                                pnl = (h.entry_price - cp) / h.entry_price * 100
                            alloc = 0.0  # will compute after totaling
                            lines.append({
                                "text": f"  {h.ticker} | {h.direction.upper()} | {h.qty} shares @ ${h.entry_price:.2f} | "
                                        f"current ${cp:.2f} | {pnl:+.2f}% | strategy: {h.strategy_name or 'manual'}",
                                "value": value,
                            })
                        # Add allocation percentages
                        formatted = []
                        for l in lines:
                            alloc_pct = (l["value"] / total_value * 100) if total_value > 0 else 0
                            formatted.append(f"{l['text']} | allocation: {alloc_pct:.1f}%")
                        total_pnl_pct = ((total_value - total_cost) / total_cost * 100) if total_cost > 0 else 0
                        header = f"Portfolio: {portfolio_name or 'All'} | Total value: ${total_value:,.2f} | Cost basis: ${total_cost:,.2f} | Return: {total_pnl_pct:+.2f}%"
                        holdings_context = header + "\n" + "\n".join(formatted)

                        # Add position archetype context for ALL holdings with thesis or non-momentum
                        pos_ctx_lines = []
                        for h in holdings:
                            pos_type = getattr(h, "position_type", None) or "momentum"
                            thesis = getattr(h, "thesis", None)
                            if pos_type != "momentum" or thesis:
                                ctx = f"  [{pos_type.upper()}] {h.ticker}"
                                if thesis:
                                    ctx += f" — Thesis: {thesis}"
                                cat_date = getattr(h, "catalyst_date", None)
                                if cat_date:
                                    from datetime import date as date_type
                                    days_until = (cat_date - date_type.today()).days
                                    cat_desc = getattr(h, "catalyst_description", None) or "event"
                                    ctx += f" | Catalyst: {cat_desc} in {days_until} days ({cat_date})"
                                max_alloc = getattr(h, "max_allocation_pct", None)
                                if max_alloc:
                                    ctx += f" | Max alloc: {max_alloc}%"
                                dca_on = getattr(h, "dca_enabled", False)
                                if dca_on:
                                    dca_thresh = getattr(h, "dca_threshold_pct", None) or 0
                                    ctx += f" | DCA enabled (threshold: {dca_thresh}%)"
                                avg_c = getattr(h, "avg_cost", None)
                                if avg_c:
                                    total_sh = getattr(h, "total_shares", None) or 0
                                    ctx += f" | Avg cost: ${avg_c:.2f}, {total_sh:.4f} shares"
                                pos_ctx_lines.append(ctx)

                        if pos_ctx_lines:
                            holdings_context += "\n\nPOSITION CONTEXT (non-momentum):\n" + "\n".join(pos_ctx_lines)
            except Exception:
                pass

            if not all_trades and not positions and not holdings_context:
                return {"answer": "No trading data available yet. Add manual holdings or connect TradingView strategies to start getting portfolio advice.", "trades_in_context": 0}

            # Enhance the question with portfolio scope
            scoped_question = req.question
            if portfolio_name:
                scoped_question = (
                    f"[Context: This question is about the '{portfolio_name}' portfolio specifically. "
                    f"Focus your analysis and recommendations ONLY on the holdings listed below. "
                    f"Manual holdings are legitimate positions the user chose — treat them with respect. "
                    f"Don't criticize holdings for being manually entered. Instead, provide constructive "
                    f"recommendations based on current market conditions, position sizing, and diversification.]\n\n"
                    f"{req.question}"
                )

            # Check cache for portfolio-specific recommendations (5-day TTL)
            import hashlib
            cache_key = None
            if req.portfolio_id:
                # Cache key = hash of question + portfolio_id (ignores market data changes)
                q_hash = hashlib.md5(req.question.lower().strip().encode()).hexdigest()[:12]
                cache_key = f"query:{req.portfolio_id}:{q_hash}"

                try:
                    from app.models.henry_cache import HenryCache
                    async with async_session() as db:
                        cached = await db.execute(
                            select(HenryCache).where(
                                HenryCache.cache_key == cache_key,
                                HenryCache.is_stale == False,
                                HenryCache.generated_at >= datetime.utcnow() - timedelta(days=5),
                            )
                        )
                        hit = cached.scalar_one_or_none()
                        if hit:
                            import json
                            content = json.loads(hit.content) if isinstance(hit.content, str) else hit.content
                            return {
                                "answer": content.get("answer", ""),
                                "trades_in_context": content.get("trades_in_context", 0),
                                "cached": True,
                                "cached_at": hit.generated_at.isoformat(),
                            }
                except Exception:
                    pass

            result = await query_trades(scoped_question, all_trades, positions, holdings_context=holdings_context)

            # Extract and save research findings (non-blocking)
            if result:
                try:
                    from app.services.research_service import extract_and_save_research
                    # Try to extract a ticker from the question for scoped research
                    import re
                    ticker_match = re.search(r'\b([A-Z]{1,5})\b', req.question)
                    q_ticker = ticker_match.group(1) if ticker_match else None
                    asyncio.create_task(extract_and_save_research(result, ticker=q_ticker))
                except Exception:
                    pass

            # Cache the result for 5 days
            if cache_key and result:
                try:
                    import json as _json
                    from app.models.henry_cache import HenryCache
                    async with async_session() as db:
                        # Upsert: delete old cache for this key
                        old = await db.execute(select(HenryCache).where(HenryCache.cache_key == cache_key))
                        old_hit = old.scalar_one_or_none()
                        if old_hit:
                            await db.delete(old_hit)
                            await db.flush()
                        db.add(HenryCache(
                            cache_key=cache_key,
                            cache_type="portfolio_query",
                            content=_json.dumps({"answer": result, "trades_in_context": len(all_trades)}),
                            ticker=None,
                            strategy=None,
                        ))
                        await db.commit()
                except Exception:
                    pass

            return {"answer": result, "trades_in_context": len(all_trades)}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
    
    @app.post("/api/ai/conflict")
    async def ai_conflict(signals: list[dict]):
        try:
            recent = await get_trades_fn(days_back=14)
            market = await get_market_data_fn() if get_market_data_fn else None
            result = await resolve_conflict(signals, recent, market)
            return result
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    # ─── HENRY CONTEXT & STATS ENDPOINTS ──────────────────────────────────

    @app.get("/api/ai/context")
    async def get_henry_context(ticker: str = None, context_type: str = None, limit: int = 50):
        """Return Henry's context entries, optionally filtered by ticker or type."""
        try:
            from app.database import async_session
            from app.models import HenryContext
            from sqlalchemy import select

            async with async_session() as db:
                query = (
                    select(HenryContext)
                    .where(
                        (HenryContext.expires_at.is_(None)) | (HenryContext.expires_at > datetime.utcnow())
                    )
                    .order_by(HenryContext.created_at.desc())
                    .limit(limit)
                )
                if ticker:
                    query = query.where(HenryContext.ticker == ticker.upper())
                if context_type:
                    query = query.where(HenryContext.context_type == context_type)

                result = await db.execute(query)
                contexts = result.scalars().all()

            return [
                {
                    "id": c.id,
                    "ticker": c.ticker,
                    "strategy": c.strategy,
                    "context_type": c.context_type,
                    "content": c.content,
                    "confidence": c.confidence,
                    "created_at": c.created_at.isoformat() if c.created_at else None,
                    "expires_at": c.expires_at.isoformat() if c.expires_at else None,
                }
                for c in contexts
            ]
        except Exception as e:
            return []

    @app.delete("/api/ai/context/{context_id}")
    async def delete_henry_context(context_id: str):
        """Delete a specific henry_context entry."""
        try:
            from app.database import async_session
            from app.models import HenryContext
            from sqlalchemy import select

            async with async_session() as db:
                result = await db.execute(
                    select(HenryContext).where(HenryContext.id == context_id)
                )
                ctx = result.scalar_one_or_none()
                if not ctx:
                    raise HTTPException(404, "Context entry not found")
                await db.delete(ctx)
                await db.commit()
            return {"deleted": context_id}
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(500, str(e))

    @app.get("/api/ai/stats")
    async def get_henry_stats():
        """Return Henry's computed stats."""
        try:
            from app.database import async_session
            from app.models import HenryStats
            from sqlalchemy import select

            async with async_session() as db:
                result = await db.execute(
                    select(HenryStats)
                    .order_by(HenryStats.computed_at.desc())
                    .limit(50)
                )
                stats = result.scalars().all()

            return [
                {
                    "id": s.id,
                    "stat_type": s.stat_type,
                    "ticker": s.ticker,
                    "strategy": s.strategy,
                    "data": s.data,
                    "period_days": s.period_days,
                    "computed_at": s.computed_at.isoformat() if s.computed_at else None,
                }
                for s in stats
            ]
        except Exception:
            return []

    @app.get("/api/ai/fundamentals/{ticker}")
    async def get_ticker_fundamentals(ticker: str):
        """Return cached fundamentals for a ticker."""
        try:
            from app.services.fmp_service import get_fundamentals
            fund = await get_fundamentals(ticker.upper())
            if not fund:
                raise HTTPException(404, f"No fundamentals for {ticker}")
            return {
                "ticker": fund.ticker,
                "company_name": fund.company_name,
                "sector": fund.sector,
                "industry": fund.industry,
                "market_cap": fund.market_cap,
                "description": fund.description,
                "company_description": getattr(fund, "company_description", None),
                "earnings_date": fund.earnings_date.isoformat() if fund.earnings_date else None,
                "earnings_time": fund.earnings_time,
                "analyst_target_low": fund.analyst_target_low,
                "analyst_target_high": fund.analyst_target_high,
                "analyst_target_consensus": fund.analyst_target_consensus,
                "analyst_rating": fund.analyst_rating,
                "analyst_count": fund.analyst_count,
                "eps_estimate_current": fund.eps_estimate_current,
                "eps_actual_last": fund.eps_actual_last,
                "eps_surprise_last": fund.eps_surprise_last,
                "revenue_estimate_current": fund.revenue_estimate_current,
                "revenue_actual_last": fund.revenue_actual_last,
                "pe_ratio": fund.pe_ratio,
                "forward_pe": getattr(fund, "forward_pe", None),
                "beta": getattr(fund, "beta", None),
                "profit_margin": getattr(fund, "profit_margin", None),
                "roe": getattr(fund, "roe", None),
                "debt_to_equity": getattr(fund, "debt_to_equity", None),
                "dcf_value": getattr(fund, "dcf_value", None),
                "dcf_diff_pct": getattr(fund, "dcf_diff_pct", None),
                "dividend_yield": getattr(fund, "dividend_yield", None),
                "short_interest_pct": fund.short_interest_pct,
                "insider_net_90d": getattr(fund, "insider_net_90d", None),
                "institutional_ownership_pct": getattr(fund, "institutional_ownership_pct", None),
                "insider_transactions_90d": fund.insider_transactions_90d,
                "updated_at": fund.updated_at.isoformat() if fund.updated_at else None,
            }
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(500, str(e))
