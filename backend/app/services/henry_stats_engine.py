"""
Henry Stats Engine
==================
Background computation of pre-computed analytics injected into Henry's prompts.
Runs every 2h during market hours via scheduler.
Each sub-function queries trades/actions, computes stats, upserts to HenryStats.
"""

import logging
from app.utils.utc import utcnow
from datetime import datetime, timedelta, timezone
from collections import defaultdict

from sqlalchemy import select, delete, func, and_

logger = logging.getLogger(__name__)


# Module-level counter tracks how many times the orchestrator has run so
# we can schedule memory clustering every Nth run (it's heavier than the
# other stats — embedding matrix + EM). Resets on process restart, which is
# fine: we'll just fit once extra on boot.
_RUN_COUNTER = 0


async def compute_all_stats():
    """Orchestrator: compute all stat types, each wrapped in try/except."""
    global _RUN_COUNTER
    from app.database import async_session
    from app.config import get_settings

    logger.info("Computing Henry stats...")
    _RUN_COUNTER += 1
    settings = get_settings()

    async with async_session() as db:
        for fn in [
            _compute_strategy_performance,
            _compute_exit_reason_analysis,
            _compute_henry_hit_rate,
            _compute_hold_time_analysis,
            _compute_portfolio_risk,
            _compute_strategy_correlation,
            _compute_conditional_probability,
            _compute_memory_decay,            # Phase 6, System 7
            _compute_confidence_calibration,  # Phase 6, System 8
            _compute_options_performance,     # Step 4B
        ]:
            try:
                await fn(db)
            except Exception as e:
                logger.error(f"Stats computation failed for {fn.__name__}: {e}")

        # Memory clustering — runs every Nth orchestrator invocation since
        # the embedding matrix + EM are heavier than the simple aggregations
        # above. Also gated by the config flag.
        fit_every = max(1, int(getattr(settings, "memory_cluster_fit_every_n_runs", 3)))
        if (
            getattr(settings, "memory_clustering_enabled", True)
            and _RUN_COUNTER % fit_every == 0
        ):
            try:
                await _compute_memory_clusters(db)
            except Exception as e:
                logger.error(f"Memory clustering failed: {e}")

        await db.commit()

    logger.info("Henry stats computation complete")


async def _compute_memory_clusters(db):
    """Fit gaussian mixture over memory embeddings; writes cluster_ids and
    a HenryStats row. Invalidates the process-local cluster cache so
    retrieval picks up fresh centroids immediately."""
    from app.services.memory_clustering import fit_memory_clusters, invalidate_cache

    summary = await fit_memory_clusters(db)
    if summary:
        logger.info(
            f"Memory clusters refit: k={summary['k']} n={summary['n_memories_fit']} "
            f"model={summary['model']} ll={summary['log_likelihood']:.2f}"
        )
        invalidate_cache()


async def _upsert_stat(db, stat_type: str, data: dict, strategy: str = None,
                       ticker: str = None, portfolio_id: str = None, period_days: int = 30):
    """Delete existing matching rows, then insert fresh."""
    from app.models import HenryStats

    conditions = [HenryStats.stat_type == stat_type]
    if strategy is not None:
        conditions.append(HenryStats.strategy == strategy)
    else:
        conditions.append(HenryStats.strategy.is_(None))
    if ticker is not None:
        conditions.append(HenryStats.ticker == ticker)
    else:
        conditions.append(HenryStats.ticker.is_(None))
    if portfolio_id is not None:
        conditions.append(HenryStats.portfolio_id == portfolio_id)
    else:
        conditions.append(HenryStats.portfolio_id.is_(None))

    await db.execute(delete(HenryStats).where(and_(*conditions)))

    stat = HenryStats(
        stat_type=stat_type,
        strategy=strategy,
        ticker=ticker,
        portfolio_id=portfolio_id,
        data=data,
        period_days=period_days,
        computed_at=utcnow(),
    )
    db.add(stat)


async def _compute_strategy_performance(db):
    """Closed trades (30 days), grouped by trader_id (strategy)."""
    from app.models import Trade, Trader
    from sqlalchemy.orm import selectinload

    cutoff = utcnow() - timedelta(days=30)
    result = await db.execute(
        select(Trade)
        .options(selectinload(Trade.trader))
        .where(Trade.status == "closed", Trade.exit_time >= cutoff)
    )
    trades = result.scalars().all()

    by_strategy = defaultdict(list)
    for t in trades:
        # Skip trades that exited at entry price — these came from the
        # trade_processor fallback when TradingView sent no exit price and
        # the price_service cache was cold. They record pnl=0 which would
        # otherwise drag down win-rate and profit-factor despite not being
        # real losses. Detection is exit_price ≈ entry_price AND pnl=0.
        if (
            t.exit_price is not None
            and t.entry_price is not None
            and abs(t.exit_price - t.entry_price) < 1e-6
            and (t.pnl_dollars or 0) == 0
        ):
            continue
        sid = t.trader.trader_id if t.trader else "unknown"
        by_strategy[sid].append(t)

    for strategy_id, strades in by_strategy.items():
        wins = [t for t in strades if (t.pnl_dollars or 0) > 0]
        losses = [t for t in strades if (t.pnl_dollars or 0) <= 0]

        win_rate = round(len(wins) / len(strades) * 100, 1) if strades else 0
        avg_gain = round(sum(t.pnl_percent or 0 for t in wins) / len(wins), 2) if wins else 0
        avg_loss = round(sum(t.pnl_percent or 0 for t in losses) / len(losses), 2) if losses else 0

        gross_profit = sum(t.pnl_dollars or 0 for t in wins)
        gross_loss = abs(sum(t.pnl_dollars or 0 for t in losses))
        profit_factor = round(gross_profit / gross_loss, 2) if gross_loss > 0 else None

        avg_hold = None
        bars = [t.bars_in_trade for t in strades if t.bars_in_trade is not None]
        if bars:
            avg_hold = round(sum(bars) / len(bars), 1)

        # Current streak
        sorted_trades = sorted(strades, key=lambda t: t.exit_time or datetime.min)
        streak = ""
        if sorted_trades:
            last_win = (sorted_trades[-1].pnl_dollars or 0) > 0
            count = 0
            for t in reversed(sorted_trades):
                is_win = (t.pnl_dollars or 0) > 0
                if is_win == last_win:
                    count += 1
                else:
                    break
            streak = f"W{count}" if last_win else f"L{count}"

        await _upsert_stat(db, "strategy_performance", {
            "win_rate": win_rate,
            "avg_gain": avg_gain,
            "avg_loss": avg_loss,
            "profit_factor": profit_factor,
            "trade_count": len(strades),
            "avg_hold_bars": avg_hold,
            "current_streak": streak,
        }, strategy=strategy_id)


async def _compute_exit_reason_analysis(db):
    """Closed trades (30 days), grouped by exit_reason."""
    from app.models import Trade

    cutoff = utcnow() - timedelta(days=30)
    result = await db.execute(
        select(Trade).where(Trade.status == "closed", Trade.exit_time >= cutoff)
    )
    trades = result.scalars().all()

    by_reason = defaultdict(list)
    for t in trades:
        reason = t.exit_reason or "unknown"
        by_reason[reason].append(t)

    reason_data = {}
    for reason, rtrades in by_reason.items():
        wins = [t for t in rtrades if (t.pnl_dollars or 0) > 0]
        avg_pnl = round(sum(t.pnl_percent or 0 for t in rtrades) / len(rtrades), 2) if rtrades else 0
        win_rate = round(len(wins) / len(rtrades) * 100, 1) if rtrades else 0
        reason_data[reason] = {
            "count": len(rtrades),
            "avg_pnl_pct": avg_pnl,
            "win_rate": win_rate,
        }

    await _upsert_stat(db, "exit_reason_analysis", reason_data)


async def _compute_henry_hit_rate(db):
    """Query approved PortfolioActions where outcome_correct IS NOT NULL."""
    from app.models import PortfolioAction

    result = await db.execute(
        select(PortfolioAction).where(
            PortfolioAction.status == "approved",
            PortfolioAction.outcome_correct.isnot(None),
        )
    )
    outcomes = result.scalars().all()

    if not outcomes:
        return

    total = len(outcomes)
    correct = sum(1 for o in outcomes if o.outcome_correct)
    overall_pct = round(correct / total * 100, 1)

    # By confidence bucket
    low = [o for o in outcomes if o.confidence and o.confidence <= 3]
    mid = [o for o in outcomes if o.confidence and 4 <= o.confidence <= 6]
    high = [o for o in outcomes if o.confidence and o.confidence >= 7]

    low_pct = round(sum(1 for o in low if o.outcome_correct) / len(low) * 100, 1) if low else None
    mid_pct = round(sum(1 for o in mid if o.outcome_correct) / len(mid) * 100, 1) if mid else None
    high_pct = round(sum(1 for o in high if o.outcome_correct) / len(high) * 100, 1) if high else None

    await _upsert_stat(db, "henry_hit_rate", {
        "overall_pct": overall_pct,
        "total_outcomes": total,
        "low_conf_pct": low_pct,
        "low_conf_count": len(low),
        "mid_conf_pct": mid_pct,
        "mid_conf_count": len(mid),
        "high_conf_pct": high_pct,
        "high_conf_count": len(high),
    })


async def _compute_hold_time_analysis(db):
    """Closed trades with bars_in_trade, split winners/losers."""
    from app.models import Trade

    cutoff = utcnow() - timedelta(days=30)
    result = await db.execute(
        select(Trade).where(
            Trade.status == "closed",
            Trade.exit_time >= cutoff,
            Trade.bars_in_trade.isnot(None),
        )
    )
    trades = result.scalars().all()

    if not trades:
        return

    winners = [t for t in trades if (t.pnl_dollars or 0) > 0]
    losers = [t for t in trades if (t.pnl_dollars or 0) <= 0]

    def _stats(trade_list):
        bars = sorted([t.bars_in_trade for t in trade_list])
        if not bars:
            return None
        avg = round(sum(bars) / len(bars), 1)
        median = bars[len(bars) // 2]
        p90 = bars[int(len(bars) * 0.9)] if len(bars) >= 5 else bars[-1]
        return {"avg": avg, "median": median, "p90": p90, "count": len(bars)}

    await _upsert_stat(db, "hold_time_analysis", {
        "winners": _stats(winners),
        "losers": _stats(losers),
        "all": _stats(trades),
    })


async def _compute_portfolio_risk(db):
    """Active holdings grouped by portfolio -- concentration, exposure."""
    from app.models import PortfolioHolding, Portfolio
    from app.services.price_service import price_service

    result = await db.execute(
        select(PortfolioHolding).where(PortfolioHolding.is_active == True)
    )
    holdings = result.scalars().all()

    by_portfolio = defaultdict(list)
    for h in holdings:
        by_portfolio[h.portfolio_id].append(h)

    for pid, pholdings in by_portfolio.items():
        ticker_values = {}
        total_value = 0.0

        for h in pholdings:
            cp = price_service.get_price(h.ticker) or h.entry_price
            val = cp * h.qty
            total_value += val
            ticker_values[h.ticker] = ticker_values.get(h.ticker, 0) + val

        if total_value <= 0:
            continue

        concentration = {
            ticker: round(val / total_value * 100, 1)
            for ticker, val in sorted(ticker_values.items(), key=lambda x: -x[1])
        }

        largest_ticker = max(ticker_values, key=ticker_values.get) if ticker_values else None
        largest_pct = concentration.get(largest_ticker, 0) if largest_ticker else 0

        await _upsert_stat(db, "portfolio_risk", {
            "total_exposure": round(total_value, 2),
            "position_count": len(pholdings),
            "ticker_count": len(ticker_values),
            "concentration": concentration,
            "largest_position": largest_ticker,
            "largest_pct": largest_pct,
        }, portfolio_id=pid)


async def _compute_strategy_correlation(db):
    """Entry trades (90 days), find same-ticker entries within 4h between strategy pairs."""
    from app.models import Trade, Trader
    from sqlalchemy.orm import selectinload

    cutoff = utcnow() - timedelta(days=90)
    result = await db.execute(
        select(Trade)
        .options(selectinload(Trade.trader))
        .where(Trade.entry_time >= cutoff)
        .order_by(Trade.entry_time)
    )
    trades = result.scalars().all()

    # Group by ticker
    by_ticker = defaultdict(list)
    for t in trades:
        if t.trader:
            by_ticker[t.ticker].append(t)

    pair_agree = defaultdict(int)
    pair_total = defaultdict(int)

    for ticker, ttrades in by_ticker.items():
        # For each pair of trades on same ticker within 4h
        for i, t1 in enumerate(ttrades):
            for t2 in ttrades[i + 1:]:
                if t1.trader.trader_id == t2.trader.trader_id:
                    continue
                if abs((t1.entry_time - t2.entry_time).total_seconds()) > 4 * 3600:
                    continue

                pair_key = tuple(sorted([t1.trader.trader_id, t2.trader.trader_id]))
                pair_total[pair_key] += 1
                if t1.direction == t2.direction:
                    pair_agree[pair_key] += 1

    if not pair_total:
        return

    correlation_data = {}
    for pair, total in pair_total.items():
        agree = pair_agree.get(pair, 0)
        correlation_data[f"{pair[0]}_{pair[1]}"] = {
            "agreement_pct": round(agree / total * 100, 1),
            "total_overlaps": total,
            "agreements": agree,
        }

    await _upsert_stat(db, "strategy_correlation", correlation_data, period_days=90)


# ───────────────────────────────────────────────────────────────────────────
# Conditional Probability Table (intelligence upgrade Phase 3, System 4)
# ───────────────────────────────────────────────────────────────────────────


# Tunable thresholds — kept module-level so they're easy to adjust.
COND_PROB_MIN_TRADES = 5      # Min total trades per strategy×ticker for any output
COND_PROB_MIN_BUCKET = 3      # Min trades per bucket to surface a conditional split
COND_PROB_LOOKBACK_DAYS = 365 # Webhook trade lookback


def _bucket_adx(adx: float | None) -> str | None:
    if adx is None:
        return None
    if adx >= 30:
        return "adx_high"
    if adx >= 20:
        return "adx_mid"
    return "adx_low"


def _bucket_vix(vix: float | None) -> str | None:
    if vix is None:
        return None
    if vix < 18:
        return "vix_low"
    if vix <= 25:
        return "vix_mid"
    return "vix_high"


def _bucket_spy_trend(close: float | None, ema: float | None) -> str | None:
    if close is None or ema is None:
        return None
    return "spy_uptrend" if close > ema else "spy_downtrend"


def _summarize_bucket(trades_in_bucket: list) -> dict:
    """Compact stats for one bucket's slice of trades."""
    n = len(trades_in_bucket)
    if n == 0:
        return {"n": 0}
    wins = [t for t in trades_in_bucket if (t.pnl_dollars or 0) > 0]
    losses = [t for t in trades_in_bucket if (t.pnl_dollars or 0) <= 0]
    win_rate = (len(wins) / n) * 100.0
    avg_gain = (
        sum(t.pnl_percent or 0 for t in wins) / len(wins) if wins else 0.0
    )
    avg_loss = (
        sum(t.pnl_percent or 0 for t in losses) / len(losses) if losses else 0.0
    )
    # EV per trade in % terms — simple expected value (not Kelly).
    ev_pct = (win_rate / 100.0) * avg_gain + (1 - win_rate / 100.0) * avg_loss
    return {
        "n": n,
        "win_rate": round(win_rate, 1),
        "avg_gain_pct": round(avg_gain, 2),
        "avg_loss_pct": round(avg_loss, 2),
        "ev_pct": round(ev_pct, 2),
    }


async def _compute_conditional_probability(db):
    """
    Per (strategy_id, ticker) with ≥ COND_PROB_MIN_TRADES closed trades,
    write a HenryStats(stat_type='conditional_probability') row containing:

      - unconditional stats (win_rate, avg_gain, avg_loss, ev_pct,
        profit_factor, avg_hold_days, n)
      - conditional breakdowns (each shown only if ≥ COND_PROB_MIN_BUCKET):
          * by_adx:   adx_high (>30) / adx_mid (20-30) / adx_low (<20)
          * by_vix:   vix_low (<18) / vix_mid (18-25) / vix_high (>25)
          * by_spy:   spy_uptrend / spy_downtrend (vs 20EMA)

    Webhook trades supply ADX (from entry_adx) + the regime snapshot
    (entry_vix / entry_spy_close / entry_spy_20ema). Backtest trades
    contribute only to the unconditional counts because their rows
    don't carry regime context.

    Stored per-pair so the prompt-injection helper can fetch by ticker
    in O(K) rather than rebuilding aggregates on every call.
    """
    from collections import defaultdict
    from datetime import timedelta
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload
    from app.models import Trade, Trader, BacktestTrade, BacktestImport

    # ── Pull webhook closed trades within the lookback window ─────────
    cutoff = utcnow() - timedelta(days=COND_PROB_LOOKBACK_DAYS)
    result = await db.execute(
        select(Trade)
        .options(selectinload(Trade.trader))
        .where(
            Trade.status == "closed",
            Trade.exit_time >= cutoff,
        )
    )
    webhook_trades = list(result.scalars().all())

    # Group webhook by (strategy_id, ticker) — same fallback-trade filter
    # as _compute_strategy_performance so Henry's conditional probability
    # tables don't include price-less exits that recorded pnl=0.
    grouped: dict[tuple[str, str], list] = defaultdict(list)
    for t in webhook_trades:
        if not t.trader or not t.ticker:
            continue
        if (
            t.exit_price is not None
            and t.entry_price is not None
            and abs(t.exit_price - t.entry_price) < 1e-6
            and (t.pnl_dollars or 0) == 0
        ):
            continue
        grouped[(t.trader.trader_id, t.ticker.upper())].append(t)

    # ── Pull backtest trades (group by ticker via BacktestImport) ────
    # BacktestTrade rows are ordered alternating Entry/Exit per the CSV
    # format. Since the existing code already imports them paired and
    # cumulative_pnl_pct is per-Exit row, we use Exit rows as the
    # "closed trade" record for unconditional stats. Strategy comes
    # from the BacktestImport.strategy field if present; ticker from
    # BacktestImport.ticker.
    bt_grouped: dict[tuple[str, str], list[dict]] = defaultdict(list)
    try:
        bt_result = await db.execute(
            select(BacktestTrade, BacktestImport)
            .join(BacktestImport, BacktestTrade.import_id == BacktestImport.id)
            .where(BacktestTrade.type.like("Exit%"))
        )
        for bt, imp in bt_result.all():
            strat = getattr(imp, "strategy", None)
            tkr = getattr(imp, "ticker", None)
            if not strat or not tkr or bt.net_pnl_pct is None:
                continue
            bt_grouped[(strat, tkr.upper())].append({
                "pnl_pct": float(bt.net_pnl_pct),
                "pnl_dollars": float(bt.net_pnl or 0.0),
            })
    except Exception as e:
        logger.debug(f"backtest trade aggregation skipped: {e}")

    all_keys = set(grouped.keys()) | set(bt_grouped.keys())
    if not all_keys:
        return

    # Clear stale conditional_probability rows so removed pairs don't linger
    # in the table after a strategy or ticker stops trading.
    from app.models import HenryStats as _HenryStats
    await db.execute(
        delete(_HenryStats).where(_HenryStats.stat_type == "conditional_probability")
    )

    for (strategy_id, ticker) in all_keys:
        wb = grouped.get((strategy_id, ticker), [])
        bt = bt_grouped.get((strategy_id, ticker), [])
        n_total = len(wb) + len(bt)
        if n_total < COND_PROB_MIN_TRADES:
            continue

        # ── Unconditional ────────────────────────────────────────────
        # Combine webhook + backtest pnl_pct for unconditional stats.
        combined_pnl_pcts: list[float] = [t.pnl_percent or 0 for t in wb]
        combined_pnl_dollars: list[float] = [t.pnl_dollars or 0 for t in wb]
        for b in bt:
            combined_pnl_pcts.append(b["pnl_pct"])
            combined_pnl_dollars.append(b["pnl_dollars"])

        wins_idx = [i for i, p in enumerate(combined_pnl_dollars) if p > 0]
        losses_idx = [i for i, p in enumerate(combined_pnl_dollars) if p <= 0]
        n = len(combined_pnl_pcts)
        win_rate = (len(wins_idx) / n) * 100.0 if n else 0.0
        avg_gain = (
            sum(combined_pnl_pcts[i] for i in wins_idx) / len(wins_idx)
            if wins_idx else 0.0
        )
        avg_loss = (
            sum(combined_pnl_pcts[i] for i in losses_idx) / len(losses_idx)
            if losses_idx else 0.0
        )
        ev_pct = (win_rate / 100.0) * avg_gain + (1 - win_rate / 100.0) * avg_loss
        gross_profit = sum(p for p in combined_pnl_dollars if p > 0)
        gross_loss = abs(sum(p for p in combined_pnl_dollars if p <= 0))
        profit_factor = round(gross_profit / gross_loss, 2) if gross_loss > 0 else None

        # Avg hold days: webhook trades only (backtest doesn't track bars
        # in days reliably without the timeframe metadata we don't store).
        bars = [t.bars_in_trade for t in wb if t.bars_in_trade is not None]
        # bars_in_trade is in TF units; without TF we can't convert to days
        # cleanly. Report avg bars and let the consumer decide.
        avg_bars = round(sum(bars) / len(bars), 1) if bars else None

        unconditional = {
            "n": n,
            "n_webhook": len(wb),
            "n_backtest": len(bt),
            "win_rate": round(win_rate, 1),
            "avg_gain_pct": round(avg_gain, 2),
            "avg_loss_pct": round(avg_loss, 2),
            "ev_pct": round(ev_pct, 2),
            "profit_factor": profit_factor,
            "avg_bars_in_trade": avg_bars,
        }

        # ── Conditional breakdowns (webhook only) ───────────────────
        by_adx: dict[str, list] = defaultdict(list)
        by_vix: dict[str, list] = defaultdict(list)
        by_spy: dict[str, list] = defaultdict(list)
        for t in wb:
            adx_b = _bucket_adx(t.entry_adx)
            if adx_b:
                by_adx[adx_b].append(t)
            vix_b = _bucket_vix(t.entry_vix)
            if vix_b:
                by_vix[vix_b].append(t)
            spy_b = _bucket_spy_trend(t.entry_spy_close, t.entry_spy_20ema)
            if spy_b:
                by_spy[spy_b].append(t)

        def _filter_buckets(d: dict[str, list]) -> dict:
            return {
                k: _summarize_bucket(v)
                for k, v in d.items()
                if len(v) >= COND_PROB_MIN_BUCKET
            }

        conditional = {
            "by_adx": _filter_buckets(by_adx),
            "by_vix": _filter_buckets(by_vix),
            "by_spy_trend": _filter_buckets(by_spy),
        }

        await _upsert_stat(
            db,
            "conditional_probability",
            {
                "unconditional": unconditional,
                "conditional": conditional,
            },
            strategy=strategy_id,
            ticker=ticker,
        )


# ───────────────────────────────────────────────────────────────────────────
# Options Performance (Step 4B)
# ───────────────────────────────────────────────────────────────────────────

OPTIONS_MIN_TRADES_FOR_STRATEGY = 3
OPTIONS_MIN_TRADES_FOR_BUCKETS = 5


def _iv_bucket(iv: float | None) -> str | None:
    if iv is None:
        return None
    try:
        # greeks_at_entry may store iv as raw decimal (0.35) or as rank 0-100.
        # Treat anything > 3.0 as a rank percentage; else as raw IV.
        rank = float(iv)
        if rank <= 3.0:
            # raw IV — rough map to rank (same approach as the selector)
            rank = max(0.0, min(100.0, (rank - 0.10) / 0.70 * 100.0))
        if rank < 20.0:
            return "iv_low"
        if rank < 50.0:
            return "iv_mid"
        return "iv_high"
    except (TypeError, ValueError):
        return None


def _dte_bucket(days: int | None) -> str | None:
    if days is None or days < 0:
        return None
    if days < 14:
        return "dte_short"
    if days < 30:
        return "dte_mid"
    if days < 45:
        return "dte_long"
    return "dte_xlong"


async def _compute_options_performance(db):
    """Aggregate closed options_trades by strategy_type.

    For each strategy type with ≥ OPTIONS_MIN_TRADES_FOR_STRATEGY closed
    trades, compute total trades, win rate, avg P&L %, avg hold days,
    avg DTE at entry, and avg theta cost per day. With ≥
    OPTIONS_MIN_TRADES_FOR_BUCKETS, add by-IV-environment and by-DTE
    conditional breakdowns.

    Stored as HenryStats(stat_type='options_performance', strategy=<type>).
    One row per strategy_type — the prompt injector reads them in bulk.
    """
    from app.models import HenryStats as _HS
    try:
        from app.models.options_trade import OptionsTrade
    except Exception as e:
        logger.debug(f"options_performance skipped (no OptionsTrade model): {e}")
        return

    result = await db.execute(
        select(OptionsTrade).where(OptionsTrade.status.in_(("closed", "expired")))
    )
    rows = list(result.scalars().all())
    if not rows:
        # Clear stale rows so nothing lingers after all trades are purged.
        await db.execute(delete(_HS).where(_HS.stat_type == "options_performance"))
        return

    # Group leg rows by (spread_group_id or leg-id) so multi-leg strategies
    # count as one trade. Attribute the group to the strategy_type of its
    # first leg (they should all match for a well-formed spread).
    groups: dict[str, list] = defaultdict(list)
    for r in rows:
        key = r.spread_group_id or r.id
        groups[key].append(r)

    strat_buckets: dict[str, list[dict]] = defaultdict(list)
    for _key, legs in groups.items():
        strategy_type = legs[0].strategy_type or "unknown"
        entry_time = min((l.entry_time for l in legs if l.entry_time), default=None)
        exit_time = max((l.exit_time for l in legs if l.exit_time), default=None)
        exp = min((l.expiration for l in legs if l.expiration), default=None)

        # Net P&L across legs
        pnl_dollars = sum(float(l.pnl_dollars or 0.0) for l in legs)
        # Net entry / exit premium (directional — long adds, short subtracts)
        def _net_prem(field: str) -> float:
            v = 0.0
            for l in legs:
                prem = getattr(l, field, None)
                if prem is None:
                    continue
                sign = 1 if l.direction == "long" else -1
                v += sign * float(prem) * int(l.quantity or 0) * 100.0
            return v
        net_entry = _net_prem("entry_premium")
        net_exit = _net_prem("exit_premium")
        # pnl_pct relative to |net entry| — for a debit strategy, entry is
        # the capital at risk; for a credit strategy it's the collateral.
        denom = abs(net_entry) if net_entry else (
            sum(abs(float(l.entry_premium or 0.0)) * int(l.quantity or 0) * 100.0 for l in legs)
            or 1.0
        )
        pnl_pct = (pnl_dollars / denom) * 100.0 if denom else 0.0

        hold_days = None
        if entry_time and exit_time:
            hold_days = max((exit_time.date() - entry_time.date()).days, 0)
        dte_at_entry = None
        if entry_time and exp:
            dte_at_entry = max((exp - entry_time.date()).days, 0)

        # Theta cost per day (losing trades only, where time decay likely
        # dominant). Use absolute theta × hold days, capped at the loss.
        theta_per_day = None
        if pnl_dollars < 0 and legs[0].greeks_at_entry and hold_days:
            theta_entry = legs[0].greeks_at_entry.get("theta") if isinstance(
                legs[0].greeks_at_entry, dict
            ) else None
            if theta_entry is not None and hold_days > 0:
                theta_per_day = abs(float(theta_entry)) * 100.0  # per-contract per-day

        # Entry IV (for bucketing)
        entry_iv = None
        g0 = legs[0].greeks_at_entry
        if isinstance(g0, dict):
            entry_iv = g0.get("iv") or g0.get("iv_rank")
        if entry_iv is None:
            entry_iv = legs[0].iv_at_entry

        strat_buckets[strategy_type].append({
            "pnl_dollars": pnl_dollars,
            "pnl_pct": pnl_pct,
            "hold_days": hold_days,
            "dte_at_entry": dte_at_entry,
            "theta_per_day": theta_per_day,
            "iv_at_entry": entry_iv,
        })

    # Clear stale rows — recompute everything
    await db.execute(delete(_HS).where(_HS.stat_type == "options_performance"))

    def _summary(trades: list[dict]) -> dict:
        n = len(trades)
        wins = [t for t in trades if (t["pnl_dollars"] or 0) > 0]
        return {
            "n": n,
            "win_rate": round(len(wins) / n * 100.0, 1) if n else 0.0,
            "avg_pnl_pct": round(
                sum(t["pnl_pct"] for t in trades) / n, 2
            ) if n else 0.0,
            "avg_hold_days": round(
                sum(t["hold_days"] for t in trades if t["hold_days"] is not None)
                / max(1, sum(1 for t in trades if t["hold_days"] is not None)), 1
            ) if any(t["hold_days"] is not None for t in trades) else None,
            "avg_dte_at_entry": round(
                sum(t["dte_at_entry"] for t in trades if t["dte_at_entry"] is not None)
                / max(1, sum(1 for t in trades if t["dte_at_entry"] is not None)), 1
            ) if any(t["dte_at_entry"] is not None for t in trades) else None,
            "avg_theta_cost_per_day": round(
                sum(t["theta_per_day"] for t in trades if t["theta_per_day"] is not None)
                / max(1, sum(1 for t in trades if t["theta_per_day"] is not None)), 2
            ) if any(t["theta_per_day"] is not None for t in trades) else None,
        }

    for strategy_type, trades in strat_buckets.items():
        if len(trades) < OPTIONS_MIN_TRADES_FOR_STRATEGY:
            continue
        data = {"overall": _summary(trades)}

        if len(trades) >= OPTIONS_MIN_TRADES_FOR_BUCKETS:
            by_iv: dict[str, list[dict]] = defaultdict(list)
            by_dte: dict[str, list[dict]] = defaultdict(list)
            for t in trades:
                b = _iv_bucket(t["iv_at_entry"])
                if b:
                    by_iv[b].append(t)
                b = _dte_bucket(t["dte_at_entry"])
                if b:
                    by_dte[b].append(t)
            data["by_iv"] = {
                k: _summary(v) for k, v in by_iv.items()
                if len(v) >= OPTIONS_MIN_TRADES_FOR_STRATEGY
            }
            data["by_dte"] = {
                k: _summary(v) for k, v in by_dte.items()
                if len(v) >= OPTIONS_MIN_TRADES_FOR_STRATEGY
            }

        await _upsert_stat(
            db,
            "options_performance",
            data,
            strategy=strategy_type,
        )


# ───────────────────────────────────────────────────────────────────────────
# Memory Importance Decay (Phase 6, System 7)
# ───────────────────────────────────────────────────────────────────────────


# Hyperparameters — System 10 (Bayesian) will tune these eventually.
DECAY_MULTIPLIER = 0.85
DECAY_INACTIVITY_DAYS = 30
PRUNE_AGE_DAYS = 90
PRUNE_IMPORTANCE_THRESHOLD = 2.0
IMPORTANCE_FLOOR = 1.0


async def _compute_memory_decay(db):
    """
    Time-based importance decay for memories that haven't been retrieved
    recently. Multiplicative ×decay_multiplier per cycle on any memory
    where last_retrieved_at < now - decay_inactivity_days (or null).

    Then identify pruning candidates: importance ≤ PRUNE_IMPORTANCE_THRESHOLD
    AND age > prune_age_days. Logged via HenryStats but NOT auto-deleted
    in this implementation — auto-delete activates after the decay rate
    is calibrated against real data (via System 10 Bayesian).

    Phase 7: decay_multiplier, decay_inactivity_days, prune_age_days now
    sourced from runtime_config so System 10 can tune them.
    """
    from datetime import timedelta
    from sqlalchemy import select, update, or_, func as _func, and_
    from app.models import HenryMemory
    from app.services import runtime_config as _rc

    decay_multiplier = float(await _rc.get_async("decay_multiplier") or DECAY_MULTIPLIER)
    decay_inactivity_days = int(await _rc.get_async("decay_inactivity_days") or DECAY_INACTIVITY_DAYS)
    prune_age_days = int(await _rc.get_async("prune_age_days") or PRUNE_AGE_DAYS)

    cutoff = utcnow() - timedelta(days=decay_inactivity_days)

    # Single SQL UPDATE for the time-based decay — no Python loop, no
    # per-row reads. Floor at IMPORTANCE_FLOOR so memories never drop
    # below 1.0. Postgres GREATEST handles the floor cleanly.
    decay_stmt = (
        update(HenryMemory)
        .where(
            or_(
                HenryMemory.last_retrieved_at.is_(None),
                HenryMemory.last_retrieved_at < cutoff,
            )
        )
        .where(HenryMemory.importance > IMPORTANCE_FLOOR)
        .values(
            importance=_func.greatest(
                HenryMemory.importance * decay_multiplier,
                IMPORTANCE_FLOOR,
            )
        )
    )
    decay_result = await db.execute(decay_stmt)
    decayed_count = decay_result.rowcount or 0

    # Count pruning candidates (don't delete yet — observability first).
    prune_age_cutoff = utcnow() - timedelta(days=prune_age_days)
    candidates_q = await db.execute(
        select(_func.count(HenryMemory.id)).where(
            and_(
                HenryMemory.importance <= PRUNE_IMPORTANCE_THRESHOLD,
                HenryMemory.created_at <= prune_age_cutoff,
            )
        )
    )
    prune_candidates = int(candidates_q.scalar() or 0)

    # Quick distribution histogram for observability.
    dist_q = await db.execute(
        select(
            _func.count(HenryMemory.id).filter(HenryMemory.importance < 3),
            _func.count(HenryMemory.id).filter(
                and_(HenryMemory.importance >= 3, HenryMemory.importance < 6)
            ),
            _func.count(HenryMemory.id).filter(HenryMemory.importance >= 6),
        )
    )
    dist_low, dist_mid, dist_high = dist_q.one()

    await _upsert_stat(
        db,
        "memory_decay",
        {
            "decay_multiplier": decay_multiplier,
            "inactivity_days": decay_inactivity_days,
            "prune_age_days": prune_age_days,
            "prune_importance_threshold": PRUNE_IMPORTANCE_THRESHOLD,
            "decayed_this_cycle": int(decayed_count),
            "prune_candidates": prune_candidates,
            "distribution": {
                "low (<3)": int(dist_low or 0),
                "mid (3-6)": int(dist_mid or 0),
                "high (>=6)": int(dist_high or 0),
            },
            "ran_at": utcnow().isoformat() + "Z",
        },
        period_days=DECAY_INACTIVITY_DAYS,
    )
    logger.info(
        f"Memory decay: {decayed_count} memories decayed; "
        f"{prune_candidates} prune candidates; "
        f"distribution L/M/H = {dist_low}/{dist_mid}/{dist_high}"
    )


# ───────────────────────────────────────────────────────────────────────────
# Confidence Calibration (Phase 6, System 8)
# ───────────────────────────────────────────────────────────────────────────


CALIBRATION_WINDOW_DAYS = 30
CALIBRATION_MIN_BUCKET = 3
CALIBRATION_MIN_TOTAL = 10  # below this, skip prompt injection entirely


async def _compute_confidence_calibration(db):
    """
    For each confidence bucket (1-10), compute:
      - n: resolved actions in bucket within calibration_window_days
      - actual_win_rate: wins / n (when outcome_correct is set)
      - predicted_win_rate: stated confidence / 10 as a proxy
      - calibration_ratio: actual / predicted (>1 = underconfident,
                                               <1 = overconfident)

    Also stores aggregated 3-tier rollup (high/medium/low) for the
    prompt-injection helper to read directly. System 9 (Adaptive Kelly)
    consumes the per-bucket calibration_ratio.

    Phase 7: calibration_window_days sourced from runtime_config so
    System 10 can tune the rolling window length.
    """
    from collections import defaultdict
    from datetime import timedelta
    from sqlalchemy import select
    from app.models import PortfolioAction
    from app.services import runtime_config as _rc

    window_days = int(await _rc.get_async("calibration_window_days") or CALIBRATION_WINDOW_DAYS)
    cutoff = utcnow() - timedelta(days=window_days)
    rows = list(
        (
            await db.execute(
                select(PortfolioAction)
                .where(PortfolioAction.outcome_correct.is_not(None))
                .where(PortfolioAction.outcome_resolved_at >= cutoff)
            )
        ).scalars().all()
    )
    if not rows:
        # Erase any stale stat row so the prompt builder knows to skip
        # the calibration section instead of injecting outdated data.
        from sqlalchemy import delete
        from app.models import HenryStats as _HS
        await db.execute(
            delete(_HS).where(_HS.stat_type == "confidence_calibration")
        )
        return

    by_bucket: dict[int, list] = defaultdict(list)
    for r in rows:
        c = max(1, min(10, int(r.confidence or 5)))
        by_bucket[c].append(r)

    per_bucket: dict[str, dict] = {}
    for bucket, items in by_bucket.items():
        if len(items) < CALIBRATION_MIN_BUCKET:
            continue
        wins = sum(1 for x in items if x.outcome_correct)
        n = len(items)
        actual = wins / n
        predicted = bucket / 10.0
        ratio = actual / predicted if predicted > 0 else 0.0
        per_bucket[str(bucket)] = {
            "n": n,
            "wins": wins,
            "actual_win_rate": round(actual, 3),
            "predicted_win_rate": round(predicted, 3),
            "calibration_ratio": round(ratio, 3),
        }

    # Three-tier aggregation for prompt readability.
    def _tier(items):
        if not items:
            return None
        wins = sum(1 for x in items if x.outcome_correct)
        n = len(items)
        avg_conf = sum(int(x.confidence or 5) for x in items) / n
        actual = wins / n
        predicted = avg_conf / 10.0
        return {
            "n": n,
            "avg_confidence": round(avg_conf, 1),
            "actual_win_rate": round(actual, 3),
            "predicted_win_rate": round(predicted, 3),
            "calibration_ratio": round(actual / predicted, 3) if predicted > 0 else 0.0,
        }

    high_items = [r for b in range(8, 11) for r in by_bucket.get(b, [])]
    mid_items = [r for b in range(5, 8) for r in by_bucket.get(b, [])]
    low_items = [r for b in range(1, 5) for r in by_bucket.get(b, [])]

    total_n = sum(len(v) for v in by_bucket.values())

    await _upsert_stat(
        db,
        "confidence_calibration",
        {
            "window_days": window_days,
            "n_total": total_n,
            "sufficient_for_prompt": total_n >= CALIBRATION_MIN_TOTAL,
            "per_bucket": per_bucket,
            "tiers": {
                "high (8-10)": _tier(high_items),
                "medium (5-7)": _tier(mid_items),
                "low (1-4)": _tier(low_items),
            },
            "ran_at": utcnow().isoformat() + "Z",
        },
        period_days=window_days,
    )
    logger.info(
        f"Confidence calibration: n={total_n} resolved actions; "
        f"buckets with data = {sorted(per_bucket.keys())}"
    )


# ───────────────────────────────────────────────────────────────────────────
# Adaptive Kelly Fraction (Phase 6, System 9)
# ───────────────────────────────────────────────────────────────────────────


KELLY_BASE_INITIAL = 0.25
KELLY_BASE_FLOOR = 0.10
KELLY_BASE_CAP = 0.50
KELLY_ERROR_TIGHTEN = 0.30   # error > this → tighten f_base
KELLY_ERROR_WIDEN = 0.15     # error < this → widen f_base
KELLY_TIGHTEN_PCT = 0.10     # downward adjustment magnitude
KELLY_WIDEN_PCT = 0.05       # upward adjustment magnitude
KELLY_EMA_ALPHA = 0.1
KELLY_MIN_TRADES_FOR_ADJUST = 8


async def compute_adaptive_kelly_weekly(db):
    """
    Weekly self-adjustment of f_base. Standalone async function (not a
    `_compute_*` stats stage) so it can be scheduled separately at
    Sunday 11pm ET. Daily would be too noisy.

    Reads resolved PortfolioActions with both outcome_correct AND
    kelly_f_effective set. Computes EMA of |predicted - actual| where
    `predicted` is win_rate from the conditional probability table at
    decision time and `actual` is 1 (win) or 0 (loss).

    If we don't have the predicted value stored on the action (older
    actions), we use `confidence / 10` as a proxy.
    """
    from collections import defaultdict
    from sqlalchemy import select
    from app.models import PortfolioAction, HenryStats
    from app.services import runtime_config as _rc

    # Phase 7 — runtime_config wins over the module-level constants
    # so System 10 can tune the adaptive thresholds and the cap.
    initial = float(await _rc.get_async("kelly_base_initial") or KELLY_BASE_INITIAL)
    base_cap = float(await _rc.get_async("kelly_base_cap") or KELLY_BASE_CAP)
    tighten_thr = float(await _rc.get_async("kelly_error_tighten_threshold") or KELLY_ERROR_TIGHTEN)
    widen_thr = float(await _rc.get_async("kelly_error_widen_threshold") or KELLY_ERROR_WIDEN)

    # Pull most recent f_base; default to runtime-config initial.
    prior_row = (
        await db.execute(
            select(HenryStats)
            .where(HenryStats.stat_type == "adaptive_kelly")
            .order_by(HenryStats.computed_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    f_base = (prior_row.data or {}).get("f_base") if prior_row else None
    if f_base is None:
        f_base = initial

    # Pull resolved actions in chronological order so EMA decays correctly.
    rows = list(
        (
            await db.execute(
                select(PortfolioAction)
                .where(PortfolioAction.outcome_correct.is_not(None))
                .where(PortfolioAction.kelly_f_effective.is_not(None))
                .order_by(PortfolioAction.outcome_resolved_at.asc())
                .limit(200)
            )
        ).scalars().all()
    )

    if len(rows) < KELLY_MIN_TRADES_FOR_ADJUST:
        # Persist the unchanged f_base so the sizing utility can still
        # find a row to read on its next call.
        await _upsert_stat(
            db,
            "adaptive_kelly",
            {
                "f_base": f_base,
                "rolling_error": None,
                "n_resolved": len(rows),
                "decision": "insufficient_data",
                "ran_at": utcnow().isoformat() + "Z",
            },
        )
        return

    # EMA of absolute prediction error
    ema = None
    for r in rows:
        actual = 1.0 if r.outcome_correct else 0.0
        # `predicted` proxy = stated confidence / 10 (best we have
        # without a historical-prediction column).
        predicted = max(1, min(10, int(r.confidence or 5))) / 10.0
        err = abs(predicted - actual)
        ema = err if ema is None else (KELLY_EMA_ALPHA * err + (1 - KELLY_EMA_ALPHA) * ema)
    rolling_error = float(ema)

    decision = "held"
    new_f_base = f_base
    if rolling_error > tighten_thr:
        new_f_base = max(KELLY_BASE_FLOOR, f_base * (1.0 - KELLY_TIGHTEN_PCT))
        decision = "tightened"
    elif rolling_error < widen_thr:
        new_f_base = min(base_cap, f_base * (1.0 + KELLY_WIDEN_PCT))
        decision = "widened"

    history = (prior_row.data or {}).get("history", []) if prior_row else []
    history = (history + [
        {
            "ts": utcnow().isoformat() + "Z",
            "from": round(f_base, 4),
            "to": round(new_f_base, 4),
            "rolling_error": round(rolling_error, 4),
            "decision": decision,
        }
    ])[-26:]  # keep ~6 months of weekly entries

    await _upsert_stat(
        db,
        "adaptive_kelly",
        {
            "f_base": round(new_f_base, 4),
            "previous_f_base": round(f_base, 4),
            "rolling_error": round(rolling_error, 4),
            "n_resolved": len(rows),
            "decision": decision,
            "thresholds": {
                "tighten_above": tighten_thr,
                "widen_below": widen_thr,
                "floor": KELLY_BASE_FLOOR,
                "cap": base_cap,
            },
            "history": history,
            "ran_at": utcnow().isoformat() + "Z",
        },
    )
    logger.info(
        f"Adaptive Kelly: f_base {f_base:.3f} → {new_f_base:.3f} "
        f"({decision}); rolling_error={rolling_error:.3f} over {len(rows)} trades"
    )
