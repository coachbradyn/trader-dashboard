"use client";

import { useState, useEffect, useCallback } from "react";
import { api } from "@/lib/api";
import { usePolling } from "@/hooks/usePolling";
import { formatCurrency, formatPercent, formatDate, formatTimeAgo, formatSource, formatExitReason, pnlColor } from "@/lib/formatters";
import { renderMarkdown } from "@/lib/markdown";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import {
  ResponsiveContainer, LineChart, Line, AreaChart, Area, BarChart, Bar,
  XAxis, YAxis, Tooltip, CartesianGrid, ReferenceLine,
  PieChart, Pie, Cell,
} from "recharts";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import type {
  Portfolio, Performance, Position, EquityPoint, DailyStats, Trade,
  BacktestImportData, PortfolioHolding, ActionStats, PortfolioAction,
  ImportedTrade, ImportPreview, ImportResult, OrderResult,
} from "@/lib/types";

const FONT_OUTFIT = { fontFamily: "'Outfit', sans-serif" } as const;
const FONT_MONO = { fontFamily: "'JetBrains Mono', monospace" } as const;

function useFonts() {
  useEffect(() => {
    if (document.getElementById("__portfolio-fonts")) return;
    const link = document.createElement("link");
    link.id = "__portfolio-fonts";
    link.rel = "stylesheet";
    link.href = "https://fonts.googleapis.com/css2?family=Outfit:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500;600;700&display=swap";
    document.head.appendChild(link);
  }, []);
}

const CHART_TOOLTIP = { background: "#1f2937", border: "1px solid #374151", borderRadius: 8 };

// ── Stat Card ───────────────────────────────────────────────────────

function StatCard({ label, value, color = "text-white", sub }: { label: string; value: string; color?: string; sub?: string }) {
  return (
    <div className="bg-surface-light/30 rounded-xl p-4 border border-border">
      <div className="text-[10px] text-gray-500 uppercase tracking-wider mb-1" style={FONT_OUTFIT}>{label}</div>
      <div className={`text-lg font-mono font-semibold ${color}`} style={FONT_MONO}>{value}</div>
      {sub && <div className="text-[10px] text-gray-600 font-mono mt-0.5">{sub}</div>}
    </div>
  );
}

// ── Performance Stats Grid ──────────────────────────────────────────

function PerformanceGrid({ perf }: { perf: Performance }) {
  return (
    <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3">
      <StatCard label="Total Return" value={formatPercent(perf.total_return_pct)} color={pnlColor(perf.total_return_pct)} />
      <StatCard label="Win Rate" value={`${perf.win_rate.toFixed(1)}%`}
        color={perf.win_rate >= 50 ? "text-profit" : "text-loss"}
        sub={`${perf.wins}W / ${perf.losses}L`} />
      <StatCard label="Profit Factor" value={perf.profit_factor.toFixed(2)}
        color={perf.profit_factor >= 1.5 ? "text-profit" : perf.profit_factor >= 1 ? "text-amber-400" : "text-loss"} />
      <StatCard label="Total P&L" value={formatCurrency(perf.total_pnl)} color={pnlColor(perf.total_pnl)}
        sub={`${perf.total_trades} trades`} />
      <StatCard label="Max Drawdown" value={formatPercent(-Math.abs(perf.max_drawdown_pct))} color="text-loss" />
      <StatCard label="Sharpe Ratio" value={perf.sharpe_ratio.toFixed(2)}
        color={perf.sharpe_ratio >= 1 ? "text-profit" : "text-gray-300"}
        sub={perf.current_streak !== 0 ? `${perf.current_streak > 0 ? "+" : ""}${perf.current_streak} streak` : undefined} />
    </div>
  );
}

// ── Equity Curve Chart ──────────────────────────────────────────────

function EquityCurveChart({ data, initialCapital }: { data: EquityPoint[]; initialCapital: number }) {
  if (!data.length) {
    return (
      <Card className="bg-surface-light/20 border-border">
        <CardContent className="p-5">
          <h3 className="font-semibold text-white text-sm mb-3" style={FONT_OUTFIT}>Equity Curve</h3>
          <p className="text-gray-500 text-sm text-center py-12">No equity data yet — trades will populate this chart</p>
        </CardContent>
      </Card>
    );
  }

  const chartData = data.map((d) => ({
    date: new Date(d.time).toLocaleDateString("en-US", { month: "short", day: "numeric" }),
    equity: d.equity,
    returnPct: ((d.equity - initialCapital) / initialCapital * 100),
  }));

  return (
    <Card className="bg-surface-light/20 border-border">
      <CardContent className="p-5">
        <h3 className="font-semibold text-white text-sm mb-4" style={FONT_OUTFIT}>Equity Curve</h3>
        <ResponsiveContainer width="100%" height={280}>
          <AreaChart data={chartData}>
            <defs>
              <linearGradient id="equityGrad" x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%" stopColor="#6366f1" stopOpacity={0.3} />
                <stop offset="95%" stopColor="#6366f1" stopOpacity={0} />
              </linearGradient>
            </defs>
            <CartesianGrid strokeDasharray="3 3" stroke="#1f2937" />
            <XAxis dataKey="date" stroke="#4b5563" tick={{ fontSize: 10, fill: "#6b7280" }} />
            <YAxis stroke="#4b5563" tick={{ fontSize: 10, fill: "#6b7280" }} tickFormatter={(v) => `$${(v / 1000).toFixed(1)}k`} />
            <Tooltip contentStyle={CHART_TOOLTIP} labelStyle={{ color: "#9ca3af" }}
              formatter={(value: number, name: string) => [
                name === "equity" ? formatCurrency(value) : formatPercent(value),
                name === "equity" ? "Equity" : "Return",
              ]} />
            <ReferenceLine y={initialCapital} stroke="#374151" strokeDasharray="3 3" />
            <Area type="monotone" dataKey="equity" stroke="#6366f1" strokeWidth={2} fill="url(#equityGrad)" dot={false} />
          </AreaChart>
        </ResponsiveContainer>
      </CardContent>
    </Card>
  );
}

// ── Drawdown Chart ──────────────────────────────────────────────────

function DrawdownChart({ data }: { data: EquityPoint[] }) {
  if (!data.length) {
    return (
      <Card className="bg-surface-light/20 border-border">
        <CardContent className="p-5">
          <h3 className="font-semibold text-white text-sm mb-3" style={FONT_OUTFIT}>Drawdown</h3>
          <p className="text-gray-500 text-sm text-center py-12">No drawdown data yet</p>
        </CardContent>
      </Card>
    );
  }

  const chartData = data.map((d) => ({
    date: new Date(d.time).toLocaleDateString("en-US", { month: "short", day: "numeric" }),
    drawdown: -Math.abs(d.drawdown_pct),
  }));

  return (
    <Card className="bg-surface-light/20 border-border">
      <CardContent className="p-5">
        <h3 className="font-semibold text-white text-sm mb-4" style={FONT_OUTFIT}>Drawdown</h3>
        <ResponsiveContainer width="100%" height={200}>
          <AreaChart data={chartData}>
            <defs>
              <linearGradient id="ddGrad" x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%" stopColor="#ef4444" stopOpacity={0.3} />
                <stop offset="95%" stopColor="#ef4444" stopOpacity={0} />
              </linearGradient>
            </defs>
            <CartesianGrid strokeDasharray="3 3" stroke="#1f2937" />
            <XAxis dataKey="date" stroke="#4b5563" tick={{ fontSize: 10, fill: "#6b7280" }} />
            <YAxis stroke="#4b5563" tick={{ fontSize: 10, fill: "#6b7280" }} tickFormatter={(v) => `${v.toFixed(1)}%`} />
            <Tooltip contentStyle={CHART_TOOLTIP} labelStyle={{ color: "#9ca3af" }}
              formatter={(value: number) => [`${value.toFixed(2)}%`, "Drawdown"]} />
            <ReferenceLine y={0} stroke="#374151" />
            <Area type="monotone" dataKey="drawdown" stroke="#ef4444" strokeWidth={1.5} fill="url(#ddGrad)" dot={false} />
          </AreaChart>
        </ResponsiveContainer>
      </CardContent>
    </Card>
  );
}

// ── Daily P&L Bar Chart ─────────────────────────────────────────────

function DailyPnlChart({ data }: { data: DailyStats[] }) {
  if (!data.length) return null;

  const chartData = data.map((d) => ({
    date: new Date(d.date).toLocaleDateString("en-US", { month: "short", day: "numeric" }),
    pnl: d.daily_pnl,
    pnlPct: d.daily_pnl_pct,
    fill: d.daily_pnl >= 0 ? "#22c55e" : "#ef4444",
  }));

  return (
    <Card className="bg-surface-light/20 border-border">
      <CardContent className="p-5">
        <h3 className="font-semibold text-white text-sm mb-4" style={FONT_OUTFIT}>Daily P&L</h3>
        <ResponsiveContainer width="100%" height={200}>
          <BarChart data={chartData}>
            <CartesianGrid strokeDasharray="3 3" stroke="#1f2937" />
            <XAxis dataKey="date" stroke="#4b5563" tick={{ fontSize: 10, fill: "#6b7280" }} />
            <YAxis stroke="#4b5563" tick={{ fontSize: 10, fill: "#6b7280" }} tickFormatter={(v) => `$${v}`} />
            <Tooltip contentStyle={CHART_TOOLTIP} labelStyle={{ color: "#9ca3af" }}
              formatter={(value: number) => [formatCurrency(value), "P&L"]} />
            <ReferenceLine y={0} stroke="#374151" />
            <Bar dataKey="pnl" radius={[3, 3, 0, 0]}>
              {chartData.map((d, i) => (
                <rect key={i} fill={d.fill} />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </CardContent>
    </Card>
  );
}

// ── Open Positions ──────────────────────────────────────────────────

function OpenPositions({ positions }: { positions: Position[] }) {
  if (!positions.length) {
    return (
      <Card className="bg-surface-light/20 border-border">
        <CardContent className="p-5">
          <h3 className="font-semibold text-white text-sm mb-3" style={FONT_OUTFIT}>Open Positions</h3>
          <p className="text-gray-500 text-sm text-center py-6">No open positions</p>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card className="bg-surface-light/20 border-border">
      <CardContent className="p-5">
        <h3 className="font-semibold text-white text-sm mb-3" style={FONT_OUTFIT}>
          Open Positions <span className="text-gray-500 font-normal">({positions.length})</span>
        </h3>
        <div className="space-y-2">
          {positions.map((p) => (
            <div key={p.trade_id} className="flex items-center gap-3 px-3 py-2 rounded-lg bg-surface/50 border border-border">
              <span className="text-sm font-bold text-white" style={FONT_OUTFIT}>{p.ticker}</span>
              <Badge className={`text-[9px] px-1.5 py-0 ${p.direction === "long" ? "bg-profit/15 text-profit" : "bg-loss/15 text-loss"}`}>
                {p.direction.toUpperCase()}
              </Badge>
              <span className="text-[11px] text-gray-500 font-mono">{p.qty} @ {formatCurrency(p.entry_price)}</span>
              {p.current_price && (
                <span className="text-[11px] text-gray-500 font-mono">→ {formatCurrency(p.current_price)}</span>
              )}
              <div className="ml-auto text-right">
                {p.unrealized_pnl != null && (
                  <span className={`text-sm font-mono font-semibold ${pnlColor(p.unrealized_pnl)}`}>
                    {formatCurrency(p.unrealized_pnl)} ({formatPercent(p.unrealized_pnl_pct ?? 0)})
                  </span>
                )}
              </div>
            </div>
          ))}
        </div>
      </CardContent>
    </Card>
  );
}

// ── Trade History ───────────────────────────────────────────────────

function TradeHistorySection({ trades }: { trades: Trade[] }) {
  const [showAll, setShowAll] = useState(false);
  const displayed = showAll ? trades : trades.slice(0, 20);

  if (!trades.length) {
    return (
      <Card className="bg-surface-light/20 border-border">
        <CardContent className="p-5">
          <h3 className="font-semibold text-white text-sm mb-3" style={FONT_OUTFIT}>Trade History</h3>
          <p className="text-gray-500 text-sm text-center py-6">No trades yet</p>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card className="bg-surface-light/20 border-border">
      <CardContent className="p-5">
        <h3 className="font-semibold text-white text-sm mb-3" style={FONT_OUTFIT}>
          Trade History <span className="text-gray-500 font-normal">({trades.length})</span>
        </h3>
        <div className="overflow-x-auto">
          <table className="w-full text-[11px] font-mono">
            <thead>
              <tr className="text-gray-500 border-b border-border">
                <th className="text-left py-2 pr-3 font-medium">Ticker</th>
                <th className="text-left py-2 pr-3 font-medium">Dir</th>
                <th className="text-right py-2 pr-3 font-medium">Entry</th>
                <th className="text-right py-2 pr-3 font-medium">Exit</th>
                <th className="text-right py-2 pr-3 font-medium">P&L</th>
                <th className="text-left py-2 pr-3 font-medium">Reason</th>
                <th className="text-right py-2 font-medium">Date</th>
              </tr>
            </thead>
            <tbody>
              {displayed.map((t) => (
                <tr key={t.id} className="border-b border-border/50 hover:bg-surface-light/20">
                  <td className="py-2 pr-3 text-white font-semibold">{t.ticker}</td>
                  <td className="py-2 pr-3">
                    <span className={t.direction === "long" ? "text-profit" : "text-loss"}>
                      {t.direction.toUpperCase()}
                    </span>
                  </td>
                  <td className="py-2 pr-3 text-right text-gray-300">{formatCurrency(t.entry_price)}</td>
                  <td className="py-2 pr-3 text-right text-gray-300">{t.exit_price ? formatCurrency(t.exit_price) : "—"}</td>
                  <td className={`py-2 pr-3 text-right font-semibold ${pnlColor(t.pnl_percent ?? 0)}`}>
                    {t.pnl_percent != null ? formatPercent(t.pnl_percent) : "open"}
                  </td>
                  <td className="py-2 pr-3 text-gray-500">{t.exit_reason || "—"}</td>
                  <td className="py-2 text-right text-gray-500">
                    {t.exit_time ? formatDate(t.exit_time) : formatDate(t.entry_time)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {trades.length > 20 && (
          <button onClick={() => setShowAll(!showAll)}
            className="mt-3 text-xs text-ai-blue hover:text-ai-blue/80 transition" style={FONT_OUTFIT}>
            {showAll ? "Show less" : `Show all ${trades.length} trades`}
          </button>
        )}
      </CardContent>
    </Card>
  );
}

// ── Backtest Summary ────────────────────────────────────────────────

function BacktestSummary({ imports }: { imports: BacktestImportData[] }) {
  if (!imports.length) return null;

  return (
    <Card className="bg-surface-light/20 border-border">
      <CardContent className="p-5">
        <h3 className="font-semibold text-white text-sm mb-3" style={FONT_OUTFIT}>
          Backtest Intelligence <span className="text-gray-500 font-normal">({imports.length} imports)</span>
        </h3>
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
          {imports.map((imp) => (
            <div key={imp.id} className="rounded-lg bg-surface/50 border border-border p-3">
              <div className="flex items-center gap-2 mb-2">
                <span className="text-sm font-bold text-white" style={FONT_OUTFIT}>{imp.ticker}</span>
                <Badge className="text-[9px] px-1.5 py-0 bg-ai-blue/15 text-ai-blue">
                  {imp.strategy_name} {imp.strategy_version || ""}
                </Badge>
              </div>
              <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-[10px] font-mono">
                <div className="flex justify-between">
                  <span className="text-gray-500">Trades</span>
                  <span className="text-white">{imp.trade_count}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-gray-500">Win Rate</span>
                  <span className={imp.win_rate && imp.win_rate >= 50 ? "text-profit" : "text-loss"}>
                    {imp.win_rate?.toFixed(1) ?? "—"}%
                  </span>
                </div>
                <div className="flex justify-between">
                  <span className="text-gray-500">PF</span>
                  <span className="text-white">{imp.profit_factor?.toFixed(2) ?? "—"}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-gray-500">Avg Hold</span>
                  <span className="text-white">{imp.avg_hold_days?.toFixed(1) ?? "—"}d</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-gray-500">Avg Win</span>
                  <span className="text-profit">+{imp.avg_gain_pct?.toFixed(2) ?? "—"}%</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-gray-500">Avg Loss</span>
                  <span className="text-loss">{imp.avg_loss_pct?.toFixed(2) ?? "—"}%</span>
                </div>
                {imp.total_pnl_pct != null && (
                  <div className="flex justify-between col-span-2 pt-1 border-t border-border/50">
                    <span className="text-gray-500">Total</span>
                    <span className={imp.total_pnl_pct >= 0 ? "text-profit" : "text-loss"}>
                      {imp.total_pnl_pct >= 0 ? "+" : ""}{imp.total_pnl_pct}%
                    </span>
                  </div>
                )}
              </div>
            </div>
          ))}
        </div>
      </CardContent>
    </Card>
  );
}

// ── Henry Insights ──────────────────────────────────────────────────

function HenryInsights({ portfolioId }: { portfolioId: string }) {
  const [insight, setInsight] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [asked, setAsked] = useState(false);
  const [cachedAt, setCachedAt] = useState<string | null>(null);
  const { data: stats } = usePolling(() => api.getActionStats(), 60000);

  // Load cached health check on mount
  useEffect(() => {
    try {
      const cached = sessionStorage.getItem(`henry-health-${portfolioId}`);
      if (cached) {
        const parsed = JSON.parse(cached);
        if (parsed.answer && parsed.at) {
          // Only use if less than 4 hours old
          const age = Date.now() - new Date(parsed.at).getTime();
          if (age < 4 * 3600 * 1000) {
            setInsight(parsed.answer);
            setCachedAt(parsed.at);
            setAsked(true);
          }
        }
      }
    } catch {}
  }, [portfolioId]);

  const askHenry = async () => {
    setLoading(true);
    setAsked(true);
    try {
      const result = await api.postQuery(
        `Give me a portfolio health check for this specific portfolio. Analyze the actual holdings — their current performance, allocation balance, and any opportunities or risks you see. Offer constructive recommendations on positioning, not criticism. Keep it under 200 words.`,
        portfolioId
      );
      setInsight(result.answer);
      const now = new Date().toISOString();
      setCachedAt(now);
      // Cache in sessionStorage
      try { sessionStorage.setItem(`henry-health-${portfolioId}`, JSON.stringify({ answer: result.answer, at: now })); } catch {}
    } catch {
      setInsight("Henry is unavailable right now. Check back later.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <Card className="bg-surface-light/20 border-border border-ai-blue/20">
      <CardContent className="p-5">
        <div className="flex items-center justify-between mb-3">
          <h3 className="font-semibold text-white text-sm flex items-center gap-2" style={FONT_OUTFIT}>
            <span className="w-2 h-2 rounded-full bg-ai-blue animate-pulse" />
            Henry&apos;s Insights
          </h3>
          <div className="flex items-center gap-2">
            {cachedAt && <span className="text-[9px] text-gray-600 font-mono">{formatTimeAgo(cachedAt)}</span>}
            {stats && stats.pending_count > 0 && (
              <Badge className="text-[9px] bg-ai-blue/15 text-ai-blue">
                {stats.pending_count} pending action{stats.pending_count > 1 ? "s" : ""}
              </Badge>
            )}
          </div>
        </div>

        {stats && (
          <div className="flex items-center gap-4 text-[11px] font-mono text-gray-500 mb-3">
            <span>{stats.total_approved} approved</span>
            {stats.hit_rate != null && <span>hit rate: <span className="text-profit">{stats.hit_rate}%</span></span>}
            {stats.hit_rate_high_confidence != null && <span>high conf: <span className="text-profit">{stats.hit_rate_high_confidence}%</span></span>}
          </div>
        )}

        {!asked ? (
          <button onClick={askHenry}
            className="w-full py-3 rounded-lg text-sm text-ai-blue bg-ai-blue/10 hover:bg-ai-blue/15 border border-ai-blue/20 transition"
            style={FONT_OUTFIT}>
            Ask Henry for a portfolio health check
          </button>
        ) : loading ? (
          <div className="space-y-2 py-4">
            <div className="h-3 w-3/4 rounded bg-ai-blue/10 animate-pulse" />
            <div className="h-3 w-full rounded bg-ai-blue/10 animate-pulse" />
            <div className="h-3 w-2/3 rounded bg-ai-blue/10 animate-pulse" />
          </div>
        ) : insight ? (
          <>
            <div
              className="ai-prose text-sm text-gray-300 leading-relaxed"
              style={FONT_OUTFIT}
              dangerouslySetInnerHTML={{ __html: renderMarkdown(insight) }}
            />
            {cachedAt && (
              <button onClick={askHenry} disabled={loading}
                className="mt-3 text-[10px] text-ai-blue/60 hover:text-ai-blue transition disabled:opacity-50">
                Refresh analysis
              </button>
            )}
          </>
        ) : null}
      </CardContent>
    </Card>
  );
}

// ── Allocation Donut Chart ──────────────────────────────────────────

const ALLOC_COLORS = ["#6366f1", "#22c55e", "#fbbf24", "#ef4444", "#8b5cf6", "#06b6d4", "#f97316", "#ec4899", "#14b8a6", "#a855f7"];

function AllocationChart({ holdings }: { holdings: PortfolioHolding[] }) {
  if (!holdings.length) return null;

  const data = holdings
    .map((h) => ({
      ticker: h.ticker,
      value: (h.current_price ?? h.entry_price) * h.qty,
      pnlPct: h.unrealized_pnl_pct ?? 0,
    }))
    .sort((a, b) => b.value - a.value);

  const total = data.reduce((s, d) => s + d.value, 0);
  const rows = data.map((d, i) => ({
    ...d,
    pct: total > 0 ? (d.value / total * 100) : 0,
    color: ALLOC_COLORS[i % ALLOC_COLORS.length],
  }));

  return (
    <Card className="bg-surface-light/20 border-border">
      <CardContent className="p-5">
        <div className="flex items-center justify-between mb-3">
          <h3 className="font-semibold text-white text-sm" style={FONT_OUTFIT}>Portfolio Allocation</h3>
          <span className="text-[10px] text-gray-500 font-mono">{formatCurrency(total)} total</span>
        </div>

        {/* Full-width stacked horizontal bar */}
        <div className="flex h-8 rounded-lg overflow-hidden mb-5">
          {rows.map((r) => (
            <div
              key={r.ticker}
              style={{ width: `${Math.max(r.pct, 1.5)}%`, backgroundColor: r.color }}
              className="relative group transition-opacity hover:opacity-80"
              title={`${r.ticker}: ${r.pct.toFixed(1)}% (${formatCurrency(r.value)})`}
            >
              {r.pct >= 6 && (
                <span className="absolute inset-0 flex items-center justify-center text-[9px] font-mono font-bold text-white/90 truncate px-1">
                  {r.ticker} {r.pct.toFixed(0)}%
                </span>
              )}
            </div>
          ))}
        </div>

        {/* Horizontal legend rows */}
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-x-6 gap-y-1.5">
          {rows.map((r) => (
            <div key={r.ticker} className="flex items-center gap-2 text-[11px] font-mono py-0.5">
              <div className="w-2.5 h-2.5 rounded-sm shrink-0" style={{ backgroundColor: r.color }} />
              <span className="text-white font-semibold w-12">{r.ticker}</span>
              <span className="text-gray-400 w-12 text-right">{r.pct.toFixed(1)}%</span>
              <span className="text-gray-500 w-16 text-right">{formatCurrency(r.value)}</span>
              <span className={`w-14 text-right font-semibold ${pnlColor(r.pnlPct)}`}>
                {r.pnlPct >= 0 ? "+" : ""}{r.pnlPct.toFixed(1)}%
              </span>
            </div>
          ))}
        </div>
      </CardContent>
    </Card>
  );
}

// ── Holdings Performance Bar Chart ─────────────────────────────────

function HoldingsPerformanceBars({ holdings }: { holdings: PortfolioHolding[] }) {
  if (!holdings.length) return null;

  const chartData = holdings
    .filter((h) => h.unrealized_pnl_pct != null)
    .map((h) => ({
      ticker: h.ticker,
      pnlPct: h.unrealized_pnl_pct!,
      fill: (h.unrealized_pnl_pct ?? 0) >= 0 ? "#22c55e" : "#ef4444",
    }))
    .sort((a, b) => b.pnlPct - a.pnlPct);

  if (!chartData.length) return null;

  return (
    <Card className="bg-surface-light/20 border-border">
      <CardContent className="p-5">
        <h3 className="font-semibold text-white text-sm mb-4" style={FONT_OUTFIT}>Holdings Performance</h3>
        <ResponsiveContainer width="100%" height={Math.max(260, chartData.length * 36)}>
          <BarChart data={chartData} layout="vertical" margin={{ left: 10, right: 20 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#1f2937" horizontal={false} />
            <XAxis type="number" stroke="#4b5563" tick={{ fontSize: 10, fill: "#6b7280" }} tickFormatter={(v) => `${v}%`} />
            <YAxis type="category" dataKey="ticker" stroke="#4b5563" tick={{ fontSize: 11, fill: "#e5e7eb", fontFamily: "'JetBrains Mono', monospace" }} width={50} />
            <Tooltip
              contentStyle={CHART_TOOLTIP}
              labelStyle={{ color: "#9ca3af" }}
              formatter={(value: number) => [`${value.toFixed(2)}%`, "Unrealized P&L"]}
            />
            <ReferenceLine x={0} stroke="#374151" />
            <Bar dataKey="pnlPct" radius={[0, 4, 4, 0]}>
              {chartData.map((d, i) => (
                <Cell key={i} fill={d.fill} />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </CardContent>
    </Card>
  );
}

// ── Portfolio Value Over Time ───────────────────────────────────────

function PortfolioValueChart({ data }: { data: { date: string; value: number; cost_basis: number }[] }) {
  if (!data.length) {
    return (
      <Card className="bg-surface-light/20 border-border">
        <CardContent className="p-5">
          <h3 className="font-semibold text-white text-sm mb-3" style={FONT_OUTFIT}>Portfolio Value</h3>
          <p className="text-gray-500 text-sm text-center py-12">No holdings data yet — add holdings to see portfolio value over time</p>
        </CardContent>
      </Card>
    );
  }

  const chartData = data.map((d) => ({
    date: new Date(d.date).toLocaleDateString("en-US", { month: "short", day: "numeric" }),
    value: d.value,
    costBasis: d.cost_basis,
    pnl: d.value - d.cost_basis,
    pnlPct: d.cost_basis > 0 ? ((d.value - d.cost_basis) / d.cost_basis * 100) : 0,
  }));

  const latestPnl = chartData.length > 0 ? chartData[chartData.length - 1].pnl : 0;
  const latestPnlPct = chartData.length > 0 ? chartData[chartData.length - 1].pnlPct : 0;

  return (
    <Card className="bg-surface-light/20 border-border">
      <CardContent className="p-5">
        <div className="flex items-center justify-between mb-4">
          <h3 className="font-semibold text-white text-sm" style={FONT_OUTFIT}>Portfolio Value</h3>
          <div className="flex items-center gap-3 text-[11px] font-mono">
            <span className="text-gray-500">P&L:</span>
            <span className={latestPnl >= 0 ? "text-profit font-semibold" : "text-loss font-semibold"}>
              {latestPnl >= 0 ? "+" : ""}{formatCurrency(latestPnl)} ({latestPnlPct >= 0 ? "+" : ""}{latestPnlPct.toFixed(2)}%)
            </span>
          </div>
        </div>
        <ResponsiveContainer width="100%" height={320}>
          <AreaChart data={chartData}>
            <defs>
              <linearGradient id="portfolioValueGrad" x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%" stopColor="#6366f1" stopOpacity={0.3} />
                <stop offset="95%" stopColor="#6366f1" stopOpacity={0} />
              </linearGradient>
            </defs>
            <CartesianGrid strokeDasharray="3 3" stroke="#1f2937" />
            <XAxis dataKey="date" stroke="#4b5563" tick={{ fontSize: 10, fill: "#6b7280" }} />
            <YAxis stroke="#4b5563" tick={{ fontSize: 10, fill: "#6b7280" }} tickFormatter={(v) => `$${(v / 1000).toFixed(1)}k`} />
            <Tooltip
              contentStyle={CHART_TOOLTIP}
              labelStyle={{ color: "#9ca3af" }}
              formatter={(value: number, name: string) => [
                formatCurrency(value),
                name === "value" ? "Portfolio Value" : "Cost Basis",
              ]}
            />
            <Area type="monotone" dataKey="value" stroke="#6366f1" strokeWidth={2} fill="url(#portfolioValueGrad)" dot={false} name="value" />
            <Line type="monotone" dataKey="costBasis" stroke="#fbbf24" strokeWidth={1.5} strokeDasharray="6 3" dot={false} name="costBasis" />
          </AreaChart>
        </ResponsiveContainer>
        <div className="flex items-center gap-6 mt-3 text-[10px] text-gray-500" style={FONT_OUTFIT}>
          <div className="flex items-center gap-1.5">
            <div className="w-3 h-0.5 bg-ai-blue rounded" />
            <span>Portfolio Value</span>
          </div>
          <div className="flex items-center gap-1.5">
            <div className="w-3 h-0.5 bg-screener-amber rounded" style={{ borderStyle: "dashed" }} />
            <span>Cost Basis</span>
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

// ── Holdings Summary ────────────────────────────────────────────────

function HoldingsSummary({ holdings }: { holdings: PortfolioHolding[] }) {
  if (!holdings.length) return null;

  const totalValue = holdings.reduce((sum, h) => sum + (h.current_price ?? h.entry_price) * h.qty, 0);
  const totalUnrealized = holdings.reduce((sum, h) => sum + (h.unrealized_pnl ?? 0), 0);

  return (
    <Card className="bg-surface-light/20 border-border">
      <CardContent className="p-5">
        <div className="flex items-center justify-between mb-3">
          <h3 className="font-semibold text-white text-sm" style={FONT_OUTFIT}>
            Holdings <span className="text-gray-500 font-normal">({holdings.length})</span>
          </h3>
          <div className="text-right">
            <span className="text-xs text-gray-500 font-mono">{formatCurrency(totalValue)} </span>
            <span className={`text-xs font-mono font-semibold ${pnlColor(totalUnrealized)}`}>
              ({formatPercent(totalValue > 0 ? totalUnrealized / totalValue * 100 : 0)})
            </span>
          </div>
        </div>
        <div className="space-y-1.5">
          {holdings.map((h) => (
            <div key={h.id} className="flex items-center gap-3 text-[11px] font-mono py-1.5 border-b border-border/30 last:border-0">
              <span className="text-white font-semibold w-12">{h.ticker}</span>
              <Badge className={`text-[8px] px-1 py-0 ${h.direction === "long" ? "bg-profit/15 text-profit" : "bg-loss/15 text-loss"}`}>
                {h.direction.toUpperCase()}
              </Badge>
              <span className="text-gray-500">{h.qty} @ {formatCurrency(h.entry_price)}</span>
              <Badge className="text-[8px] px-1 py-0 bg-surface-light text-gray-400">{formatSource(h.source)}</Badge>
              <div className="ml-auto">
                {h.unrealized_pnl != null && (
                  <span className={`font-semibold ${pnlColor(h.unrealized_pnl)}`}>
                    {formatPercent(h.unrealized_pnl_pct ?? 0)}
                  </span>
                )}
              </div>
            </div>
          ))}
        </div>
      </CardContent>
    </Card>
  );
}

// ── Action Queue (inline) ─────────────────────────────────────────

function ActionQueue({ portfolioId }: { portfolioId: string }) {
  const [actions, setActions] = useState<PortfolioAction[]>([]);
  const [filter, setFilter] = useState("pending");
  const [loading, setLoading] = useState(true);

  const fetchActions = useCallback(async () => {
    try {
      const data = await api.getActions(filter, portfolioId);
      setActions(data);
    } catch {}
    setLoading(false);
  }, [filter, portfolioId]);

  useEffect(() => { fetchActions(); }, [fetchActions]);

  const handleApprove = async (id: string) => {
    try { await api.approveAction(id); fetchActions(); } catch {}
  };
  const handleReject = async (id: string) => {
    try { await api.rejectAction(id); fetchActions(); } catch {}
  };

  return (
    <Card className="bg-surface-light/20 border-border">
      <CardContent className="p-5">
        <div className="flex items-center gap-3 mb-4">
          <h3 className="font-semibold text-white text-sm" style={FONT_OUTFIT}>Henry&apos;s Recommendations</h3>
          <div className="flex rounded-md overflow-hidden border border-border ml-auto">
            {["pending", "approved", "rejected", "all"].map((f) => (
              <button key={f} onClick={() => setFilter(f)}
                className={`px-2.5 py-1 text-[10px] font-medium capitalize ${filter === f ? "bg-ai-blue/20 text-ai-blue" : "bg-surface-light/30 text-gray-500 hover:text-gray-300"}`}>{f}</button>
            ))}
          </div>
        </div>
        {loading ? <Skeleton className="h-20 rounded-lg" /> : actions.length === 0 ? (
          <p className="text-xs text-gray-500 text-center py-6">No {filter} actions</p>
        ) : (
          <div className="space-y-2 max-h-80 overflow-y-auto">
            {actions.map((a) => (
              <div key={a.id} className="flex items-start gap-3 p-3 rounded-lg border border-border/40 bg-surface/50">
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 mb-1">
                    <span className="text-sm font-bold text-white">{a.ticker}</span>
                    <Badge className={`text-[9px] ${a.action_type === "BUY" || a.action_type === "ADD" ? "bg-profit/15 text-profit" : a.action_type === "CLOSE" || a.action_type === "SELL" ? "bg-loss/15 text-loss" : "bg-amber-500/15 text-amber-400"}`}>{a.action_type}</Badge>
                    <span className="text-[10px] text-gray-500 font-mono">conf {a.confidence}/10</span>
                    <span className="text-[10px] text-gray-600">{formatTimeAgo(a.created_at)}</span>
                  </div>
                  <p className="text-xs text-gray-400">{a.reasoning}</p>
                </div>
                {a.status === "pending" && (
                  <div className="flex flex-col gap-1">
                    <Button size="sm" onClick={() => handleApprove(a.id)} className="text-[10px] h-6 bg-profit/20 text-profit border-profit/20 hover:bg-profit/30">Approve</Button>
                    <Button size="sm" onClick={() => handleReject(a.id)} className="text-[10px] h-6 bg-loss/20 text-loss border-loss/20 hover:bg-loss/30">Reject</Button>
                  </div>
                )}
                {a.status !== "pending" && (
                  <Badge className={`text-[9px] ${a.status === "approved" ? "bg-profit/15 text-profit" : a.status === "rejected" ? "bg-loss/15 text-loss" : "bg-gray-600/20 text-gray-500"}`}>{a.status}</Badge>
                )}
              </div>
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

// ── Holdings CRUD (inline) ──────────────────────────────────────────

function PositionsManager({ portfolioId, holdings, positions, onRefresh, executionMode }: {
  portfolioId: string;
  holdings: PortfolioHolding[];
  positions: Position[];
  onRefresh: () => void;
  executionMode?: string;
}) {
  const [mode, setMode] = useState<"buy" | "sell" | null>(null);
  const [ticker, setTicker] = useState("");
  const [dir, setDir] = useState<"long" | "short">("long");
  const [price, setPrice] = useState("");
  const [qty, setQty] = useState("");
  const [submitting, setSubmitting] = useState(false);

  // Execution confirmation
  const [confirmingOrder, setConfirmingOrder] = useState(false);
  const [pendingOrder, setPendingOrder] = useState<{ side: "buy" | "sell"; ticker: string; qty: number; price?: number } | null>(null);
  const [orderResult, setOrderResult] = useState<OrderResult | null>(null);

  // Position archetype fields
  const [positionType, setPositionType] = useState<"momentum" | "accumulation" | "catalyst" | "conviction">("momentum");
  const [thesis, setThesis] = useState("");
  const [catalystDate, setCatalystDate] = useState("");
  const [catalystDescription, setCatalystDescription] = useState("");
  const [maxAllocationPct, setMaxAllocationPct] = useState("");
  const [dcaEnabled, setDcaEnabled] = useState(false);
  const [dcaThresholdPct, setDcaThresholdPct] = useState("");

  // Import state
  const [importStep, setImportStep] = useState<"idle" | "upload" | "mapping" | "preview" | "result">("idle");
  const [importFile, setImportFile] = useState<File | null>(null);
  const [importPreview, setImportPreview] = useState<ImportPreview | null>(null);
  const [importResult, setImportResult] = useState<ImportResult | null>(null);
  const [importLoading, setImportLoading] = useState(false);
  const [importError, setImportError] = useState("");
  const [dragOver, setDragOver] = useState(false);
  const [columnMapping, setColumnMapping] = useState<Record<string, string>>({ date: "", ticker: "", action: "", qty: "", price: "" });

  const [editingId, setEditingId] = useState<string | null>(null);
  const [editFields, setEditFields] = useState<Record<string, unknown>>({});

  // Fetch recent actions for notification tooltips
  const [actions, setActions] = useState<import("@/lib/types").PortfolioAction[]>([]);
  useEffect(() => {
    api.getActions("all", portfolioId).then(setActions).catch(() => {});
  }, [portfolioId]);

  const [hoveredAlert, setHoveredAlert] = useState<string | null>(null);

  const totalValue = holdings.reduce((sum, h) => sum + (h.current_price ?? h.entry_price) * h.qty, 0);
  const totalUnrealized = holdings.reduce((sum, h) => sum + (h.unrealized_pnl ?? 0), 0);

  // Deduplicate: filter out strategy positions that already exist as manual holdings
  const holdingTickers = new Set(holdings.map((h) => `${h.ticker}:${h.direction}`));
  const uniquePositions = positions.filter((p) => !holdingTickers.has(`${p.ticker}:${p.direction}`));
  const totalCount = holdings.length + uniquePositions.length;

  // Build a map of recent actions per ticker for notification tooltips
  const actionsByTicker: Record<string, typeof actions[number][]> = {};
  if (actions.length > 0) {
    for (const a of actions) {
      if (!actionsByTicker[a.ticker]) actionsByTicker[a.ticker] = [];
      if (actionsByTicker[a.ticker].length < 3) actionsByTicker[a.ticker].push(a);
    }
  }

  // Allocation helper
  const getAllocation = (price: number, qty: number) => {
    if (totalValue <= 0) return 0;
    return ((price * qty) / totalValue) * 100;
  };

  const isAlpacaMode = executionMode === "paper" || executionMode === "live";

  const executeAlpacaOrder = async (side: "buy" | "sell", orderTicker: string, orderQty: number) => {
    setSubmitting(true); setOrderResult(null);
    try {
      const result = await api.submitOrder({ portfolio_id: portfolioId, ticker: orderTicker, qty: orderQty, side });
      setOrderResult(result);
      setTicker(""); setPrice(""); setQty(""); setMode(null);
      setPositionType("momentum"); setThesis(""); setCatalystDate(""); setCatalystDescription("");
      setMaxAllocationPct(""); setDcaEnabled(false); setDcaThresholdPct("");
      onRefresh();
    } catch { setOrderResult({ status: "error", message: "Order submission failed" }); }
    setSubmitting(false); setConfirmingOrder(false); setPendingOrder(null);
  };

  const handleBuy = async () => {
    if (!ticker || !price || !qty) return;
    if (isAlpacaMode) {
      setPendingOrder({ side: "buy", ticker: ticker.toUpperCase(), qty: parseFloat(qty), price: parseFloat(price) });
      setConfirmingOrder(true);
      return;
    }
    setSubmitting(true);
    try {
      const payload: Parameters<typeof api.createHolding>[0] = {
        portfolio_id: portfolioId,
        ticker: ticker.toUpperCase(),
        direction: dir,
        entry_price: parseFloat(price),
        qty: parseFloat(qty),
        entry_date: new Date().toISOString().slice(0, 10),
        position_type: positionType,
      };
      if (positionType !== "momentum") {
        if (thesis) payload.thesis = thesis;
        if (maxAllocationPct) payload.max_allocation_pct = parseFloat(maxAllocationPct);
      }
      if (positionType === "catalyst") {
        if (catalystDate) payload.catalyst_date = catalystDate;
        if (catalystDescription) payload.catalyst_description = catalystDescription;
      }
      if (positionType === "accumulation") {
        payload.dca_enabled = dcaEnabled;
        if (dcaEnabled && dcaThresholdPct) payload.dca_threshold_pct = parseFloat(dcaThresholdPct);
      }
      await api.createHolding(payload);
      setTicker(""); setPrice(""); setQty(""); setMode(null);
      setPositionType("momentum"); setThesis(""); setCatalystDate(""); setCatalystDescription("");
      setMaxAllocationPct(""); setDcaEnabled(false); setDcaThresholdPct("");
      onRefresh();
    } catch {}
    setSubmitting(false);
  };

  const handleSell = async () => {
    if (!ticker || !qty) return;
    if (isAlpacaMode) {
      setPendingOrder({ side: "sell", ticker: ticker.toUpperCase(), qty: parseFloat(qty) });
      setConfirmingOrder(true);
      return;
    }
    setSubmitting(true);
    const t = ticker.toUpperCase();
    const matching = holdings.find((h) => h.ticker === t && h.is_active);
    if (matching) {
      const sellQty = parseFloat(qty);
      if (sellQty >= matching.qty) {
        try { await api.deleteHolding(matching.id); } catch {}
      } else {
        try {
          await api.updateHolding(matching.id, { qty: matching.qty - sellQty });
        } catch {}
      }
    }
    setTicker(""); setPrice(""); setQty(""); setMode(null);
    onRefresh();
    setSubmitting(false);
  };

  const handleDelete = async (id: string) => {
    if (!confirm("Remove this position?")) return;
    try { await api.deleteHolding(id); onRefresh(); } catch {}
  };

  const startEdit = (h: PortfolioHolding) => {
    setEditingId(h.id);
    setEditFields({
      position_type: h.position_type || "momentum",
      thesis: h.thesis || "",
      catalyst_date: h.catalyst_date || "",
      catalyst_description: h.catalyst_description || "",
      max_allocation_pct: h.max_allocation_pct || "",
      dca_enabled: h.dca_enabled || false,
      dca_threshold_pct: h.dca_threshold_pct || "",
    });
  };

  const saveEdit = async () => {
    if (!editingId) return;
    setSubmitting(true);
    try {
      const updates: Record<string, unknown> = { position_type: editFields.position_type };
      if (editFields.thesis) updates.thesis = editFields.thesis;
      else updates.thesis = null;
      if (editFields.catalyst_date) updates.catalyst_date = editFields.catalyst_date;
      else updates.catalyst_date = null;
      if (editFields.catalyst_description) updates.catalyst_description = editFields.catalyst_description;
      else updates.catalyst_description = null;
      if (editFields.max_allocation_pct) updates.max_allocation_pct = parseFloat(String(editFields.max_allocation_pct));
      else updates.max_allocation_pct = null;
      updates.dca_enabled = !!editFields.dca_enabled;
      if (editFields.dca_threshold_pct) updates.dca_threshold_pct = parseFloat(String(editFields.dca_threshold_pct));
      else updates.dca_threshold_pct = null;
      await api.updateHolding(editingId, updates);
      setEditingId(null);
      onRefresh();
    } catch {}
    setSubmitting(false);
  };

  // ── Import handlers ──
  const resetImport = () => {
    setImportStep("idle");
    setImportFile(null);
    setImportPreview(null);
    setImportResult(null);
    setImportLoading(false);
    setImportError("");
    setDragOver(false);
    setColumnMapping({ date: "", ticker: "", action: "", qty: "", price: "" });
  };

  const handleFileSelect = async (file: File) => {
    setImportFile(file);
    setImportError("");
    setImportLoading(true);
    try {
      const preview = await api.previewImportTrades(file);
      setImportPreview(preview);
      if (preview.status === "needs_mapping") {
        // Pre-fill column mapping with best guesses
        const headers = preview.headers || [];
        const lowerHeaders = headers.map(h => h.toLowerCase());
        const guessMap: Record<string, string[]> = {
          date: ["date", "activity date", "run date", "transaction date", "create time", "trade date"],
          ticker: ["symbol", "instrument", "ticker", "stock"],
          action: ["action", "trans code", "side", "transaction type", "type"],
          qty: ["quantity", "qty", "shares", "amount"],
          price: ["price", "cost", "price ($)", "cost per share"],
        };
        const newMapping: Record<string, string> = { date: "", ticker: "", action: "", qty: "", price: "" };
        for (const [field, candidates] of Object.entries(guessMap)) {
          for (const c of candidates) {
            const idx = lowerHeaders.indexOf(c);
            if (idx !== -1) {
              newMapping[field] = headers[idx];
              break;
            }
          }
        }
        setColumnMapping(newMapping);
        setImportStep("mapping");
      } else {
        setImportStep("preview");
      }
    } catch (e: unknown) {
      setImportError(e instanceof Error ? e.message : "Failed to parse CSV");
      setImportStep("upload");
    }
    setImportLoading(false);
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setDragOver(false);
    const file = e.dataTransfer.files?.[0];
    if (file && file.name.toLowerCase().endsWith(".csv")) {
      handleFileSelect(file);
    } else {
      setImportError("Please drop a CSV file");
    }
  };

  const handleParseWithMapping = async () => {
    if (!importFile) return;
    const missingFields = Object.entries(columnMapping).filter(([, v]) => !v);
    if (missingFields.length > 0) {
      setImportError(`Please map all columns: ${missingFields.map(([k]) => k).join(", ")}`);
      return;
    }
    setImportLoading(true);
    setImportError("");
    try {
      const preview = await api.parseWithMapping(importFile, columnMapping);
      setImportPreview(preview);
      setImportStep("preview");
    } catch (e: unknown) {
      setImportError(e instanceof Error ? e.message : "Failed to parse with mapping");
    }
    setImportLoading(false);
  };

  const handleConfirmImport = async () => {
    if (!importPreview?.trades) return;
    setImportLoading(true);
    setImportError("");
    try {
      const result = await api.confirmImportTrades({
        portfolio_id: portfolioId,
        trades: importPreview.trades,
      });
      setImportResult(result);
      setImportStep("result");
      onRefresh();
    } catch (e: unknown) {
      setImportError(e instanceof Error ? e.message : "Import failed");
    }
    setImportLoading(false);
  };

  const toggleImport = () => {
    if (importStep !== "idle") {
      resetImport();
    } else {
      setMode(null);
      setImportStep("upload");
    }
  };

  return (
    <Card className="bg-surface-light/20 border-border">
      <CardContent className="p-5">
        <div className="flex items-center justify-between mb-3">
          <h3 className="font-semibold text-white text-sm" style={FONT_OUTFIT}>
            Positions <span className="text-gray-500 font-normal">({totalCount})</span>
          </h3>
          <div className="flex items-center gap-2">
            {totalValue > 0 && (
              <span className={`text-xs font-mono ${pnlColor(totalUnrealized)}`}>
                {formatCurrency(totalValue)} ({formatPercent(totalValue > 0 ? totalUnrealized / totalValue * 100 : 0)})
              </span>
            )}
            {isAlpacaMode && (
              <Badge className={`text-[9px] ${executionMode === "live" ? "bg-loss/15 text-loss" : "bg-screener-amber/15 text-screener-amber"}`}>
                {executionMode === "live" ? "LIVE" : "PAPER"}
              </Badge>
            )}
            <Button size="sm" onClick={() => { setMode(mode === "buy" ? null : "buy"); if (importStep !== "idle") resetImport(); setConfirmingOrder(false); setOrderResult(null); }}
              className={`text-[10px] h-7 ${mode === "buy" ? "bg-profit text-white" : "bg-profit/10 text-profit border-profit/20 hover:bg-profit/20"}`}>
              {mode === "buy" ? "Cancel" : "Buy"}
            </Button>
            <Button size="sm" onClick={() => { setMode(mode === "sell" ? null : "sell"); if (importStep !== "idle") resetImport(); setConfirmingOrder(false); setOrderResult(null); }}
              className={`text-[10px] h-7 ${mode === "sell" ? "bg-loss text-white" : "bg-loss/10 text-loss border-loss/20 hover:bg-loss/20"}`}>
              {mode === "sell" ? "Cancel" : "Sell"}
            </Button>
            <Button size="sm" onClick={toggleImport}
              className={`text-[10px] h-7 ${importStep !== "idle" ? "bg-white text-gray-900" : "bg-white/10 text-white border-white/20 hover:bg-white/20"}`}>
              {importStep !== "idle" ? "Cancel Import" : "Import"}
            </Button>
          </div>
        </div>

        {/* Buy Form */}
        {mode === "buy" && (
          <div className="mb-4 p-3 rounded-lg border border-profit/20 bg-profit/5">
            <div className="text-[9px] text-profit font-semibold uppercase tracking-wider w-full mb-2" style={FONT_OUTFIT}>Buy Position</div>
            <div className="flex flex-wrap gap-2 mb-2">
              <Input value={ticker} onChange={(e) => setTicker(e.target.value)} placeholder="Ticker" className="w-20 h-8 text-xs font-mono bg-surface-light/30" />
              <div className="flex rounded-md overflow-hidden border border-border">
                <button onClick={() => setDir("long")} className={`px-2.5 py-1 text-[10px] ${dir === "long" ? "bg-profit/20 text-profit" : "bg-surface-light/30 text-gray-500"}`}>Long</button>
                <button onClick={() => setDir("short")} className={`px-2.5 py-1 text-[10px] ${dir === "short" ? "bg-loss/20 text-loss" : "bg-surface-light/30 text-gray-500"}`}>Short</button>
              </div>
              <Input value={price} onChange={(e) => setPrice(e.target.value)} placeholder="Price" type="number" step="0.01" className="w-24 h-8 text-xs font-mono bg-surface-light/30" />
              <Input value={qty} onChange={(e) => setQty(e.target.value)} placeholder="Shares" type="number" step="0.001" className="w-24 h-8 text-xs font-mono bg-surface-light/30" />
              <select value={positionType} onChange={(e) => setPositionType(e.target.value as typeof positionType)}
                className="h-8 text-[10px] font-mono bg-surface-light/30 border border-border rounded-md px-2 text-white appearance-none">
                <option value="momentum">Momentum</option>
                <option value="accumulation">Accumulation</option>
                <option value="catalyst">Catalyst</option>
                <option value="conviction">Conviction</option>
              </select>
              <Button size="sm" onClick={handleBuy} disabled={submitting || !ticker || !price || !qty}
                className="h-8 text-[10px] bg-profit hover:bg-profit/80">
                {submitting ? "..." : "Buy"}
              </Button>
            </div>

            {/* Progressive disclosure — extra fields for non-momentum types */}
            {positionType !== "momentum" && (
              <div className="flex flex-wrap gap-2 mt-2 pt-2 border-t border-profit/10">
                <textarea value={thesis} onChange={(e) => setThesis(e.target.value)} placeholder="Why are you in this position?"
                  rows={2} className="flex-1 min-w-[200px] text-[10px] font-mono bg-surface-light/30 border border-border rounded-md px-2 py-1.5 text-white resize-none placeholder:text-gray-600" />

                {positionType === "catalyst" && (
                  <>
                    <Input value={catalystDate} onChange={(e) => setCatalystDate(e.target.value)} type="date"
                      className="w-36 h-8 text-[10px] font-mono bg-surface-light/30" />
                    <Input value={catalystDescription} onChange={(e) => setCatalystDescription(e.target.value)}
                      placeholder="e.g., Phase 3 readout, FDA PDUFA" className="w-52 h-8 text-[10px] font-mono bg-surface-light/30" />
                  </>
                )}

                {(positionType === "accumulation" || positionType === "catalyst") && (
                  <Input value={maxAllocationPct} onChange={(e) => setMaxAllocationPct(e.target.value)}
                    placeholder="Max alloc %" type="number" step="0.5" className="w-24 h-8 text-[10px] font-mono bg-surface-light/30" />
                )}

                {positionType === "accumulation" && (
                  <>
                    <label className="flex items-center gap-1.5 cursor-pointer">
                      <input type="checkbox" checked={dcaEnabled} onChange={(e) => setDcaEnabled(e.target.checked)}
                        className="w-3 h-3 rounded border-border bg-surface-light/30 accent-ai-blue" />
                      <span className="text-[9px] text-gray-400">DCA</span>
                    </label>
                    {dcaEnabled && (
                      <Input value={dcaThresholdPct} onChange={(e) => setDcaThresholdPct(e.target.value)}
                        placeholder="DCA threshold %" type="number" step="1" className="w-28 h-8 text-[10px] font-mono bg-surface-light/30" />
                    )}
                  </>
                )}
              </div>
            )}
          </div>
        )}

        {/* Sell Form */}
        {mode === "sell" && (
          <div className="flex flex-wrap gap-2 mb-4 p-3 rounded-lg border border-loss/20 bg-loss/5">
            <div className="text-[9px] text-loss font-semibold uppercase tracking-wider w-full mb-1" style={FONT_OUTFIT}>Sell Position</div>
            <Input value={ticker} onChange={(e) => setTicker(e.target.value)} placeholder="Ticker" className="w-20 h-8 text-xs font-mono bg-surface-light/30" />
            <Input value={price} onChange={(e) => setPrice(e.target.value)} placeholder="Price (optional)" type="number" step="0.01" className="w-28 h-8 text-xs font-mono bg-surface-light/30" />
            <Input value={qty} onChange={(e) => setQty(e.target.value)} placeholder="Shares" type="number" step="0.001" className="w-24 h-8 text-xs font-mono bg-surface-light/30" />
            <Button size="sm" onClick={handleSell} disabled={submitting || !ticker || !qty}
              className="h-8 text-[10px] bg-loss hover:bg-loss/80">
              {submitting ? "..." : "Sell"}
            </Button>
          </div>
        )}

        {/* ── Alpaca Order Confirmation ── */}
        {confirmingOrder && pendingOrder && (
          <div className="mb-4 p-3 rounded-lg border border-screener-amber/30 bg-screener-amber/5">
            <div className="text-xs text-screener-amber font-semibold mb-2" style={FONT_OUTFIT}>
              Confirm {executionMode === "live" ? "LIVE" : "PAPER"} Order
            </div>
            <p className="text-xs text-gray-300 mb-3" style={FONT_MONO}>
              {pendingOrder.side.toUpperCase()} {pendingOrder.qty} {pendingOrder.ticker} @ market
              {executionMode === "live" ? " — REAL MONEY" : " — paper account"}
            </p>
            <div className="flex items-center gap-2">
              <Button size="sm" onClick={() => executeAlpacaOrder(pendingOrder.side, pendingOrder.ticker, pendingOrder.qty)}
                disabled={submitting}
                className={`text-[10px] h-7 ${pendingOrder.side === "buy" ? "bg-profit hover:bg-profit/80" : "bg-loss hover:bg-loss/80"}`}>
                {submitting ? "Submitting..." : `Confirm ${pendingOrder.side.toUpperCase()}`}
              </Button>
              <Button size="sm" variant="ghost" onClick={() => { setConfirmingOrder(false); setPendingOrder(null); }}
                className="text-[10px] h-7">Cancel</Button>
            </div>
          </div>
        )}

        {/* ── Order Result ── */}
        {orderResult && (
          <div className={`mb-4 p-3 rounded-lg border text-xs font-mono ${
            orderResult.status === "error" ? "border-loss/30 bg-loss/5 text-loss" :
            "border-profit/30 bg-profit/5 text-profit"
          }`}>
            {orderResult.status === "error" ? (
              <span>{orderResult.message}</span>
            ) : (
              <div className="space-y-1">
                <div>Order {orderResult.status}: {orderResult.side?.toUpperCase()} {orderResult.qty} {orderResult.symbol} @ market</div>
                {orderResult.fill?.filled_price && (
                  <div>Filled @ ${orderResult.fill.filled_price.toFixed(2)} | Qty: {orderResult.fill.filled_qty}</div>
                )}
                {orderResult.holding_updated && <div className="text-gray-400">Holdings updated</div>}
                <div className="text-gray-500">Mode: {orderResult.mode || "unknown"} | Order ID: {orderResult.order_id?.slice(0, 8)}...</div>
              </div>
            )}
            <button onClick={() => setOrderResult(null)} className="text-[9px] text-gray-500 hover:text-gray-300 mt-1 underline">dismiss</button>
          </div>
        )}

        {/* ── Import Flow ── */}
        {importStep === "upload" && (
          <div className="mb-4 p-4 rounded-lg border border-dashed border-border bg-surface-light/5 transition-colors"
            onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
            onDragLeave={() => setDragOver(false)}
            onDrop={handleDrop}
            style={{ borderColor: dragOver ? "#6366f1" : undefined, backgroundColor: dragOver ? "rgba(99,102,241,0.05)" : undefined }}>
            <div className="text-center py-6">
              <svg className="w-8 h-8 mx-auto mb-3 text-gray-500" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M3 16.5v2.25A2.25 2.25 0 005.25 21h13.5A2.25 2.25 0 0021 18.75V16.5m-13.5-9L12 3m0 0l4.5 4.5M12 3v13.5" />
              </svg>
              <p className="text-sm text-gray-300 mb-1" style={FONT_OUTFIT}>Drop your brokerage CSV here</p>
              <p className="text-[10px] text-gray-500 mb-3" style={FONT_OUTFIT}>or click to browse</p>
              <input type="file" accept=".csv" className="hidden" id="csv-import-input"
                onChange={(e) => { const f = e.target.files?.[0]; if (f) handleFileSelect(f); }} />
              <label htmlFor="csv-import-input">
                <Button size="sm" className="text-[10px] h-7 bg-white/10 text-white hover:bg-white/20 cursor-pointer" asChild>
                  <span>Choose File</span>
                </Button>
              </label>
              <p className="text-[9px] text-gray-600 mt-3" style={FONT_OUTFIT}>
                Supported: Robinhood, Schwab, Fidelity, Webull, E*Trade — or map columns manually
              </p>
              {importLoading && <p className="text-[10px] text-ai-blue mt-2 animate-pulse">Parsing CSV...</p>}
              {importError && <p className="text-[10px] text-loss mt-2">{importError}</p>}
            </div>
          </div>
        )}

        {importStep === "mapping" && importPreview && (
          <div className="mb-4 p-4 rounded-lg border border-border bg-surface-light/10">
            <div className="text-[9px] text-screener-amber font-semibold uppercase tracking-wider mb-3" style={FONT_OUTFIT}>
              Column Mapping Required
            </div>
            <p className="text-xs text-gray-400 mb-3" style={FONT_OUTFIT}>
              Could not auto-detect your brokerage format. Please map the columns:
            </p>

            <div className="grid grid-cols-2 sm:grid-cols-5 gap-2 mb-4">
              {(["date", "ticker", "action", "qty", "price"] as const).map((field) => (
                <div key={field}>
                  <label className="text-[9px] text-gray-500 uppercase tracking-wider block mb-1" style={FONT_OUTFIT}>
                    {field === "qty" ? "Quantity" : field.charAt(0).toUpperCase() + field.slice(1)}
                  </label>
                  <select
                    value={columnMapping[field]}
                    onChange={(e) => setColumnMapping((m) => ({ ...m, [field]: e.target.value }))}
                    className="w-full h-8 text-[10px] font-mono bg-surface-light/30 border border-border rounded-md px-2 text-white appearance-none"
                  >
                    <option value="">Select...</option>
                    {(importPreview.headers || []).map((h) => (
                      <option key={h} value={h}>{h}</option>
                    ))}
                  </select>
                </div>
              ))}
            </div>

            {/* Sample rows */}
            {importPreview.sample_rows && importPreview.sample_rows.length > 0 && (
              <div className="mb-3">
                <div className="text-[9px] text-gray-500 uppercase tracking-wider mb-1" style={FONT_OUTFIT}>Sample Data</div>
                <div className="overflow-x-auto">
                  <table className="w-full text-[10px] font-mono">
                    <thead>
                      <tr className="border-b border-border">
                        {Object.keys(importPreview.sample_rows[0]).map((k) => (
                          <th key={k} className="text-left text-gray-500 px-2 py-1 font-normal">{k}</th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {importPreview.sample_rows.map((row, i) => (
                        <tr key={i} className="border-b border-border/50">
                          {Object.values(row).map((v, j) => (
                            <td key={j} className="text-gray-300 px-2 py-1 truncate max-w-[120px]">{v}</td>
                          ))}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}

            <div className="flex items-center gap-2">
              <Button size="sm" onClick={handleParseWithMapping} disabled={importLoading}
                className="h-7 text-[10px] bg-ai-blue hover:bg-ai-blue/80 text-white">
                {importLoading ? "Parsing..." : "Parse & Preview"}
              </Button>
              <Button size="sm" onClick={resetImport} className="h-7 text-[10px] bg-white/10 text-white hover:bg-white/20">
                Cancel
              </Button>
            </div>
            {importError && <p className="text-[10px] text-loss mt-2">{importError}</p>}
          </div>
        )}

        {importStep === "preview" && importPreview && importPreview.trades && (
          <div className="mb-4 p-4 rounded-lg border border-border bg-surface-light/10">
            <div className="flex items-center gap-2 mb-3">
              <div className="text-[9px] text-profit font-semibold uppercase tracking-wider" style={FONT_OUTFIT}>
                Import Preview
              </div>
              {importPreview.brokerage && (
                <Badge className="text-[8px] px-1.5 py-0 bg-ai-blue/15 text-ai-blue">
                  {importPreview.brokerage}
                </Badge>
              )}
            </div>

            {/* Summary card */}
            {importPreview.summary && (
              <div className="flex flex-wrap gap-4 mb-4 p-3 rounded-md bg-surface-light/20 border border-border">
                <div>
                  <div className="text-[9px] text-gray-500 uppercase" style={FONT_OUTFIT}>Trades</div>
                  <div className="text-sm font-mono text-white font-semibold">{importPreview.summary.total_trades}</div>
                </div>
                <div>
                  <div className="text-[9px] text-gray-500 uppercase" style={FONT_OUTFIT}>Buys</div>
                  <div className="text-sm font-mono text-profit font-semibold">{importPreview.summary.buys}</div>
                </div>
                <div>
                  <div className="text-[9px] text-gray-500 uppercase" style={FONT_OUTFIT}>Sells</div>
                  <div className="text-sm font-mono text-loss font-semibold">{importPreview.summary.sells}</div>
                </div>
                <div>
                  <div className="text-[9px] text-gray-500 uppercase" style={FONT_OUTFIT}>Tickers</div>
                  <div className="text-sm font-mono text-white font-semibold">{importPreview.summary.tickers.length}</div>
                </div>
                <div>
                  <div className="text-[9px] text-gray-500 uppercase" style={FONT_OUTFIT}>Date Range</div>
                  <div className="text-[10px] font-mono text-gray-300">{importPreview.summary.date_range}</div>
                </div>
              </div>
            )}

            {/* Trade table (first 20) */}
            <div className="overflow-x-auto max-h-[320px] overflow-y-auto mb-3">
              <table className="w-full text-[10px] font-mono">
                <thead className="sticky top-0 bg-surface-light/30">
                  <tr className="border-b border-border">
                    <th className="text-left text-gray-500 px-2 py-1.5 font-normal">Date</th>
                    <th className="text-left text-gray-500 px-2 py-1.5 font-normal">Ticker</th>
                    <th className="text-left text-gray-500 px-2 py-1.5 font-normal">Action</th>
                    <th className="text-right text-gray-500 px-2 py-1.5 font-normal">Qty</th>
                    <th className="text-right text-gray-500 px-2 py-1.5 font-normal">Price</th>
                    <th className="text-right text-gray-500 px-2 py-1.5 font-normal">Amount</th>
                  </tr>
                </thead>
                <tbody>
                  {importPreview.trades.slice(0, 20).map((t, i) => (
                    <tr key={i} className="border-b border-border/30 hover:bg-surface-light/10">
                      <td className="text-gray-400 px-2 py-1.5">{t.date}</td>
                      <td className="text-white font-semibold px-2 py-1.5">{t.ticker}</td>
                      <td className="px-2 py-1.5">
                        <span className={`inline-block px-1.5 py-0.5 rounded text-[8px] font-semibold uppercase ${
                          t.action === "buy" ? "bg-profit/15 text-profit" : "bg-loss/15 text-loss"
                        }`}>{t.action}</span>
                      </td>
                      <td className="text-gray-300 text-right px-2 py-1.5">{t.qty}</td>
                      <td className="text-gray-300 text-right px-2 py-1.5">${t.price.toFixed(2)}</td>
                      <td className="text-gray-300 text-right px-2 py-1.5">${t.amount.toFixed(2)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {importPreview.trades.length > 20 && (
                <p className="text-[9px] text-gray-500 text-center py-2">
                  ...and {importPreview.trades.length - 20} more trades
                </p>
              )}
            </div>

            <div className="flex items-center gap-2">
              <Button size="sm" onClick={handleConfirmImport} disabled={importLoading}
                className="h-7 text-[10px] bg-profit hover:bg-profit/80 text-white">
                {importLoading ? "Importing..." : `Confirm Import (${importPreview.trades.length} trades)`}
              </Button>
              <Button size="sm" onClick={resetImport} className="h-7 text-[10px] bg-white/10 text-white hover:bg-white/20">
                Cancel
              </Button>
            </div>
            {importError && <p className="text-[10px] text-loss mt-2">{importError}</p>}
          </div>
        )}

        {importStep === "result" && importResult && (
          <div className="mb-4 p-4 rounded-lg border border-profit/30 bg-profit/5">
            <div className="flex items-center gap-2 mb-3">
              <svg className="w-5 h-5 text-profit" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
              </svg>
              <span className="text-sm text-profit font-semibold" style={FONT_OUTFIT}>Import Complete</span>
            </div>
            <div className="flex flex-wrap gap-4 text-[11px] font-mono">
              <div>
                <span className="text-gray-500">Imported:</span>{" "}
                <span className="text-white font-semibold">{importResult.imported} trades</span>
              </div>
              <div>
                <span className="text-gray-500">New positions:</span>{" "}
                <span className="text-profit font-semibold">{importResult.holdings_created}</span>
              </div>
              <div>
                <span className="text-gray-500">Updated:</span>{" "}
                <span className="text-screener-amber font-semibold">{importResult.holdings_updated}</span>
              </div>
              <div>
                <span className="text-gray-500">Closed:</span>{" "}
                <span className="text-loss font-semibold">{importResult.holdings_closed}</span>
              </div>
            </div>
            <Button size="sm" onClick={resetImport} className="mt-3 h-7 text-[10px] bg-white/10 text-white hover:bg-white/20">
              Done
            </Button>
          </div>
        )}

        {/* Combined positions list */}
        {totalCount === 0 && importStep === "idle" ? (
          <p className="text-xs text-gray-500 text-center py-6">No positions — use Buy to add your first position</p>
        ) : totalCount > 0 ? (
          <div className="space-y-2">
            {/* Manual holdings */}
            {holdings.map((h) => (
              <div key={h.id}>
                <div className="rounded-lg border border-border/50 hover:border-border p-3 cursor-pointer group transition"
                  onClick={() => editingId === h.id ? setEditingId(null) : startEdit(h)}>
                  {/* Row 1: Ticker, badges, P&L */}
                  <div className="flex items-center gap-2 mb-1.5">
                    <span className="text-sm font-bold text-white" style={FONT_OUTFIT}>{h.ticker}</span>
                    <Badge className={`text-[8px] px-1.5 py-0 ${h.direction === "long" ? "bg-profit/15 text-profit" : "bg-loss/15 text-loss"}`}>
                      {h.direction.toUpperCase()}
                    </Badge>
                    {h.position_type === "accumulation" && (
                      <Badge className="bg-ai-blue/15 text-ai-blue text-[8px] px-1.5 py-0">ACCUM</Badge>
                    )}
                    {h.position_type === "catalyst" && (
                      <Badge className="bg-amber-500/15 text-amber-400 text-[8px] px-1.5 py-0">CATALYST</Badge>
                    )}
                    {h.position_type === "conviction" && (
                      <Badge className="bg-purple-500/15 text-purple-400 text-[8px] px-1.5 py-0">HOLD</Badge>
                    )}
                    {/* Allocation */}
                    <span className="text-[9px] text-gray-600 tabular-nums ml-auto hidden sm:inline">
                      {getAllocation(h.current_price ?? h.entry_price, h.qty).toFixed(1)}%
                    </span>
                    {/* P&L — always visible */}
                    {h.unrealized_pnl != null && (
                      <div className="text-right sm:ml-0 ml-auto">
                        <span className={`text-sm font-mono font-semibold ${pnlColor(h.unrealized_pnl_pct ?? 0)}`}>
                          {formatPercent(h.unrealized_pnl_pct ?? 0)}
                        </span>
                      </div>
                    )}
                    {/* Henry notification */}
                    {actionsByTicker[h.ticker] && actionsByTicker[h.ticker].length > 0 && (
                      <div className="relative"
                        onMouseEnter={() => setHoveredAlert(h.ticker)}
                        onMouseLeave={() => setHoveredAlert(null)}
                        onClick={(e) => e.stopPropagation()}>
                        <div className={`w-5 h-5 rounded-full flex items-center justify-center cursor-pointer transition ${
                          actionsByTicker[h.ticker].some((a) => a.status === "pending")
                            ? "bg-screener-amber/20 text-screener-amber"
                            : "bg-gray-700/50 text-gray-500 hover:text-gray-300"
                        }`}>
                          <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}>
                            <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v3.75m9-.75a9 9 0 11-18 0 9 9 0 0118 0zm-9 3.75h.008v.008H12v-.008z" />
                          </svg>
                        </div>
                        {hoveredAlert === h.ticker && (
                          <div className="absolute right-0 top-7 z-50 w-72 p-3 rounded-lg bg-surface border border-border shadow-xl">
                            <div className="text-[9px] text-gray-500 uppercase tracking-wider mb-2" style={FONT_OUTFIT}>
                              Henry — {h.ticker}
                            </div>
                            <div className="space-y-2">
                              {actionsByTicker[h.ticker].map((a) => (
                                <div key={a.id} className="text-[10px]">
                                  <div className="flex items-center gap-1.5 mb-0.5">
                                    <Badge className={`text-[7px] px-1 py-0 ${
                                      a.action_type === "BUY" || a.action_type === "ADD" || a.action_type === "DCA" ? "bg-profit/15 text-profit"
                                        : a.action_type === "SELL" || a.action_type === "CLOSE" ? "bg-loss/15 text-loss"
                                        : "bg-screener-amber/15 text-screener-amber"
                                    }`}>{a.action_type}</Badge>
                                    <span className={`text-[8px] px-1 rounded ${
                                      a.status === "pending" ? "bg-screener-amber/10 text-screener-amber"
                                        : a.status === "approved" ? "bg-profit/10 text-profit"
                                        : a.status === "rejected" ? "bg-loss/10 text-loss"
                                        : "bg-gray-700 text-gray-400"
                                    }`}>{a.status}</span>
                                    <span className="text-gray-600 ml-auto">{formatTimeAgo(a.created_at)}</span>
                                  </div>
                                  <p className="text-gray-400 leading-snug line-clamp-2">{a.reasoning}</p>
                                  {a.confidence > 0 && (
                                    <div className="flex items-center gap-1 mt-0.5">
                                      <span className="text-[8px] text-gray-600">Conf:</span>
                                      <div className="flex gap-px">
                                        {Array.from({ length: 10 }).map((_, i) => (
                                          <div key={i} className={`w-1.5 h-1 rounded-sm ${i < a.confidence ? "bg-ai-blue" : "bg-gray-800"}`} />
                                        ))}
                                      </div>
                                    </div>
                                  )}
                                </div>
                              ))}
                            </div>
                          </div>
                        )}
                      </div>
                    )}
                    <button onClick={(e) => { e.stopPropagation(); startEdit(h); }} className="text-gray-600 hover:text-ai-blue opacity-0 group-hover:opacity-100 transition" title="Edit">
                      <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}><path strokeLinecap="round" strokeLinejoin="round" d="M16.862 4.487l1.687-1.688a1.875 1.875 0 112.652 2.652L10.582 16.07a4.5 4.5 0 01-1.897 1.13L6 18l.8-2.685a4.5 4.5 0 011.13-1.897l8.932-8.931z" /></svg>
                    </button>
                    <button onClick={(e) => { e.stopPropagation(); handleDelete(h.id); }} className="text-gray-600 hover:text-loss opacity-0 group-hover:opacity-100 transition" title="Remove">
                      <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}><path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" /></svg>
                    </button>
                  </div>
                  {/* Row 2: Entry details + allocation (mobile-friendly) */}
                  <div className="flex flex-wrap items-center gap-x-3 gap-y-0.5 text-[10px] font-mono text-gray-500">
                    {h.position_type === "accumulation" && h.avg_cost
                      ? <span>avg {formatCurrency(h.avg_cost)} × {h.total_shares?.toFixed(2)}</span>
                      : <span>{h.qty} @ {formatCurrency(h.entry_price)}</span>
                    }
                    {h.current_price && <span>→ {formatCurrency(h.current_price)}</span>}
                    {h.unrealized_pnl != null && (
                      <span className={pnlColor(h.unrealized_pnl)}>{formatCurrency(h.unrealized_pnl)}</span>
                    )}
                    <span className="sm:hidden">{getAllocation(h.current_price ?? h.entry_price, h.qty).toFixed(1)}% alloc</span>
                    {h.position_type === "catalyst" && h.catalyst_date && (() => {
                      const days = Math.ceil((new Date(h.catalyst_date).getTime() - Date.now()) / 86400000);
                      return days > 0
                        ? <span className="text-amber-400/70">{days}d to {h.catalyst_description || "event"}</span>
                        : <span className="text-loss/70">catalyst passed</span>;
                    })()}
                  </div>
                </div>
                {/* Inline edit form */}
                {editingId === h.id && (
                  <div className="ml-12 mr-2 mb-2 p-3 rounded-lg border border-ai-blue/20 bg-ai-blue/5">
                    <div className="text-[9px] text-ai-blue font-semibold uppercase tracking-wider mb-2" style={FONT_OUTFIT}>Edit Position</div>
                    <div className="flex flex-wrap gap-2 mb-2">
                      <select value={String(editFields.position_type)} onChange={(e) => setEditFields((f) => ({ ...f, position_type: e.target.value }))}
                        className="h-7 text-[10px] font-mono bg-surface-light/30 border border-border rounded-md px-2 text-white">
                        <option value="momentum">Momentum</option>
                        <option value="accumulation">Accumulation</option>
                        <option value="catalyst">Catalyst</option>
                        <option value="conviction">Conviction</option>
                      </select>
                      {editFields.position_type !== "momentum" && (
                        <Input value={String(editFields.thesis || "")} onChange={(e) => setEditFields((f) => ({ ...f, thesis: e.target.value }))}
                          placeholder="Investment thesis" className="flex-1 min-w-[200px] h-7 text-[10px] font-mono bg-surface-light/30" />
                      )}
                    </div>
                    <div className="flex flex-wrap gap-2 mb-2">
                      {editFields.position_type === "catalyst" && (
                        <>
                          <Input type="date" value={String(editFields.catalyst_date || "")} onChange={(e) => setEditFields((f) => ({ ...f, catalyst_date: e.target.value }))}
                            className="w-36 h-7 text-[10px] font-mono bg-surface-light/30" />
                          <Input value={String(editFields.catalyst_description || "")} onChange={(e) => setEditFields((f) => ({ ...f, catalyst_description: e.target.value }))}
                            placeholder="e.g., Phase 3 readout" className="w-40 h-7 text-[10px] font-mono bg-surface-light/30" />
                        </>
                      )}
                      {(editFields.position_type === "accumulation" || editFields.position_type === "catalyst") && (
                        <Input type="number" value={String(editFields.max_allocation_pct || "")} onChange={(e) => setEditFields((f) => ({ ...f, max_allocation_pct: e.target.value }))}
                          placeholder="Max alloc %" step="0.5" className="w-24 h-7 text-[10px] font-mono bg-surface-light/30" />
                      )}
                      {editFields.position_type === "accumulation" && (
                        <>
                          <label className="flex items-center gap-1.5 text-[10px] text-gray-400 cursor-pointer">
                            <input type="checkbox" checked={!!editFields.dca_enabled} onChange={(e) => setEditFields((f) => ({ ...f, dca_enabled: e.target.checked }))}
                              className="w-3 h-3 rounded" />
                            DCA
                          </label>
                          {editFields.dca_enabled && (
                            <Input type="number" value={String(editFields.dca_threshold_pct || "")} onChange={(e) => setEditFields((f) => ({ ...f, dca_threshold_pct: e.target.value }))}
                              placeholder="DCA threshold %" step="1" className="w-28 h-7 text-[10px] font-mono bg-surface-light/30" />
                          )}
                        </>
                      )}
                    </div>
                    <div className="flex gap-2">
                      <Button size="sm" onClick={saveEdit} disabled={submitting} className="h-6 text-[10px] bg-ai-blue hover:bg-ai-blue/80">
                        {submitting ? "..." : "Save"}
                      </Button>
                      <Button size="sm" onClick={() => setEditingId(null)} className="h-6 text-[10px] bg-white/10 text-white hover:bg-white/20">
                        Cancel
                      </Button>
                    </div>
                  </div>
                )}
              </div>
            ))}
            {/* Strategy positions (deduplicated — skip if holding already exists) */}
            {uniquePositions.map((p) => (
              <div key={p.trade_id} className="flex items-center gap-3 text-[11px] font-mono py-2 px-2 rounded-md hover:bg-surface-light/10">
                <span className="text-white font-semibold w-12">{p.ticker}</span>
                <Badge className={`text-[8px] px-1 py-0 ${p.direction === "long" ? "bg-profit/15 text-profit" : "bg-loss/15 text-loss"}`}>
                  {p.direction.toUpperCase()}
                </Badge>
                <span className="text-gray-500">{p.qty} @ {formatCurrency(p.entry_price)}</span>
                {p.current_price && (
                  <span className="text-gray-500">{String.fromCharCode(8594)} {formatCurrency(p.current_price)}</span>
                )}
                <Badge className="text-[8px] px-1 py-0 bg-ai-blue/10 text-ai-blue">strategy</Badge>
                <div className="ml-auto">
                  {p.unrealized_pnl != null && (
                    <div className="text-right">
                      <span className={`font-semibold ${pnlColor(p.unrealized_pnl)}`}>
                        {formatCurrency(p.unrealized_pnl)}
                      </span>
                      <span className={`ml-1.5 ${pnlColor(p.unrealized_pnl_pct ?? 0)}`}>
                        ({formatPercent(p.unrealized_pnl_pct ?? 0)})
                      </span>
                    </div>
                  )}
                </div>
              </div>
            ))}
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}

// ══════════════════════════════════════════════════════════════════════
// MAIN PAGE
// ══════════════════════════════════════════════════════════════════════

export default function PortfolioDetailPage({ params }: { params: { portfolioId: string } }) {
  useFonts();
  const { portfolioId } = params;

  const [cashMode, setCashMode] = useState<"deposit" | "withdraw" | null>(null);
  const [cashAmount, setCashAmount] = useState("");
  const [cashSubmitting, setCashSubmitting] = useState(false);
  const { data: portfolio, loading: loadingPortfolio } = usePolling(() => api.getPortfolio(portfolioId), 15000);
  const { data: performance } = usePolling(() => api.getPerformance(portfolioId), 60000);
  const { data: positions } = usePolling(() => api.getPositions(portfolioId), 15000);
  const { data: equity } = usePolling(() => api.getEquityHistory(portfolioId), 60000);
  const { data: dailyStats } = usePolling(() => api.getDailyStats(portfolioId), 60000);
  const { data: trades } = usePolling(() => api.getTrades({ portfolio_id: portfolioId, limit: 200 }), 15000);
  const { data: holdings } = usePolling(() => api.getHoldings(portfolioId), 15000);
  const { data: backtestImports } = usePolling(() => api.getBacktestImports(), 120000);
  const { data: portfolioHistory } = usePolling(() => api.getPortfolioHistory(portfolioId, 90), 60000);

  if (loadingPortfolio) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-24 rounded-xl" />
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3">
          {[1, 2, 3, 4, 5, 6].map((i) => <Skeleton key={i} className="h-20 rounded-xl" />)}
        </div>
        <Skeleton className="h-64 rounded-xl" />
      </div>
    );
  }

  if (!portfolio) {
    return (
      <Card className="bg-surface-light/20 border-border">
        <CardContent className="text-loss text-center py-12" style={FONT_OUTFIT}>
          Portfolio not found
        </CardContent>
      </Card>
    );
  }

  return (
    <div className="space-y-4 pb-12">
      {/* ═══ HERO ═══ */}
      <div>
        <div className="flex items-center gap-2 mb-1">
          <a href="/portfolios" className="text-gray-500 hover:text-white transition">
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M15.75 19.5L8.25 12l7.5-7.5" />
            </svg>
          </a>
          <span className="text-xs text-gray-500" style={FONT_OUTFIT}>Portfolios</span>
        </div>
        <div className="flex items-start justify-between">
          <div>
            <h1 className="text-3xl font-bold text-white tracking-tight" style={FONT_OUTFIT}>{portfolio.name}</h1>
            {portfolio.description && (
              <p className="text-xs text-gray-500 mt-1 max-w-2xl" style={FONT_OUTFIT}>{portfolio.description}</p>
            )}
            <div className="flex items-baseline gap-3 mt-2">
              <span className="text-4xl font-bold text-white" style={FONT_MONO}>{formatCurrency(portfolio.equity)}</span>
              <span className={`text-lg font-semibold ${pnlColor(portfolio.total_return_pct)}`} style={FONT_MONO}>
                {formatPercent(portfolio.total_return_pct)}
              </span>
            </div>
            <div className="flex flex-wrap items-center gap-2 mt-3">
              <span className="inline-flex items-center gap-1 px-2.5 py-1 rounded-full bg-[#1f2937] border border-[#374151] text-[10px] text-gray-400" style={FONT_OUTFIT}>
                <span className="text-gray-500">Initial</span>
                <span className="font-mono text-white">{formatCurrency(portfolio.initial_capital)}</span>
              </span>
              <span className="inline-flex items-center gap-1 px-2.5 py-1 rounded-full bg-[#1f2937] border border-[#374151] text-[10px] text-gray-400" style={FONT_OUTFIT}>
                <span className="text-gray-500">Cash</span>
                <span className="font-mono text-white">{formatCurrency(portfolio.cash)}</span>
              </span>
              {performance && (
                <>
                  <span className="inline-flex items-center gap-1 px-2.5 py-1 rounded-full bg-[#1f2937] border border-[#374151] text-[10px] text-gray-400" style={FONT_OUTFIT}>
                    <span className="text-gray-500">Drawdown</span>
                    <span className="font-mono text-loss">{formatPercent(-Math.abs(performance.max_drawdown_pct))}</span>
                  </span>
                  <span className="inline-flex items-center gap-1 px-2.5 py-1 rounded-full bg-[#1f2937] border border-[#374151] text-[10px] text-gray-400" style={FONT_OUTFIT}>
                    <span className="text-gray-500">Win Rate</span>
                    <span className={`font-mono ${performance.win_rate >= 50 ? "text-profit" : "text-loss"}`}>{performance.win_rate.toFixed(1)}%</span>
                  </span>
                </>
              )}
              <span className="inline-flex items-center gap-1 px-2.5 py-1 rounded-full bg-[#1f2937] border border-[#374151] text-[10px] text-gray-400" style={FONT_OUTFIT}>
                <span className="text-gray-500">Positions</span>
                <span className="font-mono text-white">{portfolio.open_positions}</span>
              </span>
            </div>
          </div>
          <div className="flex flex-col gap-1 shrink-0">
            <button onClick={() => { setCashMode(cashMode === "deposit" ? null : "deposit"); setCashAmount(""); }}
              className={`text-[9px] px-2.5 py-1 rounded border transition ${cashMode === "deposit" ? "bg-profit/20 border-profit text-profit" : "bg-[#1f2937] border-[#374151] text-gray-400 hover:text-profit"}`}
              style={FONT_OUTFIT}>
              Deposit
            </button>
            <button onClick={() => { setCashMode(cashMode === "withdraw" ? null : "withdraw"); setCashAmount(""); }}
              className={`text-[9px] px-2.5 py-1 rounded border transition ${cashMode === "withdraw" ? "bg-loss/20 border-loss text-loss" : "bg-[#1f2937] border-[#374151] text-gray-400 hover:text-loss"}`}
              style={FONT_OUTFIT}>
              Withdraw
            </button>
            {/* AI Portfolio management buttons */}
            {(portfolio.name?.toLowerCase().includes("ai") || portfolio.name?.toLowerCase().includes("henry")) && (
              <>
                <button onClick={async () => {
                  const ticker = prompt("Enter ticker to add to this portfolio (e.g. NOK):");
                  if (!ticker) return;
                  try {
                    const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api";
                    const res = await fetch(`${API_URL}/ai-portfolio/add-trade`, {
                      method: "POST", headers: { "Content-Type": "application/json" },
                      body: JSON.stringify({ ticker: ticker.toUpperCase(), portfolio_id: portfolioId }),
                    });
                    const data = await res.json();
                    if (res.ok) {
                      alert(data.status === "linked" ? `Added ${data.ticker} x${data.qty} @ $${data.entry_price} (cost $${data.cost})\nResized: ${data.resized ? "Yes" : "No"}\nCash remaining: $${data.portfolio_cash}` : data.status === "already_linked" ? `${data.ticker} already in portfolio` : JSON.stringify(data));
                    } else {
                      alert(`Error: ${data.detail || JSON.stringify(data)}`);
                    }
                    window.location.reload();
                  } catch (e) { alert(`Failed: ${e}`); }
                }} className="text-[9px] px-2.5 py-1 rounded border bg-[#1f2937] border-[#6366f1]/30 text-[#6366f1] hover:bg-[#6366f1]/10 transition" style={FONT_OUTFIT}>
                  + Add Trade
                </button>
                <button onClick={async () => {
                  const ticker = prompt("Ticker to resize (e.g. SNDK):");
                  if (!ticker) return;
                  const dollars = prompt("Target dollar amount for position:", "10");
                  if (!dollars) return;
                  try {
                    const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api";
                    const res = await fetch(`${API_URL}/ai-portfolio/resize-ticker`, {
                      method: "POST", headers: { "Content-Type": "application/json" },
                      body: JSON.stringify({ ticker: ticker.toUpperCase(), target_dollars: parseFloat(dollars), portfolio_id: portfolioId }),
                    });
                    const data = await res.json();
                    if (res.ok) {
                      alert(`Resized ${data.ticker}:\n${data.old_qty} → ${data.new_qty} shares\n$${data.old_cost} → $${data.new_cost}\nCash: $${data.portfolio_cash}`);
                    } else {
                      alert(`Error: ${data.detail || JSON.stringify(data)}`);
                    }
                    window.location.reload();
                  } catch (e) { alert(`Failed: ${e}`); }
                }} className="text-[9px] px-2.5 py-1 rounded border bg-[#1f2937] border-[#374151] text-gray-400 hover:text-white transition" style={FONT_OUTFIT}>
                  Resize
                </button>
                <button onClick={async () => {
                  if (!confirm("Fix portfolio: resize oversized trades and recalculate cash?")) return;
                  try {
                    const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api";
                    const res = await fetch(`${API_URL}/ai-portfolio/fix-all`, {
                      method: "POST", headers: { "Content-Type": "application/json" },
                      body: JSON.stringify({ portfolio_id: portfolioId }),
                    });
                    const data = await res.json();
                    if (res.ok) {
                      alert(`Fixed!\nCash: $${data.old_cash} → $${data.new_cash}\n${data.fixes_applied?.length || 0} trades adjusted:\n${data.fixes_applied?.map((f: Record<string, unknown>) => `  ${f.ticker}: ${f.action} (${f.old_qty} → ${f.new_qty || "closed"})`).join("\n") || "None"}`);
                    } else {
                      alert(`Error: ${data.detail || JSON.stringify(data)}`);
                    }
                    window.location.reload();
                  } catch (e) { alert(`Failed: ${e}`); }
                }} className="text-[9px] px-2.5 py-1 rounded border bg-[#1f2937] border-amber-500/30 text-amber-400 hover:bg-amber-500/10 transition" style={FONT_OUTFIT}>
                  Fix Portfolio
                </button>
              </>
            )}
          </div>
        </div>
      </div>

      {/* Deposit / Withdraw form */}
      {cashMode && (
        <div className={`flex items-center gap-2 p-3 rounded-lg border ${cashMode === "deposit" ? "border-profit/30 bg-profit/5" : "border-loss/30 bg-loss/5"}`}>
          <span className={`text-xs font-semibold ${cashMode === "deposit" ? "text-profit" : "text-loss"}`} style={FONT_OUTFIT}>
            {cashMode === "deposit" ? "Deposit" : "Withdraw"}
          </span>
          <div className="flex items-center gap-1">
            <span className="text-sm text-gray-500 font-mono">$</span>
            <input
              type="number"
              value={cashAmount}
              onChange={(e) => setCashAmount(e.target.value)}
              placeholder="0.00"
              autoFocus
              className="w-28 h-7 text-sm font-mono text-white bg-transparent border-b border-gray-600 outline-none focus:border-white"
              onKeyDown={async (e) => {
                if (e.key === "Enter" && cashAmount && parseFloat(cashAmount) > 0) {
                  setCashSubmitting(true);
                  try {
                    if (cashMode === "deposit") {
                      await api.depositToPortfolio(portfolioId, parseFloat(cashAmount));
                    } else {
                      await api.withdrawFromPortfolio(portfolioId, parseFloat(cashAmount));
                    }
                    setCashMode(null);
                    setCashAmount("");
                  } catch {}
                  setCashSubmitting(false);
                } else if (e.key === "Escape") {
                  setCashMode(null);
                }
              }}
            />
          </div>
          <button
            disabled={cashSubmitting || !cashAmount || parseFloat(cashAmount) <= 0}
            onClick={async () => {
              if (!cashAmount || parseFloat(cashAmount) <= 0) return;
              setCashSubmitting(true);
              try {
                if (cashMode === "deposit") {
                  await api.depositToPortfolio(portfolioId, parseFloat(cashAmount));
                } else {
                  await api.withdrawFromPortfolio(portfolioId, parseFloat(cashAmount));
                }
                setCashMode(null);
                setCashAmount("");
              } catch {}
              setCashSubmitting(false);
            }}
            className={`h-7 px-3 text-[10px] rounded font-medium transition ${
              cashMode === "deposit" ? "bg-profit text-black hover:bg-profit/80" : "bg-loss text-white hover:bg-loss/80"
            } disabled:opacity-30`}
            style={FONT_OUTFIT}
          >
            {cashSubmitting ? "..." : "Confirm"}
          </button>
          <button onClick={() => setCashMode(null)} className="text-gray-500 hover:text-white text-xs ml-1">✕</button>
        </div>
      )}

      {/* Performance Stats */}
      {performance && <PerformanceGrid perf={performance} />}

      {/* Portfolio Value Over Time (hero chart) */}
      {holdings && holdings.length > 0 && (
        <PortfolioValueChart data={portfolioHistory || []} />
      )}

      {/* Charts Row */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <EquityCurveChart data={equity || []} initialCapital={portfolio.initial_capital} />
        <DrawdownChart data={equity || []} />
      </div>

      {/* Daily P&L */}
      {dailyStats && dailyStats.length > 0 && <DailyPnlChart data={dailyStats} />}

      {/* Allocation (full-width horizontal) */}
      {holdings && holdings.length > 0 && <AllocationChart holdings={holdings} />}

      {/* Holdings Performance */}
      {holdings && holdings.length > 0 && <HoldingsPerformanceBars holdings={holdings} />}

      {/* Content Tabs */}
      <Tabs defaultValue="positions" className="w-full">
        <TabsList className="bg-surface-light/30 border border-border p-1 rounded-lg">
          <TabsTrigger value="positions" className="text-xs font-medium data-[state=active]:bg-surface-light data-[state=active]:text-white" style={FONT_OUTFIT}>
            Positions ({(holdings?.length ?? 0) + (positions?.length ?? 0)})
          </TabsTrigger>
          <TabsTrigger value="trades" className="text-xs font-medium data-[state=active]:bg-surface-light data-[state=active]:text-white" style={FONT_OUTFIT}>
            Trades ({trades?.length ?? 0})
          </TabsTrigger>
          <TabsTrigger value="actions" className="text-xs font-medium data-[state=active]:bg-surface-light data-[state=active]:text-white" style={FONT_OUTFIT}>
            Actions
          </TabsTrigger>
          <TabsTrigger value="henry" className="text-xs font-medium data-[state=active]:bg-surface-light data-[state=active]:text-white" style={FONT_OUTFIT}>
            Henry
          </TabsTrigger>
        </TabsList>

        <TabsContent value="positions" className="mt-4 space-y-4">
          <PositionsManager portfolioId={portfolioId} holdings={holdings || []} positions={positions || []} onRefresh={() => {}} executionMode={portfolio?.execution_mode} />
        </TabsContent>

        <TabsContent value="trades" className="mt-4">
          <TradeHistorySection trades={trades || []} />
        </TabsContent>

        <TabsContent value="actions" className="mt-4 space-y-4">
          <ActionQueue portfolioId={portfolioId} />
        </TabsContent>

        <TabsContent value="henry" className="mt-4 space-y-4">
          <HenryInsights portfolioId={portfolioId} />
          {backtestImports && backtestImports.length > 0 && <BacktestSummary imports={backtestImports} />}
        </TabsContent>
      </Tabs>
    </div>
  );
}
