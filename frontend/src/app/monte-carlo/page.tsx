"use client";

import { useState, useEffect, useCallback } from "react";
import { api } from "@/lib/api";
import { formatCurrency, formatPercent, pnlColor } from "@/lib/formatters";
import type { MonteCarloResponse, MonteCarloRequest } from "@/lib/types";
import {
  ResponsiveContainer,
  AreaChart,
  Area,
  Line,
  BarChart,
  Bar,
  Cell,
  XAxis,
  YAxis,
  Tooltip,
  CartesianGrid,
  ReferenceLine,
} from "recharts";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";

// ── Fonts ────────────────────────────────────────────────────────────────
const FONT_OUTFIT = { fontFamily: "'Outfit', sans-serif" } as const;
const FONT_MONO = { fontFamily: "'JetBrains Mono', monospace" } as const;

function useFonts() {
  useEffect(() => {
    if (document.getElementById("__mc-fonts")) return;
    const link = document.createElement("link");
    link.id = "__mc-fonts";
    link.rel = "stylesheet";
    link.href =
      "https://fonts.googleapis.com/css2?family=Outfit:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500;600;700&display=swap";
    document.head.appendChild(link);
  }, []);
}

// ── Tooltip Style ────────────────────────────────────────────────────────
const CHART_TOOLTIP_STYLE = {
  background: "#1f2937",
  border: "1px solid #374151",
  borderRadius: 8,
};

// ── StatCard ─────────────────────────────────────────────────────────────
function StatCard({
  label,
  value,
  color = "text-white",
  sub,
}: {
  label: string;
  value: string;
  color?: string;
  sub?: string;
}) {
  return (
    <div className="bg-surface-light/30 rounded-xl p-4 border border-border">
      <div
        className="text-[10px] text-gray-500 uppercase tracking-wider mb-1"
        style={FONT_OUTFIT}
      >
        {label}
      </div>
      <div
        className={`text-lg font-mono font-semibold ${color}`}
        style={FONT_MONO}
      >
        {value}
      </div>
      {sub && (
        <div className="text-[10px] text-gray-600 font-mono mt-0.5">{sub}</div>
      )}
    </div>
  );
}

// ── Custom Tooltip Formatters ────────────────────────────────────────────
function ConeTooltip({ active, payload, label }: { active?: boolean; payload?: Array<{ dataKey: string; value: number }>; label?: number }) {
  if (!active || !payload?.length) return null;
  const find = (key: string) => payload.find((p) => p.dataKey === key)?.value;
  return (
    <div style={CHART_TOOLTIP_STYLE} className="px-3 py-2 text-xs">
      <div className="text-gray-400 mb-1" style={FONT_OUTFIT}>Trade #{label}</div>
      <div className="space-y-0.5" style={FONT_MONO}>
        {find("p95") !== undefined && <div className="text-gray-500">P95: {formatCurrency(find("p95")!)}</div>}
        {find("p75") !== undefined && <div className="text-indigo-300">P75: {formatCurrency(find("p75")!)}</div>}
        {find("p50") !== undefined && <div className="text-amber-400 font-semibold">Median: {formatCurrency(find("p50")!)}</div>}
        {find("p25") !== undefined && <div className="text-indigo-300">P25: {formatCurrency(find("p25")!)}</div>}
        {find("p5") !== undefined && <div className="text-gray-500">P5: {formatCurrency(find("p5")!)}</div>}
      </div>
    </div>
  );
}

// ── Main Page ────────────────────────────────────────────────────────────
export default function MonteCarloPage() {
  useFonts();

  // Controls state
  const [source, setSource] = useState<"live" | "backtest" | "combined">("combined");
  const [strategy, setStrategy] = useState("");
  const [ticker, setTicker] = useState("");
  const [numSimulations, setNumSimulations] = useState(1000);
  const [forwardTrades, setForwardTrades] = useState(100);
  const [initialCapital, setInitialCapital] = useState(10000);
  const [positionSizePct, setPositionSizePct] = useState(100);

  // Data state
  const [results, setResults] = useState<MonteCarloResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Options for dropdowns
  const [strategies, setStrategies] = useState<string[]>([]);

  // Henry AI
  const [henryLoading, setHenryLoading] = useState(false);
  const [henryResponse, setHenryResponse] = useState<string | null>(null);

  // Load strategies on mount
  useEffect(() => {
    async function loadStrategies() {
      try {
        const [traders, imports] = await Promise.all([
          api.getTraders(),
          api.getBacktestImports(),
        ]);
        const traderIds = traders.map((t) => t.trader_id);
        const importNames = imports.map((i) => i.strategy_name);
        const unique = Array.from(new Set([...traderIds, ...importNames]));
        setStrategies(unique);
      } catch {
        // silently fail — dropdowns just won't be populated
      }
    }
    loadStrategies();
  }, []);

  // Run simulation
  const runSimulation = useCallback(async () => {
    setLoading(true);
    setError(null);
    setResults(null);
    setHenryResponse(null);
    try {
      const params: MonteCarloRequest = {
        source,
        num_simulations: numSimulations,
        forward_trades: forwardTrades,
        initial_capital: initialCapital,
        position_size_pct: positionSizePct,
      };
      if (strategy) params.strategy = strategy;
      if (ticker) params.ticker = ticker.toUpperCase();
      const data = await api.runMonteCarlo(params);
      setResults(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Simulation failed");
    } finally {
      setLoading(false);
    }
  }, [source, strategy, ticker, numSimulations, forwardTrades, initialCapital, positionSizePct]);

  // Ask Henry
  const askHenry = useCallback(async () => {
    if (!results) return;
    setHenryLoading(true);
    setHenryResponse(null);
    const s = results.summary;
    const inp = results.input_stats;
    const prompt = `Interpret these Monte Carlo simulation results for my trading system:

- Simulations: ${numSimulations} paths, ${forwardTrades} forward trades, $${initialCapital} initial capital
- Input: ${inp.total_trades_pooled} historical trades (${inp.live_trade_count} live, ${inp.backtest_trade_count} backtest), win rate ${(inp.win_rate * 100).toFixed(1)}%, profit factor ${inp.profit_factor.toFixed(2)}
- Strategies: ${inp.strategies_included.join(", ")}
- Probability of profit: ${s.probability_of_profit.toFixed(1)}%
- Probability of ruin: ${s.probability_of_ruin.toFixed(1)}%
- Median return: ${s.median_return_pct.toFixed(1)}%, Mean return: ${s.mean_return_pct.toFixed(1)}%
- Best case (95th): ${formatCurrency(s.best_case_p95)}, Worst case (5th): ${formatCurrency(s.worst_case_p5)}
- Median max drawdown: ${s.median_max_drawdown_pct.toFixed(1)}%, 95th DD: ${s.worst_drawdown_p95.toFixed(1)}%
- Sharpe estimate: ${s.sharpe_estimate.toFixed(2)}

Give a concise interpretation: is this system worth trading? What are the key risks? Any position sizing or diversification suggestions?`;

    try {
      const res = await api.postQuery(prompt);
      setHenryResponse(res.answer);
    } catch {
      setHenryResponse("Failed to get analysis from Henry.");
    } finally {
      setHenryLoading(false);
    }
  }, [results, numSimulations, forwardTrades, initialCapital]);

  // Build cone chart data
  const coneData = results
    ? results.trade_indices.map((t, i) => ({
        trade: t,
        p5: results.percentile_bands.p5[i],
        p10: results.percentile_bands.p10[i],
        p25: results.percentile_bands.p25[i],
        p50: results.percentile_bands.p50[i],
        p75: results.percentile_bands.p75[i],
        p90: results.percentile_bands.p90[i],
        p95: results.percentile_bands.p95[i],
        ...Object.fromEntries(
          results.sample_paths.map((path, j) => [`sample${j}`, path[i]])
        ),
      }))
    : [];

  // Histogram bar color helper
  const equityBarColor = (binStart: number, binEnd: number) => {
    if (binStart >= initialCapital) return "#22c55e";
    if (binEnd <= initialCapital) return "#ef4444";
    return "#fbbf24";
  };

  return (
    <div className="min-h-screen bg-surface p-4 md:p-6 space-y-6">
      {/* ── Header ────────────────────────────────────────────── */}
      <div>
        <h1
          className="text-2xl md:text-3xl font-bold text-white"
          style={FONT_OUTFIT}
        >
          Monte Carlo Simulation
        </h1>
        <p className="text-sm text-gray-500 mt-1" style={FONT_OUTFIT}>
          Probability analysis based on historical trade data
        </p>
      </div>

      {/* ── Controls Panel ────────────────────────────────────── */}
      <Card>
        <CardContent className="pt-5">
          <div className="flex flex-wrap gap-4 items-end">
            {/* Source Toggle */}
            <div className="space-y-1.5">
              <label
                className="text-[10px] text-gray-500 uppercase tracking-wider"
                style={FONT_OUTFIT}
              >
                Source
              </label>
              <div className="flex rounded-lg overflow-hidden border border-border">
                {(["live", "backtest", "combined"] as const).map((s) => (
                  <button
                    key={s}
                    onClick={() => setSource(s)}
                    className={`px-3 py-1.5 text-xs font-medium capitalize transition-colors ${
                      source === s
                        ? "bg-indigo-500 text-white"
                        : "bg-surface-light/30 text-gray-400 hover:text-white"
                    }`}
                    style={FONT_OUTFIT}
                  >
                    {s}
                  </button>
                ))}
              </div>
            </div>

            {/* Strategy Dropdown */}
            <div className="space-y-1.5">
              <label
                className="text-[10px] text-gray-500 uppercase tracking-wider"
                style={FONT_OUTFIT}
              >
                Strategy
              </label>
              <select
                value={strategy}
                onChange={(e) => setStrategy(e.target.value)}
                className="bg-surface-light/30 border border-border rounded-lg px-3 py-1.5 text-xs text-white focus:outline-none focus:ring-1 focus:ring-indigo-500"
                style={FONT_MONO}
              >
                <option value="">All Strategies</option>
                {strategies.map((s) => (
                  <option key={s} value={s}>
                    {s}
                  </option>
                ))}
              </select>
            </div>

            {/* Ticker Input */}
            <div className="space-y-1.5">
              <label
                className="text-[10px] text-gray-500 uppercase tracking-wider"
                style={FONT_OUTFIT}
              >
                Ticker
              </label>
              <input
                type="text"
                value={ticker}
                onChange={(e) => setTicker(e.target.value.toUpperCase())}
                placeholder="e.g. AAPL"
                className="bg-surface-light/30 border border-border rounded-lg px-3 py-1.5 text-xs text-white placeholder-gray-600 w-24 focus:outline-none focus:ring-1 focus:ring-indigo-500"
                style={FONT_MONO}
              />
            </div>

            {/* Simulations */}
            <div className="space-y-1.5">
              <label
                className="text-[10px] text-gray-500 uppercase tracking-wider"
                style={FONT_OUTFIT}
              >
                Simulations
              </label>
              <select
                value={numSimulations}
                onChange={(e) => setNumSimulations(Number(e.target.value))}
                className="bg-surface-light/30 border border-border rounded-lg px-3 py-1.5 text-xs text-white focus:outline-none focus:ring-1 focus:ring-indigo-500"
                style={FONT_MONO}
              >
                {[500, 1000, 2500, 5000, 10000].map((n) => (
                  <option key={n} value={n}>
                    {n.toLocaleString()}
                  </option>
                ))}
              </select>
            </div>

            {/* Forward Trades */}
            <div className="space-y-1.5">
              <label
                className="text-[10px] text-gray-500 uppercase tracking-wider"
                style={FONT_OUTFIT}
              >
                Forward Trades
              </label>
              <select
                value={forwardTrades}
                onChange={(e) => setForwardTrades(Number(e.target.value))}
                className="bg-surface-light/30 border border-border rounded-lg px-3 py-1.5 text-xs text-white focus:outline-none focus:ring-1 focus:ring-indigo-500"
                style={FONT_MONO}
              >
                {[50, 100, 200, 500].map((n) => (
                  <option key={n} value={n}>
                    {n}
                  </option>
                ))}
              </select>
            </div>

            {/* Initial Capital */}
            <div className="space-y-1.5">
              <label
                className="text-[10px] text-gray-500 uppercase tracking-wider"
                style={FONT_OUTFIT}
              >
                Initial Capital
              </label>
              <input
                type="number"
                value={initialCapital}
                onChange={(e) => setInitialCapital(Number(e.target.value))}
                className="bg-surface-light/30 border border-border rounded-lg px-3 py-1.5 text-xs text-white w-28 focus:outline-none focus:ring-1 focus:ring-indigo-500"
                style={FONT_MONO}
              />
            </div>

            {/* Position Size */}
            <div className="space-y-1.5">
              <label
                className="text-[10px] text-gray-500 uppercase tracking-wider"
                style={FONT_OUTFIT}
              >
                Position Size %
              </label>
              <input
                type="number"
                value={positionSizePct}
                onChange={(e) => setPositionSizePct(Number(e.target.value))}
                className="bg-surface-light/30 border border-border rounded-lg px-3 py-1.5 text-xs text-white w-20 focus:outline-none focus:ring-1 focus:ring-indigo-500"
                style={FONT_MONO}
              />
            </div>

            {/* Run Button */}
            <button
              onClick={runSimulation}
              disabled={loading}
              className="bg-indigo-500 hover:bg-indigo-600 disabled:opacity-50 disabled:cursor-not-allowed text-white px-5 py-1.5 rounded-lg text-xs font-semibold transition-colors flex items-center gap-2"
              style={FONT_OUTFIT}
            >
              {loading && (
                <svg
                  className="animate-spin h-3.5 w-3.5"
                  viewBox="0 0 24 24"
                  fill="none"
                >
                  <circle
                    className="opacity-25"
                    cx="12"
                    cy="12"
                    r="10"
                    stroke="currentColor"
                    strokeWidth="4"
                  />
                  <path
                    className="opacity-75"
                    fill="currentColor"
                    d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"
                  />
                </svg>
              )}
              Run Simulation
            </button>
          </div>

          {error && (
            <div className="mt-3 text-xs text-loss" style={FONT_MONO}>
              {error}
            </div>
          )}
        </CardContent>
      </Card>

      {/* ── Results ───────────────────────────────────────────── */}
      {results && (
        <div className="space-y-6">
          {/* 3a. Probability Cone Chart */}
          <Card>
            <CardContent className="pt-5">
              <h2
                className="text-sm font-semibold text-white mb-4"
                style={FONT_OUTFIT}
              >
                Probability Cone
              </h2>
              <div style={{ height: 400 }}>
                <ResponsiveContainer width="100%" height="100%">
                  <AreaChart data={coneData}>
                    <CartesianGrid
                      strokeDasharray="3 3"
                      stroke="#1f2937"
                      vertical={false}
                    />
                    <XAxis
                      dataKey="trade"
                      tick={{ fill: "#6b7280", fontSize: 10, ...FONT_MONO }}
                      axisLine={{ stroke: "#374151" }}
                      tickLine={false}
                      label={{
                        value: "Trade #",
                        position: "insideBottom",
                        offset: -5,
                        fill: "#6b7280",
                        fontSize: 10,
                        ...FONT_MONO,
                      }}
                    />
                    <YAxis
                      tick={{ fill: "#6b7280", fontSize: 10, ...FONT_MONO }}
                      axisLine={{ stroke: "#374151" }}
                      tickLine={false}
                      tickFormatter={(v: number) =>
                        `$${(v / 1000).toFixed(0)}k`
                      }
                    />
                    <Tooltip
                      content={<ConeTooltip />}
                      cursor={{ stroke: "#374151" }}
                    />
                    <ReferenceLine
                      y={initialCapital}
                      stroke="rgba(255,255,255,0.2)"
                      strokeDasharray="4 4"
                    />

                    {/* Outer band: p95 fill, then p5 eraser */}
                    <Area
                      type="monotone"
                      dataKey="p95"
                      fill="#6366f1"
                      fillOpacity={0.05}
                      stroke="none"
                      isAnimationActive={false}
                    />
                    <Area
                      type="monotone"
                      dataKey="p5"
                      fill="#111827"
                      fillOpacity={1}
                      stroke="none"
                      isAnimationActive={false}
                    />

                    {/* Middle band: p90 fill, then p10 eraser */}
                    <Area
                      type="monotone"
                      dataKey="p90"
                      fill="#6366f1"
                      fillOpacity={0.08}
                      stroke="none"
                      isAnimationActive={false}
                    />
                    <Area
                      type="monotone"
                      dataKey="p10"
                      fill="#111827"
                      fillOpacity={1}
                      stroke="none"
                      isAnimationActive={false}
                    />

                    {/* Inner band: p75 fill, then p25 eraser */}
                    <Area
                      type="monotone"
                      dataKey="p75"
                      fill="#6366f1"
                      fillOpacity={0.12}
                      stroke="none"
                      isAnimationActive={false}
                    />
                    <Area
                      type="monotone"
                      dataKey="p25"
                      fill="#111827"
                      fillOpacity={1}
                      stroke="none"
                      isAnimationActive={false}
                    />

                    {/* Sample paths */}
                    {results.sample_paths.map((_, j) => (
                      <Line
                        key={`sample${j}`}
                        type="monotone"
                        dataKey={`sample${j}`}
                        stroke="#6b7280"
                        strokeWidth={0.8}
                        strokeOpacity={0.3}
                        dot={false}
                        isAnimationActive={false}
                      />
                    ))}

                    {/* Percentile boundary lines */}
                    <Line
                      type="monotone"
                      dataKey="p95"
                      stroke="#6366f1"
                      strokeWidth={0.8}
                      strokeOpacity={0.4}
                      strokeDasharray="4 2"
                      dot={false}
                      isAnimationActive={false}
                    />
                    <Line
                      type="monotone"
                      dataKey="p5"
                      stroke="#6366f1"
                      strokeWidth={0.8}
                      strokeOpacity={0.4}
                      strokeDasharray="4 2"
                      dot={false}
                      isAnimationActive={false}
                    />

                    {/* Median hero line */}
                    <Line
                      type="monotone"
                      dataKey="p50"
                      stroke="#fbbf24"
                      strokeWidth={2.5}
                      dot={false}
                      isAnimationActive={false}
                    />
                  </AreaChart>
                </ResponsiveContainer>
              </div>
              {/* Legend */}
              <div className="flex items-center gap-6 mt-3 justify-center">
                <div className="flex items-center gap-1.5">
                  <div className="w-5 h-0.5 bg-amber-400 rounded" />
                  <span className="text-[10px] text-gray-500" style={FONT_OUTFIT}>Median</span>
                </div>
                <div className="flex items-center gap-1.5">
                  <div className="w-5 h-0.5 bg-indigo-500/60 rounded" style={{ borderStyle: "dashed" }} />
                  <span className="text-[10px] text-gray-500" style={FONT_OUTFIT}>95th percentile</span>
                </div>
                <div className="flex items-center gap-1.5">
                  <div className="w-5 h-0.5 bg-indigo-500/60 rounded" style={{ borderStyle: "dashed" }} />
                  <span className="text-[10px] text-gray-500" style={FONT_OUTFIT}>5th percentile</span>
                </div>
                <div className="flex items-center gap-1.5">
                  <div className="w-3 h-3 rounded bg-indigo-500/10 border border-indigo-500/20" />
                  <span className="text-[10px] text-gray-500" style={FONT_OUTFIT}>Probability bands</span>
                </div>
              </div>
            </CardContent>
          </Card>

          {/* 3b. Outcome Summary Cards */}
          <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3">
            <StatCard
              label="Probability of Profit"
              value={`${results.summary.probability_of_profit.toFixed(1)}%`}
              color={
                results.summary.probability_of_profit > 60
                  ? "text-profit"
                  : results.summary.probability_of_profit >= 40
                  ? "text-amber-400"
                  : "text-loss"
              }
            />
            <StatCard
              label="Median Return"
              value={formatPercent(results.summary.median_return_pct)}
              color={pnlColor(results.summary.median_return_pct)}
            />
            <StatCard
              label="Best Case (95th)"
              value={formatCurrency(results.summary.best_case_p95)}
              color="text-profit"
            />
            <StatCard
              label="Worst Case (5th)"
              value={formatCurrency(results.summary.worst_case_p5)}
              color="text-loss"
            />
            <StatCard
              label="Median Max Drawdown"
              value={`${results.summary.median_max_drawdown_pct.toFixed(1)}%`}
              color="text-loss"
              sub={`95th: -${results.summary.worst_drawdown_p95.toFixed(1)}%`}
            />
            <StatCard
              label="Risk of Ruin"
              value={`${results.summary.probability_of_ruin.toFixed(1)}%`}
              color={
                results.summary.probability_of_ruin > 5
                  ? "text-loss"
                  : results.summary.probability_of_ruin < 1
                  ? "text-profit"
                  : "text-amber-400"
              }
            />
          </div>

          {/* 3c. Final Equity Distribution */}
          <Card>
            <CardContent className="pt-5">
              <h2
                className="text-sm font-semibold text-white mb-4"
                style={FONT_OUTFIT}
              >
                Final Equity Distribution
              </h2>
              <div style={{ height: 250 }}>
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={results.equity_histogram}>
                    <CartesianGrid
                      strokeDasharray="3 3"
                      stroke="#1f2937"
                      vertical={false}
                    />
                    <XAxis
                      dataKey="label"
                      tick={{ fill: "#6b7280", fontSize: 9, ...FONT_MONO }}
                      axisLine={{ stroke: "#374151" }}
                      tickLine={false}
                      interval="preserveStartEnd"
                      angle={-30}
                      textAnchor="end"
                      height={50}
                    />
                    <YAxis
                      tick={{ fill: "#6b7280", fontSize: 10, ...FONT_MONO }}
                      axisLine={{ stroke: "#374151" }}
                      tickLine={false}
                    />
                    <Tooltip
                      contentStyle={CHART_TOOLTIP_STYLE}
                      labelStyle={{ color: "#9ca3af", fontSize: 11, ...FONT_OUTFIT }}
                      itemStyle={{ color: "#e5e7eb", fontSize: 11, ...FONT_MONO }}
                      cursor={{ fill: "rgba(99,102,241,0.08)" }}
                    />
                    <Bar dataKey="count" radius={[4, 4, 0, 0]}>
                      {results.equity_histogram.map((bin, idx) => (
                        <Cell
                          key={idx}
                          fill={equityBarColor(bin.bin_start, bin.bin_end)}
                        />
                      ))}
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </CardContent>
          </Card>

          {/* 3d. Drawdown Risk Chart */}
          <Card>
            <CardContent className="pt-5">
              <h2
                className="text-sm font-semibold text-white mb-4"
                style={FONT_OUTFIT}
              >
                Maximum Drawdown Distribution
              </h2>
              <div style={{ height: 220 }}>
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={results.drawdown_histogram}>
                    <defs>
                      <linearGradient id="ddGrad" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="0%" stopColor="#ef4444" stopOpacity={0.9} />
                        <stop offset="100%" stopColor="#ef4444" stopOpacity={0.4} />
                      </linearGradient>
                    </defs>
                    <CartesianGrid
                      strokeDasharray="3 3"
                      stroke="#1f2937"
                      vertical={false}
                    />
                    <XAxis
                      dataKey="label"
                      tick={{ fill: "#6b7280", fontSize: 9, ...FONT_MONO }}
                      axisLine={{ stroke: "#374151" }}
                      tickLine={false}
                      interval="preserveStartEnd"
                      angle={-30}
                      textAnchor="end"
                      height={50}
                    />
                    <YAxis
                      tick={{ fill: "#6b7280", fontSize: 10, ...FONT_MONO }}
                      axisLine={{ stroke: "#374151" }}
                      tickLine={false}
                    />
                    <Tooltip
                      contentStyle={CHART_TOOLTIP_STYLE}
                      labelStyle={{ color: "#9ca3af", fontSize: 11, ...FONT_OUTFIT }}
                      itemStyle={{ color: "#e5e7eb", fontSize: 11, ...FONT_MONO }}
                      cursor={{ fill: "rgba(239,68,68,0.08)" }}
                    />
                    <Bar
                      dataKey="count"
                      fill="url(#ddGrad)"
                      radius={[4, 4, 0, 0]}
                    />
                  </BarChart>
                </ResponsiveContainer>
              </div>
              <div className="flex flex-col sm:flex-row gap-3 mt-3">
                <div
                  className="text-[11px] text-gray-400"
                  style={FONT_MONO}
                >
                  50% chance max DD &le; {results.summary.median_max_drawdown_pct.toFixed(1)}%
                </div>
                <div
                  className="text-[11px] text-gray-400"
                  style={FONT_MONO}
                >
                  95% chance max DD &le; {results.summary.worst_drawdown_p95.toFixed(1)}%
                </div>
              </div>
            </CardContent>
          </Card>

          {/* 3e. Input Data Summary */}
          <Card>
            <CardContent className="pt-5">
              <h2
                className="text-sm font-semibold text-white mb-3"
                style={FONT_OUTFIT}
              >
                Input Data Summary
              </h2>
              <p className="text-xs text-gray-400 mb-4" style={FONT_MONO}>
                Based on {results.input_stats.total_trades_pooled} trades ({results.input_stats.live_trade_count} live + {results.input_stats.backtest_trade_count} backtest)
              </p>
              <div className="grid grid-cols-3 md:grid-cols-5 lg:grid-cols-9 gap-3 mb-4">
                <div>
                  <div className="text-[10px] text-gray-600 uppercase" style={FONT_OUTFIT}>Win Rate</div>
                  <div className="text-xs text-white font-semibold" style={FONT_MONO}>
                    {(results.input_stats.win_rate * 100).toFixed(1)}%
                  </div>
                </div>
                <div>
                  <div className="text-[10px] text-gray-600 uppercase" style={FONT_OUTFIT}>Avg Win</div>
                  <div className="text-xs text-profit font-semibold" style={FONT_MONO}>
                    +{results.input_stats.avg_win_pct.toFixed(2)}%
                  </div>
                </div>
                <div>
                  <div className="text-[10px] text-gray-600 uppercase" style={FONT_OUTFIT}>Avg Loss</div>
                  <div className="text-xs text-loss font-semibold" style={FONT_MONO}>
                    {results.input_stats.avg_loss_pct.toFixed(2)}%
                  </div>
                </div>
                <div>
                  <div className="text-[10px] text-gray-600 uppercase" style={FONT_OUTFIT}>Profit Factor</div>
                  <div className="text-xs text-white font-semibold" style={FONT_MONO}>
                    {results.input_stats.profit_factor.toFixed(2)}
                  </div>
                </div>
                <div>
                  <div className="text-[10px] text-gray-600 uppercase" style={FONT_OUTFIT}>Best Trade</div>
                  <div className="text-xs text-profit font-semibold" style={FONT_MONO}>
                    +{results.input_stats.best_trade_pct.toFixed(2)}%
                  </div>
                </div>
                <div>
                  <div className="text-[10px] text-gray-600 uppercase" style={FONT_OUTFIT}>Worst Trade</div>
                  <div className="text-xs text-loss font-semibold" style={FONT_MONO}>
                    {results.input_stats.worst_trade_pct.toFixed(2)}%
                  </div>
                </div>
                <div>
                  <div className="text-[10px] text-gray-600 uppercase" style={FONT_OUTFIT}>Mean PnL</div>
                  <div className={`text-xs font-semibold ${pnlColor(results.input_stats.mean_pnl_pct)}`} style={FONT_MONO}>
                    {formatPercent(results.input_stats.mean_pnl_pct)}
                  </div>
                </div>
                <div>
                  <div className="text-[10px] text-gray-600 uppercase" style={FONT_OUTFIT}>Median PnL</div>
                  <div className={`text-xs font-semibold ${pnlColor(results.input_stats.median_pnl_pct)}`} style={FONT_MONO}>
                    {formatPercent(results.input_stats.median_pnl_pct)}
                  </div>
                </div>
                <div>
                  <div className="text-[10px] text-gray-600 uppercase" style={FONT_OUTFIT}>Std Dev</div>
                  <div className="text-xs text-white font-semibold" style={FONT_MONO}>
                    {results.input_stats.std_pnl_pct.toFixed(2)}%
                  </div>
                </div>
              </div>
              <div className="flex flex-wrap gap-2">
                {results.input_stats.strategies_included.map((s) => (
                  <Badge key={s} variant="outline" className="text-[10px] text-indigo-300 border-indigo-500/30">
                    {s}
                  </Badge>
                ))}
                {results.input_stats.tickers_included.map((t) => (
                  <Badge key={t} variant="outline" className="text-[10px] text-amber-300 border-amber-500/30">
                    {t}
                  </Badge>
                ))}
              </div>
            </CardContent>
          </Card>

          {/* 3f. Ask Henry */}
          <Card>
            <CardContent className="pt-5">
              <div className="flex items-center gap-3 mb-4">
                <div className="w-2 h-2 rounded-full bg-indigo-500 animate-pulse" />
                <h2
                  className="text-sm font-semibold text-white"
                  style={FONT_OUTFIT}
                >
                  Ask Henry
                </h2>
              </div>

              {!henryResponse && !henryLoading && (
                <button
                  onClick={askHenry}
                  className="bg-indigo-500/20 hover:bg-indigo-500/30 text-indigo-300 border border-indigo-500/30 px-4 py-2 rounded-lg text-xs font-medium transition-colors"
                  style={FONT_OUTFIT}
                >
                  Ask Henry to interpret these results
                </button>
              )}

              {henryLoading && (
                <div className="flex items-center gap-2 text-xs text-gray-400" style={FONT_MONO}>
                  <svg
                    className="animate-spin h-3.5 w-3.5 text-indigo-400"
                    viewBox="0 0 24 24"
                    fill="none"
                  >
                    <circle
                      className="opacity-25"
                      cx="12"
                      cy="12"
                      r="10"
                      stroke="currentColor"
                      strokeWidth="4"
                    />
                    <path
                      className="opacity-75"
                      fill="currentColor"
                      d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"
                    />
                  </svg>
                  Henry is analyzing your results...
                </div>
              )}

              {henryResponse && (
                <div className="border-l-2 border-indigo-500 pl-4 py-2">
                  <div className="text-xs text-gray-300 leading-relaxed whitespace-pre-wrap" style={FONT_MONO}>
                    {henryResponse}
                  </div>
                </div>
              )}
            </CardContent>
          </Card>
        </div>
      )}

      {/* Empty state when no results */}
      {!results && !loading && (
        <div className="flex flex-col items-center justify-center py-20 text-center">
          <div className="text-gray-600 text-sm" style={FONT_OUTFIT}>
            Configure your parameters above and run a simulation to see probability analysis.
          </div>
        </div>
      )}
    </div>
  );
}
