"use client";

import { useState, useEffect, useCallback, useRef } from "react";
import { api } from "@/lib/api";
import { formatTimeAgo, formatCurrency } from "@/lib/formatters";
import { renderMarkdown } from "@/lib/markdown";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import type {
  AIPortfolioStatus,
  AIPortfolioComparison,
  AIPortfolioDecision,
  AIPortfolioHolding,
  EquityPoint,
} from "@/lib/types";

// ── Fonts ────────────────────────────────────────────────────────────────
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

// ── Helpers ──────────────────────────────────────────────────────────────
function deltaColor(val: number) {
  if (val > 0) return "text-profit";
  if (val < 0) return "text-loss";
  return "text-gray-400";
}

function formatDelta(val: number, suffix = "%") {
  const sign = val > 0 ? "+" : "";
  return `${sign}${val.toFixed(suffix === "%" ? 1 : 2)}${suffix}`;
}

// ── Comparison Stat Row ─────────────────────────────────────────────────
function StatRow({
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
    return String(v);
  };

  return (
    <div className="grid grid-cols-4 gap-2 py-2 px-3 text-xs border-b border-border/30 last:border-0">
      <span className="text-gray-400">{label}</span>
      <span className="text-white font-mono text-right">{fmt(ai)}</span>
      <span className="text-gray-300 font-mono text-right">{fmt(real)}</span>
      <span className={`font-mono font-bold text-right ${deltaColor(delta)}`}>
        {formatDelta(delta, format === "pct" ? "%" : "")}
      </span>
    </div>
  );
}

// ── Mini Equity Chart (SVG) ─────────────────────────────────────────────
function EquityChart({ data }: { data: EquityPoint[] }) {
  if (!data || data.length < 2) {
    return (
      <div className="h-48 flex items-center justify-center text-gray-600 text-sm">
        Not enough data for chart
      </div>
    );
  }

  const W = 600;
  const H = 180;
  const pad = { top: 10, right: 10, bottom: 10, left: 10 };
  const iw = W - pad.left - pad.right;
  const ih = H - pad.top - pad.bottom;

  const equities = data.map((d) => d.equity);
  const min = Math.min(...equities) * 0.995;
  const max = Math.max(...equities) * 1.005;
  const range = max - min || 1;

  const points = data
    .map((d, i) => {
      const x = pad.left + (i / (data.length - 1)) * iw;
      const y = pad.top + ih - ((d.equity - min) / range) * ih;
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");

  const up = equities[equities.length - 1] >= equities[0];
  const stroke = up ? "#22c55e" : "#ef4444";

  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full" style={{ height: H }}>
      <defs>
        <linearGradient id="eq-grad" x1="0" x2="0" y1="0" y2="1">
          <stop offset="0%" stopColor={stroke} stopOpacity={0.2} />
          <stop offset="100%" stopColor={stroke} stopOpacity={0} />
        </linearGradient>
      </defs>
      <polygon
        points={`${pad.left},${H - pad.bottom} ${points} ${W - pad.right},${H - pad.bottom}`}
        fill="url(#eq-grad)"
      />
      <polyline
        points={points}
        fill="none"
        stroke={stroke}
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

// ── Setup View (no AI portfolio yet) ────────────────────────────────────
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
      <h2 className="text-xl font-bold text-white mb-2" style={{ fontFamily: "'Outfit', sans-serif" }}>
        Henry&apos;s Paper Portfolio
      </h2>
      <p className="text-sm text-gray-500 max-w-md mb-6">
        Create a paper portfolio managed entirely by Henry. Every incoming signal gets evaluated —
        Henry decides what to buy and what to skip. Compare his performance against your real portfolios.
      </p>
      <div className="flex items-center gap-3">
        <div className="flex items-center gap-2">
          <span className="text-xs text-gray-500">Starting Capital:</span>
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

// ── Main Page ───────────────────────────────────────────────────────────
export default function AIPortfolioPage() {
  useFonts();

  const [status, setStatus] = useState<AIPortfolioStatus | null>(null);
  const [comparison, setComparison] = useState<AIPortfolioComparison | null>(null);
  const [equityHistory, setEquityHistory] = useState<EquityPoint[]>([]);
  const [decisions, setDecisions] = useState<AIPortfolioDecision[]>([]);
  const [holdings, setHoldings] = useState<AIPortfolioHolding[]>([]);
  const [loading, setLoading] = useState(true);
  const [activeTab, setActiveTab] = useState("overview");
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
        <Skeleton className="h-48 rounded-xl" />
        <div className="grid grid-cols-2 gap-4">
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

  return (
    <div>
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <div className="flex items-center gap-3">
          <div className="w-8 h-8 rounded-lg bg-ai-blue/10 flex items-center justify-center">
            <svg className="w-4 h-4 text-ai-blue" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M9.75 17L9 20l-1 1h8l-1-1-.75-3M3 13h18M5 17h14a2 2 0 002-2V5a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z" />
            </svg>
          </div>
          <div>
            <h1 className="text-xl font-bold text-white" style={{ fontFamily: "'Outfit', sans-serif" }}>
              Henry&apos;s Paper Portfolio
            </h1>
            <p className="text-xs text-gray-500">
              ${status.equity?.toFixed(2)} equity · {status.open_positions} open · {status.total_trades} trades
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2">
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
            className="text-[10px] font-mono px-2.5 py-1 rounded-full border border-ai-blue/20 bg-ai-blue/5 text-ai-blue hover:bg-ai-blue/10 transition"
          >
            + Add Trade
          </button>
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
            className="text-[10px] font-mono px-2.5 py-1 rounded-full border border-amber-500/20 bg-amber-500/5 text-amber-400 hover:bg-amber-500/10 transition"
          >
            Fix Portfolio
          </button>
          <span className="text-xs font-mono px-2.5 py-1 rounded-full border border-ai-blue/20 bg-ai-blue/5 text-ai-blue">
            PAPER
          </span>
        </div>
      </div>

      {/* Tabs */}
      <Tabs value={activeTab} onValueChange={setActiveTab} className="mb-6">
        <TabsList>
          <TabsTrigger value="overview">Overview</TabsTrigger>
          <TabsTrigger value="decisions">Decisions</TabsTrigger>
          <TabsTrigger value="holdings">Holdings</TabsTrigger>
          <TabsTrigger value="chat">Ask Henry</TabsTrigger>
        </TabsList>
      </Tabs>

      {/* ── Overview Tab ──────────────────────────────────────────── */}
      {activeTab === "overview" && (
        <div className="space-y-6">
          {/* Hero Stats */}
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
            <div className="rounded-xl border border-border/40 bg-surface-light/20 p-4">
              <div className="text-[10px] text-gray-500 uppercase tracking-wider mb-1">Return</div>
              <div className={`text-xl font-bold font-mono ${deltaColor(status.return_pct || 0)}`}>
                {formatDelta(status.return_pct || 0)}
              </div>
            </div>
            <div className="rounded-xl border border-border/40 bg-surface-light/20 p-4">
              <div className="text-[10px] text-gray-500 uppercase tracking-wider mb-1">Win Rate</div>
              <div className="text-xl font-bold font-mono text-white">
                {ai?.win_rate.toFixed(1) || "0"}%
              </div>
            </div>
            <div className="rounded-xl border border-border/40 bg-surface-light/20 p-4">
              <div className="text-[10px] text-gray-500 uppercase tracking-wider mb-1">Selectivity</div>
              <div className="text-xl font-bold font-mono text-white">
                {ds?.acted_on_pct.toFixed(0) || "0"}%
              </div>
              <div className="text-[10px] text-gray-600 mt-0.5">
                {ds?.acted_on || 0}/{ds?.total_signals || 0} signals
              </div>
            </div>
            <div className="rounded-xl border border-border/40 bg-surface-light/20 p-4">
              <div className="text-[10px] text-gray-500 uppercase tracking-wider mb-1">Max DD</div>
              <div className="text-xl font-bold font-mono text-loss">
                {ai?.max_drawdown_pct.toFixed(1) || "0"}%
              </div>
            </div>
          </div>

          {/* Equity Chart */}
          <div className="rounded-xl border border-border/40 bg-surface-light/10 p-4">
            <h3 className="text-sm font-semibold text-white mb-3">Equity Curve</h3>
            <EquityChart data={equityHistory} />
          </div>

          {/* Comparison Table */}
          {ai && bestReal && (
            <div className="rounded-xl border border-border/40 bg-surface-light/10 p-4">
              <h3 className="text-sm font-semibold text-white mb-3">AI vs Real Portfolio</h3>
              <div className="grid grid-cols-4 gap-2 py-2 px-3 text-[10px] text-gray-500 uppercase tracking-wider border-b border-border/50">
                <span>Metric</span>
                <span className="text-right text-ai-blue">AI Portfolio</span>
                <span className="text-right">{bestReal.name}</span>
                <span className="text-right">Delta</span>
              </div>
              <StatRow label="Total Return" ai={ai.total_return_pct} real={bestReal.total_return_pct} />
              <StatRow label="Win Rate" ai={ai.win_rate} real={bestReal.win_rate} />
              <StatRow label="Profit Factor" ai={ai.profit_factor} real={bestReal.profit_factor} format="num" />
              <StatRow label="Max Drawdown" ai={-ai.max_drawdown_pct} real={-bestReal.max_drawdown_pct} />
              <StatRow label="Trades" ai={ai.total_trades} real={bestReal.total_trades} format="num" />
            </div>
          )}

          {/* Signal Filter Stats */}
          {ds && ds.total_signals > 0 && (
            <div className="rounded-xl border border-border/40 bg-surface-light/10 p-4">
              <h3 className="text-sm font-semibold text-white mb-3">Signal Selectivity</h3>
              <div className="space-y-2 text-xs">
                <div className="flex justify-between text-gray-400">
                  <span>Signals received</span>
                  <span className="text-white font-mono">{ds.total_signals}</span>
                </div>
                <div className="flex justify-between text-gray-400">
                  <span>Acted on</span>
                  <span className="text-profit font-mono">{ds.acted_on} ({ds.acted_on_pct}%)</span>
                </div>
                <div className="flex justify-between text-gray-400">
                  <span>Skipped</span>
                  <span className="text-gray-300 font-mono">{ds.skipped}</span>
                </div>
                <div className="border-t border-border/30 pt-2 flex justify-between text-gray-400">
                  <span>Avg confidence (taken)</span>
                  <span className="text-white font-mono">{ds.avg_confidence_taken}/10</span>
                </div>
                <div className="flex justify-between text-gray-400">
                  <span>Avg confidence (skipped)</span>
                  <span className="text-gray-500 font-mono">{ds.avg_confidence_skipped}/10</span>
                </div>
              </div>
            </div>
          )}
        </div>
      )}

      {/* ── Decisions Tab ─────────────────────────────────────────── */}
      {activeTab === "decisions" && (
        <div>
          <div className="flex items-center gap-2 mb-4">
            <Tabs value={decisionFilter} onValueChange={setDecisionFilter}>
              <TabsList>
                <TabsTrigger value="all">All</TabsTrigger>
                <TabsTrigger value="taken">Taken</TabsTrigger>
                <TabsTrigger value="skipped">Skipped</TabsTrigger>
              </TabsList>
            </Tabs>
          </div>

          {decisions.length === 0 ? (
            <div className="text-center py-12 text-gray-500 text-sm">No decisions yet</div>
          ) : (
            <div className="space-y-2">
              {decisions.map((d) => (
                <div
                  key={d.id}
                  className={`rounded-lg border p-3 ${
                    d.status === "approved" && d.action_type !== "SKIP"
                      ? "border-ai-blue/20 bg-ai-blue/5"
                      : "border-border/30 bg-surface-light/10"
                  }`}
                >
                  <div className="flex items-center gap-2 mb-1.5">
                    <span className="text-sm font-bold text-white">{d.ticker}</span>
                    <span className={`text-[10px] font-mono px-1.5 py-0.5 rounded-full ${
                      d.action_type === "BUY"
                        ? "bg-profit/10 text-profit border border-profit/20"
                        : "bg-gray-700/30 text-gray-500 border border-gray-600/20"
                    }`}>
                      {d.action_type}
                    </span>
                    <span className={`text-[10px] font-mono ${
                      d.direction === "long" ? "text-profit" : "text-loss"
                    }`}>
                      {d.direction?.toUpperCase()}
                    </span>
                    <span className="text-[10px] text-gray-500 font-mono ml-auto">
                      conf {d.confidence}/10
                    </span>
                    <span className="text-[10px] text-gray-600">{formatTimeAgo(d.created_at)}</span>
                  </div>
                  <p className="text-xs text-gray-400">{d.reasoning}</p>
                  {d.outcome && (
                    <div className={`mt-2 text-xs font-mono ${d.outcome.correct ? "text-profit" : "text-loss"}`}>
                      Result: {d.outcome.pnl_pct >= 0 ? "+" : ""}{d.outcome.pnl_pct}% (${d.outcome.pnl_dollars.toFixed(2)})
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* ── Holdings Tab ──────────────────────────────────────────── */}
      {activeTab === "holdings" && (
        <div>
          {holdings.length === 0 ? (
            <div className="text-center py-12 text-gray-500 text-sm">No open positions</div>
          ) : (
            <div className="space-y-2">
              {holdings.map((h) => (
                <div key={h.trade_id} className="rounded-lg border border-border/40 bg-surface-light/20 p-4">
                  <div className="flex items-center gap-3 mb-2">
                    <span className="text-lg font-bold text-white" style={{ fontFamily: "'Outfit', sans-serif" }}>
                      {h.ticker}
                    </span>
                    <span className={`text-xs font-mono px-2 py-0.5 rounded-full ${
                      h.direction === "long"
                        ? "bg-profit/10 text-profit border border-profit/20"
                        : "bg-loss/10 text-loss border border-loss/20"
                    }`}>
                      {h.direction.toUpperCase()}
                    </span>
                    <span className="text-[10px] text-gray-500 font-mono">{h.strategy}</span>
                    <span className={`ml-auto text-sm font-mono font-bold ${deltaColor(h.pnl_pct)}`}>
                      {h.pnl_pct >= 0 ? "+" : ""}{h.pnl_pct.toFixed(2)}%
                    </span>
                  </div>
                  <div className="grid grid-cols-4 gap-4 text-xs text-gray-400 font-mono">
                    <div>
                      <span className="text-[10px] text-gray-600 block">Entry</span>
                      ${h.entry_price.toFixed(2)}
                    </div>
                    <div>
                      <span className="text-[10px] text-gray-600 block">Current</span>
                      ${h.current_price.toFixed(2)}
                    </div>
                    <div>
                      <span className="text-[10px] text-gray-600 block">Qty</span>
                      {h.qty.toFixed(4)}
                    </div>
                    <div>
                      <span className="text-[10px] text-gray-600 block">Held</span>
                      {h.hold_hours < 24 ? `${h.hold_hours.toFixed(0)}h` : `${(h.hold_hours / 24).toFixed(1)}d`}
                    </div>
                  </div>
                  {h.reasoning && (
                    <div className="mt-2 text-xs text-gray-500 border-t border-border/20 pt-2">
                      <span className="text-ai-blue/70">Henry:</span> {h.reasoning}
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* ── Chat Tab ──────────────────────────────────────────────── */}
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
        className="flex-1 rounded-xl border border-border/40 bg-surface-light/10 p-4 overflow-y-auto space-y-4"
        style={{ maxHeight: 500 }}
      >
        {messages.length === 0 && !loading && (
          <div className="flex flex-col items-center justify-center py-12 text-center">
            <div className="w-12 h-12 rounded-full bg-ai-blue/10 flex items-center justify-center mb-3">
              <svg className="w-6 h-6 text-ai-blue/50" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M7.5 8.25h9m-9 3H12m-9.75 1.51c0 1.6 1.123 2.994 2.707 3.227 1.129.166 2.27.293 3.423.379.35.026.67.21.865.501L12 21l2.755-4.133a1.14 1.14 0 01.865-.501 48.172 48.172 0 003.423-.379c1.584-.233 2.707-1.626 2.707-3.228V6.741c0-1.602-1.123-2.995-2.707-3.228A48.394 48.394 0 0012 3c-2.392 0-4.744.175-7.043.513C3.373 3.746 2.25 5.14 2.25 6.741v6.018z" />
              </svg>
            </div>
            <p className="text-sm text-gray-500 mb-4">Ask Henry about his portfolio decisions</p>
            <div className="flex flex-wrap gap-2 justify-center max-w-lg">
              {SUGGESTIONS.map((s) => (
                <button
                  key={s}
                  onClick={() => send(s)}
                  className="px-3 py-1.5 rounded-full text-xs font-mono bg-ai-blue/8 text-ai-blue/70 border border-ai-blue/15 hover:bg-ai-blue/15 hover:text-ai-blue transition"
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
                ? "bg-ai-blue/15 text-white border border-ai-blue/20"
                : "bg-surface-light/30 text-gray-300 border border-border/30"
            }`}>
              {m.role === "henry" ? (
                <div
                  className="ai-prose text-xs"
                  dangerouslySetInnerHTML={{ __html: renderMarkdown(m.text) }}
                />
              ) : (
                <span className="text-xs">{m.text}</span>
              )}
            </div>
          </div>
        ))}

        {loading && (
          <div className="flex justify-start">
            <div className="bg-surface-light/30 rounded-lg px-3 py-2 border border-border/30">
              <span className="text-xs text-ai-blue animate-pulse">Henry is thinking...</span>
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
          className="flex-1 h-9 bg-surface-light/30 border-border/50 text-sm font-mono"
        />
        <Button
          onClick={() => send(input)}
          disabled={loading || !input.trim()}
          size="sm"
          className="bg-ai-blue/15 text-ai-blue border border-ai-blue/30 hover:bg-ai-blue/25 h-9 px-4"
        >
          Send
        </Button>
      </div>
    </div>
  );
}
