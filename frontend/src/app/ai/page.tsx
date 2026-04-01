"use client";

import { useState, useEffect, useCallback } from "react";
import { api } from "@/lib/api";
import { usePolling } from "@/hooks/usePolling";
import { formatCurrency, formatPercent, formatTimeAgo, formatDate, pnlColor } from "@/lib/formatters";
import { renderMarkdown } from "@/lib/markdown";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Input } from "@/components/ui/input";
import MorningBriefing from "@/components/ai/MorningBriefing";
import AskHenry from "@/components/ai/AskHenry";
import ConflictLog from "@/components/ai/ConflictLog";
import LiveTradeFeed from "@/components/dashboard/LiveTradeFeed";
import type {
  Portfolio, PortfolioAction, ActionStats,
  HenryContextEntry, HenryStatsEntry, ScannerOpportunity,
} from "@/lib/types";

const FONT_OUTFIT = { fontFamily: "'Outfit', sans-serif" } as const;
const FONT_MONO = { fontFamily: "'JetBrains Mono', monospace" } as const;

// ── System Status Bar ──────────────────────────────────────────────

function SystemStatusBar({
  portfolios,
  pendingActions,
  actionStats,
}: {
  portfolios: Portfolio[];
  pendingActions: PortfolioAction[];
  actionStats: ActionStats | null;
}) {
  const now = new Date();
  const hour = now.getUTCHours() - 5; // EST approximation
  const day = now.getDay();
  const isWeekend = day === 0 || day === 6;
  const isPreMarket = !isWeekend && hour >= 4 && hour < 9.5;
  const isOpen = !isWeekend && hour >= 9.5 && hour < 16;

  const marketStatus = isOpen
    ? { label: "OPEN", color: "bg-profit text-profit" }
    : isPreMarket
    ? { label: "PRE-MARKET", color: "bg-amber-500 text-amber-400" }
    : { label: "CLOSED", color: "bg-gray-500 text-gray-400" };

  const hasAutoTrading = portfolios.some(
    (p) => p.execution_mode && p.execution_mode !== "local" && p.is_active
  );

  const lastAction = pendingActions.length > 0 ? pendingActions[0] : null;

  return (
    <div className="flex flex-wrap items-center gap-3 px-4 py-2.5 rounded-xl bg-surface-light/30 border border-border/50 mb-4">
      {/* Market status */}
      <div className="flex items-center gap-1.5">
        <span className={`w-2 h-2 rounded-full ${marketStatus.color.split(" ")[0]} ${isOpen ? "animate-pulse" : ""}`} />
        <span className={`text-[10px] font-mono font-bold uppercase tracking-wider ${marketStatus.color.split(" ")[1]}`}>
          {marketStatus.label}
        </span>
      </div>

      <div className="w-px h-4 bg-border" />

      {/* Auto-trading */}
      <div className="flex items-center gap-1.5">
        <span className={`w-1.5 h-1.5 rounded-full ${hasAutoTrading ? "bg-profit animate-pulse" : "bg-gray-600"}`} />
        <span className="text-[10px] text-gray-500 font-mono">
          {hasAutoTrading ? "Auto-trading" : "Manual only"}
        </span>
      </div>

      {/* Henry's last action */}
      {lastAction && (
        <>
          <div className="w-px h-4 bg-border" />
          <span className="text-[10px] text-gray-500 font-mono">
            Last: <span className="text-white">{lastAction.action_type} {lastAction.ticker}</span>
            {" "}<span className="text-gray-600">{formatTimeAgo(lastAction.created_at)}</span>
          </span>
        </>
      )}

      {/* Hit rate */}
      {actionStats?.hit_rate != null && (
        <>
          <div className="w-px h-4 bg-border hidden sm:block" />
          <span className="text-[10px] text-gray-500 font-mono hidden sm:inline">
            Hit rate: <span className="text-profit">{actionStats.hit_rate}%</span>
          </span>
        </>
      )}
    </div>
  );
}

// ── Metrics Cards ──────────────────────────────────────────────────

function MetricCard({ label, value, color = "text-white", sub }: {
  label: string; value: string; color?: string; sub?: string;
}) {
  return (
    <div className="bg-surface-light/30 rounded-xl p-4 border border-border">
      <div className="text-[10px] text-gray-500 uppercase tracking-wider mb-1" style={FONT_OUTFIT}>{label}</div>
      <div className={`text-lg font-mono font-semibold ${color}`} style={FONT_MONO}>{value}</div>
      {sub && <div className="text-[10px] text-gray-600 font-mono mt-0.5">{sub}</div>}
    </div>
  );
}

function MetricsRow({
  portfolios,
  pendingCount,
  actionStats,
  signalCount,
  scannerCount,
}: {
  portfolios: Portfolio[];
  pendingCount: number;
  actionStats: ActionStats | null;
  signalCount: number;
  scannerCount: number;
}) {
  const totalEquity = portfolios.reduce((s, p) => s + p.equity, 0);
  const totalUnrealized = portfolios.reduce((s, p) => s + p.unrealized_pnl, 0);
  const openPositions = portfolios.reduce((s, p) => s + p.open_positions, 0);
  const dailyChangePct = totalEquity > 0 ? (totalUnrealized / totalEquity) * 100 : 0;

  return (
    <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3 mb-6">
      <MetricCard
        label="Total Equity"
        value={formatCurrency(totalEquity)}
        color="text-white"
        sub={`${formatPercent(dailyChangePct)} unrealized`}
      />
      <MetricCard
        label="Open Positions"
        value={String(openPositions)}
        color="text-white"
        sub={`${portfolios.length} portfolio${portfolios.length !== 1 ? "s" : ""}`}
      />
      <MetricCard
        label="Pending Actions"
        value={String(pendingCount)}
        color={pendingCount > 0 ? "text-ai-blue" : "text-gray-400"}
        sub={actionStats ? `${actionStats.approved_today} approved today` : undefined}
      />
      <MetricCard
        label="Hit Rate"
        value={actionStats?.hit_rate != null ? `${actionStats.hit_rate}%` : "N/A"}
        color={actionStats?.hit_rate != null && actionStats.hit_rate >= 50 ? "text-profit" : "text-gray-400"}
        sub={actionStats?.hit_rate_high_confidence != null ? `High conf: ${actionStats.hit_rate_high_confidence}%` : undefined}
      />
      <MetricCard
        label="Today's Signals"
        value={String(signalCount)}
        color={signalCount > 0 ? "text-amber-400" : "text-gray-400"}
      />
      <MetricCard
        label="Scanner Opps"
        value={String(scannerCount)}
        color={scannerCount > 0 ? "text-ai-blue" : "text-gray-400"}
      />
    </div>
  );
}

// ── Action Queue Tab ───────────────────────────────────────────────

function ActionQueueTab() {
  const [actions, setActions] = useState<PortfolioAction[]>([]);
  const [filter, setFilter] = useState("pending");
  const [loading, setLoading] = useState(true);

  const fetchActions = useCallback(async () => {
    try {
      const data = await api.getActions(filter !== "all" ? filter : undefined);
      setActions(data);
    } catch {}
    setLoading(false);
  }, [filter]);

  useEffect(() => { fetchActions(); }, [fetchActions]);

  const handleApprove = async (id: string) => {
    try { await api.approveAction(id); fetchActions(); } catch {}
  };
  const handleReject = async (id: string) => {
    const reason = prompt("Rejection reason (optional):");
    try { await api.rejectAction(id, reason || undefined); fetchActions(); } catch {}
  };

  const isOpportunity = (a: PortfolioAction) =>
    a.action_type === "BUY" && a.confidence >= 7;

  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <div>
          <h2 className="text-lg font-bold text-white" style={FONT_OUTFIT}>Action Queue</h2>
          <p className="text-xs text-gray-500 mt-1">Henry&apos;s pending recommendations - approve or reject</p>
        </div>
        <div className="flex rounded-md overflow-hidden border border-border">
          {["pending", "approved", "rejected", "all"].map((f) => (
            <button key={f} onClick={() => { setFilter(f); setLoading(true); }}
              className={`px-3 py-1.5 text-[10px] font-medium capitalize transition ${
                filter === f ? "bg-ai-blue/20 text-ai-blue" : "bg-surface-light/30 text-gray-500 hover:text-gray-300"
              }`}>{f}</button>
          ))}
        </div>
      </div>

      {loading ? (
        <div className="space-y-3">
          {[1, 2, 3].map((i) => <Skeleton key={i} className="h-28 rounded-xl" />)}
        </div>
      ) : actions.length === 0 ? (
        <div className="text-center py-12">
          <div className="w-12 h-12 rounded-full bg-ai-blue/10 flex items-center justify-center mx-auto mb-3">
            <svg className="w-6 h-6 text-ai-blue/40" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M9 12.75L11.25 15 15 9.75M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
            </svg>
          </div>
          <p className="text-sm text-gray-500">No {filter} actions</p>
        </div>
      ) : (
        <div className="space-y-3">
          {actions.map((a) => (
            <div
              key={a.id}
              className={`rounded-xl border p-4 transition ${
                isOpportunity(a)
                  ? "border-l-4 border-l-ai-blue border-t-border/40 border-r-border/40 border-b-border/40 bg-ai-blue/5"
                  : "border-border/40 bg-surface-light/20"
              }`}
            >
              <div className="flex items-start gap-3">
                <div className="flex-1 min-w-0">
                  {/* Header */}
                  <div className="flex items-center gap-2 mb-2 flex-wrap">
                    <span className="text-base font-bold text-white" style={FONT_OUTFIT}>{a.ticker}</span>
                    <Badge className={`text-[9px] ${
                      a.action_type === "BUY" || a.action_type === "ADD" ? "bg-profit/15 text-profit" :
                      a.action_type === "CLOSE" || a.action_type === "SELL" ? "bg-loss/15 text-loss" :
                      "bg-amber-500/15 text-amber-400"
                    }`}>{a.action_type}</Badge>
                    <Badge className={`text-[9px] ${
                      a.direction === "long" ? "bg-profit/10 text-profit" : "bg-loss/10 text-loss"
                    }`}>{a.direction}</Badge>
                    {isOpportunity(a) && (
                      <Badge className="text-[9px] bg-ai-blue/15 text-ai-blue">Henry&apos;s Pick</Badge>
                    )}
                    <span className="text-[10px] text-gray-600 ml-auto">{formatTimeAgo(a.created_at)}</span>
                  </div>

                  {/* Confidence bar */}
                  <div className="flex items-center gap-2 mb-2">
                    <span className="text-[10px] text-gray-500 font-mono w-16">Conf {a.confidence}/10</span>
                    <div className="flex-1 h-1.5 rounded-full bg-surface-light/40 overflow-hidden">
                      <div
                        className={`h-full rounded-full transition-all ${
                          a.confidence >= 8 ? "bg-profit" : a.confidence >= 5 ? "bg-amber-400" : "bg-loss"
                        }`}
                        style={{ width: `${a.confidence * 10}%` }}
                      />
                    </div>
                  </div>

                  {/* Reasoning */}
                  <p className="text-xs text-gray-400 leading-relaxed">{a.reasoning}</p>

                  {/* Price info */}
                  <div className="flex items-center gap-4 mt-2 text-[10px] text-gray-500 font-mono">
                    {a.suggested_price != null && <span>Target: ${a.suggested_price.toFixed(2)}</span>}
                    {a.current_price != null && <span>Current: ${a.current_price.toFixed(2)}</span>}
                    {a.suggested_qty != null && <span>Qty: {a.suggested_qty}</span>}
                    {a.expires_at && (
                      <span className="text-amber-400/60">
                        Expires {formatTimeAgo(a.expires_at)}
                      </span>
                    )}
                  </div>
                </div>

                {/* Action buttons */}
                {a.status === "pending" ? (
                  <div className="flex flex-col gap-1.5 shrink-0">
                    <Button size="sm" onClick={() => handleApprove(a.id)}
                      className="text-[10px] h-7 bg-profit/20 text-profit border border-profit/20 hover:bg-profit/30">
                      Approve
                    </Button>
                    <Button size="sm" onClick={() => handleReject(a.id)}
                      className="text-[10px] h-7 bg-loss/20 text-loss border border-loss/20 hover:bg-loss/30">
                      Reject
                    </Button>
                  </div>
                ) : (
                  <Badge className={`text-[9px] shrink-0 ${
                    a.status === "approved" ? "bg-profit/15 text-profit" :
                    a.status === "rejected" ? "bg-loss/15 text-loss" :
                    "bg-gray-600/20 text-gray-500"
                  }`}>{a.status}</Badge>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ── Memory Tab ─────────────────────────────────────────────────────

function MemoryTab() {
  const [context, setContext] = useState<HenryContextEntry[]>([]);
  const [stats, setStats] = useState<HenryStatsEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [tickerFilter, setTickerFilter] = useState("");

  const fetchData = useCallback(async () => {
    try {
      const [ctx, st] = await Promise.all([
        api.getHenryContext(tickerFilter || undefined).catch(() => []),
        api.getHenryStats().catch(() => []),
      ]);
      setContext(ctx);
      setStats(st);
    } catch {}
    setLoading(false);
  }, [tickerFilter]);

  useEffect(() => { fetchData(); }, [fetchData]);

  const contextTypeBadge = (type: string) => {
    const map: Record<string, string> = {
      observation: "bg-ai-blue/15 text-ai-blue",
      decision: "bg-amber-500/15 text-amber-400",
      lesson: "bg-ai-purple/15 text-ai-purple",
      research: "bg-profit/15 text-profit",
      warning: "bg-loss/15 text-loss",
    };
    return map[type] || "bg-gray-600/15 text-gray-400";
  };

  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <div>
          <h2 className="text-lg font-bold text-white" style={FONT_OUTFIT}>Henry&apos;s Memory</h2>
          <p className="text-xs text-gray-500 mt-1">Context entries and performance statistics</p>
        </div>
        <Input
          value={tickerFilter}
          onChange={(e) => { setTickerFilter(e.target.value.toUpperCase()); setLoading(true); }}
          placeholder="Filter by ticker..."
          className="w-40 h-8 text-xs font-mono bg-surface-light/30 border-border/50"
        />
      </div>

      {loading ? (
        <div className="space-y-3">
          {[1, 2, 3].map((i) => <Skeleton key={i} className="h-20 rounded-xl" />)}
        </div>
      ) : (
        <div className="space-y-6">
          {/* Stats summary */}
          {stats.length > 0 && (
            <div>
              <h3 className="text-sm font-semibold text-white mb-3" style={FONT_OUTFIT}>Performance Stats</h3>
              <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
                {stats.map((s) => (
                  <Card key={s.id}>
                    <CardContent className="p-4">
                      <div className="flex items-center gap-2 mb-2">
                        <Badge className="text-[9px] bg-ai-blue/15 text-ai-blue">{s.stat_type}</Badge>
                        {s.ticker && <span className="text-xs font-mono text-white font-bold">{s.ticker}</span>}
                        {s.strategy && <span className="text-[10px] text-gray-500">{s.strategy}</span>}
                      </div>
                      <div className="text-[10px] text-gray-500 font-mono mb-2">
                        {s.period_days}d period | computed {formatTimeAgo(s.computed_at)}
                      </div>
                      <div className="grid grid-cols-2 gap-1 text-[10px] font-mono">
                        {Object.entries(s.data).slice(0, 6).map(([k, v]) => (
                          <div key={k} className="flex justify-between gap-1">
                            <span className="text-gray-500 truncate">{k}</span>
                            <span className="text-gray-300">{typeof v === "number" ? v.toFixed(2) : String(v)}</span>
                          </div>
                        ))}
                      </div>
                    </CardContent>
                  </Card>
                ))}
              </div>
            </div>
          )}

          {/* Context entries */}
          <div>
            <h3 className="text-sm font-semibold text-white mb-3" style={FONT_OUTFIT}>
              Context Entries <span className="text-gray-500 font-normal">({context.length})</span>
            </h3>
            {context.length === 0 ? (
              <p className="text-xs text-gray-500 text-center py-8">No context entries{tickerFilter ? ` for ${tickerFilter}` : ""}</p>
            ) : (
              <div className="space-y-2 max-h-[600px] overflow-y-auto">
                {context.map((c) => (
                  <div key={c.id} className="rounded-lg border border-border/40 bg-surface-light/20 p-3">
                    <div className="flex items-center gap-2 mb-1.5 flex-wrap">
                      <Badge className={`text-[9px] ${contextTypeBadge(c.context_type)}`}>{c.context_type}</Badge>
                      {c.ticker && <span className="text-xs font-mono text-white font-bold">{c.ticker}</span>}
                      {c.strategy && <span className="text-[10px] text-gray-500">{c.strategy}</span>}
                      {c.confidence != null && (
                        <span className="text-[10px] text-gray-500 font-mono">conf {c.confidence}/10</span>
                      )}
                      <span className="text-[10px] text-gray-600 ml-auto">{formatDate(c.created_at)}</span>
                    </div>
                    <p className="text-xs text-gray-400 leading-relaxed">{c.content}</p>
                    {c.expires_at && (
                      <div className="text-[9px] text-amber-400/60 mt-1 font-mono">
                        Expires {formatDate(c.expires_at)}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

// ── Tab definitions ────────────────────────────────────────────────

// ── Henry Activity Log ──────────────────────────────────────────────

function HenryActivityLog() {
  const [activity, setActivity] = useState<Array<{ id: string; message: string; activity_type: string; activity_label: string; ticker: string | null; created_at: string }>>([]);
  const [chatInput, setChatInput] = useState("");
  const [chatMessages, setChatMessages] = useState<Array<{ role: "user" | "henry"; text: string; at: string }>>([]);
  const [chatLoading, setChatLoading] = useState(false);
  const [loading, setLoading] = useState(true);

  const fetchActivity = useCallback(async () => {
    try {
      const data = await api.getHenryActivity(50);
      setActivity(data);
    } catch {}
  }, []);

  useEffect(() => {
    fetchActivity().finally(() => setLoading(false));
    const interval = setInterval(fetchActivity, 15000); // Poll every 15s
    return () => clearInterval(interval);
  }, [fetchActivity]);

  const handleChat = async () => {
    const q = chatInput.trim();
    if (!q || chatLoading) return;
    setChatInput("");
    setChatMessages(prev => [...prev, { role: "user", text: q, at: new Date().toISOString() }]);
    setChatLoading(true);
    try {
      const res = await api.chatWithHenry(q);
      setChatMessages(prev => [...prev, { role: "henry", text: res.answer, at: new Date().toISOString() }]);
    } catch {
      setChatMessages(prev => [...prev, { role: "henry", text: "Sorry, I couldn't process that right now.", at: new Date().toISOString() }]);
    }
    setChatLoading(false);
  };

  const activityColor: Record<string, string> = {
    trade_execute: "border-l-profit",
    trade_exit: "border-l-loss",
    scan_start: "border-l-ai-blue",
    scan_result: "border-l-amber-400",
    scan_profile: "border-l-ai-purple",
    pattern_detect: "border-l-cyan-400",
    error: "border-l-loss",
    trade_skip: "border-l-gray-500",
    status: "border-l-gray-600",
    analysis: "border-l-ai-blue",
  };

  return (
    <div className="space-y-4">
      {/* Chat Input */}
      <div className="flex gap-2">
        <input
          value={chatInput}
          onChange={(e) => setChatInput(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && handleChat()}
          placeholder="Ask Henry about his decisions..."
          className="flex-1 h-9 rounded-lg border border-border bg-surface-light/30 px-3 text-sm text-white placeholder:text-gray-500 focus:outline-none focus:ring-1 focus:ring-ai-blue/50"
        />
        <button onClick={handleChat} disabled={chatLoading || !chatInput.trim()}
          className="px-4 h-9 rounded-lg bg-ai-blue/20 text-ai-blue border border-ai-blue/30 text-sm font-medium hover:bg-ai-blue/30 transition disabled:opacity-50">
          {chatLoading ? "..." : "Ask"}
        </button>
      </div>

      {/* Chat Messages */}
      {chatMessages.length > 0 && (
        <div className="space-y-3 max-h-60 overflow-y-auto">
          {chatMessages.map((msg, i) => (
            <div key={i} className={`p-3 rounded-lg text-sm ${
              msg.role === "user"
                ? "bg-surface-light/30 border border-border/40 text-gray-300 ml-8"
                : "bg-ai-blue/5 border border-ai-blue/20 text-gray-300 mr-8"
            }`}>
              <div className="text-[9px] text-gray-500 mb-1 font-mono">
                {msg.role === "user" ? "You" : "Henry"} · {formatTimeAgo(msg.at)}
              </div>
              {msg.role === "henry" ? (
                <div className="ai-prose" dangerouslySetInnerHTML={{ __html: renderMarkdown(msg.text) }} />
              ) : (
                <span>{msg.text}</span>
              )}
            </div>
          ))}
          {chatLoading && (
            <div className="bg-ai-blue/5 border border-ai-blue/20 p-3 rounded-lg mr-8">
              <div className="flex items-center gap-2 text-xs text-ai-blue/60">
                <span className="w-2 h-2 rounded-full bg-ai-blue animate-pulse" />
                Henry is thinking...
              </div>
            </div>
          )}
        </div>
      )}

      {/* Activity Feed */}
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold text-white">Activity Log</h3>
        <span className="text-[9px] text-gray-500 font-mono">{activity.length} entries · auto-refreshing</span>
      </div>

      {loading ? (
        <div className="space-y-2">
          {[1,2,3,4,5].map(i => <div key={i} className="h-10 rounded-lg bg-surface-light/20 animate-pulse" />)}
        </div>
      ) : activity.length === 0 ? (
        <div className="text-center py-12 text-gray-500 text-sm">
          No activity yet. Henry logs entries when he scans, analyzes, and trades.
        </div>
      ) : (
        <div className="space-y-1 max-h-[500px] overflow-y-auto">
          {activity.map((a) => (
            <div key={a.id}
              className={`flex items-start gap-3 px-3 py-2 rounded-lg border-l-2 bg-surface-light/10 hover:bg-surface-light/20 transition ${
                activityColor[a.activity_type] || "border-l-gray-600"
              }`}>
              <span className="text-[10px] shrink-0 mt-0.5">{a.activity_label}</span>
              <div className="flex-1 min-w-0">
                <span className="text-xs text-gray-300">{a.message}</span>
                {a.ticker && (
                  <span className="ml-2 text-[9px] font-mono text-white bg-surface-light/40 px-1.5 py-0.5 rounded">{a.ticker}</span>
                )}
              </div>
              <span className="text-[9px] text-gray-600 font-mono shrink-0">{formatTimeAgo(a.created_at)}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}


const TABS = [
  {
    id: "briefing",
    label: "Briefing",
    icon: (
      <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
        <path strokeLinecap="round" strokeLinejoin="round" d="M12 3v2.25m6.364.386l-1.591 1.591M21 12h-2.25m-.386 6.364l-1.591-1.591M12 18.75V21m-4.773-4.227l-1.591 1.591M5.25 12H3m4.227-4.773L5.636 5.636M15.75 12a3.75 3.75 0 11-7.5 0 3.75 3.75 0 017.5 0z" />
      </svg>
    ),
  },
  {
    id: "activity",
    label: "Activity",
    icon: (
      <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
        <path strokeLinecap="round" strokeLinejoin="round" d="M3.75 6.75h16.5M3.75 12h16.5m-16.5 5.25H12" />
      </svg>
    ),
  },
  {
    id: "ask",
    label: "Ask Henry",
    icon: (
      <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
        <path strokeLinecap="round" strokeLinejoin="round" d="M7.5 8.25h9m-9 3H12m-9.75 1.51c0 1.6 1.123 2.994 2.707 3.227 1.129.166 2.27.293 3.423.379.35.026.67.21.865.501L12 21l2.755-4.133a1.14 1.14 0 01.865-.501 48.172 48.172 0 003.423-.379c1.584-.233 2.707-1.626 2.707-3.228V6.741c0-1.602-1.123-2.995-2.707-3.228A48.394 48.394 0 0012 3c-2.392 0-4.744.175-7.043.513C3.373 3.746 2.25 5.14 2.25 6.741v6.018z" />
      </svg>
    ),
  },
  {
    id: "actions",
    label: "Action Queue",
    icon: (
      <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
        <path strokeLinecap="round" strokeLinejoin="round" d="M3.75 12h16.5m-16.5 3.75h16.5M3.75 19.5h16.5M5.625 4.5h12.75a1.875 1.875 0 010 3.75H5.625a1.875 1.875 0 010-3.75z" />
      </svg>
    ),
    dot: "bg-ai-blue",
  },
  {
    id: "memory",
    label: "Memory",
    icon: (
      <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
        <path strokeLinecap="round" strokeLinejoin="round" d="M20.25 6.375c0 2.278-3.694 4.125-8.25 4.125S3.75 8.653 3.75 6.375m16.5 0c0-2.278-3.694-4.125-8.25-4.125S3.75 4.097 3.75 6.375m16.5 0v11.25c0 2.278-3.694 4.125-8.25 4.125s-8.25-1.847-8.25-4.125V6.375m16.5 0v3.75m-16.5-3.75v3.75m16.5 0v3.75C20.25 16.153 16.556 18 12 18s-8.25-1.847-8.25-4.125v-3.75m16.5 0c0 2.278-3.694 4.125-8.25 4.125s-8.25-1.847-8.25-4.125" />
      </svg>
    ),
  },
  {
    id: "conflicts",
    label: "Conflicts",
    icon: (
      <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
        <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v3.75m-9.303 3.376c-.866 1.5.217 3.374 1.948 3.374h14.71c1.73 0 2.813-1.874 1.948-3.374L13.949 3.378c-.866-1.5-3.032-1.5-3.898 0L2.697 16.126zM12 15.75h.007v.008H12v-.008z" />
      </svg>
    ),
  },
  {
    id: "feed",
    label: "Live Feed",
    icon: (
      <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
        <path strokeLinecap="round" strokeLinejoin="round" d="M3.75 12h16.5m-16.5 3.75h16.5M3.75 19.5h16.5M5.625 4.5h12.75a1.875 1.875 0 010 3.75H5.625a1.875 1.875 0 010-3.75z" />
      </svg>
    ),
    dot: "bg-profit",
  },
];

// ── Main Page ──────────────────────────────────────────────────────

export default function HomePage() {
  const [activeTab, setActiveTab] = useState("briefing");

  // Fetch dashboard data
  const { data: portfolios } = usePolling(() => api.getPortfolios(), 30000);
  const { data: actionStats } = usePolling(() => api.getActionStats(), 30000);
  const { data: pendingActions } = usePolling(() => api.getActions("pending"), 15000);
  const [signalCount, setSignalCount] = useState(0);
  const [scannerCount, setScannerCount] = useState(0);

  useEffect(() => {
    api.getScreenerAlerts({ hours: 24 }).then((a) => setSignalCount(a.length)).catch(() => {});
    api.getScannerResults().then((r) => setScannerCount(r.length)).catch(() => {});
  }, []);

  return (
    <div className="flex flex-col lg:flex-row gap-0 lg:gap-6 -mx-3 sm:-mx-4 lg:mx-0">
      {/* ── Mobile: Horizontal scrollable tab bar ── */}
      <div className="lg:hidden overflow-x-auto border-b border-border bg-surface/60 backdrop-blur sticky top-14 z-40">
        <div className="flex min-w-max px-3 sm:px-4">
          {TABS.map((tab) => (
            <button
              key={tab.id}
              onClick={() => setActiveTab(tab.id)}
              className={`flex items-center gap-2 px-4 py-3 text-xs font-medium whitespace-nowrap border-b-2 transition ${
                activeTab === tab.id
                  ? "text-white border-ai-blue"
                  : "text-gray-500 border-transparent hover:text-gray-300"
              }`}
            >
              {tab.dot && (
                <span className="relative flex h-1.5 w-1.5">
                  <span className={`animate-ping absolute inline-flex h-full w-full rounded-full ${tab.dot} opacity-75`} />
                  <span className={`relative inline-flex rounded-full h-1.5 w-1.5 ${tab.dot}`} />
                </span>
              )}
              <span className={activeTab === tab.id ? "text-ai-blue" : "text-gray-600"}>{tab.icon}</span>
              {tab.label}
              {tab.id === "actions" && pendingActions && pendingActions.length > 0 && (
                <span className="ml-1 text-[9px] font-mono bg-ai-blue/20 text-ai-blue px-1.5 py-0.5 rounded-full">
                  {pendingActions.length}
                </span>
              )}
            </button>
          ))}
        </div>
      </div>

      {/* ── Desktop: Left sidebar ── */}
      <aside className="hidden lg:flex flex-col w-52 shrink-0 sticky top-20 self-start">
        <div className="mb-4">
          <div className="flex items-center gap-2.5">
            <div className="w-8 h-8 rounded-lg bg-ai-blue/10 flex items-center justify-center">
              <svg className="w-4 h-4 text-ai-blue" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M9.75 17L9 20l-1 1h8l-1-1-.75-3M3 13h18M5 17h14a2 2 0 002-2V5a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z" />
              </svg>
            </div>
            <div>
              <h1 className="text-sm font-bold text-white leading-tight">Command Center</h1>
              <p className="text-[10px] text-gray-500">Henry AI Co-Pilot</p>
            </div>
          </div>
        </div>

        <nav className="flex flex-col gap-0.5">
          {TABS.map((tab) => (
            <button
              key={tab.id}
              onClick={() => setActiveTab(tab.id)}
              className={`flex items-center gap-2.5 px-3 py-2.5 rounded-lg text-sm text-left transition ${
                activeTab === tab.id
                  ? "text-white bg-surface-light/50 border border-border"
                  : "text-gray-500 hover:text-gray-300 hover:bg-surface-light/20"
              }`}
            >
              <span className={activeTab === tab.id ? "text-ai-blue" : "text-gray-600"}>
                {tab.icon}
              </span>
              {tab.label}
              {tab.id === "actions" && pendingActions && pendingActions.length > 0 && (
                <span className="text-[9px] font-mono bg-ai-blue/20 text-ai-blue px-1.5 py-0.5 rounded-full ml-auto">
                  {pendingActions.length}
                </span>
              )}
              {tab.dot && tab.id !== "actions" && (
                <span className="relative flex h-1.5 w-1.5 ml-auto">
                  <span className={`animate-ping absolute inline-flex h-full w-full rounded-full ${tab.dot} opacity-75`} />
                  <span className={`relative inline-flex rounded-full h-1.5 w-1.5 ${tab.dot}`} />
                </span>
              )}
            </button>
          ))}
        </nav>
      </aside>

      {/* ── Content area ── */}
      <div className="flex-1 min-w-0 px-3 sm:px-4 lg:px-0 pt-4 lg:pt-0">
        {/* System Status + Metrics (always visible) */}
        <SystemStatusBar
          portfolios={portfolios || []}
          pendingActions={pendingActions || []}
          actionStats={actionStats}
        />
        <MetricsRow
          portfolios={portfolios || []}
          pendingCount={pendingActions?.length || 0}
          actionStats={actionStats}
          signalCount={signalCount}
          scannerCount={scannerCount}
        />

        {/* Tab content */}
        {activeTab === "briefing" && <MorningBriefing />}
        {activeTab === "activity" && <HenryActivityLog />}
        {activeTab === "ask" && <AskHenry />}
        {activeTab === "actions" && <ActionQueueTab />}
        {activeTab === "memory" && <MemoryTab />}
        {activeTab === "conflicts" && <ConflictLog />}
        {activeTab === "feed" && (
          <div>
            <div className="mb-4">
              <h2 className="text-lg font-bold text-white">Live Trade Feed</h2>
              <p className="text-xs text-gray-500 mt-1">
                Real-time entries and exits — updates every 5 seconds
              </p>
            </div>
            <LiveTradeFeed limit={100} />
          </div>
        )}
      </div>
    </div>
  );
}
