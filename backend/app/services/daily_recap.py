"""
Daily recap: end-of-day summary of what Henry decided and committed to memory
=============================================================================
Scheduled job that writes one recap per AI portfolio after the close.

For each AI-managed / AI-evaluation-enabled portfolio, summarises:
    - every decision Henry made today (counts by status + action_type)
    - trades opened and closed today + realised PnL
    - positions still open at EOD + unrealised PnL

The summary is persisted in three places so it shows up everywhere the user
expects to see Henry's work:

    1. ``PortfolioAction`` with ``action_type="EOD_RECAP"`` — renders in the
       Decisions tab alongside per-trade decisions so the user has a single
       daily "here's what I did" row.
    2. ``HenryMemory`` (``memory_type="observation"``, importance=8) via
       ``save_memory`` — retrievable by future Henry prompts, so tomorrow's
       decisions can cite yesterday's.
    3. ``HenryContext`` (``context_type="portfolio_note"``) via
       ``save_context`` — feeds the portfolio-scoped context retriever.

The activity feed also gets a compact one-liner.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import async_session
from app.models import Portfolio, PortfolioAction, PortfolioTrade, Trade
from app.services.price_service import price_service
from app.utils.utc import utcnow

logger = logging.getLogger(__name__)


def _day_bounds_utc(today_et: date) -> tuple[datetime, datetime]:
    """Return [start, end) UTC bounds for the ET trading day.

    Using midnight-to-midnight in UTC is close enough for a recap — the
    market closes at 16:00 ET, the job runs at 16:15 ET, and we just want
    "today's" activity. A small pre-market overlap from 00:00-09:30 ET is
    fine because nothing material happens there anyway.
    """
    start = datetime(today_et.year, today_et.month, today_et.day, tzinfo=timezone.utc)
    end = start + timedelta(days=1)
    # Persist naive UTC — the rest of the schema stores naive UTC datetimes.
    return start.replace(tzinfo=None), end.replace(tzinfo=None)


async def _recap_one_portfolio(portfolio: Portfolio, db: AsyncSession) -> str | None:
    """Build + persist the recap for a single portfolio. Returns the summary
    string (or None if there was literally nothing to say and we skipped).
    """
    today = datetime.now(tz=timezone.utc).date()
    start, end = _day_bounds_utc(today)

    # ── Today's decisions ────────────────────────────────────────────────
    action_rows = (
        await db.execute(
            select(PortfolioAction)
            .where(
                PortfolioAction.portfolio_id == portfolio.id,
                PortfolioAction.created_at >= start,
                PortfolioAction.created_at < end,
                # Don't count prior recap rows in the new recap.
                PortfolioAction.action_type != "EOD_RECAP",
            )
            .order_by(PortfolioAction.created_at.asc())
        )
    ).scalars().all()

    status_counts: dict[str, int] = {}
    type_counts: dict[str, int] = {}
    approved_tickers: list[str] = []
    skipped_tickers: list[str] = []
    for a in action_rows:
        status_counts[a.status] = status_counts.get(a.status, 0) + 1
        type_counts[a.action_type] = type_counts.get(a.action_type, 0) + 1
        if a.status == "approved" and a.action_type != "SKIP":
            approved_tickers.append(a.ticker)
        else:
            skipped_tickers.append(a.ticker)

    # ── Today's trades ───────────────────────────────────────────────────
    opened_today = (
        await db.execute(
            select(Trade)
            .join(PortfolioTrade)
            .where(
                PortfolioTrade.portfolio_id == portfolio.id,
                Trade.entry_time >= start,
                Trade.entry_time < end,
            )
        )
    ).scalars().all()

    closed_today = (
        await db.execute(
            select(Trade)
            .join(PortfolioTrade)
            .where(
                PortfolioTrade.portfolio_id == portfolio.id,
                Trade.exit_time.isnot(None),
                Trade.exit_time >= start,
                Trade.exit_time < end,
            )
        )
    ).scalars().all()

    realised_pnl = sum(t.pnl_dollars or 0.0 for t in closed_today)
    winners = sum(1 for t in closed_today if (t.pnl_dollars or 0.0) > 0)
    losers = sum(1 for t in closed_today if (t.pnl_dollars or 0.0) < 0)

    # ── Open at EOD ──────────────────────────────────────────────────────
    still_open = (
        await db.execute(
            select(Trade)
            .join(PortfolioTrade)
            .where(
                PortfolioTrade.portfolio_id == portfolio.id,
                Trade.status == "open",
            )
        )
    ).scalars().all()
    unrealised = 0.0
    for t in still_open:
        cp = price_service.get_price(t.ticker) or t.entry_price
        if t.direction == "long":
            unrealised += (cp - t.entry_price) * t.qty
        else:
            unrealised += (t.entry_price - cp) * t.qty

    # Nothing to say — skip so we don't pollute memory with empty recaps.
    if not action_rows and not opened_today and not closed_today and not still_open:
        return None

    # ── Compose ──────────────────────────────────────────────────────────
    lines: list[str] = [
        f"EOD recap {today.isoformat()} — {portfolio.name}",
        f"Decisions: {len(action_rows)}"
        + (
            f" (" + ", ".join(f"{k}={v}" for k, v in sorted(status_counts.items())) + ")"
            if status_counts
            else ""
        ),
    ]
    if type_counts:
        lines.append(
            "By type: "
            + ", ".join(f"{k}={v}" for k, v in sorted(type_counts.items()))
        )
    if approved_tickers:
        lines.append("Taken: " + ", ".join(approved_tickers[:12]))
    if skipped_tickers:
        lines.append("Skipped: " + ", ".join(skipped_tickers[:12]))
    if opened_today:
        lines.append(
            f"Opened {len(opened_today)}: "
            + ", ".join(sorted({t.ticker for t in opened_today}))[:400]
        )
    if closed_today:
        lines.append(
            f"Closed {len(closed_today)} (W{winners}/L{losers}, realised ${realised_pnl:+.2f}): "
            + ", ".join(
                f"{t.ticker}{(t.pnl_percent or 0):+.1f}%" for t in closed_today[:12]
            )
        )
    if still_open:
        lines.append(
            f"Open at close: {len(still_open)} (unrealised ${unrealised:+.2f}) — "
            + ", ".join(sorted({t.ticker for t in still_open}))[:400]
        )

    summary = "\n".join(lines)

    # ── Persist: Decisions row ───────────────────────────────────────────
    # Idempotent per (portfolio, day): upsert instead of inserting a second
    # recap if the job runs twice (retry, manual trigger). We key on
    # trigger_ref="eod_recap:<date>" so lookup is a single equality query.
    trigger_ref = f"eod_recap:{today.isoformat()}"
    existing = (
        await db.execute(
            select(PortfolioAction).where(
                PortfolioAction.portfolio_id == portfolio.id,
                PortfolioAction.trigger_ref == trigger_ref,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        existing.reasoning = summary
        existing.resolved_at = utcnow()
    else:
        db.add(
            PortfolioAction(
                portfolio_id=portfolio.id,
                ticker="—",
                direction="n/a",
                action_type="EOD_RECAP",
                confidence=0,
                reasoning=summary,
                trigger_type="SCHEDULED_REVIEW",
                trigger_ref=trigger_ref,
                priority_score=0.0,
                status="approved",
                resolved_at=utcnow(),
            )
        )
    await db.commit()

    # ── Persist: Memory + Context (best-effort, non-blocking) ────────────
    try:
        from app.services.ai_service import save_memory, save_context

        await save_memory(
            content=summary,
            memory_type="observation",
            ticker=None,
            strategy_id=None,
            importance=8,
            source="eod_recap",
        )
        await save_context(
            content=summary,
            context_type="portfolio_note",
            portfolio_id=portfolio.id,
            expires_days=60,
        )
    except Exception as e:
        logger.warning(f"EOD recap memory save failed for {portfolio.name}: {e}")

    # Activity-feed ping for the dashboard.
    try:
        from app.services.henry_activity import log_activity

        headline = (
            f"EOD {portfolio.name}: {len(action_rows)} decisions, "
            f"{len(opened_today)} opened, {len(closed_today)} closed "
            f"(realised ${realised_pnl:+.2f})"
        )
        await log_activity(headline, "status")
    except Exception:
        pass

    return summary


async def run_daily_recap_job() -> int:
    """Entry point for the scheduler. Returns how many portfolios were
    recapped so the caller can log the count.
    """
    count = 0
    try:
        async with async_session() as db:
            portfolios = (
                await db.execute(
                    select(Portfolio).where(
                        Portfolio.is_active == True,
                        (Portfolio.is_ai_managed == True)
                        | (Portfolio.ai_evaluation_enabled == True),
                    )
                )
            ).scalars().all()

            for p in portfolios:
                try:
                    summary = await _recap_one_portfolio(p, db)
                    if summary:
                        count += 1
                        logger.info(f"EOD recap saved for {p.name}")
                except Exception as e:
                    # One portfolio's failure shouldn't poison the others —
                    # the session commits per-portfolio in _recap_one_portfolio.
                    logger.exception(f"EOD recap failed for {p.name}: {e}")
    except Exception as e:
        logger.exception(f"EOD recap job failed: {e}")
    return count
