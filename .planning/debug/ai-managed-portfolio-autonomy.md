---
status: investigating
trigger: "AI-managed portfolios are not under complete autonomous management by Henry. Three linked symptoms reported."
created: 2026-04-14T00:00:00Z
updated: 2026-04-14T00:00:00Z
---

## Current Focus

hypothesis: H1 confirmed — query_trades() never receives portfolio_id, so _build_system_prompt is called without it. H2/H3 eliminated. H4 confirmed (0%-P&L trades pollute stats, but no filter guards).
test: Read ai_service.py query_trades signature and its _build_system_prompt call; read scheduler.py cron entry; read _get_ai_enabled_portfolios.
expecting: Fix required in two places: query_trades signature + _build_system_prompt call site.
next_action: Fix query_trades to accept and forward portfolio_id; filter 0%-P&L trades in henry_stats_engine.

## Symptoms

expected: Henry recognizes live AI portfolio holdings, runs 2:30 PM stop/take-profit closes, and stats are clean.
actual: (1) Henry hallucinates holdings. (2) No autonomous closes on live AI portfolio. (3) Stats skewed by 0%-P&L trades.
errors: No exceptions — silent wrong behavior.
reproduction: Ask Henry about holdings in is_ai_managed portfolio; check Actions tab after 2:30 PM.
started: Present throughout; live portfolio never autonomously managed until PR#3.

## Eliminated

- hypothesis: H2 — _get_ai_enabled_portfolios() has wrong OR/AND
  evidence: autonomous_trading.py:44 uses sqlalchemy or_() correctly across is_ai_managed and ai_evaluation_enabled
  timestamp: 2026-04-14

- hypothesis: H3 — scheduler missing the review job
  evidence: scheduler.py line 794-800 has CronTrigger(hour=14, minute=30) calling _run_ai_portfolio_review → scheduled_ai_portfolio_review; fully wired
  timestamp: 2026-04-14

- hypothesis: H5 — scoping SQL bug
  evidence: main.py get_trades_for_ai uses outerjoin on PortfolioTrade and carries portfolio_id; the /api/ai/query endpoint filters correctly at lines 2114-2119. The bug is upstream in query_trades, not in the SQL.
  timestamp: 2026-04-14

## Evidence

- timestamp: 2026-04-14
  checked: ai_service.py query_trades() signature (line 1627-1632)
  found: Signature is (question, all_trades, open_positions, holdings_context) — no portfolio_id parameter
  implication: portfolio_id can never reach _build_system_prompt from the chat path

- timestamp: 2026-04-14
  checked: ai_service.py line 1708
  found: _build_system_prompt called as _build_system_prompt(enable_web_search=True, query_text=question) — no portfolio_id, scope defaults to "general"
  implication: BROKERAGE ACCOUNTS block (line 161-223) is reached (scope="general" is allowed) BUT without portfolio_id the query fetches ALL portfolios, not the one being asked about. More critically, scope is not "portfolio" so the fetch may include irrelevant brokerage data.

- timestamp: 2026-04-14
  checked: ai_service.py line 2252 — call site in ai_query endpoint
  found: query_trades(scoped_question, all_trades, positions, holdings_context=holdings_context) — req.portfolio_id is available in scope but not forwarded to query_trades
  implication: Even though req.portfolio_id is used to scope holdings_context and filter trades, it is dropped before _build_system_prompt executes. Henry's system prompt does not know which portfolio is being queried — no portfolio-scoped brokerage account data (equity, buying power) is injected.

- timestamp: 2026-04-14
  checked: henry_stats_engine.py _compute_strategy_performance (lines 120-177)
  found: No filter for pnl_percent == 0 or exit_price == entry_price. All closed trades including 0%-fallback trades are included in avg_gain/avg_loss/profit_factor calculations.
  implication: Trades closed via the entry_price fallback in _process_exit skew win_rate down (they appear as losses since pnl_dollars=0 falls in the losses bucket: pnl_dollars<=0) and reduce avg_loss toward 0.

- timestamp: 2026-04-14
  checked: trade_processor.py _process_exit lines 355-363
  found: Final fallback sets exit_price = entry_price and records a warning, but pnl_percent = 0.0, pnl_dollars = 0. These appear in _compute_strategy_performance losses bucket (pnl_dollars<=0 at line 140).
  implication: Every price-service-cold exit adds a fake 0%-loss trade to the strategy's record.

## Resolution

root_cause: |
  THREE bugs:
  1. (PRIMARY — symptom #1) query_trades() in ai_service.py has no portfolio_id param and calls _build_system_prompt without it. The /api/ai/query endpoint has req.portfolio_id available but drops it before calling query_trades, so Henry's system prompt never gets the portfolio-scoped brokerage account info (equity, buying power, holdings) that _build_system_prompt would inject when portfolio_id is present. Henry fabricates holdings because the live portfolio's context is absent.
  2. (SECONDARY — symptom #2) The scheduled review and portfolio discovery are correctly wired (PR#3). No code bug. The live portfolio was simply never reviewed until PR#3 merged. This symptom should self-resolve once PR#3 code is running.
  3. (TERTIARY — symptom #3) henry_stats_engine._compute_strategy_performance includes trades where exit_price was forced to entry_price (0% P&L). These pollute avg_loss and profit_factor. No filter exists to skip malformed/fallback-priced exits.

fix: |
  Fix 1 (ai_service.py — query_trades + call site):
    a. Add portfolio_id: str = None to query_trades() signature.
    b. Forward it to _build_system_prompt: change the call at line 1708 to
       _build_system_prompt(enable_web_search=True, query_text=question, portfolio_id=portfolio_id, scope="portfolio" if portfolio_id else "general")
    c. In the ai_query endpoint (line 2252), pass portfolio_id:
       query_trades(scoped_question, all_trades, positions, holdings_context=holdings_context, portfolio_id=req.portfolio_id)

  Fix 2 (henry_stats_engine.py — filter 0%-fallback trades):
    In _compute_strategy_performance, add a filter before grouping:
       trades = [t for t in trades if not (t.pnl_percent == 0.0 and t.exit_reason and "fallback" in (t.exit_reason or "").lower())]
    OR more robustly, skip trades where exit_price == entry_price:
       trades = [t for t in trades if t.exit_price is None or t.entry_price is None or abs(t.exit_price - t.entry_price) > 0.001]

verification: pending
files_changed:
  - backend/app/services/ai_service.py (query_trades signature + _build_system_prompt call + ai_query call site)
  - backend/app/services/henry_stats_engine.py (_compute_strategy_performance filter)
