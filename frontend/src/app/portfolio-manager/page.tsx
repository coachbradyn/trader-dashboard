"use client";

import { useState, useEffect, useCallback, useRef } from "react";
import { api } from "@/lib/api";
import { usePolling } from "@/hooks/usePolling";
import { formatCurrency, formatPercent, formatDate, formatTimeAgo, pnlColor } from "@/lib/formatters";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import { Skeleton } from "@/components/ui/skeleton";
import type {
  PortfolioAction, PortfolioHolding, ActionStats,
  BacktestImportData, BacktestTradeData, PortfolioSettings,
} from "@/lib/types";

const FONT_OUTFIT = { fontFamily: "'Outfit', sans-serif" } as const;
const FONT_MONO = { fontFamily: "'JetBrains Mono', monospace" } as const;

function useFonts() {
  useEffect(() => {
    if (document.getElementById("__pm-fonts")) return;
    const link = document.createElement("link");
    link.id = "__pm-fonts";
    link.rel = "stylesheet";
    link.href = "https://fonts.googleapis.com/css2?family=Outfit:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500;600;700&display=swap";
    document.head.appendChild(link);
  }, []);
}

// ── Helpers ──────────────────────────────────────────────────────────

function triggerBorder(type: string) {
  if (type === "THRESHOLD") return "border-l-loss";
  if (type === "SIGNAL") return "border-l-amber-500";
  return "border-l-gray-600";
}

function triggerLabel(type: string) {
  if (type === "THRESHOLD") return "Threshold";
  if (type === "SIGNAL") return "Signal";
  return "Review";
}

function triggerBadgeColor(type: string) {
  if (type === "THRESHOLD") return "bg-loss/15 text-loss";
  if (type === "SIGNAL") return "bg-amber-500/15 text-amber-400";
  return "bg-gray-700 text-gray-400";
}

function actionBadgeColor(type: string) {
  const map: Record<string, string> = {
    BUY: "bg-profit/15 text-profit",
    ADD: "bg-profit/15 text-profit",
    SELL: "bg-loss/15 text-loss",
    CLOSE: "bg-loss/15 text-loss",
    TRIM: "bg-amber-500/15 text-amber-400",
    REBALANCE: "bg-ai-blue/15 text-ai-blue",
  };
  return map[type] || "bg-gray-700 text-gray-400";
}

function expiryCountdown(expiresAt: string | null): string {
  if (!expiresAt) return "";
  const diff = new Date(expiresAt).getTime() - Date.now();
  if (diff <= 0) return "expired";
  const h = Math.floor(diff / 3600000);
  const m = Math.floor((diff % 3600000) / 60000);
  return `${h}h ${m}m`;
}

function ConfidenceGauge({ value }: { value: number }) {
  const pct = value * 10;
  const color = value >= 7 ? "bg-profit" : value >= 4 ? "bg-amber-500" : "bg-loss";
  return (
    <div className="flex items-center gap-2">
      <div className="w-16 h-1.5 rounded-full bg-surface-light overflow-hidden">
        <div className={`h-full rounded-full ${color} transition-all`} style={{ width: `${pct}%` }} />
      </div>
      <span className="text-xs font-mono text-gray-400">{value}/10</span>
    </div>
  );
}

function Toast({ message, type }: { message: string; type: "success" | "error" }) {
  const c = type === "success" ? "bg-profit/15 text-profit border-profit/30" : "bg-loss/15 text-loss border-loss/30";
  return (
    <div className="fixed bottom-6 right-6 z-50 animate-fade-in" style={FONT_OUTFIT}>
      <div className={`px-5 py-3 rounded-lg text-sm font-medium shadow-2xl backdrop-blur-md border ${c}`}>{message}</div>
    </div>
  );
}

function SectionTitle({ children }: { children: React.ReactNode }) {
  return <h3 className="text-sm font-semibold text-white tracking-wide uppercase" style={FONT_OUTFIT}>{children}</h3>;
}

// ══════════════════════════════════════════════════════════════════════
// ACTION QUEUE TAB
// ══════════════════════════════════════════════════════════════════════

function ActionQueueTab({ onToast }: { onToast: (msg: string, type: "success" | "error") => void }) {
  const { data: actions, loading, refresh } = usePolling(() => api.getActions("pending"), 15000);
  const { data: stats, refresh: refreshStats } = usePolling(() => api.getActionStats(), 30000);
  const [actionFilter, setActionFilter] = useState<"pending" | "approved" | "rejected" | "all">("pending");
  const [filteredActions, setFilteredActions] = useState<PortfolioAction[]>([]);
  const [loadingFilter, setLoadingFilter] = useState(false);

  useEffect(() => {
    if (actionFilter === "pending" && actions) {
      setFilteredActions(actions);
      return;
    }
    setLoadingFilter(true);
    api.getActions(actionFilter).then((a) => {
      setFilteredActions(a);
      setLoadingFilter(false);
    }).catch(() => setLoadingFilter(false));
  }, [actionFilter, actions]);

  const handleApprove = async (id: string) => {
    try {
      await api.approveAction(id);
      onToast("Action approved", "success");
      refresh();
      refreshStats();
    } catch { onToast("Failed to approve", "error"); }
  };

  const handleReject = async (id: string) => {
    try {
      await api.rejectAction(id);
      onToast("Action rejected", "success");
      refresh();
      refreshStats();
    } catch { onToast("Failed to reject", "error"); }
  };

  return (
    <div className="space-y-4">
      {/* Stats Bar */}
      <div className="flex flex-wrap items-center gap-3 text-xs font-mono" style={FONT_MONO}>
        <span className="text-white">{stats?.pending_count ?? 0} pending</span>
        <span className="text-gray-600">|</span>
        <span className="text-profit">{stats?.approved_today ?? 0} approved today</span>
        <span className="text-gray-600">|</span>
        <span className="text-gray-400">
          Hit rate: {stats?.hit_rate != null ? `${stats.hit_rate}%` : "—"}
          {stats?.hit_rate_high_confidence != null && (
            <span className="text-gray-600 ml-1">({stats.hit_rate_high_confidence}% high conf)</span>
          )}
        </span>
      </div>

      {/* Filter */}
      <div className="flex gap-1">
        {(["pending", "approved", "rejected", "all"] as const).map((f) => (
          <button key={f} onClick={() => setActionFilter(f)}
            className={`px-3 py-1 text-xs rounded-md font-medium transition ${
              actionFilter === f ? "bg-surface-light text-white" : "text-gray-500 hover:text-gray-300"
            }`} style={FONT_MONO}
          >{f}</button>
        ))}
      </div>

      {/* Action Cards */}
      {loading || loadingFilter ? (
        <div className="space-y-3">{[1, 2, 3].map((i) => <Skeleton key={i} className="h-32 rounded-xl" />)}</div>
      ) : filteredActions.length === 0 ? (
        <div className="py-16 text-center">
          <div className="w-14 h-14 mx-auto mb-4 rounded-2xl bg-surface-light/50 flex items-center justify-center">
            <svg className="w-7 h-7 text-gray-600" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M9 12.75 11.25 15 15 9.75M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z" />
            </svg>
          </div>
          <p className="text-gray-500 text-sm" style={FONT_OUTFIT}>
            {actionFilter === "pending" ? "No pending actions — portfolio is on track" : `No ${actionFilter} actions`}
          </p>
        </div>
      ) : (
        <div className="space-y-3">
          {filteredActions.map((a) => (
            <Card key={a.id} className={`border-l-4 ${triggerBorder(a.trigger_type)} bg-surface-light/30 border-border`}>
              <CardContent className="p-4">
                <div className="flex items-start justify-between gap-4">
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 mb-2 flex-wrap">
                      <span className="text-lg font-bold text-white" style={FONT_OUTFIT}>{a.ticker}</span>
                      <Badge className={`text-[10px] px-2 py-0.5 ${a.direction === "long" ? "bg-profit/15 text-profit" : "bg-loss/15 text-loss"}`}>
                        {a.direction.toUpperCase()}
                      </Badge>
                      <Badge className={`text-[10px] px-2 py-0.5 ${actionBadgeColor(a.action_type)}`}>
                        {a.action_type}
                      </Badge>
                      <Badge className={`text-[10px] px-2 py-0.5 ${triggerBadgeColor(a.trigger_type)}`}>
                        {triggerLabel(a.trigger_type)}
                      </Badge>
                      {a.status !== "pending" && (
                        <Badge className={`text-[10px] px-2 py-0.5 ${
                          a.status === "approved" ? "bg-profit/15 text-profit" :
                          a.status === "rejected" ? "bg-loss/15 text-loss" :
                          "bg-gray-700 text-gray-400"
                        }`}>{a.status}</Badge>
                      )}
                    </div>
                    <p className="text-sm text-gray-300 mb-2 leading-relaxed" style={FONT_OUTFIT}>{a.reasoning}</p>
                    <div className="flex items-center gap-4 flex-wrap">
                      <ConfidenceGauge value={a.confidence} />
                      {a.suggested_qty && (
                        <span className="text-xs text-gray-500 font-mono">qty: {a.suggested_qty}</span>
                      )}
                      {a.current_price && (
                        <span className="text-xs text-gray-500 font-mono">@ {formatCurrency(a.current_price)}</span>
                      )}
                      {a.status === "pending" && a.expires_at && (
                        <span className="text-xs text-gray-600 font-mono">expires {expiryCountdown(a.expires_at)}</span>
                      )}
                      {a.outcome_correct != null && (
                        <span className={`text-xs font-mono ${a.outcome_correct ? "text-profit" : "text-loss"}`}>
                          outcome: {a.outcome_pnl != null ? formatPercent(a.outcome_pnl) : (a.outcome_correct ? "correct" : "wrong")}
                        </span>
                      )}
                    </div>
                  </div>
                  {a.status === "pending" && (
                    <div className="flex flex-col gap-2 shrink-0">
                      <Button size="sm" onClick={() => handleApprove(a.id)}
                        className="bg-profit/20 text-profit hover:bg-profit/30 border border-profit/30 text-xs h-8 px-4">
                        Approve
                      </Button>
                      <Button size="sm" variant="ghost" onClick={() => handleReject(a.id)}
                        className="text-gray-500 hover:text-gray-300 text-xs h-8 px-4">
                        Reject
                      </Button>
                    </div>
                  )}
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}

// ══════════════════════════════════════════════════════════════════════
// HOLDINGS TAB
// ══════════════════════════════════════════════════════════════════════

function HoldingsTab({ portfolios, onToast }: { portfolios: PortfolioSettings[]; onToast: (msg: string, type: "success" | "error") => void }) {
  const [selectedPortfolio, setSelectedPortfolio] = useState<string>("");
  const { data: holdings, loading, refresh } = usePolling(
    () => api.getHoldings(selectedPortfolio || undefined),
    15000,
  );
  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState({
    portfolio_id: "", ticker: "", direction: "long", entry_price: "", qty: "", entry_date: "", strategy_name: "", notes: "",
  });

  useEffect(() => {
    if (portfolios.length > 0 && !selectedPortfolio) {
      setSelectedPortfolio(portfolios[0].id);
    }
  }, [portfolios, selectedPortfolio]);

  const handleSubmit = async () => {
    if (!form.portfolio_id || !form.ticker || !form.entry_price || !form.qty || !form.entry_date) {
      onToast("Fill in required fields", "error");
      return;
    }
    try {
      await api.createHolding({
        portfolio_id: form.portfolio_id,
        ticker: form.ticker.toUpperCase(),
        direction: form.direction,
        entry_price: parseFloat(form.entry_price),
        qty: parseFloat(form.qty),
        entry_date: new Date(form.entry_date).toISOString(),
        strategy_name: form.strategy_name || undefined,
        notes: form.notes || undefined,
      });
      onToast("Holding added", "success");
      setShowForm(false);
      setForm({ portfolio_id: form.portfolio_id, ticker: "", direction: "long", entry_price: "", qty: "", entry_date: "", strategy_name: "", notes: "" });
      refresh();
    } catch { onToast("Failed to add holding", "error"); }
  };

  const handleDelete = async (id: string) => {
    try {
      await api.deleteHolding(id);
      onToast("Holding removed", "success");
      refresh();
    } catch { onToast("Failed to remove", "error"); }
  };

  // Concentration bar
  const totalValue = (holdings || []).filter((h) => h.is_active && h.current_price).reduce((sum, h) => sum + (h.current_price! * h.qty), 0);
  const tickerValues: Record<string, number> = {};
  (holdings || []).filter((h) => h.is_active && h.current_price).forEach((h) => {
    tickerValues[h.ticker] = (tickerValues[h.ticker] || 0) + h.current_price! * h.qty;
  });
  const concentrationColors = ["bg-ai-blue", "bg-profit", "bg-amber-500", "bg-loss", "bg-purple-500", "bg-cyan-500", "bg-pink-500"];

  return (
    <div className="space-y-4">
      {/* Portfolio selector */}
      <div className="flex items-center gap-3 flex-wrap">
        <select value={selectedPortfolio} onChange={(e) => setSelectedPortfolio(e.target.value)}
          className="bg-surface-light border border-border rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:ring-1 focus:ring-ai-blue"
          style={FONT_MONO}>
          <option value="">All Portfolios</option>
          {portfolios.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
        </select>
        <Button size="sm" onClick={() => { setShowForm(!showForm); setForm({ ...form, portfolio_id: selectedPortfolio || (portfolios[0]?.id ?? "") }); }}
          className="bg-ai-blue/20 text-ai-blue hover:bg-ai-blue/30 border border-ai-blue/30 text-xs h-9 px-4">
          {showForm ? "Cancel" : "+ Add Holding"}
        </Button>
      </div>

      {/* Concentration bar */}
      {totalValue > 0 && (
        <div className="space-y-1.5">
          <div className="flex items-center justify-between">
            <span className="text-xs text-gray-500" style={FONT_OUTFIT}>Concentration</span>
            <span className="text-xs text-gray-500 font-mono">{formatCurrency(totalValue)} total</span>
          </div>
          <div className="flex h-2 rounded-full overflow-hidden bg-surface-light">
            {Object.entries(tickerValues).sort((a, b) => b[1] - a[1]).map(([ticker, val], i) => (
              <div key={ticker} className={`${concentrationColors[i % concentrationColors.length]} transition-all`}
                style={{ width: `${(val / totalValue) * 100}%` }} title={`${ticker}: ${((val / totalValue) * 100).toFixed(1)}%`} />
            ))}
          </div>
          <div className="flex gap-3 flex-wrap">
            {Object.entries(tickerValues).sort((a, b) => b[1] - a[1]).map(([ticker, val], i) => (
              <span key={ticker} className="flex items-center gap-1 text-[10px] text-gray-500">
                <span className={`w-2 h-2 rounded-full ${concentrationColors[i % concentrationColors.length]}`} />
                {ticker} {((val / totalValue) * 100).toFixed(0)}%
              </span>
            ))}
          </div>
        </div>
      )}

      {/* Add holding form */}
      {showForm && (
        <Card className="bg-surface-light/30 border-border border-ai-blue/30">
          <CardContent className="p-4 space-y-3">
            <SectionTitle>Add Holding</SectionTitle>
            <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 gap-3">
              <div>
                <label className="text-[10px] text-gray-500 mb-1 block" style={FONT_OUTFIT}>Portfolio</label>
                <select value={form.portfolio_id} onChange={(e) => setForm({ ...form, portfolio_id: e.target.value })}
                  className="w-full bg-surface border border-border rounded-md px-2 py-1.5 text-xs text-white" style={FONT_MONO}>
                  {portfolios.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
                </select>
              </div>
              <div>
                <label className="text-[10px] text-gray-500 mb-1 block" style={FONT_OUTFIT}>Ticker</label>
                <Input value={form.ticker} onChange={(e) => setForm({ ...form, ticker: e.target.value })}
                  placeholder="AAPL" className="h-8 text-xs bg-surface border-border" style={FONT_MONO} />
              </div>
              <div>
                <label className="text-[10px] text-gray-500 mb-1 block" style={FONT_OUTFIT}>Direction</label>
                <div className="inline-flex rounded-md border border-border overflow-hidden">
                  {["long", "short"].map((d) => (
                    <button key={d} onClick={() => setForm({ ...form, direction: d })}
                      className={`px-3 py-1.5 text-[11px] font-mono font-medium transition-all ${
                        form.direction === d ? "bg-primary text-white" : "bg-surface-light/40 text-gray-500 hover:text-gray-300"
                      }`}>{d.toUpperCase()}</button>
                  ))}
                </div>
              </div>
              <div>
                <label className="text-[10px] text-gray-500 mb-1 block" style={FONT_OUTFIT}>Entry Price</label>
                <Input type="number" step="0.01" value={form.entry_price} onChange={(e) => setForm({ ...form, entry_price: e.target.value })}
                  placeholder="150.00" className="h-8 text-xs bg-surface border-border" style={FONT_MONO} />
              </div>
              <div>
                <label className="text-[10px] text-gray-500 mb-1 block" style={FONT_OUTFIT}>Quantity</label>
                <Input type="number" step="1" value={form.qty} onChange={(e) => setForm({ ...form, qty: e.target.value })}
                  placeholder="10" className="h-8 text-xs bg-surface border-border" style={FONT_MONO} />
              </div>
              <div>
                <label className="text-[10px] text-gray-500 mb-1 block" style={FONT_OUTFIT}>Entry Date</label>
                <Input type="date" value={form.entry_date} onChange={(e) => setForm({ ...form, entry_date: e.target.value })}
                  className="h-8 text-xs bg-surface border-border" style={FONT_MONO} />
              </div>
              <div>
                <label className="text-[10px] text-gray-500 mb-1 block" style={FONT_OUTFIT}>Strategy</label>
                <Input value={form.strategy_name} onChange={(e) => setForm({ ...form, strategy_name: e.target.value })}
                  placeholder="S1, manual..." className="h-8 text-xs bg-surface border-border" style={FONT_MONO} />
              </div>
              <div className="flex items-end">
                <Button size="sm" onClick={handleSubmit}
                  className="bg-ai-blue hover:bg-ai-blue/80 text-white text-xs h-8 px-6 w-full">
                  Add
                </Button>
              </div>
            </div>
          </CardContent>
        </Card>
      )}

      {/* Holdings list */}
      {loading ? (
        <div className="space-y-2">{[1, 2, 3].map((i) => <Skeleton key={i} className="h-16 rounded-lg" />)}</div>
      ) : !holdings?.length ? (
        <div className="py-16 text-center">
          <div className="w-14 h-14 mx-auto mb-4 rounded-2xl bg-surface-light/50 flex items-center justify-center">
            <svg className="w-7 h-7 text-gray-600" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M2.25 18.75a60.07 60.07 0 0 1 15.797 2.101c.727.198 1.453-.342 1.453-1.096V18.75M3.75 4.5v.75A.75.75 0 0 1 3 6h-.75m0 0v-.375c0-.621.504-1.125 1.125-1.125H20.25M2.25 6v9m18-10.5v.75c0 .414.336.75.75.75h.75m-1.5-1.5h.375c.621 0 1.125.504 1.125 1.125v9.75c0 .621-.504 1.125-1.125 1.125h-.375m1.5-1.5H21a.75.75 0 0 0-.75.75v.75m0 0H3.75m0 0h-.375a1.125 1.125 0 0 1-1.125-1.125V15m1.5 1.5v-.75A.75.75 0 0 0 3 15h-.75M15 10.5a3 3 0 1 1-6 0 3 3 0 0 1 6 0Zm3 0h.008v.008H18V10.5Zm-12 0h.008v.008H6V10.5Z" />
            </svg>
          </div>
          <p className="text-gray-500 text-sm" style={FONT_OUTFIT}>No holdings yet — add your current positions above</p>
        </div>
      ) : (
        <div className="space-y-2">
          {holdings.map((h) => (
            <div key={h.id} className="flex items-center gap-3 px-4 py-3 rounded-lg bg-surface-light/20 border border-border hover:border-border/80 transition group">
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2 mb-0.5">
                  <span className="text-sm font-bold text-white" style={FONT_OUTFIT}>{h.ticker}</span>
                  <Badge className={`text-[9px] px-1.5 py-0 ${h.direction === "long" ? "bg-profit/15 text-profit" : "bg-loss/15 text-loss"}`}>
                    {h.direction.toUpperCase()}
                  </Badge>
                  <Badge className="text-[9px] px-1.5 py-0 bg-surface-light text-gray-400">
                    {h.source}
                  </Badge>
                  {h.trade_id && <Badge className="text-[9px] px-1.5 py-0 bg-ai-blue/15 text-ai-blue">linked</Badge>}
                </div>
                <div className="flex items-center gap-3 text-[11px] text-gray-500 font-mono">
                  <span>{h.qty} @ {formatCurrency(h.entry_price)}</span>
                  {h.current_price && <span>now {formatCurrency(h.current_price)}</span>}
                  <span>{formatDate(h.entry_date)}</span>
                </div>
              </div>
              {h.unrealized_pnl != null && (
                <div className="text-right shrink-0">
                  <div className={`text-sm font-mono font-semibold ${pnlColor(h.unrealized_pnl)}`}>
                    {formatCurrency(h.unrealized_pnl)}
                  </div>
                  <div className={`text-[11px] font-mono ${pnlColor(h.unrealized_pnl_pct ?? 0)}`}>
                    {formatPercent(h.unrealized_pnl_pct ?? 0)}
                  </div>
                </div>
              )}
              <button onClick={() => handleDelete(h.id)}
                className="opacity-0 group-hover:opacity-100 text-gray-600 hover:text-loss transition p-1">
                <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="m14.74 9-.346 9m-4.788 0L9.26 9m9.968-3.21c.342.052.682.107 1.022.166m-1.022-.165L18.16 19.673a2.25 2.25 0 0 1-2.244 2.077H8.084a2.25 2.25 0 0 1-2.244-2.077L4.772 5.79m14.456 0a48.108 48.108 0 0 0-3.478-.397m-12 .562c.34-.059.68-.114 1.022-.165m0 0a48.11 48.11 0 0 1 3.478-.397m7.5 0v-.916c0-1.18-.91-2.164-2.09-2.201a51.964 51.964 0 0 0-3.32 0c-1.18.037-2.09 1.022-2.09 2.201v.916m7.5 0a48.667 48.667 0 0 0-7.5 0" />
                </svg>
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ══════════════════════════════════════════════════════════════════════
// BACKTEST TAB
// ══════════════════════════════════════════════════════════════════════

function BacktestTab({ onToast }: { onToast: (msg: string, type: "success" | "error") => void }) {
  const { data: imports, loading, refresh } = usePolling(() => api.getBacktestImports(), 60000);
  const [uploading, setUploading] = useState(false);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [expandedTrades, setExpandedTrades] = useState<BacktestTradeData[]>([]);
  const [dragActive, setDragActive] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  const handleUpload = async (files: FileList | File[]) => {
    const csvFiles = Array.from(files).filter((f) => f.name.endsWith(".csv"));
    if (!csvFiles.length) { onToast("No CSV files selected", "error"); return; }
    setUploading(true);
    try {
      const result = await api.uploadBacktests(csvFiles);
      onToast(`Imported ${result.length} backtest(s)`, "success");
      refresh();
    } catch (e) {
      onToast(e instanceof Error ? e.message : "Upload failed", "error");
    } finally { setUploading(false); }
  };

  const handleDelete = async (id: string) => {
    try {
      await api.deleteBacktestImport(id);
      onToast("Import deleted", "success");
      refresh();
    } catch { onToast("Failed to delete", "error"); }
  };

  const toggleExpand = async (id: string) => {
    if (expandedId === id) { setExpandedId(null); return; }
    setExpandedId(id);
    try {
      const trades = await api.getBacktestTrades(id);
      setExpandedTrades(trades);
    } catch { setExpandedTrades([]); }
  };

  return (
    <div className="space-y-4">
      {/* Drop zone */}
      <div
        className={`border-2 border-dashed rounded-xl p-8 text-center transition cursor-pointer ${
          dragActive ? "border-ai-blue bg-ai-blue/5" : "border-border hover:border-gray-600"
        }`}
        onDragOver={(e) => { e.preventDefault(); setDragActive(true); }}
        onDragLeave={() => setDragActive(false)}
        onDrop={(e) => { e.preventDefault(); setDragActive(false); handleUpload(e.dataTransfer.files); }}
        onClick={() => fileRef.current?.click()}
      >
        <input ref={fileRef} type="file" accept=".csv" multiple className="hidden"
          onChange={(e) => e.target.files && handleUpload(e.target.files)} />
        <svg className="w-10 h-10 mx-auto mb-3 text-gray-600" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
          <path strokeLinecap="round" strokeLinejoin="round" d="M3 16.5v2.25A2.25 2.25 0 0 0 5.25 21h13.5A2.25 2.25 0 0 0 21 18.75V16.5m-13.5-9L12 3m0 0 4.5 4.5M12 3v13.5" />
        </svg>
        <p className="text-sm text-gray-400" style={FONT_OUTFIT}>
          {uploading ? "Uploading..." : "Drop CSV files here or click to browse"}
        </p>
        <p className="text-[11px] text-gray-600 mt-1 font-mono">STRATEGY_VERSION_EXCHANGE_TICKER_DATE.csv</p>
      </div>

      {/* Import list */}
      {loading ? (
        <div className="space-y-2">{[1, 2, 3].map((i) => <Skeleton key={i} className="h-20 rounded-lg" />)}</div>
      ) : !imports?.length ? (
        <div className="py-8 text-center">
          <p className="text-gray-500 text-sm" style={FONT_OUTFIT}>No backtests imported yet</p>
        </div>
      ) : (
        <div className="space-y-2">
          {imports.map((imp) => (
            <div key={imp.id}>
              <div className="flex items-center gap-3 px-4 py-3 rounded-lg bg-surface-light/20 border border-border hover:border-border/80 transition cursor-pointer"
                onClick={() => toggleExpand(imp.id)}>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 mb-0.5">
                    <span className="text-sm font-bold text-white" style={FONT_OUTFIT}>{imp.ticker}</span>
                    <Badge className="text-[9px] px-1.5 py-0 bg-ai-blue/15 text-ai-blue">{imp.strategy_name} {imp.strategy_version || ""}</Badge>
                    {imp.exchange && <Badge className="text-[9px] px-1.5 py-0 bg-surface-light text-gray-400">{imp.exchange}</Badge>}
                  </div>
                  <div className="flex items-center gap-3 text-[11px] text-gray-500 font-mono flex-wrap">
                    <span>{imp.trade_count} trades</span>
                    {imp.win_rate != null && <span className="text-profit">{imp.win_rate}% WR</span>}
                    {imp.profit_factor != null && <span>PF {imp.profit_factor}</span>}
                    {imp.avg_gain_pct != null && <span className="text-profit">avg +{imp.avg_gain_pct}%</span>}
                    {imp.avg_loss_pct != null && <span className="text-loss">avg {imp.avg_loss_pct}%</span>}
                    {imp.avg_hold_days != null && <span>{imp.avg_hold_days}d avg hold</span>}
                    {imp.total_pnl_pct != null && (
                      <span className={imp.total_pnl_pct >= 0 ? "text-profit" : "text-loss"}>
                        total {imp.total_pnl_pct >= 0 ? "+" : ""}{imp.total_pnl_pct}%
                      </span>
                    )}
                  </div>
                </div>
                <button onClick={(e) => { e.stopPropagation(); handleDelete(imp.id); }}
                  className="text-gray-600 hover:text-loss transition p-1 shrink-0">
                  <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
                    <path strokeLinecap="round" strokeLinejoin="round" d="m14.74 9-.346 9m-4.788 0L9.26 9m9.968-3.21c.342.052.682.107 1.022.166m-1.022-.165L18.16 19.673a2.25 2.25 0 0 1-2.244 2.077H8.084a2.25 2.25 0 0 1-2.244-2.077L4.772 5.79m14.456 0a48.108 48.108 0 0 0-3.478-.397m-12 .562c.34-.059.68-.114 1.022-.165m0 0a48.11 48.11 0 0 1 3.478-.397m7.5 0v-.916c0-1.18-.91-2.164-2.09-2.201a51.964 51.964 0 0 0-3.32 0c-1.18.037-2.09 1.022-2.09 2.201v.916m7.5 0a48.667 48.667 0 0 0-7.5 0" />
                  </svg>
                </button>
                <svg className={`w-4 h-4 text-gray-500 transition ${expandedId === imp.id ? "rotate-180" : ""}`}
                  fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="m19.5 8.25-7.5 7.5-7.5-7.5" />
                </svg>
              </div>

              {/* Expanded trades */}
              {expandedId === imp.id && (
                <div className="mt-1 ml-4 border-l-2 border-border pl-4 py-2 space-y-1 max-h-[400px] overflow-y-auto">
                  {expandedTrades.filter((t) => t.type.toLowerCase().includes("exit")).map((t) => {
                    const entry = expandedTrades.find((e) => e.trade_number === t.trade_number && e.type.toLowerCase().includes("entry"));
                    return (
                      <div key={t.id} className="flex items-center gap-3 text-[11px] font-mono text-gray-400 py-1">
                        <span className="text-gray-600 w-6">#{t.trade_number}</span>
                        <span className="w-20">{entry ? new Date(entry.trade_date).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "2-digit" }) : ""}</span>
                        <span className="text-gray-500">→</span>
                        <span className="w-20">{new Date(t.trade_date).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "2-digit" })}</span>
                        <Badge className={`text-[9px] px-1.5 py-0 ${(t.net_pnl ?? 0) >= 0 ? "bg-profit/15 text-profit" : "bg-loss/15 text-loss"}`}>
                          {t.net_pnl_pct != null ? `${t.net_pnl_pct >= 0 ? "+" : ""}${t.net_pnl_pct}%` : "—"}
                        </Badge>
                        <span className="text-gray-600">{t.signal}</span>
                        {t.adverse_excursion_pct != null && <span className="text-loss/60">MAE {t.adverse_excursion_pct}%</span>}
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ══════════════════════════════════════════════════════════════════════
// MAIN PAGE
// ══════════════════════════════════════════════════════════════════════

export default function PortfolioManagerPage() {
  useFonts();
  const [toast, setToast] = useState<{ message: string; type: "success" | "error" } | null>(null);
  const [portfolios, setPortfolios] = useState<PortfolioSettings[]>([]);

  useEffect(() => {
    api.getSettingsPortfolios().then(setPortfolios).catch(() => {});
  }, []);

  const showToast = useCallback((message: string, type: "success" | "error") => {
    setToast({ message, type });
    setTimeout(() => setToast(null), 3000);
  }, []);

  return (
    <div className="space-y-6 pb-12">
      {toast && <Toast message={toast.message} type={toast.type} />}

      {/* Header */}
      <div>
        <h1 className="text-2xl font-bold text-white tracking-tight" style={FONT_OUTFIT}>
          Portfolio Manager
        </h1>
        <p className="text-sm text-gray-500 mt-1" style={FONT_OUTFIT}>
          Henry&apos;s recommendations, your positions, and backtest intelligence
        </p>
      </div>

      {/* Tabs */}
      <Tabs defaultValue="actions" className="w-full">
        <TabsList className="bg-surface-light/30 border border-border p-1 rounded-lg">
          <TabsTrigger value="actions" className="text-xs font-medium data-[state=active]:bg-surface-light data-[state=active]:text-white" style={FONT_OUTFIT}>
            Action Queue
          </TabsTrigger>
          <TabsTrigger value="holdings" className="text-xs font-medium data-[state=active]:bg-surface-light data-[state=active]:text-white" style={FONT_OUTFIT}>
            Holdings
          </TabsTrigger>
          <TabsTrigger value="backtests" className="text-xs font-medium data-[state=active]:bg-surface-light data-[state=active]:text-white" style={FONT_OUTFIT}>
            Backtest Data
          </TabsTrigger>
        </TabsList>

        <TabsContent value="actions" className="mt-4">
          <ActionQueueTab onToast={showToast} />
        </TabsContent>

        <TabsContent value="holdings" className="mt-4">
          <HoldingsTab portfolios={portfolios} onToast={showToast} />
        </TabsContent>

        <TabsContent value="backtests" className="mt-4">
          <BacktestTab onToast={showToast} />
        </TabsContent>
      </Tabs>
    </div>
  );
}
