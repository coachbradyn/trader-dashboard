"use client";

import { useState, useEffect, useCallback } from "react";
import { api } from "@/lib/api";
import { usePolling } from "@/hooks/usePolling";
import { formatCurrency, formatPercent, formatDate, formatTimeAgo, pnlColor } from "@/lib/formatters";
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
  const { data: stats } = usePolling(() => api.getActionStats(), 60000);

  const askHenry = async () => {
    setLoading(true);
    setAsked(true);
    try {
      const result = await api.postQuery(
        `Give me a brief portfolio health check. Focus on concentration risk, recent performance trends, and one actionable recommendation. Keep it under 150 words.`
      );
      setInsight(result.answer);
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
          {stats && stats.pending_count > 0 && (
            <Badge className="text-[9px] bg-ai-blue/15 text-ai-blue">
              {stats.pending_count} pending action{stats.pending_count > 1 ? "s" : ""}
            </Badge>
          )}
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
          <div className="text-sm text-gray-300 leading-relaxed whitespace-pre-wrap" style={FONT_OUTFIT}>
            {insight}
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}

// ── Allocation Donut Chart ──────────────────────────────────────────

const ALLOC_COLORS = ["#6366f1", "#22c55e", "#fbbf24", "#ef4444", "#8b5cf6", "#06b6d4", "#f97316", "#ec4899", "#14b8a6", "#a855f7"];

function AllocationDonut({ holdings }: { holdings: PortfolioHolding[] }) {
  if (!holdings.length) return null;

  const data = holdings
    .map((h) => ({
      ticker: h.ticker,
      value: (h.current_price ?? h.entry_price) * h.qty,
    }))
    .sort((a, b) => b.value - a.value);

  const total = data.reduce((s, d) => s + d.value, 0);
  const chartData = data.map((d) => ({ ...d, pct: total > 0 ? (d.value / total * 100) : 0 }));

  return (
    <Card className="bg-surface-light/20 border-border">
      <CardContent className="p-5">
        <h3 className="font-semibold text-white text-sm mb-4" style={FONT_OUTFIT}>Allocation</h3>
        <ResponsiveContainer width="100%" height={260}>
          <PieChart>
            <Pie
              data={chartData}
              dataKey="value"
              nameKey="ticker"
              cx="50%"
              cy="50%"
              innerRadius={60}
              outerRadius={100}
              paddingAngle={2}
              stroke="none"
              label={({ ticker, pct }) => `${ticker} ${pct.toFixed(1)}%`}
            >
              {chartData.map((_, i) => (
                <Cell key={i} fill={ALLOC_COLORS[i % ALLOC_COLORS.length]} />
              ))}
            </Pie>
            <Tooltip
              contentStyle={CHART_TOOLTIP}
              labelStyle={{ color: "#9ca3af" }}
              formatter={(value: number, name: string) => [
                `$${value.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })} (${total > 0 ? (value / total * 100).toFixed(1) : 0}%)`,
                name,
              ]}
            />
          </PieChart>
        </ResponsiveContainer>
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
              <Badge className="text-[8px] px-1 py-0 bg-surface-light text-gray-400">{h.source}</Badge>
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

function HoldingsManager({ portfolioId, holdings, onRefresh }: { portfolioId: string; holdings: PortfolioHolding[]; onRefresh: () => void }) {
  const [showAdd, setShowAdd] = useState(false);
  const [ticker, setTicker] = useState("");
  const [dir, setDir] = useState<"long" | "short">("long");
  const [price, setPrice] = useState("");
  const [qty, setQty] = useState("");
  const [adding, setAdding] = useState(false);

  const totalValue = holdings.reduce((sum, h) => sum + (h.current_price ?? h.entry_price) * h.qty, 0);
  const totalUnrealized = holdings.reduce((sum, h) => sum + (h.unrealized_pnl ?? 0), 0);

  const handleAdd = async () => {
    if (!ticker || !price || !qty) return;
    setAdding(true);
    try {
      await api.createHolding({
        portfolio_id: portfolioId,
        ticker: ticker.toUpperCase(),
        direction: dir,
        entry_price: parseFloat(price),
        qty: parseFloat(qty),
        entry_date: new Date().toISOString().slice(0, 10),
      });
      setTicker(""); setPrice(""); setQty(""); setShowAdd(false);
      onRefresh();
    } catch {}
    setAdding(false);
  };

  const handleDelete = async (id: string) => {
    if (!confirm("Remove this holding?")) return;
    try { await api.deleteHolding(id); onRefresh(); } catch {}
  };

  return (
    <Card className="bg-surface-light/20 border-border">
      <CardContent className="p-5">
        <div className="flex items-center justify-between mb-3">
          <h3 className="font-semibold text-white text-sm" style={FONT_OUTFIT}>
            Holdings <span className="text-gray-500 font-normal">({holdings.length})</span>
          </h3>
          <div className="flex items-center gap-3">
            {holdings.length > 0 && (
              <span className={`text-xs font-mono ${pnlColor(totalUnrealized)}`}>
                {formatCurrency(totalValue)} ({formatPercent(totalValue > 0 ? totalUnrealized / totalValue * 100 : 0)})
              </span>
            )}
            <Button size="sm" variant="outline" onClick={() => setShowAdd(!showAdd)} className="text-[10px] h-7">
              {showAdd ? "Cancel" : "+ Add Holding"}
            </Button>
          </div>
        </div>

        {showAdd && (
          <div className="flex flex-wrap gap-2 mb-4 p-3 rounded-lg border border-border/40 bg-surface/50">
            <Input value={ticker} onChange={(e) => setTicker(e.target.value)} placeholder="Ticker" className="w-20 h-8 text-xs font-mono bg-surface-light/30" />
            <div className="flex rounded-md overflow-hidden border border-border">
              <button onClick={() => setDir("long")} className={`px-2.5 py-1 text-[10px] ${dir === "long" ? "bg-profit/20 text-profit" : "bg-surface-light/30 text-gray-500"}`}>Long</button>
              <button onClick={() => setDir("short")} className={`px-2.5 py-1 text-[10px] ${dir === "short" ? "bg-loss/20 text-loss" : "bg-surface-light/30 text-gray-500"}`}>Short</button>
            </div>
            <Input value={price} onChange={(e) => setPrice(e.target.value)} placeholder="Price" type="number" className="w-24 h-8 text-xs font-mono bg-surface-light/30" />
            <Input value={qty} onChange={(e) => setQty(e.target.value)} placeholder="Qty" type="number" className="w-20 h-8 text-xs font-mono bg-surface-light/30" />
            <Button size="sm" onClick={handleAdd} disabled={adding || !ticker || !price || !qty} className="h-8 text-[10px]">
              {adding ? "Adding..." : "Add"}
            </Button>
          </div>
        )}

        {holdings.length === 0 ? (
          <p className="text-xs text-gray-500 text-center py-6">No holdings — add manually or receive via webhooks</p>
        ) : (
          <div className="space-y-1.5">
            {holdings.map((h) => (
              <div key={h.id} className="flex items-center gap-3 text-[11px] font-mono py-2 px-2 border-b border-border/30 last:border-0 group">
                <span className="text-white font-semibold w-12">{h.ticker}</span>
                <Badge className={`text-[8px] px-1 py-0 ${h.direction === "long" ? "bg-profit/15 text-profit" : "bg-loss/15 text-loss"}`}>{h.direction.toUpperCase()}</Badge>
                <span className="text-gray-500">{h.qty} @ {formatCurrency(h.entry_price)}</span>
                <Badge className="text-[8px] px-1 py-0 bg-surface-light text-gray-400">{h.source}</Badge>
                <div className="ml-auto flex items-center gap-2">
                  {h.unrealized_pnl != null && (
                    <span className={`font-semibold ${pnlColor(h.unrealized_pnl)}`}>{formatPercent(h.unrealized_pnl_pct ?? 0)}</span>
                  )}
                  <button onClick={() => handleDelete(h.id)} className="text-gray-600 hover:text-loss opacity-0 group-hover:opacity-100 transition">
                    <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}><path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" /></svg>
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}
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
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold text-white tracking-tight" style={FONT_OUTFIT}>{portfolio.name}</h1>
          {portfolio.description && (
            <p className="text-sm text-gray-500 mt-1" style={FONT_OUTFIT}>{portfolio.description}</p>
          )}
        </div>
        <div className="flex items-center gap-4 text-right">
          <div>
            <div className="text-[10px] text-gray-500 uppercase tracking-wider" style={FONT_OUTFIT}>Equity</div>
            <div className="text-xl font-mono font-bold text-white">{formatCurrency(portfolio.equity)}</div>
          </div>
          <div>
            <div className="text-[10px] text-gray-500 uppercase tracking-wider" style={FONT_OUTFIT}>Return</div>
            <div className={`text-xl font-mono font-bold ${pnlColor(portfolio.total_return_pct)}`}>
              {formatPercent(portfolio.total_return_pct)}
            </div>
          </div>
        </div>
      </div>

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

      {/* Allocation + Performance */}
      {holdings && holdings.length > 0 && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          <AllocationDonut holdings={holdings} />
          <HoldingsPerformanceBars holdings={holdings} />
        </div>
      )}

      {/* Content Tabs */}
      <Tabs defaultValue="holdings" className="w-full">
        <TabsList className="bg-surface-light/30 border border-border p-1 rounded-lg">
          <TabsTrigger value="holdings" className="text-xs font-medium data-[state=active]:bg-surface-light data-[state=active]:text-white" style={FONT_OUTFIT}>
            Holdings ({holdings?.length ?? 0})
          </TabsTrigger>
          <TabsTrigger value="positions" className="text-xs font-medium data-[state=active]:bg-surface-light data-[state=active]:text-white" style={FONT_OUTFIT}>
            Positions ({positions?.length ?? 0})
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

        <TabsContent value="holdings" className="mt-4 space-y-4">
          <HoldingsManager portfolioId={portfolioId} holdings={holdings || []} onRefresh={() => {}} />
        </TabsContent>

        <TabsContent value="positions" className="mt-4 space-y-4">
          <OpenPositions positions={positions || []} />
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
