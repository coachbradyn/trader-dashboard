"use client";

/**
 * Bayesian Hyperparameter Optimization Panel (Phase 7.5)
 * =======================================================
 *
 * Shows the current state of the System 10 weekly optimizer:
 *   - Current active config (which params are running, source: defaults
 *     vs adopted suggestion)
 *   - Observation count + best objective so far + latest objective
 *   - Latest suggestion if one exists, with diff vs current and EI
 *   - Buttons: Run cycle now, Adopt suggestion, Reject suggestion
 *   - Search space table with per-param range / default / current value
 *
 * All admin actions gated by ADMIN_SECRET (cached per sessionStorage tab,
 * same flow as MemoryCurationPanel + MemoryMap3D).
 */

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { OptimizationStatus, HyperParamSpec } from "@/lib/types";

function promptSecret(): string | null {
  const cached = sessionStorage.getItem("memory_admin_secret");
  if (cached) return cached;
  const entered = window.prompt(
    "Enter ADMIN_SECRET (stored only for this browser tab):"
  );
  if (entered) sessionStorage.setItem("memory_admin_secret", entered);
  return entered;
}

interface Props {
  // Re-load callback so the parent (3D Map) can pick up newly-adopted
  // settings on its next projection refresh.
  onChanged?: () => void;
}

export function BayesianOptimizationPanel({ onChanged }: Props) {
  const [status, setStatus] = useState<OptimizationStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);

  const load = async () => {
    setLoading(true);
    setError(null);
    try {
      const s = await api.getOptimizationStatus();
      setStatus(s);
    } catch (e) {
      setError((e as Error).message || "Failed to load");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
  }, []);

  const runNow = async () => {
    const secret = promptSecret();
    if (!secret) return;
    setBusy("run");
    setMsg("Running cycle…");
    try {
      const res = await api.adminOptimizationRunNow(secret);
      if (!res.ok) {
        setMsg(`Run failed: ${res.reason || "unknown"}`);
      } else {
        const sum = res.summary as { decision?: string; reason?: string } | undefined;
        setMsg(`Decision: ${sum?.decision || "?"}${sum?.reason ? " — " + sum.reason : ""}`);
        await load();
        onChanged?.();
      }
    } catch (e) {
      setMsg(`Run request failed: ${(e as Error).message}`);
    } finally {
      setBusy(null);
    }
  };

  const adopt = async () => {
    if (!status?.latest_suggestion) return;
    const secret = promptSecret();
    if (!secret) return;
    if (
      !confirm(
        "Adopt the latest suggestion? This makes those parameter values active immediately for all consumers (memory retrieval, decay, kelly, warnings)."
      )
    )
      return;
    setBusy("adopt");
    setMsg("Adopting…");
    try {
      const res = await api.adminOptimizationAdopt(secret, {
        adopt_latest_suggestion: true,
      });
      if (!res.ok) {
        setMsg(`Adopt failed: ${res.reason || "unknown"}`);
      } else {
        setMsg("Suggestion adopted. New config active immediately.");
        await load();
        onChanged?.();
      }
    } catch (e) {
      setMsg(`Adopt request failed: ${(e as Error).message}`);
    } finally {
      setBusy(null);
    }
  };

  const reject = async () => {
    if (!status?.latest_suggestion) return;
    const secret = promptSecret();
    if (!secret) return;
    setBusy("reject");
    setMsg("Rejecting…");
    try {
      const res = await api.adminOptimizationReject(secret);
      if (!res.ok) {
        setMsg(`Reject failed: ${res.reason || "unknown"}`);
      } else {
        setMsg("Suggestion rejected.");
        await load();
      }
    } catch (e) {
      setMsg(`Reject request failed: ${(e as Error).message}`);
    } finally {
      setBusy(null);
    }
  };

  if (loading) {
    return <div className="text-xs text-gray-500 p-3">Loading…</div>;
  }
  if (error) {
    return (
      <div className="p-3 rounded border border-red-500/20 bg-red-500/5 text-xs">
        <p className="text-red-400 mb-2">Failed to load optimization status</p>
        <p className="text-red-300/80 font-mono break-words">{error}</p>
        <button
          onClick={load}
          className="mt-2 text-xs px-2 py-1 rounded bg-[#6366f1]/15 text-[#6366f1] hover:bg-[#6366f1]/25"
        >
          Retry
        </button>
      </div>
    );
  }
  if (!status) return null;

  const sug = status.latest_suggestion;
  const sugDiff = sug?.diff_vs_current ?? {};
  const sugDiffKeys = Object.keys(sugDiff);
  const objective = status.latest_observation?.objective;
  const best = status.best_observation;

  return (
    <div className="rounded-lg border border-border bg-[#1f2937]/30 p-4 space-y-3 text-xs">
      <div className="flex items-center justify-between gap-2 flex-wrap">
        <div>
          <p className="text-[10px] uppercase tracking-wide text-gray-500">
            Bayesian Optimization (System 10)
          </p>
          <p className="text-gray-400">
            {status.n_observations_with_objective}/{status.n_observations}{" "}
            observations with objective · weekly cycle Sun 22:00 ET
          </p>
        </div>
        <div className="flex gap-1.5">
          <button
            onClick={load}
            className="text-[11px] px-2 py-1 rounded bg-[#1f2937]/50 text-gray-300 hover:bg-[#1f2937] border border-border"
          >
            Refresh
          </button>
          <button
            onClick={runNow}
            disabled={busy !== null}
            className="text-[11px] px-2 py-1 rounded bg-[#6366f1]/15 text-[#6366f1] hover:bg-[#6366f1]/25 disabled:opacity-40"
            title="Trigger a single cycle synchronously instead of waiting for Sunday."
          >
            {busy === "run" ? "Running…" : "Run cycle now"}
          </button>
        </div>
      </div>

      {msg && (
        <p className="text-[11px] text-gray-300 font-mono break-words">{msg}</p>
      )}

      {/* Objectives */}
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
        <ObjectiveCard label="Latest 30-day objective" objective={objective ?? null} />
        <ObjectiveCard
          label="Best objective seen"
          objective={best?.objective ?? null}
          subtitle={best?.ts ? `at ${best.ts.slice(0, 10)}` : undefined}
        />
      </div>

      {/* Suggestion */}
      {sug && !sug.adopted && !sug.rejected && (
        <div className="rounded border border-amber-500/30 bg-amber-500/5 p-2.5 space-y-2">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-[10px] uppercase tracking-wide text-amber-400">
              Pending suggestion
            </span>
            <span className="text-[10px] text-gray-400 font-mono">
              EI {sug.ei.toFixed(4)} · predicted {sug.predicted_mean.toFixed(3)} ±{" "}
              {sug.predicted_std.toFixed(3)} (current best{" "}
              {sug.current_best_objective.toFixed(3)})
            </span>
          </div>
          {sugDiffKeys.length === 0 ? (
            <p className="text-[11px] text-gray-400 italic">
              No meaningful changes vs current config (within rounding).
            </p>
          ) : (
            <div className="space-y-1">
              <p className="text-[10px] uppercase tracking-wide text-gray-500">
                Changes ({sugDiffKeys.length})
              </p>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-1">
                {sugDiffKeys.map((k) => {
                  const d = sugDiff[k];
                  return (
                    <div
                      key={k}
                      className="rounded p-1.5 bg-[#0b0f19] border border-border"
                    >
                      <div className="flex items-center gap-1 text-[11px]">
                        <span className="font-mono text-gray-300">{k}</span>
                        <span className="text-gray-500">:</span>
                        <span className="font-mono text-gray-400">{d.from}</span>
                        <span className="text-gray-500">→</span>
                        <span className="font-mono text-emerald-400">
                          {d.to}
                        </span>
                        {d.delta_pct !== undefined && (
                          <span className="text-[10px] text-gray-500 ml-auto">
                            {d.delta_pct > 0 ? "+" : ""}
                            {d.delta_pct.toFixed(1)}%
                          </span>
                        )}
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          )}
          <div className="flex gap-1.5 justify-end pt-1">
            <button
              onClick={reject}
              disabled={busy !== null}
              className="text-[11px] px-3 py-1 rounded bg-[#1f2937]/60 text-gray-300 hover:bg-[#1f2937] border border-border disabled:opacity-40"
            >
              {busy === "reject" ? "…" : "Reject"}
            </button>
            <button
              onClick={adopt}
              disabled={busy !== null}
              className="text-[11px] px-3 py-1 rounded bg-[#10b981]/20 text-[#10b981] hover:bg-[#10b981]/30 disabled:opacity-40"
            >
              {busy === "adopt" ? "…" : "Adopt suggestion"}
            </button>
          </div>
        </div>
      )}

      {sug && sug.adopted && (
        <div className="rounded border border-emerald-500/20 bg-emerald-500/5 p-2 text-[11px] text-emerald-400">
          ✓ Latest suggestion adopted{" "}
          {sug.adopted_at && `at ${sug.adopted_at.slice(0, 16)}`}
        </div>
      )}
      {sug && sug.rejected && (
        <div className="rounded border border-gray-500/20 bg-gray-500/5 p-2 text-[11px] text-gray-400">
          Latest suggestion rejected
          {sug.rejected_at && ` at ${sug.rejected_at.slice(0, 16)}`}
        </div>
      )}
      {!sug && (
        <div className="rounded border border-border bg-[#0b0f19] p-2 text-[11px] text-gray-500 italic">
          No suggestion yet. The optimizer needs at least 8 observations
          (~8 weeks) to start proposing — we&apos;re in pure-exploration phase.
        </div>
      )}

      {/* Search space + current values */}
      <details>
        <summary className="text-[11px] text-gray-500 cursor-pointer hover:text-gray-400">
          Search space ({status.search_space.length} parameters · current
          source: {status.current_config_source})
        </summary>
        <div className="mt-2 space-y-1 max-h-[40vh] overflow-y-auto pr-1">
          {status.search_space.map((p) => {
            const cur = status.current_config[p.name];
            const isDefault = cur === p.default;
            return <ParamRow key={p.name} spec={p} current={cur} isDefault={isDefault} />;
          })}
        </div>
      </details>
    </div>
  );
}

function ObjectiveCard({
  label,
  objective,
  subtitle,
}: {
  label: string;
  objective: import("@/lib/types").BayesianObjective | null;
  subtitle?: string;
}) {
  return (
    <div className="rounded border border-border bg-[#0b0f19] p-2.5">
      <p className="text-[10px] uppercase tracking-wide text-gray-500">{label}</p>
      {!objective ? (
        <p className="text-[11px] text-gray-500 italic mt-1">
          Insufficient data (&lt;10 trades resolved)
        </p>
      ) : (
        <>
          <p className="text-base font-mono text-gray-200 mt-0.5">
            {objective.adjusted_sharpe.toFixed(3)}
          </p>
          <p className="text-[10px] text-gray-500 font-mono">
            raw {objective.raw_sharpe.toFixed(2)} · dd{" "}
            {(objective.max_drawdown * 100).toFixed(1)}% · {objective.trade_count}{" "}
            trades
            {subtitle && ` · ${subtitle}`}
          </p>
        </>
      )}
    </div>
  );
}

function ParamRow({
  spec,
  current,
  isDefault,
}: {
  spec: HyperParamSpec;
  current: number | undefined;
  isDefault: boolean;
}) {
  return (
    <div className="rounded p-1.5 bg-[#0b0f19] border border-border text-[11px]">
      <div className="flex items-center gap-2 flex-wrap">
        <span className="font-mono text-gray-300">{spec.name}</span>
        <span className="text-[10px] text-gray-500">[{spec.kind}]</span>
        <span className="text-[10px] text-gray-500">
          {spec.low} – {spec.high}
        </span>
        <span className="ml-auto text-[10px]">
          default{" "}
          <span className="font-mono text-gray-500">{spec.default}</span> · current{" "}
          <span
            className={
              "font-mono " + (isDefault ? "text-gray-500" : "text-emerald-400")
            }
          >
            {current ?? "—"}
          </span>
        </span>
      </div>
      {spec.notes && (
        <p className="text-[10px] text-gray-500 mt-0.5">{spec.notes}</p>
      )}
    </div>
  );
}

export default BayesianOptimizationPanel;
