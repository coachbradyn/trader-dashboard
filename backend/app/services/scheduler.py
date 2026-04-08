"""
Scheduled Jobs
==============
APScheduler-based background jobs for:
1. Morning market summary (9:30 AM ET)
2. Nightly market summary (4:15 PM ET)
3. Screener analysis refresh (every 30 minutes during market hours)
"""

import logging
from app.utils.utc import utcnow
from datetime import datetime, timedelta, timezone

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
                pnl_pct = ((current_price - t.entry_price) / t.entry_price * 100) if t.direction == "long" else ((t.entry_price - current_price) / t.entry_price * 100) if t.entry_price and t.entry_price > 0 else 0.0
                positions.append({
                    "trader": t.trader.trader_id,
                    "dir": t.direction,
                    "ticker": t.ticker,
                    "entry_price": t.entry_price,
                    "current_price": current_price,
                    "pnl_pct": round(pnl_pct, 2),
                })

            # Get yesterday's trades
            yesterday = utcnow() - timedelta(days=1)
            result = await db.execute(
                select(Trade).where(Trade.created_at >= yesterday, Trade.is_simulated == False)
            )
            yesterday_trades = result.scalars().all()

            # Get screener data (last 12h)
            cutoff = utcnow() - timedelta(hours=12)
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
            today_start = utcnow().replace(hour=0, minute=0, second=0)
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

            # Template-based nightly summary — no AI call needed
            lines = [f"## Nightly Recap\n"]
            lines.append(f"**Closed trades today:** {len(closed_today)} | **Day P&L:** ${day_pnl:.2f}\n")
            if closed_today:
                winners = [t for t in closed_today if (t.pnl_dollars or 0) > 0]
                losers = [t for t in closed_today if (t.pnl_dollars or 0) <= 0]
                lines.append(f"Winners: {len(winners)} | Losers: {len(losers)}\n")
                for t in sorted(closed_today, key=lambda x: x.pnl_dollars or 0, reverse=True)[:5]:
                    emoji = "✓" if (t.pnl_dollars or 0) > 0 else "✗"
                    lines.append(f"- {emoji} **{t.ticker}** ${t.pnl_dollars or 0:.2f} ({t.exit_reason or 'closed'})")
            lines.append(f"\n**Scanner alerts today:** {len(alerts)}")
            if top_tickers:
                lines.append("\n**Most active tickers:**")
                for t in top_tickers[:5]:
                    lines.append(f"- **{t['ticker']}** — {t['alert_count']} alerts ({', '.join(t['indicators'][:3])})")
            if picks_data:
                lines.append("\n**Morning picks scorecard:**")
                for p in (picks_data if isinstance(picks_data, list) else [])[:4]:
                    lines.append(f"- {p.get('ticker', '?')} ({p.get('direction', '?')}) — conf {p.get('confidence', '?')}/10")
            content = "\n".join(lines)

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
            cutoff = utcnow() - timedelta(hours=24)
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


async def _refresh_fundamentals():
    """Daily refresh of FMP fundamentals for all watchlist tickers."""
    logger.info("Refreshing fundamentals data...")
    try:
        from app.services.fmp_service import refresh_all_watchlist_tickers
        refreshed = await refresh_all_watchlist_tickers()
        logger.info(f"Fundamentals refresh complete: {refreshed} tickers updated")
    except Exception as e:
        logger.error(f"Fundamentals refresh failed: {e}")


async def _run_auto_research():
    """Run auto-research for tickers needing context."""
    logger.info("Running auto-research...")
    try:
        from app.services.research_service import run_auto_research
        count = await run_auto_research()
        logger.info(f"Auto-research complete: {count} tickers researched")
    except Exception as e:
        logger.error(f"Auto-research failed: {e}")


async def _run_scanner():
    """Run the FMP-powered stock scanner with watchlist priority."""
    logger.info("Running FMP scanner...")
    try:
        from app.services.scanner_service import run_scanner, run_watchlist_scan
        # Phase 1: Scan watchlist tickers first (fast, targeted)
        try:
            wl_opps = await run_watchlist_scan()
            if wl_opps:
                logger.info(f"Watchlist scan: {len(wl_opps)} opportunities")
        except Exception as e:
            logger.warning(f"Watchlist scan failed (continuing with full scan): {e}")
        # Phase 2: Full universe scan
        opportunities = await run_scanner()
        logger.info(f"Scanner complete: {len(opportunities)} opportunities found")
    except Exception as e:
        logger.error(f"Scanner run failed: {e}")


async def _run_autonomous_trading():
    """Henry's autonomous trading loop — scans for and executes trades in the AI portfolio."""
    logger.info("Running autonomous trading...")
    try:
        from app.services.autonomous_trading import run_autonomous_trading
        result = await run_autonomous_trading()
        total = result.get("scanner_trades", 0) + result.get("pattern_trades", 0)
        logger.info(f"Autonomous trading complete: {total} trades ({result})")
    except Exception as e:
        logger.error(f"Autonomous trading failed: {e}")


async def _check_autonomous_exits():
    """Check AI portfolio positions for autonomous exit signals."""
    try:
        from app.services.autonomous_trading import check_autonomous_exits
        closed = await check_autonomous_exits()
        if closed:
            logger.info(f"Autonomous exits: {closed} positions closed")
    except Exception as e:
        logger.error(f"Autonomous exit check failed: {e}")


async def _run_intraday_monitor():
    """Run intraday entry-level and position monitoring."""
    try:
        from app.services.intraday_monitor import monitor_entry_levels, monitor_positions
        entry_alerts = await monitor_entry_levels()
        position_alerts = await monitor_positions()
        if entry_alerts or position_alerts:
            logger.info(f"Intraday monitor: {entry_alerts} entry alerts, {position_alerts} position alerts")
    except Exception as e:
        logger.error(f"Intraday monitor failed: {e}")


async def _reconcile_alpaca_positions():
    """Reconcile DB holdings against live Alpaca positions for paper/live portfolios."""
    logger.info("Running Alpaca position reconciliation...")
    try:
        from app.database import async_session
        from app.models import Portfolio
        from app.models.portfolio_holding import PortfolioHolding
        from app.services.alpaca_service import alpaca_service
        from app.services.henry_activity import log_activity
        from sqlalchemy import select

        async with async_session() as db:
            result = await db.execute(
                select(Portfolio).where(
                    Portfolio.execution_mode.in_(["paper", "live"]),
                    Portfolio.is_active == True,
                    Portfolio.alpaca_api_key.isnot(None),
                )
            )
            portfolios = result.scalars().all()

        if not portfolios:
            return

        for portfolio in portfolios:
            try:
                is_paper = portfolio.execution_mode == "paper"
                alpaca_positions = await alpaca_service.get_positions(
                    api_key=portfolio.alpaca_api_key_decrypted,
                    secret_key=portfolio.alpaca_secret_key_decrypted,
                    paper=is_paper,
                )

                async with async_session() as db:
                    # Re-fetch portfolio for fresh state
                    port_result = await db.execute(
                        select(Portfolio).where(Portfolio.id == portfolio.id)
                    )
                    port = port_result.scalar_one_or_none()
                    if not port:
                        continue

                    holdings_result = await db.execute(
                        select(PortfolioHolding).where(
                            PortfolioHolding.portfolio_id == portfolio.id,
                            PortfolioHolding.is_active == True,
                        )
                    )
                    existing_holdings = {h.ticker: h for h in holdings_result.scalars().all()}

                    # Guard: empty Alpaca response with existing holdings is likely an API error
                    if not alpaca_positions and existing_holdings:
                        await log_activity(
                            f"Alpaca returned 0 positions for {port.name} but DB has {len(existing_holdings)} active holdings — possible API/credentials error, skipping removal",
                            "error",
                        )
                        continue

                    synced = 0
                    created = 0
                    for pos in alpaca_positions:
                        ticker = pos["symbol"]
                        qty = pos["qty"]
                        entry_price = pos["avg_entry_price"]

                        if ticker in existing_holdings:
                            h = existing_holdings[ticker]
                            # Update qty if drifted by more than 0.01%
                            if h.qty > 0 and abs(qty - h.qty) / h.qty > 0.0001:
                                h.qty = qty
                                synced += 1
                            if abs(entry_price - h.entry_price) > 0.001:
                                h.entry_price = entry_price
                                if not synced or existing_holdings[ticker].qty == qty:
                                    synced += 1
                        else:
                            new_holding = PortfolioHolding(
                                portfolio_id=portfolio.id,
                                ticker=ticker,
                                direction="long" if pos.get("side", "long") == "long" else "short",
                                entry_price=entry_price,
                                qty=qty,
                                entry_date=utcnow(),
                                is_active=True,
                                notes="alpaca_reconcile",
                            )
                            db.add(new_holding)
                            created += 1

                    # Removal pass: close DB holdings not in Alpaca
                    alpaca_tickers = {pos["symbol"] for pos in alpaca_positions}
                    closed = 0
                    today_str = utcnow().strftime("%Y-%m-%d")
                    for ticker, holding in existing_holdings.items():
                        if ticker not in alpaca_tickers:
                            holding.is_active = False
                            holding.notes = (holding.notes or "") + f" | reconciled_out_{today_str}"
                            closed += 1

                    await db.commit()

                    # Only log if drift was detected
                    if synced > 0 or closed > 0 or created > 0:
                        await log_activity(
                            f"Alpaca reconcile [{port.name}]: synced={synced} created={created} closed={closed}",
                            "status",
                        )
                        logger.info(f"Alpaca reconcile [{port.name}]: synced={synced} created={created} closed={closed}")

            except Exception as e:
                logger.error(f"Alpaca reconciliation failed for portfolio {portfolio.name}: {e}")

    except Exception as e:
        logger.error(f"Alpaca reconciliation failed: {e}")


async def _cleanup_expired_context():
    """Delete expired HenryContext rows and old non-outcome rows."""
    logger.info("Cleaning up expired Henry context...")
    try:
        from app.database import async_session
        from app.models import HenryContext
        from sqlalchemy import delete, and_, or_

        async with async_session() as db:
            now = utcnow()

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

        # Prune HenryMemory rows
        try:
            from app.models import HenryMemory
            from sqlalchemy import delete, func

            async with async_session() as db:
                now = utcnow()

                # (a) Low-importance, never-referenced, older than 14 days
                cutoff_14d = now - timedelta(days=14)
                result_a = await db.execute(
                    delete(HenryMemory).where(
                        HenryMemory.importance < 4,
                        HenryMemory.reference_count == 0,
                        HenryMemory.created_at < cutoff_14d,
                    )
                )
                deleted_low = result_a.rowcount

                # (b) Invalidated memories older than 60 days
                cutoff_60d = now - timedelta(days=60)
                result_b = await db.execute(
                    delete(HenryMemory).where(
                        HenryMemory.validated == False,
                        HenryMemory.created_at < cutoff_60d,
                    )
                )
                deleted_invalid = result_b.rowcount

                # (c) Cap total rows at 500 — delete oldest, exempt user-added and high-importance
                from sqlalchemy import select as sa_select
                count_result = await db.execute(
                    sa_select(func.count()).select_from(HenryMemory)
                )
                total = count_result.scalar() or 0

                deleted_overflow = 0
                if total > 500:
                    excess = total - 500
                    # Find oldest non-exempt IDs
                    oldest = await db.execute(
                        sa_select(HenryMemory.id)
                        .where(
                            HenryMemory.source != "user",
                            HenryMemory.importance < 9,
                        )
                        .order_by(HenryMemory.created_at.asc())
                        .limit(excess)
                    )
                    ids_to_delete = [row[0] for row in oldest.all()]
                    if ids_to_delete:
                        result_c = await db.execute(
                            delete(HenryMemory).where(HenryMemory.id.in_(ids_to_delete))
                        )
                        deleted_overflow = result_c.rowcount

                await db.commit()
                total_pruned = deleted_low + deleted_invalid + deleted_overflow
                if total_pruned:
                    logger.info(
                        f"Henry memory pruning: {deleted_low} low-importance, "
                        f"{deleted_invalid} invalidated, {deleted_overflow} overflow — "
                        f"{total_pruned} total removed"
                    )

        except Exception as e:
            logger.error(f"Henry memory pruning failed: {e}")

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
    """Start the APScheduler with all jobs. All times in US Eastern."""
    ET = "America/New_York"

    # Morning summary at 9:30 AM ET
    scheduler.add_job(
        _generate_morning_summary,
        CronTrigger(hour=9, minute=30, timezone=ET),
        id="morning_summary",
        replace_existing=True,
    )

    # Nightly summary at 4:15 PM ET
    scheduler.add_job(
        _generate_nightly_summary,
        CronTrigger(hour=16, minute=15, timezone=ET),
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

    # Portfolio threshold checks every hour during market hours (10 AM - 4 PM ET, M-F)
    scheduler.add_job(
        _run_threshold_checks,
        CronTrigger(hour="10-15", minute=0, timezone=ET, day_of_week="mon-fri"),
        id="portfolio_thresholds",
        replace_existing=True,
    )

    # Daily portfolio review at 10:00 AM ET — after market opens and settles
    scheduler.add_job(
        _run_daily_portfolio_review,
        CronTrigger(hour=10, minute=0, timezone=ET, day_of_week="mon-fri"),
        id="portfolio_daily_review",
        replace_existing=True,
    )

    # Henry stats computation every 2h during market hours (10, 12, 2, 4 PM ET)
    scheduler.add_job(
        _compute_henry_stats,
        CronTrigger(hour="10,12,14,16", minute=30, timezone=ET, day_of_week="mon-fri"),
        id="henry_stats",
        replace_existing=True,
    )

    # AI portfolio review at 2:30 PM ET — after market stabilizes
    scheduler.add_job(
        _run_ai_portfolio_review,
        CronTrigger(hour=14, minute=30, timezone=ET, day_of_week="mon-fri"),
        id="ai_portfolio_review",
        replace_existing=True,
    )

    # Henry context cleanup daily at midnight ET
    scheduler.add_job(
        _cleanup_expired_context,
        CronTrigger(hour=0, minute=0, timezone=ET),
        id="henry_context_cleanup",
        replace_existing=True,
    )

    # Weekly fundamentals refresh on Monday at 5:00 PM ET (after market close)
    scheduler.add_job(
        _refresh_fundamentals,
        CronTrigger(hour=17, minute=0, timezone=ET, day_of_week="mon"),
        id="fundamentals_refresh",
        replace_existing=True,
    )

    # Auto-research at 9:00 AM ET (before market open, after fundamentals are fresh)
    scheduler.add_job(
        _run_auto_research,
        CronTrigger(hour=9, minute=0, timezone=ET, day_of_week="mon-fri"),
        id="auto_research",
        replace_existing=True,
    )

    # FMP Scanner: 6 runs M-F covering key market windows
    # 8:30 AM — pre-market: position for open
    # 9:30 AM — open: catch gap breakouts at market open
    # 10:00 AM — post-open: first 30min settled, catch momentum
    # 12:00 PM — midday: mid-session opportunities
    # 2:00 PM — afternoon: catch dead-cat bounces + late setups
    # 4:30 PM — after-close: plan for next day
    for scan_id, hour, minute in [
        ("scanner_premarket", 8, 30),
        ("scanner_open", 9, 30),
        ("scanner_postopen", 10, 0),
        ("scanner_midday", 12, 0),
        ("scanner_afternoon", 14, 0),
        ("scanner_afterclose", 16, 30),
    ]:
        scheduler.add_job(
            _run_scanner,
            CronTrigger(hour=hour, minute=minute, timezone=ET, day_of_week="mon-fri"),
            id=scan_id,
            replace_existing=True,
        )

    # Intraday monitor: every 5 minutes during market hours (9:30 AM - 4:00 PM ET, M-F)
    scheduler.add_job(
        _run_intraday_monitor,
        CronTrigger(
            hour="9-15",
            minute="*/5",
            timezone=ET,
            day_of_week="mon-fri",
        ),
        id="intraday_monitor",
        replace_existing=True,
    )

    # Henry autonomous trading: scan + pattern detect + execute at 10:15 AM and 1:15 PM ET
    scheduler.add_job(
        _run_autonomous_trading,
        CronTrigger(hour="10,13", minute=15, timezone=ET, day_of_week="mon-fri"),
        id="autonomous_trading",
        replace_existing=True,
    )

    # Autonomous exit monitoring: every 30 minutes during market hours
    scheduler.add_job(
        _check_autonomous_exits,
        CronTrigger(hour="10-15", minute="0,30", timezone=ET, day_of_week="mon-fri"),
        id="autonomous_exits",
        replace_existing=True,
    )

    # Alpaca position reconciliation: every 30 min during market hours (9:30 AM – 4:15 PM ET, M-F)
    scheduler.add_job(
        _reconcile_alpaca_positions,
        CronTrigger(hour="9-16", minute="0,30", timezone=ET, day_of_week="mon-fri"),
        id="alpaca_reconcile",
        replace_existing=True,
    )

    scheduler.start()
    logger.info(
        "Scheduler started (all times US Eastern): morning (9:30 AM), nightly (4:15 PM), "
        "screener (every 30m), thresholds (hourly M-F 10AM-3PM), "
        "portfolio review (daily 10:00 AM), "
        "henry stats (every 2h M-F 10AM-4PM), context cleanup (daily midnight), "
        "fundamentals refresh (Monday 5:00 PM), auto-research (daily 9:00 AM), "
        "FMP scanner (8:30 AM, 12:00 PM, 4:30 PM M-F), "
        "intraday monitor (every 5m 9:30 AM-4:00 PM M-F), "
        "autonomous trading (10:15 AM, 1:15 PM M-F), autonomous exits (every 30m M-F), "
        "alpaca reconcile (every 30m market hours M-F)"
    )


def stop_scheduler():
    """Stop the scheduler gracefully."""
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped")
