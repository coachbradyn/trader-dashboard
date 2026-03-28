"""
Scheduled Jobs
==============
APScheduler-based background jobs for:
1. Morning market summary (9:30 AM ET)
2. Nightly market summary (4:15 PM ET)
3. Screener analysis refresh (every 30 minutes during market hours)
"""

import logging
from datetime import datetime, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler()


async def _generate_morning_summary():
    """Generate morning market summary."""
    logger.info("Generating morning summary...")
    try:
        # Invalidate stale caches for the new trading day
        from app.services.henry_cache import invalidate_by_type
        from app.database import async_session as _as
        async with _as() as cdb:
            await invalidate_by_type(cdb, "ticker_analysis")
            await invalidate_by_type(cdb, "signal_eval")
            await cdb.commit()
    except Exception:
        pass
    try:
        from app.database import async_session
        from app.models import Trade, IndicatorAlert, MarketSummary
        from app.models.indicator_alert import IndicatorAlert
        from app.models.market_summary import MarketSummary
        from app.services.screener_ai import generate_market_summary
        from app.services.price_service import price_service
        from sqlalchemy import select
        from sqlalchemy.orm import selectinload

        async with async_session() as db:
            # Get open positions
            result = await db.execute(
                select(Trade)
                .options(selectinload(Trade.trader))
                .where(Trade.status == "open", Trade.is_simulated == False)
            )
            open_trades = result.scalars().all()

            positions = []
            for t in open_trades:
                current_price = price_service.get_price(t.ticker) or t.entry_price
                pnl_pct = ((current_price - t.entry_price) / t.entry_price * 100) if t.direction == "long" else ((t.entry_price - current_price) / t.entry_price * 100)
                positions.append({
                    "trader": t.trader.trader_id,
                    "dir": t.direction,
                    "ticker": t.ticker,
                    "entry_price": t.entry_price,
                    "current_price": current_price,
                    "pnl_pct": round(pnl_pct, 2),
                })

            # Get yesterday's trades
            yesterday = datetime.utcnow() - timedelta(days=1)
            result = await db.execute(
                select(Trade).where(Trade.created_at >= yesterday, Trade.is_simulated == False)
            )
            yesterday_trades = result.scalars().all()

            # Get screener data (last 12h)
            cutoff = datetime.utcnow() - timedelta(hours=12)
            result = await db.execute(
                select(IndicatorAlert).where(IndicatorAlert.created_at >= cutoff)
            )
            alerts = result.scalars().all()

            # Aggregate by ticker
            ticker_map = {}
            for a in alerts:
                if a.ticker not in ticker_map:
                    ticker_map[a.ticker] = {"ticker": a.ticker, "alert_count": 0, "indicators": set()}
                ticker_map[a.ticker]["alert_count"] += 1
                ticker_map[a.ticker]["indicators"].add(a.indicator)

            top_tickers = sorted(ticker_map.values(), key=lambda x: x["alert_count"], reverse=True)[:5]
            for t in top_tickers:
                t["indicators"] = list(t["indicators"])

            # Generate summary
            content = await generate_market_summary(
                "morning",
                {"positions": positions, "trades": [{"id": t.id} for t in yesterday_trades]},
                {"tickers": list(ticker_map.values()), "alert_count": len(alerts), "top_tickers": top_tickers},
            )

            # Store
            summary = MarketSummary(
                summary_type="morning",
                scope="combined",
                content=content,
                tickers_analyzed=[t["ticker"] for t in top_tickers],
            )
            db.add(summary)
            await db.commit()

            logger.info("Morning summary generated successfully")

    except Exception as e:
        logger.error(f"Morning summary failed: {e}")


async def _generate_nightly_summary():
    """Generate nightly market summary."""
    logger.info("Generating nightly summary...")
    try:
        from app.database import async_session
        from app.models import Trade
        from app.models.indicator_alert import IndicatorAlert
        from app.models.market_summary import MarketSummary
        from app.models.screener_analysis import ScreenerAnalysis
        from app.services.screener_ai import generate_market_summary
        from sqlalchemy import select, desc

        async with async_session() as db:
            # Get today's closed trades
            today_start = datetime.utcnow().replace(hour=0, minute=0, second=0)
            result = await db.execute(
                select(Trade).where(
                    Trade.status == "closed",
                    Trade.exit_time >= today_start,
                )
            )
            closed_today = result.scalars().all()

            day_pnl = sum(t.pnl_dollars or 0 for t in closed_today)

            # Get today's alerts
            result = await db.execute(
                select(IndicatorAlert).where(IndicatorAlert.created_at >= today_start)
            )
            alerts = result.scalars().all()

            ticker_map = {}
            for a in alerts:
                if a.ticker not in ticker_map:
                    ticker_map[a.ticker] = {"ticker": a.ticker, "alert_count": 0, "indicators": set()}
                ticker_map[a.ticker]["alert_count"] += 1
                ticker_map[a.ticker]["indicators"].add(a.indicator)

            top_tickers = sorted(ticker_map.values(), key=lambda x: x["alert_count"], reverse=True)[:5]
            for t in top_tickers:
                t["indicators"] = list(t["indicators"])

            # Get morning picks for scorecard
            result = await db.execute(
                select(ScreenerAnalysis)
                .where(ScreenerAnalysis.generated_at >= today_start)
                .order_by(desc(ScreenerAnalysis.generated_at))
                .limit(1)
            )
            latest_analysis = result.scalar_one_or_none()
            picks_data = latest_analysis.picks if latest_analysis else None

            content = await generate_market_summary(
                "nightly",
                {
                    "closed_today": [{"ticker": t.ticker, "pnl": t.pnl_dollars} for t in closed_today],
                    "day_pnl": f"${day_pnl:.2f}",
                },
                {"tickers": list(ticker_map.values()), "alert_count": len(alerts), "top_tickers": top_tickers},
                picks_data=picks_data,
            )

            summary = MarketSummary(
                summary_type="nightly",
                scope="combined",
                content=content,
                tickers_analyzed=[t["ticker"] for t in top_tickers],
            )
            db.add(summary)
            await db.commit()

            logger.info("Nightly summary generated successfully")

    except Exception as e:
        logger.error(f"Nightly summary failed: {e}")


async def _refresh_screener_analysis():
    """Refresh screener analysis with latest alerts."""
    logger.info("Refreshing screener analysis...")
    try:
        from app.database import async_session
        from app.models.indicator_alert import IndicatorAlert
        from app.models.screener_analysis import ScreenerAnalysis
        from app.services.screener_ai import analyze_screener_signals
        from app.services.chart_service import get_daily_chart
        from sqlalchemy import select, desc

        async with async_session() as db:
            cutoff = datetime.utcnow() - timedelta(hours=24)
            result = await db.execute(
                select(IndicatorAlert)
                .where(IndicatorAlert.created_at >= cutoff)
                .order_by(desc(IndicatorAlert.created_at))
            )
            alerts = result.scalars().all()

            if not alerts:
                logger.info("No alerts to analyze")
                return

            # Build aggregations
            ticker_map = {}
            alerts_list = []
            for a in alerts:
                alerts_list.append({
                    "ticker": a.ticker,
                    "indicator": a.indicator,
                    "value": a.value,
                    "signal": a.signal,
                    "timeframe": a.timeframe,
                    "created_at": a.created_at.isoformat(),
                })
                if a.ticker not in ticker_map:
                    ticker_map[a.ticker] = {
                        "ticker": a.ticker,
                        "alert_count": 0,
                        "indicators": set(),
                        "latest_signal": a.signal,
                    }
                ticker_map[a.ticker]["alert_count"] += 1
                ticker_map[a.ticker]["indicators"].add(a.indicator)

            agg_list = sorted(ticker_map.values(), key=lambda x: x["alert_count"], reverse=True)
            for t in agg_list:
                t["indicators"] = list(t["indicators"])

            # Fetch chart data for top 5 tickers
            chart_data = {}
            for t in agg_list[:5]:
                try:
                    chart_data[t["ticker"]] = await get_daily_chart(t["ticker"], 30)
                except Exception:
                    pass

            # Call AI
            result = await analyze_screener_signals(
                alerts=alerts_list,
                ticker_aggregations=agg_list,
                chart_data=chart_data if chart_data else None,
            )

            # Store
            analysis = ScreenerAnalysis(
                picks=result.get("picks"),
                market_context=result.get("market_context"),
                alerts_analyzed=len(alerts),
            )
            db.add(analysis)
            await db.commit()

            logger.info(f"Screener analysis refreshed: {len(result.get('picks', []))} picks")

    except Exception as e:
        logger.error(f"Screener analysis refresh failed: {e}")


async def _run_threshold_checks():
    """Hourly lightweight portfolio threshold checks (no Claude call)."""
    logger.info("Running portfolio threshold checks...")
    try:
        from app.database import async_session
        from app.services.portfolio_analysis import evaluate_thresholds

        async with async_session() as db:
            await evaluate_thresholds(db)
    except Exception as e:
        logger.error(f"Threshold checks failed: {e}")


async def _run_daily_portfolio_review():
    """Daily deep portfolio review by Henry (Claude call)."""
    logger.info("Running daily portfolio review...")
    try:
        from app.database import async_session
        from app.services.portfolio_analysis import scheduled_review
        from app.services.henry_cache import invalidate_by_type

        # Invalidate cached reviews so fresh analysis runs
        async with async_session() as db:
            await invalidate_by_type(db, "scheduled_review")
            await invalidate_by_type(db, "signal_eval")
            await db.commit()

        async with async_session() as db:
            await scheduled_review(db)
    except Exception as e:
        logger.error(f"Daily portfolio review failed: {e}")


async def _compute_henry_stats():
    """Compute Henry's pre-computed analytics (strategy performance, hit rate, etc.)."""
    logger.info("Computing Henry stats...")
    try:
        from app.services.henry_stats_engine import compute_all_stats
        await compute_all_stats()
    except Exception as e:
        logger.error(f"Henry stats computation failed: {e}")


async def _run_ai_portfolio_review():
    """Daily review of AI portfolio positions by Henry."""
    logger.info("Running AI portfolio scheduled review...")
    try:
        from app.services.ai_portfolio import scheduled_ai_portfolio_review
        await scheduled_ai_portfolio_review()
    except Exception as e:
        logger.error(f"AI portfolio review failed: {e}")


async def _cleanup_expired_context():
    """Delete expired HenryContext rows and old non-outcome rows."""
    logger.info("Cleaning up expired Henry context...")
    try:
        from app.database import async_session
        from app.models import HenryContext
        from sqlalchemy import delete, and_, or_

        async with async_session() as db:
            now = datetime.utcnow()

            # Delete expired rows (where expires_at < now)
            await db.execute(
                delete(HenryContext).where(
                    HenryContext.expires_at.isnot(None),
                    HenryContext.expires_at < now,
                )
            )

            # Delete non-outcome rows older than 90 days
            cutoff_90d = now - timedelta(days=90)
            await db.execute(
                delete(HenryContext).where(
                    HenryContext.context_type != "outcome",
                    HenryContext.created_at < cutoff_90d,
                )
            )

            await db.commit()
            logger.info("Henry context cleanup complete")

        # Also clean up old henry_cache entries
        from app.services.henry_cache import cleanup_old_cache
        async with async_session() as db:
            deleted = await cleanup_old_cache(db, days=7)
            await db.commit()
            if deleted:
                logger.info(f"Henry cache cleanup: removed {deleted} old entries")

    except Exception as e:
        logger.error(f"Henry context cleanup failed: {e}")


def start_scheduler():
    """Start the APScheduler with all jobs."""
    # Morning summary at 9:30 AM ET (13:30 UTC)
    scheduler.add_job(
        _generate_morning_summary,
        CronTrigger(hour=13, minute=30, timezone="UTC"),
        id="morning_summary",
        replace_existing=True,
    )

    # Nightly summary at 4:15 PM ET (20:15 UTC)
    scheduler.add_job(
        _generate_nightly_summary,
        CronTrigger(hour=20, minute=15, timezone="UTC"),
        id="nightly_summary",
        replace_existing=True,
    )

    # Screener analysis every 30 minutes during market hours
    scheduler.add_job(
        _refresh_screener_analysis,
        IntervalTrigger(minutes=30),
        id="screener_refresh",
        replace_existing=True,
    )

    # Portfolio threshold checks every hour during market hours (14:30-21:00 UTC)
    scheduler.add_job(
        _run_threshold_checks,
        CronTrigger(hour="14-20", minute=0, timezone="UTC", day_of_week="mon-fri"),
        id="portfolio_thresholds",
        replace_existing=True,
    )

    # Daily portfolio review at 10:00 AM ET (14:00 UTC) — after market opens and settles
    scheduler.add_job(
        _run_daily_portfolio_review,
        CronTrigger(hour=14, minute=0, timezone="UTC", day_of_week="mon-fri"),
        id="portfolio_daily_review",
        replace_existing=True,
    )

    # Henry stats computation every 2h during market hours
    scheduler.add_job(
        _compute_henry_stats,
        CronTrigger(hour="14,16,18,20", minute=30, timezone="UTC", day_of_week="mon-fri"),
        id="henry_stats",
        replace_existing=True,
    )

    # AI portfolio review at 2:30 PM ET (18:30 UTC) — after market stabilizes
    scheduler.add_job(
        _run_ai_portfolio_review,
        CronTrigger(hour=18, minute=30, timezone="UTC", day_of_week="mon-fri"),
        id="ai_portfolio_review",
        replace_existing=True,
    )

    # Henry context cleanup daily at 5 UTC (midnight ET)
    scheduler.add_job(
        _cleanup_expired_context,
        CronTrigger(hour=5, minute=0, timezone="UTC"),
        id="henry_context_cleanup",
        replace_existing=True,
    )

    scheduler.start()
    logger.info(
        "Scheduler started: morning (13:30 UTC), nightly (20:15 UTC), "
        "screener (every 30m), thresholds (hourly M-F 14-20 UTC), "
        "portfolio review (daily 14:00 UTC), "
        "henry stats (every 2h M-F 14-20 UTC), context cleanup (daily 5 UTC)"
    )


def stop_scheduler():
    """Stop the scheduler gracefully."""
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped")
