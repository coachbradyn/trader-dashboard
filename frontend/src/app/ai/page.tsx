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
import AskHenry from "@/components/ai/AskHenry";
import OnboardingWizard from "@/components/OnboardingWizard";
import type {
  Portfolio, PortfolioAction, ActionStats,
  BriefingResponse, HenryContextEntry, HenryStatsEntry,
} from "@/lib/types";

const FONT_OUTFIT = { fontFamily: "'Outfit', sans-serif" } as const;
const FONT_MONO = { fontFamily: "'JetBrains Mono', monospace" } as const;

function useFonts() {
  useEffect(() => {
    if (document.getElementById("__home-fonts")) return;
    const link = document.createElement("link");
    link.id = "__home-fonts";
    link.rel = "stylesheet";
    link.href =
      "https://fonts.googleapis.com/css2?family=Outfit:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500;600;700&display=swap";
    document.head.appendChild(link);
  }, []);
}

// ── Greeting helper ──────────────────────────────────────────────
function getGreeting(): string {
  const now = new Date();
  const etHour = parseInt(
    now.toLocaleString("en-US", { hour: "numeric", hour12: false, timeZone: "America/New_York" })
  );
  if (etHour < 12) return "Good morning";
  if (etHour < 17) return "Good afternoon";
  return "Good evening";
}

function getMarketStatus() {
  const now = new Date();
  const etStr = now.toLocaleString("en-US", {
    timeZone: "America/New_York",
    hour: "numeric",
    minute: "numeric",
    hour12: false,
  });
  const [h, m] = etStr.split(":").map(Number);
  const mins = h * 60 + m;
  const day = parseInt(
    now.toLocaleString("en-US", { weekday: "short", timeZone: "America/New_York" }).slice(0, 1)
  );
  const dayOfWeek = now.toLocaleString("en-US", { weekday: "short", timeZone: "America/New_York" });
  const isWeekend = dayOfWeek === "Sat" || dayOfWeek === "Sun";

  if (isWeekend) return { label: "CLOSED", color: "text-gray-400", dotColor: "bg-gray-500" };
  if (mins >= 570 && mins < 960) return { label: "MARKET OPEN", color: "text-profit", dotColor: "bg-profit" };
  if (mins >= 240 && mins < 570) return { label: "PRE-MARKET", color: "text-amber-400", dotColor: "bg-amber-500" };
  if (mins >= 960 && mins < 1200) return { label: "AFTER-HOURS", color: "text-amber-400", dotColor: "bg-amber-500" };
  return { label: "CLOSED", color: "text-gray-400", dotColor: "bg-gray-500" };
}

// ── Hero Section ──────────────────────────────────────────────────

function HeroSection({
  portfolios,
  pendingActions,
  activityCount,
}: {
  portfolios: Portfolio[];
  pendingActions: PortfolioAction[];
  activityCount: number;
}) {
  const now = new Date();
  const dateStr = now.toLocaleDateString("en-US", {
    weekday: "long",
    month: "long",
    day: "numeric",
    year: "numeric",
    timeZone: "America/New_York",
  });
  const market = getMarketStatus();

  const totalEquity = portfolios.reduce((s, p) => s + p.equity, 0);
  const totalUnrealized = portfolios.reduce((s, p) => s + p.unrealized_pnl, 0);
  const openPositions = portfolios.reduce((s, p) => s + p.open_positions, 0);

  return (
    <div className="mb-8">
      {/* Greeting row */}
      <div className="flex flex-col sm:flex-row sm:items-end sm:justify-between gap-2 mb-6">
        <div>
          <h1
            className="text-3xl sm:text-4xl font-bold text-white tracking-tight"
            style={FONT_OUTFIT}
          >
            {getGreeting()}
          </h1>
          <p className="text-sm text-gray-500 mt-1" style={FONT_OUTFIT}>
            {dateStr}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <span className={`relative flex h-2 w-2`}>
            <span
              className={`absolute inline-flex h-full w-full rounded-full ${market.dotColor} ${
                market.label === "MARKET OPEN" ? "animate-ping opacity-75" : "opacity-50"
              }`}
            />
            <span className={`relative inline-flex rounded-full h-2 w-2 ${market.dotColor}`} />
          </span>
          <span
            className={`text-xs font-semibold tracking-wider uppercase ${market.color}`}
            style={FONT_MONO}
          >
            {market.label}
          </span>
        </div>
      </div>

      {/* Metric bar */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        {/* Total Equity */}
        <div className="rounded-xl border border-border/50 bg-[#1f2937]/40 p-5">
          <div
            className="text-2xl sm:text-3xl font-bold text-white tracking-tight"
            style={FONT_MONO}
          >
            {formatCurrency(totalEquity)}
          </div>
          <div
            className="text-xs text-gray-500 mt-1 uppercase tracking-wider"
            style={FONT_OUTFIT}
          >
            Total Equity
          </div>
        </div>

        {/* Today's P&L */}
        <div className="rounded-xl border border-border/50 bg-[#1f2937]/40 p-5">
          <div
            className={`text-2xl sm:text-3xl font-bold tracking-tight ${pnlColor(totalUnrealized)}`}
            style={FONT_MONO}
          >
            {totalUnrealized >= 0 ? "+" : ""}
            {formatCurrency(totalUnrealized)}
          </div>
          <div
            className="text-xs text-gray-500 mt-1 uppercase tracking-wider"
            style={FONT_OUTFIT}
          >
            Unrealized P&L
          </div>
        </div>

        {/* Open Positions */}
        <div className="rounded-xl border border-border/50 bg-[#1f2937]/40 p-5">
          <div
            className="text-2xl sm:text-3xl font-bold text-white tracking-tight"
            style={FONT_MONO}
          >
            {openPositions}
          </div>
          <div
            className="text-xs text-gray-500 mt-1 uppercase tracking-wider"
            style={FONT_OUTFIT}
          >
            Open Positions
          </div>
        </div>

        {/* Henry's Activity */}
        <div className="rounded-xl border border-border/50 bg-[#1f2937]/40 p-5">
          <div
            className="text-2xl sm:text-3xl font-bold text-ai-blue tracking-tight"
            style={FONT_MONO}
          >
            {activityCount}
          </div>
          <div
            className="text-xs text-gray-500 mt-1 uppercase tracking-wider"
            style={FONT_OUTFIT}
          >
            Henry&apos;s Activity
          </div>
        </div>
      </div>
    </div>
  );
}

// ── Briefing Tab ──────────────────────────────────────────────────

function BriefingTab() {
  const [data, setData] = useState<BriefingResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  const fetchBriefing = useCallback(async (isRefresh = false) => {
    try {
      if (isRefresh) setRefreshing(true);
      else setLoading(true);
      setError(null);
      const result = isRefresh ? await api.refreshBriefing() : await api.getBriefing();
      setData(result);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load briefing");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    fetchBriefing();
  }, [fetchBriefing]);

  return (
    <div>
      {/* Header row */}
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-3">
          <h2 className="text-lg font-bold text-white" style={FONT_OUTFIT}>
            Today&apos;s Briefing
          </h2>
          {data && (
            <Badge className="text-[10px] bg-ai-blue/15 text-ai-blue border-ai-blue/20">
              {data.open_positions} position{data.open_positions !== 1 ? "s" : ""}
            </Badge>
          )}
          {data?.cached && (
            <Badge className="text-[10px] bg-gray-700/30 text-gray-500 border-gray-600/30">
              cached
            </Badge>
          )}
        </div>
        <div className="flex items-center gap-3">
          {data?.generated_at && (
            <span className="text-[10px] text-gray-600 font-mono hidden sm:inline">
              {formatTimeAgo(data.generated_at)}
            </span>
          )}
          <Button
            size="sm"
            onClick={() => fetchBriefing(true)}
            disabled={refreshing}
            className="text-xs h-8 bg-ai-blue/10 text-ai-blue border border-ai-blue/20 hover:bg-ai-blue/20"
          >
            <svg
              className={`w-3.5 h-3.5 mr-1.5 ${refreshing ? "animate-spin" : ""}`}
              fill="none"
              viewBox="0 0 24 24"
              stroke="currentColor"
              strokeWidth={2}
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"
              />
            </svg>
            {refreshing ? "Refreshing..." : "Refresh"}
          </Button>
        </div>
      </div>

      {/* Content Card */}
      <Card className="border-border/50 bg-[#1f2937]/40">
        <CardContent className="p-6">
          {loading && (
            <div className="space-y-3">
              {[1, 2, 3, 4, 5].map((i) => (
                <Skeleton key={i} className="h-4 rounded" style={{ width: `${60 + Math.random() * 35}%` }} />
              ))}
            </div>
          )}

          {error && (
            <div className="flex items-center gap-2 py-4 px-4 rounded-lg border border-loss/30 bg-loss/5">
              <svg
                className="w-4 h-4 text-loss flex-shrink-0"
                fill="none"
                viewBox="0 0 24 24"
                stroke="currentColor"
                strokeWidth={2}
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-2.5L13.732 4c-.77-.833-1.964-.833-2.732 0L4.082 16.5c-.77.833.192 2.5 1.732 2.5z"
                />
              </svg>
              <span className="text-sm text-loss">{error}</span>
              <button
                onClick={() => fetchBriefing()}
                className="text-sm text-loss underline hover:text-loss/80 ml-1"
              >
                retry
              </button>
            </div>
          )}

          {data && !loading && (
            <div
              className="ai-prose"
              dangerouslySetInnerHTML={{ __html: renderMarkdown(data.briefing) }}
            />
          )}
        </CardContent>
      </Card>
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
        <div className="flex rounded-lg overflow-hidden border border-border/50">
          {["pending", "approved", "rejected", "all"].map((f) => (
            <button key={f} onClick={() => { setFilter(f); setLoading(true); }}
              className={`px-3 py-1.5 text-xs font-medium capitalize transition ${
                filter === f ? "bg-ai-blue/20 text-ai-blue" : "bg-[#1f2937]/40 text-gray-500 hover:text-gray-300"
              }`}>{f}</button>
          ))}
        </div>
      </div>

      {loading ? (
        <div className="space-y-3">
          {[1, 2, 3].map((i) => <Skeleton key={i} className="h-28 rounded-xl" />)}
        </div>
      ) : actions.length === 0 ? (
        <div className="text-center py-16">
          <div className="w-14 h-14 rounded-full bg-ai-blue/10 flex items-center justify-center mx-auto mb-4">
            <svg className="w-7 h-7 text-ai-blue/40" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
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
              className={`rounded-xl border p-5 transition ${
                isOpportunity(a)
                  ? "border-l-4 border-l-ai-blue border-t-border/40 border-r-border/40 border-b-border/40 bg-ai-blue/5"
                  : "border-border/40 bg-[#1f2937]/30"
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
                    <div className="flex-1 h-1.5 rounded-full bg-[#111827]/60 overflow-hidden">
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
          className="w-40 h-8 text-xs font-mono bg-[#1f2937]/40 border-border/50"
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
                  <Card key={s.id} className="border-border/50 bg-[#1f2937]/40">
                    <CardContent className="p-4">
                      <div className="flex items-center gap-2 mb-2">
                        <Badge className={`text-[9px] ${contextTypeBadge(s.stat_type)}`}>{s.stat_type}</Badge>
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
                  <div key={c.id} className="rounded-xl border border-border/40 bg-[#1f2937]/30 p-4">
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
    const interval = setInterval(fetchActivity, 15000);
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
      {/* Chat Input - prominent */}
      <div className="rounded-xl border border-ai-blue/20 bg-ai-blue/5 p-4">
        <div className="flex gap-2">
          <input
            value={chatInput}
            onChange={(e) => setChatInput(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && handleChat()}
            placeholder="Ask Henry about his decisions..."
            className="flex-1 h-10 rounded-lg border border-border/50 bg-[#111827]/60 px-4 text-sm text-white placeholder:text-gray-500 focus:outline-none focus:ring-1 focus:ring-ai-blue/50"
          />
          <button onClick={handleChat} disabled={chatLoading || !chatInput.trim()}
            className="px-5 h-10 rounded-lg bg-ai-blue/20 text-ai-blue border border-ai-blue/30 text-sm font-semibold hover:bg-ai-blue/30 transition disabled:opacity-50">
            {chatLoading ? "..." : "Ask"}
          </button>
        </div>
      </div>

      {/* Chat Messages */}
      {chatMessages.length > 0 && (
        <div className="space-y-3 max-h-60 overflow-y-auto">
          {chatMessages.map((msg, i) => (
            <div key={i} className={`p-3 rounded-xl text-sm ${
              msg.role === "user"
                ? "bg-[#1f2937]/40 border border-border/40 text-gray-300 ml-8"
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
            <div className="bg-ai-blue/5 border border-ai-blue/20 p-3 rounded-xl mr-8">
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
        <h3 className="text-sm font-semibold text-white" style={FONT_OUTFIT}>Activity Log</h3>
        <span className="text-[9px] text-gray-500 font-mono">{activity.length} entries · auto-refreshing</span>
      </div>

      {loading ? (
        <div className="space-y-2">
          {[1,2,3,4,5].map(i => <Skeleton key={i} className="h-10 rounded-lg" />)}
        </div>
      ) : activity.length === 0 ? (
        <div className="text-center py-12 text-gray-500 text-sm">
          No activity yet. Henry logs entries when he scans, analyzes, and trades.
        </div>
      ) : (
        <div className="space-y-1 max-h-[500px] overflow-y-auto">
          {activity.map((a) => (
            <div key={a.id}
              className={`flex items-start gap-3 px-3 py-2.5 rounded-lg border-l-2 bg-[#1f2937]/20 hover:bg-[#1f2937]/40 transition ${
                activityColor[a.activity_type] || "border-l-gray-600"
              }`}>
              <span className="text-[10px] shrink-0 mt-0.5">{a.activity_label}</span>
              <div className="flex-1 min-w-0">
                <span className="text-xs text-gray-300">{a.message}</span>
                {a.ticker && (
                  <span className="ml-2 text-[9px] font-mono text-white bg-[#1f2937]/60 px-1.5 py-0.5 rounded">{a.ticker}</span>
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

// ── Tab definitions ────────────────────────────────────────────────

const TABS = [
  { id: "briefing", label: "Briefing" },
  { id: "activity", label: "Activity" },
  { id: "ask", label: "Ask Henry" },
  { id: "actions", label: "Actions" },
  { id: "memory", label: "Memory" },
];

// ── Main Page ──────────────────────────────────────────────────────

export default function HomePage() {
  useFonts();
  const [activeTab, setActiveTab] = useState("briefing");

  // Fetch dashboard data
  const { data: portfolios } = usePolling(() => api.getPortfolios(), 30000);
  const { data: actionStats } = usePolling(() => api.getActionStats(), 30000);
  const { data: pendingActions } = usePolling(() => api.getActions("pending"), 15000);
  const [signalCount, setSignalCount] = useState(0);
  const [scannerCount, setScannerCount] = useState(0);
  const [activityCount, setActivityCount] = useState(0);
  const [traders, setTraders] = useState<{ id: string }[] | null>(null);

  useEffect(() => {
    api.getScreenerAlerts({ hours: 24 }).then((a) => setSignalCount(a.length)).catch(() => {});
    api.getScannerResults().then((r) => setScannerCount(r.length)).catch(() => {});
    api.getHenryActivity(50).then((a) => setActivityCount(a.length)).catch(() => {});
    api.getTraders().then((t) => setTraders(t)).catch(() => setTraders([]));
  }, []);

  if (traders !== null && traders.length === 0 && (!portfolios || portfolios.length === 0)) {
    return <OnboardingWizard />;
  }

  return (
    <div className="max-w-5xl mx-auto">
      {/* Hero */}
      <HeroSection
        portfolios={portfolios || []}
        pendingActions={pendingActions || []}
        activityCount={activityCount}
      />

      {/* Tab bar - horizontal pills */}
      <div className="flex items-center gap-1 mb-6 overflow-x-auto pb-1">
        {TABS.map((tab) => (
          <button
            key={tab.id}
            onClick={() => setActiveTab(tab.id)}
            className={`px-4 py-2 rounded-full text-sm font-medium whitespace-nowrap transition-all ${
              activeTab === tab.id
                ? "bg-ai-blue text-white shadow-lg shadow-ai-blue/20"
                : "bg-[#1f2937]/40 text-gray-400 hover:text-white hover:bg-[#1f2937]/70"
            }`}
            style={FONT_OUTFIT}
          >
            {tab.label}
            {tab.id === "actions" && pendingActions && pendingActions.length > 0 && (
              <span className={`ml-1.5 text-[10px] font-mono px-1.5 py-0.5 rounded-full ${
                activeTab === tab.id
                  ? "bg-white/20 text-white"
                  : "bg-ai-blue/20 text-ai-blue"
              }`}>
                {pendingActions.length}
              </span>
            )}
          </button>
        ))}
      </div>

      {/* Tab content */}
      <div>
        {activeTab === "briefing" && <BriefingTab />}
        {activeTab === "activity" && <HenryActivityLog />}
        {activeTab === "ask" && <AskHenry />}
        {activeTab === "actions" && <ActionQueueTab />}
        {activeTab === "memory" && <MemoryTab />}
      </div>
    </div>
  );
}
