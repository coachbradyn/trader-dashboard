"use client";

import { useState, useEffect, useCallback } from "react";
import { useParams, useRouter } from "next/navigation";
import { api } from "@/lib/api";
import { formatTimeAgo, formatCurrency, formatPercent, pnlColor } from "@/lib/formatters";
import { renderMarkdown } from "@/lib/markdown";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Badge } from "@/components/ui/badge";
import {
  ResponsiveContainer, AreaChart, Area, Line,
  XAxis, YAxis, Tooltip, CartesianGrid, ReferenceLine,
} from "recharts";
import type {
  WatchlistTickerDetail, ChartDataPoint, BacktestImportData,
  BacktestTradeData, MonteCarloResponse, MonteCarloRequest,
} from "@/lib/types";

const FONT_OUTFIT = { fontFamily: "'Outfit', sans-serif" } as const;
const FONT_MONO = { fontFamily: "'JetBrains Mono', monospace" } as const;

function useFonts() {
  useEffect(() => {
    if (document.getElementById("__ticker-fonts")) return;
    const link = document.createElement("link");
    link.id = "__ticker-fonts";
    link.rel = "stylesheet";
    link.href = "https://fonts.googleapis.com/css2?family=Outfit:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500;600;700&display=swap";
    document.head.appendChild(link);
  }, []);
}

function signalDot(signal: string) {
  const s = signal.toLowerCase();
  if (s === "bullish") return "bg-profit";
  if (s === "bearish") return "bg-loss";
  return "bg-gray-500";
}

function consensusLabel(dir: string) {
  if (dir === "bullish") return { label: "Bullish", color: "text-profit" };
  if (dir === "bearish") return { label: "Bearish", color: "text-loss" };
  if (dir === "mixed") return { label: "Mixed", color: "text-yellow-400" };
  return { label: "No Data", color: "text-gray-500" };
}

const CHART_TOOLTIP = { background: "#1f2937", border: "1px solid #374151", borderRadius: 8 };

export default function TickerDetailPage() {
  useFonts();
  const params = useParams();
  const router = useRouter();
  const ticker = (params.ticker as string || "").toUpperCase();

  const [detail, setDetail] = useState<WatchlistTickerDetail | null>(null);
  const [chartData, setChartData] = useState<ChartDataPoint[]>([]);
  const [backtests, setBacktests] = useState<BacktestImportData[]>([]);
  const [btTrades, setBtTrades] = useState<Record<string, BacktestTradeData[]>>({});
  const [expandedBt, setExpandedBt] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);

  // Monte Carlo state
  const [mcResults, setMcResults] = useState<MonteCarloResponse | null>(null);
  const [mcLoading, setMcLoading] = useState(false);
  const [mcSource, setMcSource] = useState<"combined" | "live" | "backtest">("combined");
  const [mcSims, setMcSims] = useState(1000);
  const [mcTrades, setMcTrades] = useState(100);
  const [mcCapital, setMcCapital] = useState(10000);

  const fetchAll = useCallback(async () => {
    try {
      const [d, c, allBt] = await Promise.all([
        api.getWatchlistDetail(ticker).catch(() => null),
        api.getScreenerChart(ticker, 90).catch(() => []),
        api.getBacktestImports().catch(() => []),
      ]);
      setDetail(d);
      setChartData(c);
      setBacktests(allBt.filter((b) => b.ticker === ticker));
    } catch {}
  }, [ticker]);

  useEffect(() => {
    fetchAll().finally(() => setLoading(false));
  }, [fetchAll]);

  const handleRefreshSummary = async () => {
    setRefreshing(true);
    try {
      await api.refreshWatchlistSummary(ticker);
      setTimeout(async () => {
        const updated = await api.getWatchlistDetail(ticker).catch(() => null);
        if (updated) setDetail(updated);
        setRefreshing(false);
      }, 3000);
    } catch { setRefreshing(false); }
  };

  const handleRemove = async () => {
    if (!confirm(`Remove ${ticker} from watchlist?`)) return;
    try {
      await api.removeWatchlistTicker(ticker);
      router.push("/screener");
    } catch {}
  };

  const loadBtTrades = async (importId: string) => {
    if (btTrades[importId]) { setExpandedBt(expandedBt === importId ? null : importId); return; }
    try {
      const trades = await api.getBacktestTrades(importId);
      setBtTrades((prev) => ({ ...prev, [importId]: trades }));
      setExpandedBt(importId);
    } catch {}
  };

  const runMC = async () => {
    setMcLoading(true);
    try {
      const params: MonteCarloRequest = {
        source: mcSource,
        ticker,
        num_simulations: mcSims,
        forward_trades: mcTrades,
        initial_capital: mcCapital,
        position_size_pct: 100,
      };
      const r = await api.runMonteCarlo(params);
      setMcResults(r);
    } catch {}
    setMcLoading(false);
  };

  const cons = detail ? consensusLabel(detail.consensus.direction) : null;

  // Chart data for price chart
  const priceChartData = chartData.map((d) => ({ date: d.date, close: d.close, volume: d.volume }));

  // MC cone data
  const mcConeData = mcResults
    ? mcResults.trade_indices.map((t, i) => {
        const p: Record<string, number> = {
          trade: t,
          p5: mcResults.percentile_bands.p5[i],
          p25: mcResults.percentile_bands.p25[i],
          p50: mcResults.percentile_bands.p50[i],
          p75: mcResults.percentile_bands.p75[i],
          p95: mcResults.percentile_bands.p95[i],
        };
        if (mcResults.buyhold) {
          const bh = mcResults.buyhold.percentile_bands;
          if (bh.p50?.[i] !== undefined) p.bh_p50 = bh.p50[i];
          if (bh.p25?.[i] !== undefined) p.bh_p25 = bh.p25[i];
          if (bh.p75?.[i] !== undefined) p.bh_p75 = bh.p75[i];
        }
        return p;
      })
    : [];

  if (loading) return (
    <div className="space-y-4">
      <Skeleton className="h-12 w-48 rounded-lg" />
      <Skeleton className="h-64 rounded-xl" />
      <div className="grid grid-cols-2 gap-4">
        <Skeleton className="h-40 rounded-xl" />
        <Skeleton className="h-40 rounded-xl" />
      </div>
    </div>
  );

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <button onClick={() => router.push("/screener")} className="text-gray-500 hover:text-white transition">
            <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M15.75 19.5L8.25 12l7.5-7.5" />
            </svg>
          </button>
          <h1 className="text-2xl font-bold text-white" style={FONT_OUTFIT}>{ticker}</h1>
          {cons && <span className={`text-sm font-semibold ${cons.color}`}>{cons.label}</span>}
          {detail && detail.consensus.total_signals > 0 && (
            <span className="text-xs text-gray-500 font-mono">
              {detail.consensus.bullish_count}B / {detail.consensus.bearish_count}B
            </span>
          )}
        </div>
        <div className="flex items-center gap-2">
          <Button variant="outline" size="sm" onClick={handleRemove} className="text-loss/70 border-loss/20 hover:text-loss">
            Remove
          </Button>
        </div>
      </div>

      {/* Price Chart */}
      {priceChartData.length >= 2 && (
        <Card>
          <CardContent className="pt-5">
            <h2 className="text-sm font-semibold text-white mb-3" style={FONT_OUTFIT}>Price (90d)</h2>
            <div style={{ height: 250 }}>
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart data={priceChartData}>
                  <defs>
                    <linearGradient id="priceGrad" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="0%" stopColor="#6366f1" stopOpacity={0.15} />
                      <stop offset="100%" stopColor="#6366f1" stopOpacity={0} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid strokeDasharray="3 3" stroke="#1f2937" vertical={false} />
                  <XAxis dataKey="date" tick={{ fill: "#6b7280", fontSize: 9, ...FONT_MONO }} axisLine={{ stroke: "#374151" }} tickLine={false} />
                  <YAxis tick={{ fill: "#6b7280", fontSize: 10, ...FONT_MONO }} axisLine={{ stroke: "#374151" }} tickLine={false} tickFormatter={(v: number) => `$${v}`} domain={["auto", "auto"]} />
                  <Tooltip contentStyle={CHART_TOOLTIP} labelStyle={{ color: "#9ca3af", fontSize: 11 }} itemStyle={{ color: "#e5e7eb", fontSize: 11, ...FONT_MONO }} />
                  <Area type="monotone" dataKey="close" stroke="#6366f1" strokeWidth={2} fill="url(#priceGrad)" isAnimationActive={false} />
                </AreaChart>
              </ResponsiveContainer>
            </div>
          </CardContent>
        </Card>
      )}

      {/* Henry's AI Summary */}
      <Card>
        <CardContent className="pt-5">
          <div className="flex items-center justify-between mb-2">
            <h2 className="text-sm font-semibold text-white flex items-center gap-2" style={FONT_OUTFIT}>
              <span className="w-2 h-2 rounded-full bg-ai-blue animate-pulse" />
              Henry&apos;s Analysis
            </h2>
            <button onClick={handleRefreshSummary} disabled={refreshing}
              className="text-xs text-ai-blue/70 hover:text-ai-blue flex items-center gap-1 transition disabled:opacity-50">
              <svg className={`w-3 h-3 ${refreshing ? "animate-spin" : ""}`} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
              </svg>
              {refreshing ? "Generating..." : "Refresh"}
            </button>
          </div>
          {detail?.cached_summary ? (
            <div className={`rounded-lg border p-3 ${detail.cached_summary.is_stale ? "border-yellow-500/20 bg-yellow-500/5" : "border-ai-blue/20 bg-ai-blue/5"}`}>
              {detail.cached_summary.is_stale && (
                <div className="flex items-center gap-1.5 mb-2 text-[10px] text-yellow-400">Stale — new data since {formatTimeAgo(detail.cached_summary.generated_at)}</div>
              )}
              <div className="text-sm text-gray-300 ai-prose" dangerouslySetInnerHTML={{ __html: renderMarkdown(detail.cached_summary.summary) }} />
              <div className="text-[10px] text-gray-600 mt-2 font-mono">Generated {formatTimeAgo(detail.cached_summary.generated_at)}</div>
            </div>
          ) : (
            <div className="text-center py-4">
              <button onClick={handleRefreshSummary} disabled={refreshing} className="text-xs text-ai-blue hover:underline">{refreshing ? "Generating..." : "Generate analysis"}</button>
            </div>
          )}
        </CardContent>
      </Card>

      {/* Indicator Signals + Strategy Positions side by side */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {/* Signals */}
        <Card>
          <CardContent className="pt-5">
            <h2 className="text-sm font-semibold text-white mb-3" style={FONT_OUTFIT}>Indicator Signals</h2>
            {detail && detail.all_signals.length > 0 ? (
              <div className="space-y-1 max-h-64 overflow-y-auto">
                {detail.all_signals.map((s) => (
                  <div key={s.id} className="flex items-center gap-2 text-xs py-1.5 px-2 rounded bg-surface-light/20">
                    <span className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${signalDot(s.signal)}`} />
                    <span className="text-gray-300 font-mono flex-1">{s.indicator}</span>
                    <span className="text-gray-500 font-mono">{s.value.toFixed(2)}</span>
                    <span className={s.signal === "bullish" ? "text-profit" : s.signal === "bearish" ? "text-loss" : "text-gray-500"}>{s.signal}</span>
                    <span className="text-gray-600">{formatTimeAgo(s.created_at)}</span>
                  </div>
                ))}
              </div>
            ) : (
              <p className="text-xs text-gray-600 py-4 text-center">No indicator signals</p>
            )}
          </CardContent>
        </Card>

        {/* Strategy Positions + Trade History */}
        <Card>
          <CardContent className="pt-5">
            <h2 className="text-sm font-semibold text-white mb-3" style={FONT_OUTFIT}>Positions &amp; History</h2>
            {detail && detail.strategy_positions.length > 0 && (
              <div className="space-y-1.5 mb-4">
                {detail.strategy_positions.map((p) => (
                  <div key={p.strategy_id} className="flex items-center gap-3 text-xs py-2 px-3 rounded-lg bg-surface-light/20 border border-border/30">
                    <span className="text-gray-300">{p.strategy_name}</span>
                    <span className={`font-mono font-bold ${p.direction === "long" ? "text-profit" : "text-loss"}`}>{p.direction.toUpperCase()}</span>
                    <span className="text-gray-500 font-mono ml-auto">@ ${p.entry_price.toFixed(2)}</span>
                    {p.pnl_pct != null && (
                      <span className={`font-mono font-bold ${(p.pnl_pct ?? 0) >= 0 ? "text-profit" : "text-loss"}`}>{(p.pnl_pct ?? 0) >= 0 ? "+" : ""}{p.pnl_pct?.toFixed(2)}%</span>
                    )}
                  </div>
                ))}
              </div>
            )}
            {detail && detail.trade_history.length > 0 ? (
              <div className="space-y-1 max-h-48 overflow-y-auto">
                <div className="text-[10px] text-gray-600 uppercase tracking-wider mb-1">Recent Trades</div>
                {detail.trade_history.map((t, i) => (
                  <div key={i} className="flex items-center gap-2 text-xs py-1.5 px-2 rounded bg-surface-light/20">
                    <span className="text-gray-400">{t.strategy_name}</span>
                    <span className={t.direction === "long" ? "text-profit" : "text-loss"}>{t.direction.toUpperCase()}</span>
                    <span className="text-gray-500 font-mono">${t.entry_price.toFixed(2)} → ${t.exit_price?.toFixed(2) ?? "?"}</span>
                    <span className={`font-mono ml-auto font-bold ${t.pnl_pct >= 0 ? "text-profit" : "text-loss"}`}>{t.pnl_pct >= 0 ? "+" : ""}{t.pnl_pct.toFixed(2)}%</span>
                  </div>
                ))}
              </div>
            ) : (
              <p className="text-xs text-gray-600 py-2 text-center">No trade history</p>
            )}
          </CardContent>
        </Card>
      </div>

      {/* Backtest Data */}
      <Card>
        <CardContent className="pt-5">
          <h2 className="text-sm font-semibold text-white mb-3" style={FONT_OUTFIT}>Backtest Data — {ticker}</h2>
          {backtests.length > 0 ? (
            <div className="space-y-2">
              {backtests.map((bt) => (
                <div key={bt.id} className="border border-border/40 rounded-lg overflow-hidden">
                  <button onClick={() => loadBtTrades(bt.id)} className="w-full flex items-center gap-3 p-3 text-left hover:bg-surface-light/20 transition">
                    <span className="text-sm text-white font-medium">{bt.strategy_name}</span>
                    <Badge variant="outline" className="text-[10px]">{bt.trade_count} trades</Badge>
                    {bt.win_rate != null && <span className="text-xs text-gray-400 font-mono">WR {bt.win_rate.toFixed(1)}%</span>}
                    {bt.profit_factor != null && <span className="text-xs text-gray-400 font-mono">PF {bt.profit_factor.toFixed(2)}</span>}
                    {bt.total_pnl_pct != null && (
                      <span className={`text-xs font-mono ml-auto font-bold ${bt.total_pnl_pct >= 0 ? "text-profit" : "text-loss"}`}>
                        {bt.total_pnl_pct >= 0 ? "+" : ""}{bt.total_pnl_pct.toFixed(2)}%
                      </span>
                    )}
                    <svg className={`w-4 h-4 text-gray-500 transition ${expandedBt === bt.id ? "rotate-180" : ""}`} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                      <path strokeLinecap="round" strokeLinejoin="round" d="M19.5 8.25l-7.5 7.5-7.5-7.5" />
                    </svg>
                  </button>
                  {expandedBt === bt.id && btTrades[bt.id] && (
                    <div className="border-t border-border/30 p-3 max-h-48 overflow-y-auto">
                      <div className="space-y-1">
                        {btTrades[bt.id].map((t) => (
                          <div key={t.id} className="flex items-center gap-2 text-xs py-1 px-2 rounded bg-surface-light/10">
                            <span className="text-gray-500 font-mono w-16">{t.trade_date?.slice(0, 10)}</span>
                            <span className={t.direction === "long" ? "text-profit" : "text-loss"}>{t.type}</span>
                            <span className="text-gray-400 font-mono">${t.price.toFixed(2)}</span>
                            {t.net_pnl_pct != null && (
                              <span className={`font-mono ml-auto ${t.net_pnl_pct >= 0 ? "text-profit" : "text-loss"}`}>
                                {t.net_pnl_pct >= 0 ? "+" : ""}{t.net_pnl_pct.toFixed(2)}%
                              </span>
                            )}
                          </div>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              ))}
            </div>
          ) : (
            <p className="text-xs text-gray-600 py-4 text-center">No backtest data for {ticker}. Upload CSV files in Settings.</p>
          )}
        </CardContent>
      </Card>

      {/* Monte Carlo Simulation */}
      <Card>
        <CardContent className="pt-5">
          <h2 className="text-sm font-semibold text-white mb-3" style={FONT_OUTFIT}>Monte Carlo — {ticker}</h2>
          <div className="flex flex-wrap items-end gap-3 mb-4">
            <div className="space-y-1">
              <label className="text-[10px] text-gray-500 uppercase tracking-wider" style={FONT_OUTFIT}>Source</label>
              <div className="flex rounded-lg overflow-hidden border border-border">
                {(["combined", "live", "backtest"] as const).map((s) => (
                  <button key={s} onClick={() => setMcSource(s)}
                    className={`px-3 py-1.5 text-xs font-medium capitalize ${mcSource === s ? "bg-indigo-500 text-white" : "bg-surface-light/30 text-gray-400 hover:text-white"}`}>{s}</button>
                ))}
              </div>
            </div>
            <div className="space-y-1">
              <label className="text-[10px] text-gray-500 uppercase tracking-wider" style={FONT_OUTFIT}>Sims</label>
              <select value={mcSims} onChange={(e) => setMcSims(Number(e.target.value))}
                className="bg-surface-light/30 border border-border rounded-lg px-3 py-1.5 text-xs text-white" style={FONT_MONO}>
                {[500, 1000, 2500, 5000].map((n) => <option key={n} value={n}>{n.toLocaleString()}</option>)}
              </select>
            </div>
            <div className="space-y-1">
              <label className="text-[10px] text-gray-500 uppercase tracking-wider" style={FONT_OUTFIT}>Trades</label>
              <select value={mcTrades} onChange={(e) => setMcTrades(Number(e.target.value))}
                className="bg-surface-light/30 border border-border rounded-lg px-3 py-1.5 text-xs text-white" style={FONT_MONO}>
                {[50, 100, 200].map((n) => <option key={n} value={n}>{n}</option>)}
              </select>
            </div>
            <div className="space-y-1">
              <label className="text-[10px] text-gray-500 uppercase tracking-wider" style={FONT_OUTFIT}>Capital</label>
              <input type="number" value={mcCapital} onChange={(e) => setMcCapital(Number(e.target.value))}
                className="bg-surface-light/30 border border-border rounded-lg px-3 py-1.5 text-xs text-white w-24" style={FONT_MONO} />
            </div>
            <button onClick={runMC} disabled={mcLoading}
              className="bg-indigo-500 hover:bg-indigo-600 disabled:opacity-50 text-white px-5 py-1.5 rounded-lg text-xs font-semibold flex items-center gap-2" style={FONT_OUTFIT}>
              {mcLoading && <svg className="animate-spin h-3.5 w-3.5" viewBox="0 0 24 24" fill="none"><circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" /><path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" /></svg>}
              Run
            </button>
          </div>

          {mcResults && (
            <div className="space-y-4">
              {/* MC Cone Chart */}
              <div style={{ height: 300 }}>
                <ResponsiveContainer width="100%" height="100%">
                  <AreaChart data={mcConeData}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#1f2937" vertical={false} />
                    <XAxis dataKey="trade" tick={{ fill: "#6b7280", fontSize: 10, ...FONT_MONO }} axisLine={{ stroke: "#374151" }} tickLine={false} />
                    <YAxis tick={{ fill: "#6b7280", fontSize: 10, ...FONT_MONO }} axisLine={{ stroke: "#374151" }} tickLine={false} tickFormatter={(v: number) => `$${(v / 1000).toFixed(0)}k`} />
                    <Tooltip contentStyle={CHART_TOOLTIP} />
                    <ReferenceLine y={mcCapital} stroke="rgba(255,255,255,0.2)" strokeDasharray="4 4" />
                    <Area type="monotone" dataKey="p95" fill="#6366f1" fillOpacity={0.05} stroke="none" isAnimationActive={false} />
                    <Area type="monotone" dataKey="p5" fill="#111827" fillOpacity={1} stroke="none" isAnimationActive={false} />
                    <Area type="monotone" dataKey="p75" fill="#6366f1" fillOpacity={0.1} stroke="none" isAnimationActive={false} />
                    <Area type="monotone" dataKey="p25" fill="#111827" fillOpacity={1} stroke="none" isAnimationActive={false} />
                    <Line type="monotone" dataKey="p50" stroke="#fbbf24" strokeWidth={2.5} dot={false} isAnimationActive={false} />
                    {mcResults.buyhold && (
                      <>
                        <Area type="monotone" dataKey="bh_p75" fill="#22c55e" fillOpacity={0.06} stroke="none" isAnimationActive={false} />
                        <Area type="monotone" dataKey="bh_p25" fill="#111827" fillOpacity={1} stroke="none" isAnimationActive={false} />
                        <Line type="monotone" dataKey="bh_p50" stroke="#22c55e" strokeWidth={2.5} dot={false} isAnimationActive={false} />
                      </>
                    )}
                  </AreaChart>
                </ResponsiveContainer>
              </div>

              {/* MC Summary Stats */}
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
                <div className="bg-surface-light/30 rounded-xl p-3 border border-border">
                  <div className="text-[10px] text-gray-500 uppercase" style={FONT_OUTFIT}>Prob. Profit</div>
                  <div className={`text-lg font-mono font-semibold ${mcResults.summary.probability_of_profit > 60 ? "text-profit" : mcResults.summary.probability_of_profit >= 40 ? "text-amber-400" : "text-loss"}`}>
                    {mcResults.summary.probability_of_profit.toFixed(1)}%
                  </div>
                </div>
                <div className="bg-surface-light/30 rounded-xl p-3 border border-border">
                  <div className="text-[10px] text-gray-500 uppercase" style={FONT_OUTFIT}>Median Return</div>
                  <div className={`text-lg font-mono font-semibold ${pnlColor(mcResults.summary.median_return_pct)}`}>
                    {formatPercent(mcResults.summary.median_return_pct)}
                  </div>
                </div>
                <div className="bg-surface-light/30 rounded-xl p-3 border border-border">
                  <div className="text-[10px] text-gray-500 uppercase" style={FONT_OUTFIT}>Best (P95)</div>
                  <div className="text-lg font-mono font-semibold text-profit">{formatCurrency(mcResults.summary.best_case_p95)}</div>
                </div>
                <div className="bg-surface-light/30 rounded-xl p-3 border border-border">
                  <div className="text-[10px] text-gray-500 uppercase" style={FONT_OUTFIT}>Worst (P5)</div>
                  <div className="text-lg font-mono font-semibold text-loss">{formatCurrency(mcResults.summary.worst_case_p5)}</div>
                </div>
              </div>

              {/* Buy & Hold comparison if available */}
              {mcResults.buyhold && (
                <div className="flex items-center gap-4 text-xs px-3 py-2 rounded-lg bg-surface-light/20 border border-border/30">
                  <span className="text-gray-400">Strategy median:</span>
                  <span className={`font-mono font-bold ${pnlColor(mcResults.summary.median_return_pct)}`}>{formatPercent(mcResults.summary.median_return_pct)}</span>
                  <span className="text-gray-600">vs</span>
                  <span className="text-gray-400">Buy &amp; Hold:</span>
                  <span className={`font-mono font-bold ${pnlColor(mcResults.buyhold.summary.median_return_pct)}`}>{formatPercent(mcResults.buyhold.summary.median_return_pct)}</span>
                  {(() => {
                    const edge = mcResults.summary.median_return_pct - mcResults.buyhold.summary.median_return_pct;
                    return <span className={`font-mono font-bold ml-auto ${edge >= 0 ? "text-profit" : "text-loss"}`}>Edge: {edge >= 0 ? "+" : ""}{edge.toFixed(1)}%</span>;
                  })()}
                </div>
              )}

              {/* Legend */}
              <div className="flex items-center gap-4 text-[10px] text-gray-600 justify-center">
                <div className="flex items-center gap-1"><div className="w-4 h-0.5 bg-amber-400 rounded" /> Strategy</div>
                <div className="flex items-center gap-1"><div className="w-3 h-3 rounded bg-indigo-500/10 border border-indigo-500/20" /> Probability bands</div>
                {mcResults.buyhold && <div className="flex items-center gap-1"><div className="w-4 h-0.5 bg-green-500 rounded" /> Buy &amp; Hold</div>}
              </div>
            </div>
          )}

          {!mcResults && !mcLoading && (
            <p className="text-xs text-gray-600 text-center py-4">Configure parameters and run a simulation</p>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
