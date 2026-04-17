"use client";

/**
 * Decision Signal Posteriors Panel (Bayesian Decision Learning)
 * =============================================================
 *
 * Visualizes per-signal Beta posteriors so the user can see which of
 * Henry's signal dimensions historically predict good outcomes.
 * Horizontal bar chart with CI error bars, archetype tabs.
 */

import { useEffect, useState, useCallback } from "react";
import { api } from "@/lib/api";
import type { DecisionSignalStatus, SignalPosterior } from "@/lib/types";

const SIGNAL_LABELS: Record<string, string> = {
  technical_strength: "Technical Strength",
  fundamental_value: "Fundamental Value",
  thesis_quality: "Thesis Quality",
  catalyst_proximity: "Catalyst Proximity",
  risk_reward_ratio: "Risk/Reward Ratio",
  memory_alignment: "Memory Alignment",
  regime_fit: "Regime Fit",
  entry_timing: "Entry Timing",
};

const ARCHETYPES = ["global", "momentum", "accumulation", "catalyst", "conviction"];

function SignalBar({ label, info }: { label: string; info: SignalPosterior }) {
  const pct = Math.round(info.mean * 100);
  const ciLow = Math.round(info.ci[0] * 100);
  const ciHigh = Math.round(info.ci[1] * 100);

  let color = "bg-gray-500";
  if (info.n >= 5) {
    if (info.mean >= 0.65) color = "bg-emerald-500";
    else if (info.mean >= 0.5) color = "bg-amber-500";
    else color = "bg-red-500";
  }

  return (
    <div className="flex items-center gap-3 py-1.5">
      <div className="w-36 text-[11px] text-gray-400 truncate">{label}</div>
      <div className="flex-1 relative h-5 bg-gray-800 rounded overflow-hidden">
        {/* CI range */}
        {info.n >= 5 && (
          <div
            className="absolute top-0 h-full bg-white/5 rounded"
            style={{ left: `${ciLow}%`, width: `${ciHigh - ciLow}%` }}
          />
        )}
        {/* Mean bar */}
        <div
          className={`absolute top-0 h-full ${color} rounded transition-all duration-500`}
          style={{ width: `${pct}%`, opacity: info.n >= 5 ? 0.8 : 0.3 }}
        />
        {/* 50% marker */}
        <div className="absolute top-0 left-1/2 h-full w-px bg-gray-600" />
      </div>
      <div className="w-20 text-right text-[11px] font-mono">
        <span className={info.n >= 5 ? "text-white" : "text-gray-600"}>
          {pct}%
        </span>
        <span className="text-gray-600 ml-1">({info.n})</span>
      </div>
    </div>
  );
}

export default function DecisionSignalPanel() {
  const [data, setData] = useState<DecisionSignalStatus | null>(null);
  const [tab, setTab] = useState("global");
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    try {
      const d = await api.getDecisionSignalPosteriors();
      setData(d);
    } catch {}
    setLoading(false);
  }, []);

  useEffect(() => { refresh(); }, [refresh]);

  if (loading) {
    return (
      <div className="p-4 text-center text-gray-500 text-sm">
        Loading signal posteriors...
      </div>
    );
  }

  if (!data || !data.global || Object.keys(data.global).length === 0) {
    return (
      <div className="p-4">
        <h3 className="text-sm font-semibold text-white mb-2">Decision Signal Quality</h3>
        <p className="text-xs text-gray-500">
          No signal posteriors yet. Posteriors will appear after Henry makes 5+
          decisions with signal weights and their outcomes resolve.
        </p>
      </div>
    );
  }

  const sigs = tab === "global"
    ? data.global
    : (data.by_archetype?.[tab] || {});

  const sorted = Object.entries(sigs).sort(
    (a, b) => (b[1]?.mean ?? 0) - (a[1]?.mean ?? 0)
  );

  return (
    <div className="p-4">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-sm font-semibold text-white">Decision Signal Quality</h3>
        <button
          onClick={refresh}
          className="text-[10px] text-indigo-400 hover:text-indigo-300"
        >
          Refresh
        </button>
      </div>

      {/* Tags for top/weak */}
      {(data.top_signals.length > 0 || data.weak_signals.length > 0) && (
        <div className="flex flex-wrap gap-1.5 mb-3">
          {data.top_signals.map((s) => (
            <span key={s} className="text-[9px] px-1.5 py-0.5 rounded bg-emerald-500/15 text-emerald-400">
              {SIGNAL_LABELS[s] || s}
            </span>
          ))}
          {data.weak_signals.map((s) => (
            <span key={s} className="text-[9px] px-1.5 py-0.5 rounded bg-red-500/15 text-red-400">
              {SIGNAL_LABELS[s] || s}
            </span>
          ))}
        </div>
      )}

      {/* Archetype tabs */}
      <div className="flex gap-1 mb-3 flex-wrap">
        {ARCHETYPES.map((a) => {
          const hasData = a === "global"
            ? Object.keys(data.global).length > 0
            : Object.keys(data.by_archetype?.[a] || {}).length > 0;
          if (!hasData && a !== "global") return null;
          return (
            <button
              key={a}
              onClick={() => setTab(a)}
              className={`text-[10px] px-2 py-0.5 rounded transition ${
                tab === a
                  ? "bg-indigo-500/20 text-indigo-400"
                  : "text-gray-500 hover:text-gray-300"
              }`}
            >
              {a.charAt(0).toUpperCase() + a.slice(1)}
            </button>
          );
        })}
      </div>

      {/* Signal bars */}
      <div>
        {sorted.map(([key, info]) => (
          <SignalBar
            key={key}
            label={SIGNAL_LABELS[key] || key}
            info={info}
          />
        ))}
      </div>

      {data.computed_at && (
        <div className="mt-2 text-[9px] text-gray-600 text-right">
          {data.total_actions_with_weights} decisions tracked
          {" · "}
          Updated {new Date(data.computed_at).toLocaleString()}
        </div>
      )}
    </div>
  );
}
