"""
Decision Signal Weights
=======================
Defines the 8 signal dimensions that decompose Henry's buy/skip decisions.
Provides validation and defaults for the signal_weights JSON column on
PortfolioAction.

Used by:
  - ai_portfolio.py (signal eval prompt → capture)
  - autonomous_trading.py (pattern eval → capture)
  - henry_stats_engine.py (posterior computation)
  - position_sizing.py (signal quality multiplier)
"""
from __future__ import annotations

SIGNAL_KEYS: list[str] = [
    "technical_strength",
    "fundamental_value",
    "thesis_quality",
    "catalyst_proximity",
    "risk_reward_ratio",
    "memory_alignment",
    "regime_fit",
    "entry_timing",
]

_SIGNAL_SET = frozenset(SIGNAL_KEYS)

SIGNAL_ACTIVE_THRESHOLD = 0.5

SIGNAL_WEIGHTS_PROMPT_FRAGMENT = (
    'Also return "signal_weights" scoring each dimension 0.0-1.0: '
    + ", ".join(SIGNAL_KEYS)
    + "."
)


def validate_signal_weights(raw: dict | None) -> dict | None:
    """Clamp values to [0, 1], drop unknown keys. Returns None if empty."""
    if not raw or not isinstance(raw, dict):
        return None
    out: dict[str, float] = {}
    for k in SIGNAL_KEYS:
        v = raw.get(k)
        if v is None:
            continue
        try:
            out[k] = max(0.0, min(1.0, float(v)))
        except (TypeError, ValueError):
            continue
    return out if out else None


def default_signal_weights() -> dict[str, float]:
    """Uninformative prior — all 0.5."""
    return {k: 0.5 for k in SIGNAL_KEYS}
