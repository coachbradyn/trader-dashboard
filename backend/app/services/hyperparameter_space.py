"""
Hyperparameter Search Space (Phase 7, System 10)
=================================================

Single source of truth for the tunable hyperparameters Bayesian
optimization is allowed to adjust. Each entry declares the parameter's
type, range, default, and which consumer reads it.

Adding/removing a parameter here is the only place the optimizer's
search space changes — bayesian_optimizer reads PARAMS at fit time.

Kept deliberately small to start (~13 params per the brief). Adding
more dimensions exponentially grows the GP exploration cost; keep new
additions to params with measurable trade-outcome impact.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional


@dataclass
class HyperParam:
    name: str
    kind: str               # 'int' | 'float'
    low: float              # inclusive
    high: float             # inclusive
    default: float          # value used until adoption
    consumer: str           # short module/path so refactor sites are obvious
    notes: str = ""

    def clamp(self, value: Any) -> float:
        if value is None:
            return self.default
        try:
            v = float(value)
        except (TypeError, ValueError):
            return self.default
        v = max(self.low, min(self.high, v))
        if self.kind == "int":
            v = round(v)
        return v


# Order matters only for display — optimization is permutation-invariant.
PARAMS: list[HyperParam] = [
    # ── Retrieval (ai_service._build_system_prompt) ──────────────────────
    HyperParam(
        name="memory_top_k",
        kind="int", low=5, high=15, default=8,
        consumer="ai_service._build_system_prompt",
        notes="Max memories injected via semantic retrieval per AI call.",
    ),
    HyperParam(
        name="memory_cluster_weight",
        kind="float", low=0.10, high=0.50, default=0.30,
        consumer="ai_service._build_system_prompt",
        notes="Weight on P(cluster|query) in retrieval score.",
    ),
    HyperParam(
        name="importance_nudge_divisor",
        kind="float", low=25.0, high=100.0, default=50.0,
        consumer="ai_service._build_system_prompt",
        notes="Divisor turning importance 1-10 into a 0-0.4 score nudge.",
    ),

    # ── Memory decay (henry_stats_engine._compute_memory_decay) ──────────
    HyperParam(
        name="decay_multiplier",
        kind="float", low=0.70, high=0.95, default=0.85,
        consumer="henry_stats_engine._compute_memory_decay",
        notes="Importance multiplier applied per nightly cycle on inactive memories.",
    ),
    HyperParam(
        name="decay_inactivity_days",
        kind="int", low=14, high=60, default=30,
        consumer="henry_stats_engine._compute_memory_decay",
        notes="Days since last_retrieved_at after which decay kicks in.",
    ),
    HyperParam(
        name="prune_age_days",
        kind="int", low=60, high=180, default=90,
        consumer="henry_stats_engine._compute_memory_decay",
        notes="Min age to be considered a pruning candidate.",
    ),

    # ── Confidence calibration (henry_stats_engine) ──────────────────────
    HyperParam(
        name="calibration_window_days",
        kind="int", low=14, high=60, default=30,
        consumer="henry_stats_engine._compute_confidence_calibration",
        notes="Rolling window of resolved actions feeding the calibration curve.",
    ),

    # ── Adaptive Kelly (henry_stats_engine.compute_adaptive_kelly_weekly) ─
    HyperParam(
        name="kelly_base_initial",
        kind="float", low=0.10, high=0.50, default=0.25,
        consumer="position_sizing.compute_size",
        notes="Initial Kelly fraction before adaptation kicks in.",
    ),
    HyperParam(
        name="kelly_error_tighten_threshold",
        kind="float", low=0.20, high=0.40, default=0.30,
        consumer="henry_stats_engine.compute_adaptive_kelly_weekly",
        notes="Rolling error above which f_base tightens.",
    ),
    HyperParam(
        name="kelly_error_widen_threshold",
        kind="float", low=0.08, high=0.20, default=0.15,
        consumer="henry_stats_engine.compute_adaptive_kelly_weekly",
        notes="Rolling error below which f_base widens.",
    ),
    HyperParam(
        name="kelly_base_cap",
        kind="float", low=0.30, high=0.60, default=0.50,
        consumer="henry_stats_engine.compute_adaptive_kelly_weekly",
        notes="Upper bound on adaptive f_base.",
    ),

    # ── Trade warnings (trade_warnings) ──────────────────────────────────
    HyperParam(
        name="concentration_limit_ticker",
        kind="float", low=0.08, high=0.25, default=0.15,
        consumer="trade_warnings.compute_trade_warnings",
        notes="Ticker exposure (fraction of portfolio) above which we warn.",
    ),
    HyperParam(
        name="concentration_limit_sector",
        kind="float", low=0.25, high=0.50, default=0.35,
        consumer="trade_warnings.compute_trade_warnings",
        notes="Sector exposure (fraction of portfolio) above which we warn.",
    ),
]


def defaults() -> dict[str, float]:
    """Convenient {name: default} dict for cold-start config."""
    return {p.name: p.default for p in PARAMS}


def by_name(name: str) -> Optional[HyperParam]:
    for p in PARAMS:
        if p.name == name:
            return p
    return None


def names() -> list[str]:
    return [p.name for p in PARAMS]


def clamp_config(cfg: dict[str, Any]) -> dict[str, float]:
    """Return a fully-validated config dict — out-of-range values clamped,
    missing keys filled from defaults."""
    return {p.name: p.clamp(cfg.get(p.name)) for p in PARAMS}
