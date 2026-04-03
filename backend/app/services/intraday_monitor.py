"""
Intraday Monitor Service
========================
Two monitoring functions that run during market hours:
1. monitor_entry_levels()  - Watch pending OPPORTUNITY actions for price proximity
2. monitor_positions()     - Watch active holdings for technical alerts (RSI/ADX)

Both respect FMP rate limits.
"""

import logging
from app.utils.utc import utcnow
from datetime import datetime, timezone

from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import async_session
from app.models.portfolio_action import PortfolioAction
from app.models.portfolio_holding import PortfolioHolding

logger = logging.getLogger(__name__)


async def monitor_entry_levels() -> int:
    """
    For pending OPPORTUNITY actions, poll FMP quotes.
    If price is within 1% of suggested_price, increase priority.
    Returns count of alerts triggered.
    """
    from app.services.fmp_service import get_quote, get_api_usage

    # Respect rate limits
    usage = get_api_usage()
    if usage["throttled"]:
        logger.debug("Intraday monitor: FMP throttled, skipping entry level check")
        return 0

    triggered = 0
    try:
        async with async_session() as db:
            # Get pending OPPORTUNITY actions that haven't expired
            result = await db.execute(
                select(PortfolioAction).where(
                    PortfolioAction.action_type == "OPPORTUNITY",
                    PortfolioAction.status == "pending",
                    PortfolioAction.expires_at > utcnow(),
                    PortfolioAction.suggested_price.isnot(None),
                )
            )
            opportunities = result.scalars().all()

            if not opportunities:
                return 0

            # Group by ticker to minimize API calls
            by_ticker: dict[str, list[PortfolioAction]] = {}
            for opp in opportunities:
                if opp.ticker not in by_ticker:
                    by_ticker[opp.ticker] = []
                by_ticker[opp.ticker].append(opp)

            for ticker, actions in by_ticker.items():
                # Check rate limit before each call
                usage = get_api_usage()
                if usage["throttled"]:
                    logger.debug(f"Intraday monitor: FMP throttled, stopping at {ticker}")
                    break

                quote_data = await get_quote(ticker)
                if not quote_data or not isinstance(quote_data, list) or len(quote_data) == 0:
                    continue

                current_price = quote_data[0].get("price")
                if current_price is None:
                    continue

                for action in actions:
                    suggested = action.suggested_price
                    if suggested is None or suggested <= 0:
                        continue

                    # Always update current price
                    action.current_price = current_price

                    # Check if price is within 1% of suggested entry
                    pct_diff = abs(current_price - suggested) / suggested * 100
                    if pct_diff <= 1.0:
                        # Only alert once — check if we already boosted priority
                        if action.priority_score < 9.0:
                            action.priority_score = 9.0
                            triggered += 1
                            logger.info(
                                f"Entry alert: {ticker} at ${current_price:.2f} "
                                f"(target ${suggested:.2f}, {pct_diff:.1f}% away)"
                            )

            await db.commit()

    except Exception as e:
        logger.error(f"Entry level monitor failed: {e}")

    if triggered:
        logger.info(f"Intraday monitor: {triggered} entry alerts triggered")
    return triggered


async def monitor_positions() -> int:
    """
    For active holdings, fetch daily technicals.
    If RSI > 75 (overbought) or ADX drops below 15 (no trend), create alert context.
    Returns count of alerts generated.
    """
    from app.services.fmp_service import get_technical_snapshot, get_api_usage

    usage = get_api_usage()
    if usage["throttled"]:
        logger.debug("Intraday monitor: FMP throttled, skipping position monitor")
        return 0

    alerts = 0
    try:
        async with async_session() as db:
            # Get all active holdings
            result = await db.execute(
                select(PortfolioHolding).where(PortfolioHolding.is_active == True)
            )
            holdings = result.scalars().all()

            if not holdings:
                return 0

            # Get unique tickers
            tickers_seen: set[str] = set()
            ticker_holdings: dict[str, list] = {}
            for h in holdings:
                if h.ticker not in ticker_holdings:
                    ticker_holdings[h.ticker] = []
                ticker_holdings[h.ticker].append(h)

            for ticker, hlist in ticker_holdings.items():
                # Check rate limit
                usage = get_api_usage()
                if usage["throttled"]:
                    logger.debug(f"Intraday monitor: FMP throttled at {ticker}")
                    break

                try:
                    snapshot = await get_technical_snapshot(ticker)
                except Exception as e:
                    logger.debug(f"Position monitor: snapshot failed for {ticker}: {e}")
                    continue

                rsi = snapshot.get("rsi")
                adx = snapshot.get("adx")
                price = snapshot.get("price")

                alert_reasons = []
                if rsi is not None and rsi > 75:
                    alert_reasons.append(f"RSI={rsi:.1f} (overbought >75)")
                if adx is not None and adx < 15:
                    alert_reasons.append(f"ADX={adx:.1f} (weak trend <15)")

                if alert_reasons:
                    alert_text = f"{ticker}: {', '.join(alert_reasons)}"
                    if price:
                        alert_text += f" at ${price:.2f}"

                    # Save as context for Henry
                    try:
                        from app.services.ai_service import save_context
                        import asyncio
                        asyncio.create_task(save_context(
                            content=alert_text,
                            context_type="technical_alert",
                            ticker=ticker,
                            confidence=7,
                            expires_days=1,
                        ))
                    except Exception:
                        pass

                    # Check for existing pending threshold actions to avoid spam
                    existing = await db.execute(
                        select(PortfolioAction).where(
                            PortfolioAction.ticker == ticker,
                            PortfolioAction.status == "pending",
                            PortfolioAction.trigger_type.in_(["THRESHOLD", "SCANNER"]),
                            PortfolioAction.created_at > utcnow().replace(hour=0, minute=0, second=0),
                        )
                    )
                    if existing.scalars().first():
                        continue

                    # Create alert action for each holding's portfolio
                    for h in hlist:
                        if rsi is not None and rsi > 75:
                            action = PortfolioAction(
                                portfolio_id=h.portfolio_id,
                                ticker=ticker,
                                direction=h.direction,
                                action_type="TRIM",
                                confidence=6,
                                reasoning=f"Technical alert: {', '.join(alert_reasons)}. Consider taking partial profits.",
                                trigger_type="THRESHOLD",
                                current_price=price,
                                priority_score=18.0,  # 3.0 * 6
                                suggested_price=price,
                            )
                            db.add(action)
                            alerts += 1

                    logger.info(f"Position alert: {alert_text}")

            await db.commit()

    except Exception as e:
        logger.error(f"Position monitor failed: {e}")

    if alerts:
        logger.info(f"Intraday monitor: {alerts} position alerts created")
    return alerts
