"""
Screener AI Integration
========================
Analyzes indicator alerts from the screener and generates:
1. Trade ideas with price targets, entry/stop levels
2. Market context (sector heat, catalysts, noise ratio)
"""

import json
import logging
from datetime import datetime, timedelta

import anthropic

logger = logging.getLogger(__name__)

MODEL = "claude-sonnet-4-5-20250929"  # Sonnet 4.5

try:
    CLIENT = anthropic.Anthropic()
except Exception:
    CLIENT = None

SCREENER_SYSTEM_PROMPT = """You are Henry, an AI trading analyst embedded in a multi-strategy trading dashboard.
You analyze indicator alerts from a real-time screener to identify high-conviction trade opportunities.

Your job:
1. Filter noise from signal — most alerts are noise, identify the ones that matter
2. Cross-reference multiple indicators on the same ticker to find convergence
3. Consider indicator type, timeframe alignment, and volume of signals
4. Produce specific, actionable trade ideas with entry zones, price targets, and stop losses
5. Provide market context — what sectors are heating up, upcoming catalysts, overall signal quality

You speak concisely and directly. No fluff. Use numbers to back up every claim.
Format currency as $X.XX. Format percentages as X.X%.
If data is insufficient, say so rather than speculating."""


async def analyze_screener_signals(
    alerts: list[dict],
    ticker_aggregations: list[dict],
    chart_data: dict[str, list[dict]] | None = None,
    portfolio_positions: list[dict] | None = None,
) -> dict:
    """
    Analyze screener alerts and generate trade ideas.

    Args:
        alerts: Recent indicator alerts
        ticker_aggregations: Aggregated view per ticker with alert counts
        chart_data: Optional daily OHLCV data keyed by ticker
        portfolio_positions: Current portfolio positions for context

    Returns:
        dict with keys: picks (list of trade ideas), market_context
    """
    if not alerts:
        return {"picks": [], "market_context": {"sector_heat": "No data", "catalysts": "No data", "noise_ratio": "No alerts"}}

    # Pre-compute signal consensus per ticker (replace AI counting with code)
    consensus = {}
    for a in alerts:
        tk = a.get("ticker", "?")
        sig = a.get("signal", "neutral")
        if tk not in consensus:
            consensus[tk] = {"bullish": 0, "bearish": 0, "neutral": 0, "indicators": set()}
        consensus[tk][sig] = consensus[tk].get(sig, 0) + 1
        consensus[tk]["indicators"].add(a.get("indicator", "?"))

    # Format pre-computed consensus (saves AI from counting)
    agg_lines = []
    for tk, c in sorted(consensus.items(), key=lambda x: sum(v for k, v in x[1].items() if k != "indicators"), reverse=True)[:15]:
        total = c["bullish"] + c["bearish"] + c["neutral"]
        bias = "BULLISH" if c["bullish"] > c["bearish"] * 1.5 else ("BEARISH" if c["bearish"] > c["bullish"] * 1.5 else "MIXED")
        agg_lines.append(
            f"  {tk}: {total} alerts | {bias} ({c['bullish']}B/{c['bearish']}b/{c['neutral']}n) | "
            f"{', '.join(sorted(c['indicators']))}"
        )
    agg_text = "\n".join(agg_lines)

    # Only include alerts for top tickers (not all 100)
    top_tickers_set = {line.split(":")[0].strip() for line in agg_lines}
    alert_lines = []
    for a in alerts:
        if a.get("ticker", "?") in top_tickers_set:
            alert_lines.append(
                f"  {a.get('ticker','?')} | {a.get('indicator','?')} = {a.get('value',0):.2f} | "
                f"signal={a.get('signal','?')} | tf={a.get('timeframe','?')}"
            )
    alerts_text = "\n".join(alert_lines[:30])

    # Format chart data for top tickers
    chart_text = "Not available."
    if chart_data:
        chart_lines = []
        for ticker, data in list(chart_data.items())[:5]:
            if data:
                latest = data[-1]
                prev = data[-2] if len(data) > 1 else latest
                change = ((latest["close"] - prev["close"]) / prev["close"] * 100) if prev["close"] > 0 else 0
                high_20d = max(d["high"] for d in data[-20:]) if len(data) >= 20 else max(d["high"] for d in data)
                low_20d = min(d["low"] for d in data[-20:]) if len(data) >= 20 else min(d["low"] for d in data)
                chart_lines.append(
                    f"  {ticker}: Close=${latest['close']:.2f} ({change:+.2f}%) | "
                    f"20D Range: ${low_20d:.2f}-${high_20d:.2f} | Vol={latest.get('volume',0):,}"
                )
        chart_text = "\n".join(chart_lines) if chart_lines else "Not available."

    # Format positions
    positions_text = "No current positions."
    if portfolio_positions:
        pos_lines = []
        for p in portfolio_positions:
            pos_lines.append(
                f"  {p.get('dir','?').upper()} {p.get('ticker','?')} @ ${p.get('entry_price',0):.2f} | "
                f"current=${p.get('current_price',0):.2f} | pnl={p.get('pnl_pct',0):.2f}%"
            )
        positions_text = "\n".join(pos_lines)

    prompt = f"""Analyze the screener signals below and generate trade ideas.

TICKER SUMMARY (by alert volume):
{agg_text}

RECENT ALERTS (last 24h):
{alerts_text}

CHART DATA (daily):
{chart_text}

CURRENT PORTFOLIO POSITIONS:
{positions_text}

Respond in EXACTLY this JSON format (no markdown, no backticks):
{{
  "picks": [
    {{
      "ticker": "AAPL",
      "direction": "LONG",
      "entry_zone": "$185-$187",
      "price_target": "$195",
      "stop_loss": "$182",
      "confidence": 8,
      "thesis": "3 bullish indicators converging + volume breakout above 20D high",
      "indicators": ["RSI", "MACD_CROSS", "VOL_SPIKE"]
    }}
  ],
  "market_context": {{
    "sector_heat": "Tech names clustering bullish, energy cooling",
    "catalysts": "FOMC Wednesday, AAPL earnings Thursday",
    "noise_ratio": "12 tickers alerting, 3 worth watching"
  }}
}}

Rules:
- Maximum 4 picks, minimum 0 (if nothing is compelling, return empty picks)
- Confidence 1-10 (only recommend trades with confidence >= 6)
- Price targets should be realistic (2-8% moves for swing trades)
- Flag any tickers that overlap with current portfolio positions
- Be specific about WHY the indicator convergence matters
- Sort picks by confidence descending"""

    try:
        from app.services.ai_provider import call_ai
        raw = await call_ai(SCREENER_SYSTEM_PROMPT, prompt, function_name="screener_analysis", max_tokens=1200)

        # Parse JSON response
        clean = raw.strip().replace("```json", "").replace("```", "").strip()
        result = json.loads(clean)

        return {
            "picks": result.get("picks", []),
            "market_context": result.get("market_context", {
                "sector_heat": "Unable to determine",
                "catalysts": "Unable to determine",
                "noise_ratio": "Unable to determine",
            }),
        }
    except json.JSONDecodeError:
        logger.error(f"Failed to parse screener AI response")
        return {"picks": [], "market_context": {"sector_heat": "Parse error", "catalysts": "N/A", "noise_ratio": "N/A"}}
    except Exception as e:
        logger.error(f"Screener AI analysis failed: {e}")
        return {"picks": [], "market_context": {"sector_heat": "Error", "catalysts": "N/A", "noise_ratio": "N/A"}}


TICKER_ANALYSIS_SYSTEM_PROMPT_TEMPLATE = """You are Henry, an AI trading analyst embedded in a multi-strategy trading dashboard.
You are analyzing a SPECIFIC ticker based on real-time screener alerts and portfolio context.

{strategies_section}

PLAY TYPE CLASSIFICATION:
- DAILY play: Majority of alerts on intraday timeframes (1m, 5m, 15m, 1H),
  pattern suggests same-day move, momentum indicators dominant (RSI, MACD),
  breakout/volume spike patterns. Typical hold: hours to 1 day.
- WEEKLY play: Majority of alerts on higher timeframes (4H, D, W),
  structural/SMC levels involved (Order Blocks, FVGs, CHoCH),
  trend indicators dominant (ADX, SMA crossovers, Kalman filter).
  Typical hold: 2-10 days.

You speak concisely and directly. No fluff. Use numbers to back up every claim.
Format currency as $X.XX. Format percentages as X.X%.
If data is insufficient, say so rather than speculating."""


def _get_strategies_section() -> str:
    """Dynamically build the strategies section from the traders table."""
    try:
        from app.database import async_session
        from app.models.trader import Trader
        from sqlalchemy import select
        import asyncio

        async def _fetch():
            async with async_session() as db:
                result = await db.execute(
                    select(Trader).where(Trader.is_active == True)
                )
                return result.scalars().all()

        # Try to get the event loop; if we're in a sync context, run in a new loop
        try:
            loop = asyncio.get_running_loop()
            # We're in an async context but this function is sync — use cached or fallback
            return _CACHED_STRATEGIES_SECTION or "Strategies are loaded dynamically from the database."
        except RuntimeError:
            traders = asyncio.run(_fetch())

        if not traders:
            return "No strategies configured."

        lines = ["The Pine Script strategies in this system are:"]
        for t in traders:
            desc = getattr(t, "strategy_description", None) or t.description or "No description"
            lines.append(f"  - {t.trader_id} ({t.display_name}): {desc}")
        section = "\n".join(lines)
        global _CACHED_STRATEGIES_SECTION
        _CACHED_STRATEGIES_SECTION = section
        return section
    except Exception:
        return "Strategies are loaded dynamically from the database."

_CACHED_STRATEGIES_SECTION: str | None = None


async def refresh_strategies_cache() -> None:
    """Refresh the cached strategies section. Call on startup or when traders change."""
    from app.database import async_session
    from app.models.trader import Trader
    from sqlalchemy import select

    try:
        async with async_session() as db:
            result = await db.execute(
                select(Trader).where(Trader.is_active == True)
            )
            traders = result.scalars().all()

        if traders:
            lines = ["The Pine Script strategies in this system are:"]
            for t in traders:
                desc = getattr(t, "strategy_description", None) or t.description or "No description"
                lines.append(f"  - {t.trader_id} ({t.display_name}): {desc}")
            global _CACHED_STRATEGIES_SECTION
            _CACHED_STRATEGIES_SECTION = "\n".join(lines)
    except Exception:
        pass


def _classify_play_type(alerts: list[dict]) -> str:
    """Heuristic play type classification based on timeframe distribution."""
    intraday_tfs = {"1M", "5M", "15M", "30M", "1H"}
    swing_tfs = {"4H", "D", "W", "1D", "1W"}

    intraday_count = sum(
        1 for a in alerts
        if (a.get("timeframe") or "").upper() in intraday_tfs
    )
    swing_count = sum(
        1 for a in alerts
        if (a.get("timeframe") or "").upper() in swing_tfs
    )

    return "WEEKLY" if swing_count > intraday_count else "DAILY"


async def analyze_single_ticker(
    ticker: str,
    alerts: list[dict],
    chart_data: list[dict] | None = None,
    portfolio_positions: list[dict] | None = None,
    trade_history: list[dict] | None = None,
) -> dict:
    """
    Generate a detailed analysis for a single ticker when its card is opened.

    Returns dict matching TickerAnalysisResponse schema fields.
    """
    if not alerts:
        return {
            "play_type": "DAILY",
            "direction": "LONG",
            "confidence": 0,
            "thesis": "No alerts to analyze.",
            "entry_zone": "N/A",
            "price_target": "N/A",
            "stop_loss": "N/A",
            "risk_reward": "N/A",
            "indicators_firing": [],
            "signal_breakdown": {"bullish": 0, "bearish": 0, "neutral": 0},
            "dominant_signal": "neutral",
            "historical_matches": [],
            "strategy_alignment": [],
            "alert_timeline_summary": "No alerts.",
            "timeframes_represented": [],
        }

    # Pre-compute signal breakdown
    bullish = sum(1 for a in alerts if a.get("signal", "").lower() == "bullish")
    bearish = sum(1 for a in alerts if a.get("signal", "").lower() == "bearish")
    neutral = len(alerts) - bullish - bearish
    dominant = "bullish" if bullish >= bearish else "bearish"

    # Pre-compute indicators firing
    indicators_firing = list({a.get("indicator", "?") for a in alerts})

    # Pre-compute timeframe distribution
    tf_counts: dict[str, int] = {}
    for a in alerts:
        tf = a.get("timeframe") or "unknown"
        tf_counts[tf] = tf_counts.get(tf, 0) + 1
    tf_text = ", ".join(f"{tf}: {c}" for tf, c in sorted(tf_counts.items(), key=lambda x: -x[1]))
    timeframes = list(tf_counts.keys())

    heuristic_play = _classify_play_type(alerts)

    # Format alerts for prompt
    alert_lines = []
    for a in alerts[:50]:
        alert_lines.append(
            f"  {a.get('indicator','?')} = {a.get('value',0):.2f} | "
            f"signal={a.get('signal','?')} | tf={a.get('timeframe','?')} | {a.get('created_at','?')}"
        )
    alerts_text = "\n".join(alert_lines)

    # Format chart data
    chart_text = "Not available."
    if chart_data and len(chart_data) >= 2:
        latest = chart_data[-1]
        prev = chart_data[-2]
        change_1d = ((latest["close"] - prev["close"]) / prev["close"] * 100) if prev["close"] > 0 else 0
        high_20d = max(d["high"] for d in chart_data[-20:]) if len(chart_data) >= 20 else max(d["high"] for d in chart_data)
        low_20d = min(d["low"] for d in chart_data[-20:]) if len(chart_data) >= 20 else min(d["low"] for d in chart_data)
        first_20 = chart_data[-20] if len(chart_data) >= 20 else chart_data[0]
        change_20d = ((latest["close"] - first_20["close"]) / first_20["close"] * 100) if first_20["close"] > 0 else 0
        chart_text = (
            f"  Current: ${latest['close']:.2f} | 1D Change: {change_1d:+.2f}%\n"
            f"  20D Range: ${low_20d:.2f} - ${high_20d:.2f} | 20D Change: {change_20d:+.2f}%\n"
            f"  Volume: {latest.get('volume', 0):,}"
        )

    # Format positions
    positions_text = "None."
    if portfolio_positions:
        pos_lines = []
        for p in portfolio_positions:
            pos_lines.append(
                f"  {p.get('strategy_name','?')}: {p.get('dir','?').upper()} @ ${p.get('entry_price',0):.2f} | "
                f"current=${p.get('current_price',0):.2f} | pnl={p.get('pnl_pct',0):.2f}%"
            )
        positions_text = "\n".join(pos_lines)

    # Format trade history
    history_text = "No recent history."
    if trade_history:
        hist_lines = []
        for t in trade_history[:15]:
            hist_lines.append(
                f"  {t.get('strategy_name','?')} | {t.get('dir','?').upper()} @ ${t.get('entry_price',0):.2f} → "
                f"${t.get('exit_price',0):.2f} | pnl={t.get('pnl_pct',0):+.2f}% | "
                f"bars={t.get('bars_in_trade',0)} | reason={t.get('exit_reason','?')} | "
                f"{t.get('exit_time','?')}"
            )
        history_text = "\n".join(hist_lines)

    prompt = f"""Analyze {ticker} based on the screener data below.

ALERTS FOR {ticker} ({len(alerts)} alerts):
{alerts_text}

TIMEFRAME DISTRIBUTION:
  {tf_text}
  Heuristic classification: {heuristic_play} play

SIGNAL BREAKDOWN:
  Bullish: {bullish} | Bearish: {bearish} | Neutral: {neutral}

CHART DATA (daily):
{chart_text}

CURRENT PORTFOLIO POSITIONS ON {ticker}:
{positions_text}

TRADE HISTORY ON {ticker} (last 30 days):
{history_text}

Respond in EXACTLY this JSON format (no markdown, no backticks):
{{
  "play_type": "DAILY" or "WEEKLY",
  "direction": "LONG" or "SHORT",
  "confidence": 1-100,
  "thesis": "2-3 sentences explaining the opportunity with specific numbers",
  "entry_zone": "$X.XX - $X.XX",
  "price_target": "$X.XX",
  "stop_loss": "$X.XX",
  "risk_reward": "1:X.X",
  "historical_matches": [
    {{
      "pattern": "description of the indicator convergence pattern",
      "occurrences": N,
      "avg_return_pct": X.X,
      "win_rate": X.X,
      "avg_bars_held": N,
      "sample_dates": ["YYYY-MM-DD"]
    }}
  ],
  "strategy_alignment": [
    {{
      "strategy_name": "Strategy Display Name",
      "strategy_id": "strategy-slug",
      "has_active_position": true/false,
      "position_direction": "long"/"short"/null,
      "latest_signal": "entry"/"exit"/null,
      "signal_agrees": true/false,
      "notes": "Brief explanation"
    }}
  ],
  "alert_timeline_summary": "Description of alert velocity and clustering pattern",
  "timeframes_represented": ["5m", "1H", "4H"]
}}

Rules:
- play_type: Use the timeframe distribution and indicator types to classify. SMC/structural indicators suggest WEEKLY even on shorter timeframes.
- confidence: 1-100 scale. Consider indicator convergence, volume confirmation, strategy alignment, and historical reliability.
- historical_matches: Infer from trade history what happened when similar indicator combinations fired. If insufficient data, return empty array and note in thesis.
- strategy_alignment: List all strategies from the system (names provided in the system prompt). If a strategy has no data, still list it with has_active_position: false.
- Be specific about entry, target, stop levels using the chart data.
- If data is insufficient for a confident call, lower confidence and say so in thesis."""

    try:
        from app.services.ai_provider import call_ai
        system = TICKER_ANALYSIS_SYSTEM_PROMPT_TEMPLATE.format(
            strategies_section=_get_strategies_section()
        )
        raw = await call_ai(system, prompt, function_name="screener_analysis", max_tokens=2500)
        clean = raw.strip().replace("```json", "").replace("```", "").strip()
        result = json.loads(clean)

        return {
            "play_type": result.get("play_type", heuristic_play),
            "direction": result.get("direction", "LONG" if dominant == "bullish" else "SHORT"),
            "confidence": result.get("confidence", 50),
            "thesis": result.get("thesis", "Analysis unavailable."),
            "entry_zone": result.get("entry_zone", "N/A"),
            "price_target": result.get("price_target", "N/A"),
            "stop_loss": result.get("stop_loss", "N/A"),
            "risk_reward": result.get("risk_reward", "N/A"),
            "indicators_firing": indicators_firing,
            "signal_breakdown": {"bullish": bullish, "bearish": bearish, "neutral": neutral},
            "dominant_signal": dominant,
            "historical_matches": result.get("historical_matches", []),
            "strategy_alignment": result.get("strategy_alignment", []),
            "alert_timeline_summary": result.get("alert_timeline_summary", f"{len(alerts)} alerts"),
            "timeframes_represented": result.get("timeframes_represented", timeframes),
        }
    except json.JSONDecodeError:
        logger.error("Failed to parse ticker analysis AI response")
        return {
            "play_type": heuristic_play,
            "direction": "LONG" if dominant == "bullish" else "SHORT",
            "confidence": 30,
            "thesis": "AI analysis response could not be parsed. Signal data suggests a directional bias based on indicator convergence.",
            "entry_zone": "N/A",
            "price_target": "N/A",
            "stop_loss": "N/A",
            "risk_reward": "N/A",
            "indicators_firing": indicators_firing,
            "signal_breakdown": {"bullish": bullish, "bearish": bearish, "neutral": neutral},
            "dominant_signal": dominant,
            "historical_matches": [],
            "strategy_alignment": [],
            "alert_timeline_summary": f"{len(alerts)} alerts in window",
            "timeframes_represented": timeframes,
        }
    except Exception as e:
        logger.error(f"Ticker analysis failed for {ticker}: {e}")
        return {
            "play_type": heuristic_play,
            "direction": "LONG" if dominant == "bullish" else "SHORT",
            "confidence": 0,
            "thesis": f"Analysis failed: {str(e)[:100]}",
            "entry_zone": "N/A",
            "price_target": "N/A",
            "stop_loss": "N/A",
            "risk_reward": "N/A",
            "indicators_firing": indicators_firing,
            "signal_breakdown": {"bullish": bullish, "bearish": bearish, "neutral": neutral},
            "dominant_signal": dominant,
            "historical_matches": [],
            "strategy_alignment": [],
            "alert_timeline_summary": f"{len(alerts)} alerts in window",
            "timeframes_represented": timeframes,
        }


async def generate_market_summary(
    summary_type: str,
    portfolio_data: dict,
    screener_data: dict,
    picks_data: list[dict] | None = None,
) -> str:
    """
    Generate morning/nightly market summary combining portfolio + screener data.

    Args:
        summary_type: "morning" or "nightly"
        portfolio_data: Dict with positions, trades, performance
        screener_data: Dict with alerts, ticker aggregations
        picks_data: Previous picks to score (for nightly)
    """
    if summary_type == "morning":
        prompt = f"""Generate a concise morning briefing combining portfolio and screener data.

PORTFOLIO:
  Open positions: {json.dumps(portfolio_data.get('positions', []), indent=2)[:1000]}
  Yesterday's trades: {len(portfolio_data.get('trades', []))} trades

SCREENER (last 12h):
  Active tickers: {len(screener_data.get('tickers', []))}
  Total alerts: {screener_data.get('alert_count', 0)}
  Top tickers: {json.dumps(screener_data.get('top_tickers', [])[:5], indent=2)[:500]}

Write a 4-section briefing:
1. PORTFOLIO OUTLOOK — What's live, overnight gaps, immediate risk
2. SCREENER DIGEST — What's lighting up, any clustering patterns
3. TODAY'S FOCUS — Top 3 tickers to watch with reasoning
4. RISK CALLOUTS — Events, overbought signals, correlation risks

Keep it under 300 words. Be direct and specific."""

    else:  # nightly
        picks_scorecard = ""
        if picks_data:
            picks_scorecard = f"\nMORNING PICKS PERFORMANCE:\n{json.dumps(picks_data[:4], indent=2)[:500]}"

        prompt = f"""Generate a concise nightly summary combining portfolio and screener data.

PORTFOLIO:
  Today's closed trades: {len(portfolio_data.get('closed_today', []))}
  Day P&L: {portfolio_data.get('day_pnl', 'N/A')}

SCREENER (full day):
  Total alerts fired: {screener_data.get('alert_count', 0)}
  Tickers that alerted: {len(screener_data.get('tickers', []))}
  Top tickers: {json.dumps(screener_data.get('top_tickers', [])[:5], indent=2)[:500]}
{picks_scorecard}

Write a 4-section recap:
1. PERFORMANCE RECAP — Wins/losses, net P&L across portfolios
2. PICK SCORECARD — How morning recommendations performed (if available)
3. SCREENER PATTERNS — What indicators fired most, sector trends
4. TOMORROW SETUP — 3 tickers worth watching into tomorrow

Keep it under 300 words. Be direct and specific."""

    try:
        from app.services.ai_provider import call_ai
        return await call_ai(SCREENER_SYSTEM_PROMPT, prompt, function_name="screener_analysis", max_tokens=1500)
    except Exception as e:
        logger.error(f"Market summary generation failed: {e}")
        return f"Summary generation failed: {str(e)}"
