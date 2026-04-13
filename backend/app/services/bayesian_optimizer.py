"""
Bayesian Hyperparameter Optimization (Phase 7, System 10)
==========================================================

Slow outer feedback loop. Once per week:
  1. Compute the trailing-30-day adjusted Sharpe under current config
  2. Append (config, objective) to the observation log (HenryStats row
     of stat_type='bayesian_observation')
  3. If we have ≥8 observations, fit a Matérn 5/2 Gaussian process on
     the log and propose the next configuration via Expected Improvement
  4. Write the suggestion to HenryStats(stat_type='bayesian_suggestion')

Manual-approval flow per the brief — never auto-applies. The admin
panel adopts a suggestion via runtime_config.adopt().

Pure numpy. No sklearn/scipy. The GP impl is intentionally minimal —
~80 lines of math — because we have at most ~52 obs/year, so even an
O(n³) Cholesky inverse at every fit is sub-millisecond.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import timedelta
from typing import Optional

import numpy as np

from app.utils.utc import utcnow
from app.services.hyperparameter_space import PARAMS, by_name, defaults

logger = logging.getLogger(__name__)


# ─── Tunables of the optimizer itself (NOT in the search space) ────────────

OBJECTIVE_WINDOW_DAYS = 30
MIN_TRADES_FOR_OBJECTIVE = 10
MIN_OBS_FOR_SUGGESTION = 8
DRAWDOWN_PENALTY_COEF = 0.5
GP_LENGTH_SCALE = 0.30          # in normalized [0,1] coords
GP_SIGNAL_VARIANCE = 1.0
GP_NOISE_VARIANCE = 0.05
EI_CANDIDATES = 1024            # Latin-hypercube search density


# ─── Param normalization ───────────────────────────────────────────────────


def _normalize(cfg: dict[str, float]) -> np.ndarray:
    """Map a config dict → vector in [0,1]^d using each param's range."""
    vec = np.empty(len(PARAMS), dtype=np.float64)
    for i, p in enumerate(PARAMS):
        v = float(cfg.get(p.name, p.default))
        span = max(p.high - p.low, 1e-12)
        vec[i] = (v - p.low) / span
    return np.clip(vec, 0.0, 1.0)


def _denormalize(vec: np.ndarray) -> dict[str, float]:
    """Inverse of _normalize; clips to [low, high] and rounds ints."""
    out: dict[str, float] = {}
    for i, p in enumerate(PARAMS):
        v = float(vec[i]) * (p.high - p.low) + p.low
        v = max(p.low, min(p.high, v))
        if p.kind == "int":
            v = round(v)
        out[p.name] = v
    return out


# ─── Matérn 5/2 kernel + GP posterior ──────────────────────────────────────


def _matern52(X: np.ndarray, Y: np.ndarray, length_scale: float) -> np.ndarray:
    """Pairwise Matérn 5/2 kernel matrix between rows of X and Y."""
    diff = X[:, None, :] - Y[None, :, :]
    dist = np.sqrt(np.sum(diff * diff, axis=2)) / max(length_scale, 1e-9)
    sqrt5_d = math.sqrt(5.0) * dist
    return (1.0 + sqrt5_d + (5.0 / 3.0) * dist * dist) * np.exp(-sqrt5_d)


@dataclass
class GPPosterior:
    """Trained GP — call .predict(X*) for posterior mean and variance."""
    X: np.ndarray
    y: np.ndarray
    L: np.ndarray              # Cholesky of (K + σ²I)
    alpha: np.ndarray          # K⁻¹ y
    length_scale: float
    signal_var: float

    def predict(self, X_star: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        K_star = self.signal_var * _matern52(X_star, self.X, self.length_scale)
        mean = K_star @ self.alpha
        # var(x*) = k(x*, x*) - k_*ᵀ K⁻¹ k_*  via Cholesky
        v = np.linalg.solve(self.L, K_star.T)
        prior_var = self.signal_var * np.ones(X_star.shape[0])
        var = prior_var - np.sum(v * v, axis=0)
        return mean, np.maximum(var, 1e-12)


def _fit_gp(X: np.ndarray, y: np.ndarray) -> GPPosterior:
    n = X.shape[0]
    K = GP_SIGNAL_VARIANCE * _matern52(X, X, GP_LENGTH_SCALE)
    K += GP_NOISE_VARIANCE * np.eye(n)
    L = np.linalg.cholesky(K + 1e-9 * np.eye(n))
    alpha = np.linalg.solve(L.T, np.linalg.solve(L, y))
    return GPPosterior(
        X=X, y=y, L=L, alpha=alpha,
        length_scale=GP_LENGTH_SCALE,
        signal_var=GP_SIGNAL_VARIANCE,
    )


def _expected_improvement(
    mean: np.ndarray, var: np.ndarray, best: float, xi: float = 0.01
) -> np.ndarray:
    """EI under a Gaussian posterior. xi adds mild exploration bias."""
    sigma = np.sqrt(var)
    # Avoid div-by-zero in normal CDF
    z = np.where(sigma > 1e-9, (mean - best - xi) / sigma, 0.0)
    # Φ and φ via erf — no scipy needed
    Phi = 0.5 * (1.0 + np.vectorize(math.erf)(z / math.sqrt(2.0)))
    phi = (1.0 / math.sqrt(2.0 * math.pi)) * np.exp(-0.5 * z * z)
    ei = (mean - best - xi) * Phi + sigma * phi
    return np.where(sigma > 1e-9, ei, 0.0)


def _latin_hypercube(n: int, d: int, rng: np.random.Generator) -> np.ndarray:
    """Latin hypercube sample in [0,1]^d. Uniform in each dim, no clumping."""
    cut = np.linspace(0.0, 1.0, n + 1)
    u = rng.uniform(size=(n, d))
    samples = cut[:n][:, None] + u * (1.0 / n)
    # Permute each column independently to break correlation across dims
    out = np.empty_like(samples)
    for j in range(d):
        out[:, j] = samples[rng.permutation(n), j]
    return out


# ─── Objective function: 30-day adjusted Sharpe ────────────────────────────


async def compute_objective(db) -> Optional[dict]:
    """
    30-day adjusted Sharpe = (mean / std × √252) − 0.5 × max_drawdown_pct

    Daily return aggregated from resolved Trade rows in the window.
    Days with no resolved trade are excluded from the Sharpe calc to
    avoid diluting with zero-return days. Drawdown is computed on the
    cumulative-return curve.

    Returns None when fewer than MIN_TRADES_FOR_OBJECTIVE trades
    resolved — caller should skip the observation entirely.
    """
    from sqlalchemy import select
    from app.models import Trade

    cutoff = utcnow() - timedelta(days=OBJECTIVE_WINDOW_DAYS)
    rows = list(
        (
            await db.execute(
                select(Trade)
                .where(Trade.status == "closed")
                .where(Trade.exit_time >= cutoff)
                .where(Trade.pnl_percent.is_not(None))
                .order_by(Trade.exit_time.asc())
            )
        ).scalars().all()
    )
    if len(rows) < MIN_TRADES_FOR_OBJECTIVE:
        return None

    # Group P&L% by exit date
    by_day: dict[str, list[float]] = {}
    for t in rows:
        d = t.exit_time.date().isoformat() if t.exit_time else None
        if not d:
            continue
        by_day.setdefault(d, []).append(float(t.pnl_percent or 0.0))

    daily_returns = np.array([sum(v) / 100.0 for v in by_day.values()])
    if len(daily_returns) < 2:
        return None

    mean_r = float(np.mean(daily_returns))
    std_r = float(np.std(daily_returns, ddof=1))
    if std_r <= 1e-9:
        sharpe = 0.0
    else:
        sharpe = (mean_r / std_r) * math.sqrt(252.0)

    # Drawdown on the cumulative-return curve
    cum = np.cumsum(daily_returns)
    peak = np.maximum.accumulate(cum)
    drawdown = peak - cum
    max_dd_pct = float(np.max(drawdown))

    adjusted = sharpe - DRAWDOWN_PENALTY_COEF * max_dd_pct

    return {
        "adjusted_sharpe": round(adjusted, 4),
        "raw_sharpe": round(sharpe, 4),
        "mean_daily_return": round(mean_r, 5),
        "std_daily_return": round(std_r, 5),
        "max_drawdown": round(max_dd_pct, 5),
        "trade_count": len(rows),
        "trading_days_with_activity": len(daily_returns),
    }


# ─── Main weekly cycle ──────────────────────────────────────────────────────


async def run_weekly_cycle(db) -> dict:
    """
    Single tick of the optimization loop. Steps:
      1. Compute objective for the trailing 30d window
      2. Snapshot the currently-active runtime config
      3. Append observation row
      4. If ≥ MIN_OBS_FOR_SUGGESTION observations, fit GP + propose
      5. Persist suggestion as a HenryStats(stat_type='bayesian_suggestion')

    Returns a summary dict suitable for both logging and the admin
    status endpoint.
    """
    from sqlalchemy import select
    from app.models import HenryStats
    from app.services.runtime_config import all_current

    objective = await compute_objective(db)
    current_cfg = await all_current()

    summary: dict = {
        "ts": utcnow().isoformat() + "Z",
        "current_config": current_cfg,
        "objective": objective,
    }

    if objective is None:
        summary["decision"] = "skipped"
        summary["reason"] = (
            f"Fewer than {MIN_TRADES_FOR_OBJECTIVE} trades resolved in the "
            f"last {OBJECTIVE_WINDOW_DAYS} days — objective undefined."
        )
        # Still write a heartbeat observation so the user can confirm the
        # job is running, but with null objective.
        await _persist_observation(db, current_cfg, None, summary["reason"])
        return summary

    # 3) Persist this observation
    await _persist_observation(db, current_cfg, objective, None)

    # 4) Pull the full observation log for the GP fit
    obs_rows = list(
        (
            await db.execute(
                select(HenryStats)
                .where(HenryStats.stat_type == "bayesian_observation")
                .where(HenryStats.data.is_not(None))
                .order_by(HenryStats.computed_at.asc())
            )
        ).scalars().all()
    )
    valid = [
        r for r in obs_rows
        if r.data and r.data.get("objective") and r.data["objective"].get("adjusted_sharpe") is not None
    ]
    summary["observation_count"] = len(valid)

    if len(valid) < MIN_OBS_FOR_SUGGESTION:
        summary["decision"] = "exploring"
        summary["reason"] = (
            f"{len(valid)}/{MIN_OBS_FOR_SUGGESTION} observations — still in "
            f"pure-exploration phase, no suggestion generated."
        )
        return summary

    # 5) Fit GP and pick next via EI
    X = np.vstack([_normalize(r.data["params"]) for r in valid])
    y = np.array([r.data["objective"]["adjusted_sharpe"] for r in valid], dtype=np.float64)

    try:
        gp = _fit_gp(X, y)
    except np.linalg.LinAlgError as e:
        # Degenerate kernel matrix — push more noise and retry once.
        logger.warning(f"GP fit failed (linalg): {e}; retrying with extra jitter")
        K = GP_SIGNAL_VARIANCE * _matern52(X, X, GP_LENGTH_SCALE)
        K += (GP_NOISE_VARIANCE + 0.5) * np.eye(X.shape[0])
        L = np.linalg.cholesky(K + 1e-6 * np.eye(X.shape[0]))
        alpha = np.linalg.solve(L.T, np.linalg.solve(L, y))
        gp = GPPosterior(
            X=X, y=y, L=L, alpha=alpha,
            length_scale=GP_LENGTH_SCALE, signal_var=GP_SIGNAL_VARIANCE,
        )

    rng = np.random.default_rng(42)
    candidates = _latin_hypercube(EI_CANDIDATES, X.shape[1], rng)
    # Mix in the current config + a tight Gaussian neighborhood so EI
    # can suggest small refinements as readily as bold jumps.
    cur_vec = _normalize(current_cfg)
    near_current = np.clip(
        cur_vec + rng.normal(0.0, 0.05, size=(64, X.shape[1])),
        0.0, 1.0,
    )
    candidates = np.vstack([candidates, cur_vec[None, :], near_current])

    mean, var = gp.predict(candidates)
    best_obs = float(np.max(y))
    ei = _expected_improvement(mean, var, best_obs)

    best_idx = int(np.argmax(ei))
    suggested_cfg = _denormalize(candidates[best_idx])
    suggested_ei = float(ei[best_idx])
    suggested_pred_mean = float(mean[best_idx])
    suggested_pred_std = float(np.sqrt(var[best_idx]))

    suggestion_payload = {
        "ts": utcnow().isoformat() + "Z",
        "params": suggested_cfg,
        "ei": round(suggested_ei, 5),
        "predicted_mean": round(suggested_pred_mean, 4),
        "predicted_std": round(suggested_pred_std, 4),
        "current_best_objective": round(best_obs, 4),
        "n_observations": len(valid),
        "current_config": current_cfg,
        "diff_vs_current": _config_diff(current_cfg, suggested_cfg),
        "adopted": False,
        "rejected": False,
    }
    await _persist_suggestion(db, suggestion_payload)

    summary["decision"] = "suggested"
    summary["suggestion"] = suggestion_payload
    return summary


def _config_diff(a: dict[str, float], b: dict[str, float]) -> dict[str, dict]:
    """Return only the params that meaningfully differ. ≥5% relative
    change OR an integer-bucket flip."""
    out: dict[str, dict] = {}
    for p in PARAMS:
        av, bv = float(a.get(p.name, p.default)), float(b.get(p.name, p.default))
        if p.kind == "int":
            if round(av) != round(bv):
                out[p.name] = {"from": round(av), "to": round(bv)}
        else:
            denom = max(abs(av), 1e-9)
            if abs(av - bv) / denom >= 0.05:
                out[p.name] = {
                    "from": round(av, 4),
                    "to": round(bv, 4),
                    "delta_pct": round((bv - av) / denom * 100, 1),
                }
    return out


async def _persist_observation(
    db,
    cfg: dict[str, float],
    objective: Optional[dict],
    skip_reason: Optional[str],
) -> None:
    """Append a single bayesian_observation row. Distinct from suggestion
    rows so the optimizer can fit on observations only."""
    from app.models import HenryStats
    db.add(HenryStats(
        stat_type="bayesian_observation",
        strategy=None, ticker=None, portfolio_id=None,
        data={
            "params": cfg,
            "objective": objective,
            "skip_reason": skip_reason,
            "ts": utcnow().isoformat() + "Z",
        },
        period_days=OBJECTIVE_WINDOW_DAYS,
        computed_at=utcnow(),
    ))
    await db.commit()


async def _persist_suggestion(db, payload: dict) -> None:
    """Replace the latest bayesian_suggestion row. Only one is 'live'
    at a time — adoption marks it as decided, then the next cycle
    overwrites it."""
    from sqlalchemy import select, delete
    from app.models import HenryStats

    await db.execute(
        delete(HenryStats).where(
            (HenryStats.stat_type == "bayesian_suggestion")
            & (HenryStats.strategy.is_(None))
            & (HenryStats.ticker.is_(None))
        )
    )
    db.add(HenryStats(
        stat_type="bayesian_suggestion",
        strategy=None, ticker=None, portfolio_id=None,
        data=payload,
        period_days=OBJECTIVE_WINDOW_DAYS,
        computed_at=utcnow(),
    ))
    await db.commit()
