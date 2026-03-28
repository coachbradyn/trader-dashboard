"""
Watchlist AI Summary Service
==============================
Generates and caches per-ticker AI summaries for watchlist tickers.
Summaries are only regenerated when significant new data arrives.
"""

import json
import logging
from datetime import datetime, timedelta

import anthropic

from app.database import async_session
from app.models.watchlist_summary import WatchlistSummary
from app.models.indicator_alert import IndicatorAlert
from app.models.trade import Trade
from app.models.trader import Trader
from app.models.henry_context import HenryContext
from app.models.backtest_import import BacktestImport

from sqlalchemy import select, func, desc, and_
from sqlalchemy.orm import selectinload

logger = logging.getLogger(__name__)

MODEL = "claude-sonnet-4-5-20250929"
MODEL_FALLBACK = "claude-sonnet-4-6"
MODEL_LAST_RESORT = "claude-haiku-4-5-20251001"

try:
    CLIENT = anthropic.Anthropic()
except Exception:
    CLIENT = None


async def generate_watchlist_summary(ticker: str) -> None:
    """
    Generate a cached AI summary for a watchlist ticker.
    Non-blocking — called as a background task.
    Overwrites the existing summary for this ticker.
    """
    if CLIENT is None:
        logger.warning("Watchlist summary skipped — no Anthropic client")
        return

    try:
        async with async_session() as db:
            # 1. Latest indicator signals (most recent per indicator)
            subq = (
                select(
                    IndicatorAlert.indicator,
                    func.max(IndicatorAlert.created_at).label("max_created_at"),
                )
                .where(IndicatorAlert.ticker == ticker)
                .group_by(IndicatorAlert.indicator)
                .subquery()
            )
            result = await db.execute(
                select(IndicatorAlert)
                .join(
                    subq,
                    and_(
                        IndicatorAlert.indicator == subq.c.indicator,
                        IndicatorAlert.created_at == subq.c.max_created_at,
                    ),
                )
                .where(IndicatorAlert.ticker == ticker)
                .order_by(desc(IndicatorAlert.created_at))
            )
            signals = result.scalars().all()

            signal_lines = []
            bullish = 0
            bearish = 0
            for s in signals:
                signal_lines.append(
                    f"  {s.indicator}: {s.signal} (value={s.value:.2f}, tf={s.timeframe or '?'}, at={s.created_at.isoformat()})"
                )
                if s.signal == "bullish":
                    bullish += 1
                elif s.signal == "bearish":
                    bearish += 1
            signals_text = "\n".join(signal_lines) if signal_lines else "No indicator signals."

            # 2. Open strategy positions (dynamic from traders table)
            pos_result = await db.execute(
                select(Trade)
                .options(selectinload(Trade.trader))
                .where(Trade.ticker == ticker, Trade.status == "open")
            )
            open_positions = pos_result.scalars().all()

            positions_text = "None."
            if open_positions:
                from app.services.price_service import price_service
                pos_lines = []
                for t in open_positions:
                    current_price = price_service.get_price(t.ticker) or t.entry_price
                    if t.direction == "long":
                        pnl = ((current_price - t.entry_price) / t.entry_price * 100)
                    else:
                        pnl = ((t.entry_price - current_price) / t.entry_price * 100)
                    pos_lines.append(
                        f"  {t.trader.display_name} ({t.trader.trader_id}): {t.direction.upper()} @ ${t.entry_price:.2f}, "
                        f"current ${current_price:.2f}, pnl {pnl:+.2f}%"
                    )
                positions_text = "\n".join(pos_lines)

            # 3. Recent trade history
            history_cutoff = datetime.utcnow() - timedelta(days=30)
            hist_result = await db.execute(
                select(Trade)
                .options(selectinload(Trade.trader))
                .where(
                    Trade.ticker == ticker,
                    Trade.status == "closed",
                    Trade.entry_time >= history_cutoff,
                )
                .order_by(desc(Trade.exit_time))
                .limit(15)
            )
            history = hist_result.scalars().all()

            history_text = "No recent history."
            if history:
                hist_lines = []
                for t in history:
                    hist_lines.append(
                        f"  {t.trader.display_name}: {t.direction.upper()} @ ${t.entry_price:.2f} -> "
                        f"${t.exit_price:.2f if t.exit_price else 0}, pnl {t.pnl_percent or 0:+.2f}%, "
                        f"reason={t.exit_reason or '?'}"
                    )
                history_text = "\n".join(hist_lines)

            # 4. Backtest stats
            bt_result = await db.execute(
                select(BacktestImport)
                .where(BacktestImport.ticker == ticker)
                .order_by(desc(BacktestImport.imported_at))
                .limit(5)
            )
            backtests = bt_result.scalars().all()

            bt_text = "No backtest data."
            if backtests:
                bt_lines = []
                for b in backtests:
                    bt_lines.append(
                        f"  {b.strategy_name}: {b.trade_count} trades, "
                        f"WR {b.win_rate:.1f}%, PF {b.profit_factor:.2f}, "
                        f"avg gain {b.avg_gain_pct:.2f}%" if b.win_rate else f"  {b.strategy_name}: {b.trade_count} trades"
                    )
                bt_text = "\n".join(bt_lines)

            # 5. Henry's prior notes
            ctx_result = await db.execute(
                select(HenryContext)
                .where(
                    HenryContext.ticker == ticker,
                    (HenryContext.expires_at.is_(None)) | (HenryContext.expires_at > datetime.utcnow()),
                )
                .order_by(desc(HenryContext.created_at))
                .limit(5)
            )
            contexts = ctx_result.scalars().all()

            ctx_text = "No prior notes."
            if contexts:
                ctx_lines = [f"  [{c.context_type}] {c.content}" for c in contexts]
                ctx_text = "\n".join(ctx_lines)

            # 6. Get total alert count for staleness tracking
            alert_count_result = await db.execute(
                select(func.count(IndicatorAlert.id))
                .where(IndicatorAlert.ticker == ticker)
            )
            current_alert_count = alert_count_result.scalar() or 0

            # 7. Consensus summary
            consensus = "no data"
            if bullish > bearish:
                consensus = f"bullish ({bullish}B vs {bearish}B)"
            elif bearish > bullish:
                consensus = f"bearish ({bearish}B vs {bullish}B)"
            elif bullish + bearish > 0:
                consensus = f"mixed ({bullish}B vs {bearish}B)"

            # Build prompt
            prompt = f"""Analyze {ticker} for the watchlist dashboard. Provide a concise 2-4 sentence analysis.

LATEST INDICATOR SIGNALS (most recent per indicator):
{signals_text}

SIGNAL CONSENSUS: {consensus}

OPEN STRATEGY POSITIONS:
{positions_text}

RECENT TRADE HISTORY (30d):
{history_text}

BACKTEST STATS:
{bt_text}

PRIOR NOTES:
{ctx_text}

Provide a 2-4 sentence analysis covering:
1. Signal consensus — are indicators aligned?
2. Key observations — what stands out?
3. Would you act on this setup? Why or why not?

Be direct and specific. Use numbers. No fluff."""

            system = """You are Henry, an AI trading analyst. You're writing a brief watchlist summary for a specific ticker.
Be concise (2-4 sentences), data-driven, and actionable. Format currency as $X.XX. Format percentages as X.X%."""

            # Call AI (routes through dual provider system)
            from app.services.ai_provider import call_ai
            summary_text = await call_ai(system, prompt, function_name="watchlist_summary", max_tokens=500)

            if not summary_text or summary_text == "AI analysis temporarily unavailable.":
                logger.error(f"AI call failed for watchlist summary of {ticker}")
                return

            # Upsert the summary
            existing = await db.execute(
                select(WatchlistSummary).where(WatchlistSummary.ticker == ticker)
            )
            ws = existing.scalar_one_or_none()

            if ws:
                ws.summary = summary_text
                ws.alert_count_at_generation = current_alert_count
                ws.generated_at = datetime.utcnow()
            else:
                ws = WatchlistSummary(
                    ticker=ticker,
                    summary=summary_text,
                    alert_count_at_generation=current_alert_count,
                )
                db.add(ws)

            await db.commit()
            logger.info(f"Watchlist summary generated for {ticker}")

    except Exception as e:
        logger.error(f"Watchlist summary generation failed for {ticker}: {e}", exc_info=True)


async def check_and_regenerate_if_stale(ticker: str) -> None:
    """
    Check if a watchlist ticker's summary is stale and regenerate if needed.
    Called after new alerts or trade signals arrive.
    """
    try:
        async with async_session() as db:
            # Check if ticker is on watchlist
            result = await db.execute(
                select(WatchlistTicker)
                .where(WatchlistTicker.ticker == ticker, WatchlistTicker.is_active == True)
            )
            wt = result.scalar_one_or_none()
            if not wt:
                return  # Not on watchlist, skip

            # Check if summary exists and is stale
            summary_result = await db.execute(
                select(WatchlistSummary).where(WatchlistSummary.ticker == ticker)
            )
            summary = summary_result.scalar_one_or_none()

            if not summary:
                # No summary yet — generate one
                import asyncio
                asyncio.create_task(generate_watchlist_summary(ticker))
                return

            # Count alerts since generation
            alert_count_result = await db.execute(
                select(func.count(IndicatorAlert.id))
                .where(IndicatorAlert.ticker == ticker)
            )
            current_count = alert_count_result.scalar() or 0
            new_alerts = current_count - summary.alert_count_at_generation

            age_hours = (datetime.utcnow() - summary.generated_at).total_seconds() / 3600

            # Check for trade signals since generation
            trade_result = await db.execute(
                select(func.count(Trade.id))
                .where(Trade.ticker == ticker, Trade.created_at > summary.generated_at)
            )
            trades_since = trade_result.scalar() or 0

            if new_alerts > 2 or age_hours > 4 or trades_since > 0:
                import asyncio
                asyncio.create_task(generate_watchlist_summary(ticker))

    except Exception as e:
        logger.warning(f"Staleness check failed for {ticker}: {e}")


# Need the import here to avoid circular imports at module level
from app.models.watchlist_ticker import WatchlistTicker
