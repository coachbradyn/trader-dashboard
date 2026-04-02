"use client";

import { useState, useEffect, useCallback, useRef } from "react";
import { api } from "@/lib/api";
import { formatTimeAgo, formatCurrency, formatPercent, pnlColor } from "@/lib/formatters";
import { renderMarkdown } from "@/lib/markdown";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import {
  ResponsiveContainer, AreaChart, Area,
  XAxis, YAxis, Tooltip, CartesianGrid, ReferenceLine,
} from "recharts";
import type {
  AIPortfolioStatus,
  AIPortfolioComparison,
  AIPortfolioDecision,
  AIPortfolioHolding,
  EquityPoint,
} from "@/lib/types";

// ── Fonts & Constants ───────────────────────────────────────────────
const FONT_OUTFIT = { fontFamily: "'Outfit', sans-serif" } as const;
const FONT_MONO = { fontFamily: "'JetBrains Mono', monospace" } as const;
const CHART_TOOLTIP = { background: "#1f2937", border: "1px solid #374151", borderRadius: 8 };

function useFonts() {
  useEffect(() => {
    if (document.getElementById("__aip-fonts")) return;
    const link = document.createElement("link");
    link.id = "__aip-fonts";
    link.rel = "stylesheet";
    link.href =
      "https://fonts.googleapis.com/css2?family=Outfit:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500;600;700&display=swap";
    document.head.appendChild(link);
  }, []);
}

// ── Helpers ──────────────────────────────────────────────────────────
function deltaColor(val: number) {
  if (val > 0) return "text-profit";
  if (val < 0) return "text-loss";
  return "text-gray-400";
}

function formatDelta(val: number, suffix = "%") {
  const sign = val > 0 ? "+" : "";
  return `${sign}${val.toFixed(suffix === "%" ? 1 : 2)}${suffix}`;
}

// ── Stat Pill (hero) ────────────────────────────────────────────────
function StatPill({ label, value, color = "text-white" }: { label: string; value: string; color?: string }) {
  return (
    <span className="inline-flex items-center gap-1 px-2.5 py-1 rounded-full bg-[#1f2937] border border-[#374151] text-[10px] text-gray-400" style={FONT_OUTFIT}>
      <span className="text-gray-500">{label}</span>
      <span className={`font-mono ${color}`} style={FONT_MONO}>{value}</span>
    </span>
  );
}

// ── Comparison Row ──────────────────────────────────────────────────
function ComparisonRow({
  label,
  ai,
  real,
  format = "pct",
}: {
  label: string;
  ai: number;
  real: number;
  format?: "pct" | "num" | "dollar";
}) {
  const delta = ai - real;
  const fmt = (v: number) => {
    if (format === "pct") return `${v.toFixed(1)}%`;
    if (format === "dollar") return formatCurrency(v);
    return v.toFixed(2);
  };

  return (
    <div className="grid grid-cols-4 gap-2 py-2.5 px-4 text-xs border-b border-[#374151]/50 last:border-0">
      <span className="text-gray-400" style={FONT_OUTFIT}>{label}</span>
      <span className="text-white font-semibold text-right" style={FONT_MONO}>{fmt(ai)}</span>
      <span className="text-gray-300 text-right" style={FONT_MONO}>{fmt(real)}</span>
      <span className={`font-semibold text-right ${deltaColor(delta)}`} style={FONT_MONO}>
        {formatDelta(delta, format === "pct" ? "%" : "")}
      </span>
    </div>
  );
}

// ── Equity Curve Chart (Recharts) ───────────────────────────────────
function EquityChart({ data, initialCapital }: { data: EquityPoint[]; initialCapital: number }) {
  if (!data || data.length < 2) {
    return (
      <div className="h-64 flex items-center justify-center text-gray-600 text-sm" style={FONT_OUTFIT}>
        Not enough data for chart
      </div>
    );
  }

  const chartData = data.map((d) => ({
    date: new Date(d.time).toLocaleDateString("en-US", { month: "short", day: "numeric" }),
    equity: d.equity,
    returnPct: ((d.equity - initialCapital) / initialCapital * 100),
  }));

  const up = data[data.length - 1].equity >= data[0].equity;
  const strokeColor = up ? "#22c55e" : "#ef4444";
  const gradId = "aip-eq-grad";

  return (
    <ResponsiveContainer width="100%" height={280}>
      <AreaChart data={chartData}>
        <defs>
          <linearGradient id={gradId} x1="0" y1="0" x2="0" y2="1">
            <stop offset="5%" stopColor={strokeColor} stopOpacity={0.25} />
            <stop offset="95%" stopColor={strokeColor} stopOpacity={0} />
          </linearGradient>
        </defs>
        <CartesianGrid strokeDasharray="3 3" stroke="#1f2937" />
        <XAxis dataKey="date" stroke="#4b5563" tick={{ fontSize: 10, fill: "#6b7280" }} />
        <YAxis stroke="#4b5563" tick={{ fontSize: 10, fill: "#6b7280" }} tickFormatter={(v: number) => `$${(v / 1000).toFixed(1)}k`} />
        <Tooltip
          contentStyle={CHART_TOOLTIP}
          labelStyle={{ color: "#9ca3af" }}
          formatter={(value: number, name: string) => [
            name === "equity" ? formatCurrency(value) : formatPercent(value),
            name === "equity" ? "Equity" : "Return",
          ]}
        />
        <ReferenceLine y={initialCapital} stroke="#374151" strokeDasharray="3 3" />
        <Area type="monotone" dataKey="equity" stroke={strokeColor} strokeWidth={2} fill={`url(#${gradId})`} dot={false} />
      </AreaChart>
    </ResponsiveContainer>
  );
}

// ── Tab Pill Button ─────────────────────────────────────────────────
function TabPill({ label, active, onClick }: { label: string; active: boolean; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      className={`px-4 py-1.5 rounded-full text-xs font-medium transition ${
        active
          ? "bg-[#6366f1]/15 text-[#6366f1] border border-[#6366f1]/30"
          : "bg-[#1f2937]/50 text-gray-400 border border-[#374151] hover:text-white hover:border-gray-500"
      }`}
      style={FONT_OUTFIT}
    >
      {label}
    </button>
  );
}

// ── Setup View (no AI portfolio yet) ────────────────────────────────
function SetupView({ onCreated }: { onCreated: () => void }) {
  const [capital, setCapital] = useState("10000");
  const [creating, setCreating] = useState(false);

  const handleCreate = async () => {
    setCreating(true);
    try {
      await api.createAIPortfolio({ initial_capital: parseFloat(capital) || 10000 });
      onCreated();
    } catch {}
    setCreating(false);
  };

  return (
    <div className="flex flex-col items-center justify-center py-20 text-center">
      <div className="w-16 h-16 rounded-full bg-ai-blue/10 flex items-center justify-center mb-4">
        <svg className="w-8 h-8 text-ai-blue/60" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
          <path strokeLinecap="round" strokeLinejoin="round" d="M9.75 17L9 20l-1 1h8l-1-1-.75-3M3 13h18M5 17h14a2 2 0 002-2V5a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z" />
        </svg>
      </div>
      <h2 className="text-xl font-bold text-white mb-2" style={FONT_OUTFIT}>
        Henry&apos;s Paper Portfolio
      </h2>
      <p className="text-sm text-gray-500 max-w-md mb-6" style={FONT_OUTFIT}>
        Create a paper portfolio managed entirely by Henry. Every incoming signal gets evaluated —
        Henry decides what to buy and what to skip. Compare his performance against your real portfolios.
      </p>
      <div className="flex items-center gap-3">
        <div className="flex items-center gap-2">
          <span className="text-xs text-gray-500" style={FONT_OUTFIT}>Starting Capital:</span>
          <Input
            type="number"
            value={capital}
            onChange={(e) => setCapital(e.target.value)}
            className="w-28 h-8 text-xs bg-surface-light/30 border-border/50 font-mono"
          />
        </div>
        <Button
          onClick={handleCreate}
          disabled={creating}
          className="bg-ai-blue/15 text-ai-blue border border-ai-blue/30 hover:bg-ai-blue/25 h-8 px-4 text-xs"
        >
          {creating ? "Creating..." : "Create AI Portfolio"}
        </Button>
      </div>
    </div>
  );
}

// ── Main Page ───────────────────────────────────────────────────────
export default function AIPortfolioPage() {
  useFonts();

  const [status, setStatus] = useState<AIPortfolioStatus | null>(null);
  const [comparison, setComparison] = useState<AIPortfolioComparison | null>(null);
  const [equityHistory, setEquityHistory] = useState<EquityPoint[]>([]);
  const [decisions, setDecisions] = useState<AIPortfolioDecision[]>([]);
  const [holdings, setHoldings] = useState<AIPortfolioHolding[]>([]);
  const [loading, setLoading] = useState(true);
  const [activeTab, setActiveTab] = useState("holdings");
  const [decisionFilter, setDecisionFilter] = useState("all");

  const fetchAll = useCallback(async () => {
    try {
      const s = await api.getAIPortfolioStatus();
      setStatus(s);
      if (s.exists) {
        const [comp, eq, dec, hold] = await Promise.all([
          api.getAIPortfolioComparison().catch(() => null),
          api.getAIPortfolioEquityHistory(90).catch(() => []),
          api.getAIPortfolioDecisions("all", 50).catch(() => []),
          api.getAIPortfolioHoldings().catch(() => []),
        ]);
        if (comp) setComparison(comp);
        setEquityHistory(eq);
        setDecisions(dec);
        setHoldings(hold);
      }
    } catch {}
  }, []);

  useEffect(() => {
    fetchAll().finally(() => setLoading(false));
  }, [fetchAll]);

  // Auto-refresh
  useEffect(() => {
    const interval = setInterval(fetchAll, 30000);
    return () => clearInterval(interval);
  }, [fetchAll]);

  // Fetch decisions when filter changes
  useEffect(() => {
    if (status?.exists) {
      api.getAIPortfolioDecisions(decisionFilter, 50).then(setDecisions).catch(() => {});
    }
  }, [decisionFilter, status?.exists]);

  if (loading) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-12 w-64 rounded-lg" />
        <Skeleton className="h-6 w-48 rounded-lg" />
        <div className="flex gap-2 mt-3">
          {[1, 2, 3, 4, 5].map((i) => <Skeleton key={i} className="h-7 w-24 rounded-full" />)}
        </div>
        <Skeleton className="h-72 rounded-xl mt-6" />
        <div className="grid grid-cols-2 gap-4 mt-4">
          <Skeleton className="h-32 rounded-xl" />
          <Skeleton className="h-32 rounded-xl" />
        </div>
      </div>
    );
  }

  if (!status?.exists) {
    return <SetupView onCreated={fetchAll} />;
  }

  const ai = comparison?.ai_portfolio;
  const bestReal = comparison?.real_portfolios?.[0];
  const ds = comparison?.decision_stats;
  const initialCapital = status.initial_capital || 10000;

  return (
    <div className="space-y-6 pb-12">

      {/* ═══════════════════ A. HERO ═══════════════════ */}
      <div>
        <div className="flex items-start justify-between">
          <div>
            <h1 className="text-3xl font-bold text-white tracking-tight" style={FONT_OUTFIT}>
              Henry&apos;s AI Portfolio
            </h1>
            <div className="flex items-baseline gap-3 mt-2">
              <span className="text-4xl font-bold text-white" style={FONT_MONO}>
                {formatCurrency(status.equity || 0)}
              </span>
              <span className={`text-lg font-semibold ${pnlColor(status.return_pct || 0)}`} style={FONT_MONO}>
                {formatPercent(status.return_pct || 0)}
              </span>
            </div>
            <div className="flex flex-wrap items-center gap-2 mt-3">
              <StatPill label="Initial" value={formatCurrency(initialCapital)} />
              <StatPill label="Cash" value={formatCurrency(status.cash || 0)} />
              <StatPill label="Positions" value={String(status.open_positions || 0)} />
              <StatPill
                label="Win Rate"
                value={`${(ai?.win_rate ?? 0).toFixed(1)}%`}
                color={(ai?.win_rate ?? 0) >= 50 ? "text-profit" : "text-loss"}
              />
              <StatPill label="Trades" value={String(status.total_trades || 0)} />
            </div>
          </div>
          <div className="flex flex-col gap-1.5 shrink-0">
            <span className="text-[9px] font-semibold px-3 py-1 rounded-full bg-[#6366f1]/10 text-[#6366f1] border border-[#6366f1]/25 text-center" style={FONT_MONO}>
              PAPER
            </span>
            <button
              onClick={async () => {
                if (!confirm("Fix portfolio: resize oversized trades and recalculate cash?")) return;
                try {
                  const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api";
                  const res = await fetch(`${API_URL}/ai-portfolio/fix-all`, { method: "POST", headers: { "Content-Type": "application/json" } });
                  const data = await res.json();
                  alert(`Fixed! Old cash: $${data.old_cash} → New cash: $${data.new_cash}. ${data.fixes_applied?.length || 0} trades adjusted.`);
                  fetchAll();
                } catch { alert("Fix failed"); }
              }}
              className="text-[9px] px-3 py-1 rounded border bg-[#1f2937] border-amber-500/30 text-amber-400 hover:bg-amber-500/10 transition"
              style={FONT_OUTFIT}
            >
              Fix Portfolio
            </button>
            <button
              onClick={async () => {
                const ticker = prompt("Enter ticker to add (e.g. NOK):");
                if (!ticker) return;
                try {
                  const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api";
                  const res = await fetch(`${API_URL}/ai-portfolio/add-trade`, {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ ticker: ticker.toUpperCase() }),
                  });
                  const data = await res.json();
                  if (data.status === "linked") {
                    alert(`Added ${data.ticker} ${data.direction} x${data.qty} @ $${data.entry_price}`);
                    fetchAll();
                  } else if (data.status === "already_linked") {
                    alert(`${data.ticker} is already in the AI portfolio`);
                  } else {
                    alert(data.detail || "Could not add trade");
                  }
                } catch { alert("Failed to add trade"); }
              }}
              className="text-[9px] px-3 py-1 rounded border bg-[#1f2937] border-[#6366f1]/30 text-[#6366f1] hover:bg-[#6366f1]/10 transition"
              style={FONT_OUTFIT}
            >
              + Add Trade
            </button>
          </div>
        </div>
      </div>

      {/* ═══════════════════ B. COMPARISON ═══════════════════ */}
      {ai && bestReal && (
        <div className="rounded-xl border border-[#374151] bg-[#1f2937]/40 overflow-hidden">
          <div className="px-4 py-3 border-b border-[#374151]/60 flex items-center justify-between">
            <h3 className="text-sm font-semibold text-white" style={FONT_OUTFIT}>
              Henry vs {bestReal.name}
            </h3>
            {ds && ds.total_signals > 0 && (
              <span className="text-[10px] text-gray-500" style={FONT_MONO}>
                {ds.acted_on}/{ds.total_signals} signals taken ({ds.acted_on_pct.toFixed(0)}%)
              </span>
            )}
          </div>
          <div className="grid grid-cols-4 gap-2 py-2 px-4 text-[10px] text-gray-500 uppercase tracking-wider border-b border-[#374151]/40">
            <span style={FONT_OUTFIT}>Metric</span>
            <span className="text-right text-[#6366f1]" style={FONT_OUTFIT}>Henry</span>
            <span className="text-right" style={FONT_OUTFIT}>{bestReal.name}</span>
            <span className="text-right" style={FONT_OUTFIT}>Delta</span>
          </div>
          <ComparisonRow label="Total Return" ai={ai.total_return_pct} real={bestReal.total_return_pct} />
          <ComparisonRow label="Win Rate" ai={ai.win_rate} real={bestReal.win_rate} />
          <ComparisonRow label="Profit Factor" ai={ai.profit_factor} real={bestReal.profit_factor} format="num" />
          <ComparisonRow label="Max Drawdown" ai={-ai.max_drawdown_pct} real={-bestReal.max_drawdown_pct} />
          <ComparisonRow label="Trades" ai={ai.total_trades} real={bestReal.total_trades} format="num" />
        </div>
      )}

      {/* ═══════════════════ C. EQUITY CHART ═══════════════════ */}
      <div className="rounded-xl border border-[#374151] bg-[#1f2937]/30 p-5">
        <h3 className="font-semibold text-white text-sm mb-4" style={FONT_OUTFIT}>Equity Curve</h3>
        <EquityChart data={equityHistory} initialCapital={initialCapital} />
      </div>

      {/* ═══════════════════ D. TABS ═══════════════════ */}
      <div className="flex items-center gap-2">
        <TabPill label="Holdings" active={activeTab === "holdings"} onClick={() => setActiveTab("holdings")} />
        <TabPill label="Decisions" active={activeTab === "decisions"} onClick={() => setActiveTab("decisions")} />
        <TabPill label="Ask Henry" active={activeTab === "chat"} onClick={() => setActiveTab("chat")} />
      </div>

      {/* ── Holdings Tab ────────────────────────────────────────── */}
      {activeTab === "holdings" && (
        <div className="rounded-xl border border-[#374151] bg-[#1f2937]/30 overflow-hidden">
          <div className="px-5 py-3 border-b border-[#374151]/60">
            <h3 className="font-semibold text-white text-sm" style={FONT_OUTFIT}>
              Open Positions <span className="text-gray-500 font-normal">({holdings.length})</span>
            </h3>
          </div>
          {holdings.length === 0 ? (
            <div className="text-center py-12 text-gray-500 text-sm" style={FONT_OUTFIT}>No open positions</div>
          ) : (
            <div className="divide-y divide-[#374151]/40">
              {holdings.map((h) => (
                <div key={h.trade_id} className="px-5 py-4 hover:bg-white/[0.02] transition">
                  <div className="flex items-center gap-3 mb-2">
                    <span className="text-lg font-bold text-white" style={FONT_OUTFIT}>
                      {h.ticker}
                    </span>
                    <span className={`text-[9px] font-semibold px-2 py-0.5 rounded-full ${
                      h.direction === "long"
                        ? "bg-profit/10 text-profit border border-profit/20"
                        : "bg-loss/10 text-loss border border-loss/20"
                    }`} style={FONT_MONO}>
                      {h.direction.toUpperCase()}
                    </span>
                    <span className="text-[10px] text-gray-500" style={FONT_MONO}>{h.strategy}</span>
                    <div className="ml-auto text-right">
                      <span className={`text-sm font-semibold ${pnlColor(h.pnl_pct)}`} style={FONT_MONO}>
                        {h.pnl_pct >= 0 ? "+" : ""}{h.pnl_pct.toFixed(2)}%
                      </span>
                      <span className={`text-xs ml-2 ${pnlColor(h.pnl_dollars)}`} style={FONT_MONO}>
                        {formatCurrency(h.pnl_dollars)}
                      </span>
                    </div>
                  </div>
                  <div className="grid grid-cols-4 gap-4 text-xs text-gray-400" style={FONT_MONO}>
                    <div>
                      <span className="text-[10px] text-gray-600 block" style={FONT_OUTFIT}>Entry</span>
                      ${h.entry_price.toFixed(2)}
                    </div>
                    <div>
                      <span className="text-[10px] text-gray-600 block" style={FONT_OUTFIT}>Current</span>
                      ${h.current_price.toFixed(2)}
                    </div>
                    <div>
                      <span className="text-[10px] text-gray-600 block" style={FONT_OUTFIT}>Qty</span>
                      {h.qty.toFixed(4)}
                    </div>
                    <div>
                      <span className="text-[10px] text-gray-600 block" style={FONT_OUTFIT}>Held</span>
                      {h.hold_hours < 24 ? `${h.hold_hours.toFixed(0)}h` : `${(h.hold_hours / 24).toFixed(1)}d`}
                    </div>
                  </div>
                  {h.reasoning && (
                    <div className="mt-2 text-xs text-gray-500 border-t border-[#374151]/30 pt-2" style={FONT_OUTFIT}>
                      <span className="text-[#6366f1]/70">Henry:</span> {h.reasoning}
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* ── Decisions Tab ───────────────────────────────────────── */}
      {activeTab === "decisions" && (
        <div>
          <div className="flex items-center gap-2 mb-4">
            {(["all", "taken", "skipped"] as const).map((f) => (
              <button
                key={f}
                onClick={() => setDecisionFilter(f)}
                className={`px-3 py-1 rounded-full text-[10px] font-medium transition border ${
                  decisionFilter === f
                    ? "bg-[#6366f1]/15 text-[#6366f1] border-[#6366f1]/30"
                    : "bg-[#1f2937]/50 text-gray-400 border-[#374151] hover:text-white"
                }`}
                style={FONT_OUTFIT}
              >
                {f.charAt(0).toUpperCase() + f.slice(1)}
              </button>
            ))}
          </div>

          {decisions.length === 0 ? (
            <div className="text-center py-12 text-gray-500 text-sm" style={FONT_OUTFIT}>No decisions yet</div>
          ) : (
            <div className="space-y-2">
              {decisions.map((d) => (
                <div
                  key={d.id}
                  className={`rounded-xl border p-4 transition ${
                    d.status === "approved" && d.action_type !== "SKIP"
                      ? "border-[#6366f1]/20 bg-[#6366f1]/[0.04]"
                      : "border-[#374151] bg-[#1f2937]/30"
                  }`}
                >
                  <div className="flex items-center gap-2 mb-1.5">
                    <span className="text-sm font-bold text-white" style={FONT_OUTFIT}>{d.ticker}</span>
                    <span className={`text-[9px] font-semibold px-2 py-0.5 rounded-full ${
                      d.action_type === "BUY"
                        ? "bg-profit/10 text-profit border border-profit/20"
                        : "bg-gray-700/30 text-gray-500 border border-gray-600/20"
                    }`} style={FONT_MONO}>
                      {d.action_type}
                    </span>
                    <span className={`text-[10px] ${
                      d.direction === "long" ? "text-profit" : "text-loss"
                    }`} style={FONT_MONO}>
                      {d.direction?.toUpperCase()}
                    </span>
                    <span className="text-[10px] text-gray-500 ml-auto" style={FONT_MONO}>
                      conf {d.confidence}/10
                    </span>
                    <span className="text-[10px] text-gray-600" style={FONT_OUTFIT}>{formatTimeAgo(d.created_at)}</span>
                  </div>
                  <p className="text-xs text-gray-400 leading-relaxed" style={FONT_OUTFIT}>{d.reasoning}</p>
                  {d.outcome && (
                    <div className={`mt-2 text-xs font-semibold ${d.outcome.correct ? "text-profit" : "text-loss"}`} style={FONT_MONO}>
                      Result: {d.outcome.pnl_pct >= 0 ? "+" : ""}{d.outcome.pnl_pct}% ({formatCurrency(d.outcome.pnl_dollars)})
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* ── Chat Tab ────────────────────────────────────────────── */}
      {activeTab === "chat" && <HenryChat />}
    </div>
  );
}

// ── Henry Chat Component ────────────────────────────────────────────────
function HenryChat() {
  const [input, setInput] = useState("");
  const [messages, setMessages] = useState<Array<{ role: "user" | "henry"; text: string }>>([]);
  const [loading, setLoading] = useState(false);
  const viewportRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (viewportRef.current) {
      viewportRef.current.scrollTop = viewportRef.current.scrollHeight;
    }
  }, [messages, loading]);

  const SUGGESTIONS = [
    "Why did you skip the last signal?",
    "What's your best trade so far?",
    "Are any positions concerning you?",
    "How would you change your strategy?",
    "What's your overall assessment of the portfolio?",
  ];

  const send = async (question: string) => {
    if (!question.trim() || loading) return;
    const q = question.trim();
    setInput("");
    setMessages((prev) => [...prev, { role: "user", text: q }]);
    setLoading(true);
    try {
      const res = await api.chatAIPortfolio(q);
      setMessages((prev) => [...prev, { role: "henry", text: res.answer }]);
    } catch {
      setMessages((prev) => [...prev, { role: "henry", text: "Failed to get a response. Try again." }]);
    }
    setLoading(false);
  };

  return (
    <div className="flex flex-col" style={{ minHeight: 500 }}>
      {/* Messages */}
      <div
        ref={viewportRef}
        className="flex-1 rounded-xl border border-[#374151] bg-[#1f2937]/30 p-4 overflow-y-auto space-y-4"
        style={{ maxHeight: 500 }}
      >
        {messages.length === 0 && !loading && (
          <div className="flex flex-col items-center justify-center py-12 text-center">
            <div className="w-12 h-12 rounded-full bg-[#6366f1]/10 flex items-center justify-center mb-3">
              <svg className="w-6 h-6 text-[#6366f1]/50" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M7.5 8.25h9m-9 3H12m-9.75 1.51c0 1.6 1.123 2.994 2.707 3.227 1.129.166 2.27.293 3.423.379.35.026.67.21.865.501L12 21l2.755-4.133a1.14 1.14 0 01.865-.501 48.172 48.172 0 003.423-.379c1.584-.233 2.707-1.626 2.707-3.228V6.741c0-1.602-1.123-2.995-2.707-3.228A48.394 48.394 0 0012 3c-2.392 0-4.744.175-7.043.513C3.373 3.746 2.25 5.14 2.25 6.741v6.018z" />
              </svg>
            </div>
            <p className="text-sm text-gray-500 mb-4" style={FONT_OUTFIT}>Ask Henry about his portfolio decisions</p>
            <div className="flex flex-wrap gap-2 justify-center max-w-lg">
              {SUGGESTIONS.map((s) => (
                <button
                  key={s}
                  onClick={() => send(s)}
                  className="px-3 py-1.5 rounded-full text-xs bg-[#6366f1]/[0.06] text-[#6366f1]/70 border border-[#6366f1]/15 hover:bg-[#6366f1]/15 hover:text-[#6366f1] transition"
                  style={FONT_MONO}
                >
                  {s}
                </button>
              ))}
            </div>
          </div>
        )}

        {messages.map((m, i) => (
          <div key={i} className={`flex ${m.role === "user" ? "justify-end" : "justify-start"}`}>
            <div className={`max-w-[80%] rounded-lg px-3 py-2 text-sm ${
              m.role === "user"
                ? "bg-[#6366f1]/15 text-white border border-[#6366f1]/20"
                : "bg-[#1f2937] text-gray-300 border border-[#374151]"
            }`}>
              {m.role === "henry" ? (
                <div
                  className="ai-prose text-xs"
                  dangerouslySetInnerHTML={{ __html: renderMarkdown(m.text) }}
                />
              ) : (
                <span className="text-xs" style={FONT_OUTFIT}>{m.text}</span>
              )}
            </div>
          </div>
        ))}

        {loading && (
          <div className="flex justify-start">
            <div className="bg-[#1f2937] rounded-lg px-3 py-2 border border-[#374151]">
              <span className="text-xs text-[#6366f1] animate-pulse" style={FONT_OUTFIT}>Henry is thinking...</span>
            </div>
          </div>
        )}
      </div>

      {/* Input */}
      <div className="mt-3 flex items-center gap-2">
        <Input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(input); } }}
          placeholder="Ask Henry about his trades..."
          disabled={loading}
          className="flex-1 h-9 bg-[#1f2937]/50 border-[#374151] text-sm"
          style={FONT_MONO}
        />
        <Button
          onClick={() => send(input)}
          disabled={loading || !input.trim()}
          size="sm"
          className="bg-[#6366f1]/15 text-[#6366f1] border border-[#6366f1]/30 hover:bg-[#6366f1]/25 h-9 px-4"
        >
          Send
        </Button>
      </div>
    </div>
  );
}
