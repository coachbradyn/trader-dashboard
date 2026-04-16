"use client";

import { useState, useEffect, useRef, useCallback, Suspense } from "react";
import { useSearchParams } from "next/navigation";
import { api } from "@/lib/api";
import { usePolling } from "@/hooks/usePolling";
import { renderMarkdown } from "@/lib/markdown";
import { formatCurrency, formatPercent, formatTimeAgo, pnlColor } from "@/lib/formatters";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import dynamic from "next/dynamic";

// 3D memory map is heavy (three.js) — load lazily so it doesn't block
// the Chat/Activity tabs on first paint.
const MemoryMap3D = dynamic(
  () => import("@/components/ai/MemoryMap3D").then((m) => m.MemoryMap3D),
  { ssr: false, loading: () => <div className="text-xs text-gray-500 p-6">Loading 3D view…</div> }
);

const FONT_OUTFIT = { fontFamily: "'Outfit', sans-serif" } as const;
const FONT_MONO = { fontFamily: "'JetBrains Mono', monospace" } as const;

function useFonts() {
  useEffect(() => {
    if (document.getElementById("__henry-fonts")) return;
    const link = document.createElement("link");
    link.id = "__henry-fonts";
    link.rel = "stylesheet";
    link.href = "https://fonts.googleapis.com/css2?family=Outfit:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500;600;700&display=swap";
    document.head.appendChild(link);
  }, []);
}

// ══════════════════════════════════════════════════════════════════════
// CHAT TAB — Persistent conversation with Henry
// ══════════════════════════════════════════════════════════════════════

interface ChatMessage {
  id: string;
  role: "user" | "henry";
  text: string;
  created_at: string;
}

const SUGGESTIONS = [
  "What trades did you make today?",
  "Why did you skip that signal?",
  "What's your current strategy?",
  "How is the AI portfolio performing?",
  "What are you watching right now?",
];

// Phase 4 chat citation parser:
// Henry's prompt now lists memories with [mem:<12-char id>] tags and is
// instructed to cite them inline. Post-render, we replace each token
// with an anchor that deep-links to the 3D memory map and pulses the
// memory there. Done after renderMarkdown so it survives any inline
// formatting the model might apply around the tag.
const MEM_TAG_RE = /\[mem:([a-f0-9]{6,36})\]/gi;
function injectMemoryCitations(html: string): string {
  return html.replace(MEM_TAG_RE, (_match, id) => {
    const safe = String(id).toLowerCase().replace(/[^a-f0-9]/g, "");
    if (!safe) return _match;
    const href = `/henry?tab=memory-3d&focus=${encodeURIComponent(safe)}`;
    // Inline-pill styling so citations are visually obvious without
    // dominating the message body. Title shows the full id on hover.
    return (
      `<a href="${href}" ` +
      `class="inline-block px-1.5 py-0.5 mx-0.5 rounded text-[10px] font-mono ` +
      `bg-[#6366f1]/15 text-[#6366f1] hover:bg-[#6366f1]/30 hover:text-white ` +
      `border border-[#6366f1]/30 no-underline" ` +
      `title="Memory ${safe} — open in 3D map">` +
      `mem:${safe.slice(0, 6)}</a>`
    );
  });
}

function ChatTab() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [historyLoaded, setHistoryLoaded] = useState(false);
  const viewportRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  // Load chat history on mount
  useEffect(() => {
    api.getChatHistory(100).then((history) => {
      setMessages(history.map((h) => ({
        id: h.id,
        role: h.role as "user" | "henry",
        text: h.text,
        created_at: h.created_at,
      })));
      setHistoryLoaded(true);
    }).catch(() => setHistoryLoaded(true));
  }, []);

  const scrollToBottom = useCallback(() => {
    if (viewportRef.current) {
      viewportRef.current.scrollTop = viewportRef.current.scrollHeight;
    }
  }, []);

  useEffect(() => { scrollToBottom(); }, [messages, loading, scrollToBottom]);

  const sendMessage = async (text: string) => {
    if (!text.trim() || loading) return;
    const q = text.trim();
    setInput("");
    setLoading(true);

    const userMsg: ChatMessage = {
      id: `user-${Date.now()}`,
      role: "user",
      text: q,
      created_at: new Date().toISOString(),
    };
    setMessages((prev) => [...prev, userMsg]);

    try {
      const result = await api.chatWithHenry(q);
      const henryMsg: ChatMessage = {
        id: `henry-${Date.now()}`,
        role: "henry",
        text: result.answer,
        created_at: new Date().toISOString(),
      };
      setMessages((prev) => [...prev, henryMsg]);
    } catch (e) {
      setMessages((prev) => [...prev, {
        id: `error-${Date.now()}`,
        role: "henry",
        text: `Error: ${e instanceof Error ? e.message : "Failed to get response"}`,
        created_at: new Date().toISOString(),
      }]);
    } finally {
      setLoading(false);
      inputRef.current?.focus();
    }
  };

  const clearHistory = async () => {
    if (!confirm("Clear all chat history with Henry?")) return;
    try {
      await api.clearChatHistory();
      setMessages([]);
    } catch {}
  };

  return (
    <div className="flex flex-col h-[calc(100vh-220px)] min-h-[500px]">
      {/* Chat viewport */}
      <div ref={viewportRef} className="flex-1 overflow-y-auto rounded-xl border border-border/50 bg-[#0d1117] p-4 space-y-4">
        {!historyLoaded ? (
          <div className="space-y-3">
            <Skeleton className="h-12 w-3/4" />
            <Skeleton className="h-20 w-full" />
            <Skeleton className="h-12 w-2/3" />
          </div>
        ) : messages.length === 0 && !loading ? (
          <div className="h-full flex flex-col items-center justify-center">
            <div className="w-16 h-16 rounded-full bg-[#6366f1]/10 flex items-center justify-center mb-4">
              <svg className="w-8 h-8 text-[#6366f1]/40" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M7.5 8.25h9m-9 3H12m-9.75 1.51c0 1.6 1.123 2.994 2.707 3.227 1.129.166 2.27.293 3.423.379.35.026.67.21.865.501L12 21l2.755-4.133a1.14 1.14 0 01.865-.501 48.172 48.172 0 003.423-.379c1.584-.233 2.707-1.626 2.707-3.228V6.741c0-1.602-1.123-2.995-2.707-3.228A48.394 48.394 0 0012 3c-2.392 0-4.744.175-7.043.513C3.373 3.746 2.25 5.14 2.25 6.741v6.018z" />
              </svg>
            </div>
            <p className="text-sm text-gray-500 mb-1" style={FONT_OUTFIT}>Chat with Henry</p>
            <p className="text-xs text-gray-600 mb-6" style={FONT_OUTFIT}>Ask about trades, strategy, or portfolio decisions</p>
            <div className="flex flex-wrap gap-2 justify-center max-w-lg">
              {SUGGESTIONS.map((s) => (
                <button key={s} onClick={() => sendMessage(s)}
                  className="px-3 py-1.5 rounded-full text-xs bg-[#6366f1]/8 text-[#6366f1]/70 border border-[#6366f1]/15 hover:bg-[#6366f1]/15 hover:text-[#6366f1] transition">
                  {s}
                </button>
              ))}
            </div>
          </div>
        ) : (
          messages.map((msg) => (
            <div key={msg.id} className={`flex ${msg.role === "user" ? "justify-end" : "justify-start"}`}>
              <div className={`max-w-[85%] rounded-xl px-4 py-3 ${
                msg.role === "user"
                  ? "bg-[#6366f1]/15 border border-[#6366f1]/20 text-gray-200"
                  : "bg-[#1f2937]/60 border border-[#374151]/50 text-gray-300"
              }`}>
                {msg.role === "henry" ? (
                  <div className="text-[12px] leading-relaxed prose-sm" style={FONT_MONO}
                    dangerouslySetInnerHTML={{ __html: injectMemoryCitations(renderMarkdown(msg.text)) }} />
                ) : (
                  <p className="text-[13px]" style={FONT_OUTFIT}>{msg.text}</p>
                )}
                <p className="text-[9px] text-gray-600 mt-1.5 font-mono">{formatTimeAgo(msg.created_at)}</p>
              </div>
            </div>
          ))
        )}
        {loading && (
          <div className="flex justify-start">
            <div className="bg-[#1f2937]/60 border border-[#374151]/50 rounded-xl px-4 py-3">
              <span className="text-[#6366f1] font-mono animate-pulse">Thinking...</span>
            </div>
          </div>
        )}
      </div>

      {/* Input bar */}
      <div className="mt-3 flex items-center gap-2 rounded-xl border border-border/60 bg-[#0d1117] px-4 py-2.5">
        <Input ref={inputRef} type="text" value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendMessage(input); } }}
          placeholder="Ask Henry anything..."
          disabled={loading}
          className="flex-1 bg-transparent text-gray-200 placeholder:text-gray-600 border-0 focus-visible:ring-0 h-auto p-0 text-sm" />
        <button onClick={clearHistory} className="text-gray-600 hover:text-gray-400 transition p-1" title="Clear history">
          <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M14.74 9l-.346 9m-4.788 0L9.26 9m9.968-3.21c.342.052.682.107 1.022.166m-1.022-.165L18.16 19.673a2.25 2.25 0 01-2.244 2.077H8.084a2.25 2.25 0 01-2.244-2.077L4.772 5.79m14.456 0a48.108 48.108 0 00-3.478-.397m-12 .562c.34-.059.68-.114 1.022-.165m0 0a48.11 48.11 0 013.478-.397m7.5 0v-.916c0-1.18-.91-2.164-2.09-2.201a51.964 51.964 0 00-3.32 0c-1.18.037-2.09 1.022-2.09 2.201v.916m7.5 0a48.667 48.667 0 00-7.5 0" />
          </svg>
        </button>
        <Button onClick={() => sendMessage(input)} disabled={loading || !input.trim()}
          className="bg-[#6366f1] hover:bg-[#6366f1]/80 text-white text-xs px-4 h-8">
          Send
        </Button>
      </div>
    </div>
  );
}

// ══════════════════════════════════════════════════════════════════════
// ACTIVITY TAB — Full-page feed with filters
// ══════════════════════════════════════════════════════════════════════

const ACTIVITY_ICONS: Record<string, string> = {
  scan_start: "🔍", scan_result: "📊", scan_profile: "🎯",
  trade_execute: "💰", trade_skip: "⏭️", trade_exit: "📤",
  pattern_detect: "🔬", analysis: "🧠", error: "❌", status: "📋",
};

function ActivityTab() {
  const [activities, setActivities] = useState<Array<{
    id: string; message: string; activity_type: string; activity_label: string;
    ticker: string | null; created_at: string;
  }>>([]);
  const [loading, setLoading] = useState(true);
  const [typeFilter, setTypeFilter] = useState("");
  const [tickerFilter, setTickerFilter] = useState("");

  const fetchActivities = useCallback(async () => {
    try {
      const data = await api.getHenryActivity(200, tickerFilter || undefined);
      setActivities(typeFilter ? data.filter((a) => a.activity_type === typeFilter) : data);
    } catch {}
    setLoading(false);
  }, [typeFilter, tickerFilter]);

  useEffect(() => { fetchActivities(); }, [fetchActivities]);
  useEffect(() => { const iv = setInterval(fetchActivities, 30000); return () => clearInterval(iv); }, [fetchActivities]);

  const types = Array.from(new Set(activities.map((a) => a.activity_type))).sort();

  return (
    <div>
      {/* Filters */}
      <div className="flex flex-wrap items-center gap-2 mb-4">
        <select value={typeFilter} onChange={(e) => setTypeFilter(e.target.value)}
          className="h-8 text-[11px] font-mono bg-[#1f2937]/50 border border-border rounded-lg px-2 text-white">
          <option value="">All types</option>
          {types.map((t) => <option key={t} value={t}>{t}</option>)}
        </select>
        <Input value={tickerFilter} onChange={(e) => setTickerFilter(e.target.value.toUpperCase())}
          placeholder="Filter by ticker" className="w-32 h-8 text-[11px] font-mono bg-[#1f2937]/50 border-border" />
        <span className="text-[10px] text-gray-600 ml-auto" style={FONT_MONO}>{activities.length} entries</span>
      </div>

      {loading ? (
        <div className="space-y-2">{[1,2,3,4,5].map((i) => <Skeleton key={i} className="h-14" />)}</div>
      ) : activities.length === 0 ? (
        <p className="text-gray-500 text-sm text-center py-12">No activity yet</p>
      ) : (
        <div className="space-y-1">
          {activities.map((a) => (
            <div key={a.id} className="flex items-start gap-3 px-3 py-2.5 rounded-lg hover:bg-[#1f2937]/30 transition">
              <span className="text-base mt-0.5 shrink-0">{ACTIVITY_ICONS[a.activity_type] || "📋"}</span>
              <div className="flex-1 min-w-0">
                <p className="text-[12px] text-gray-300 leading-relaxed">{a.message}</p>
                <div className="flex items-center gap-2 mt-1">
                  <Badge className="text-[8px] px-1.5 py-0 bg-[#1f2937] text-gray-500 border-border/30">{a.activity_type}</Badge>
                  {a.ticker && <Badge className="text-[8px] px-1.5 py-0 bg-[#6366f1]/10 text-[#6366f1]">{a.ticker}</Badge>}
                  <span className="text-[9px] text-gray-600 font-mono ml-auto">{formatTimeAgo(a.created_at)}</span>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ══════════════════════════════════════════════════════════════════════
// DECISIONS TAB — Signal → Evaluation → Action → Outcome
// ══════════════════════════════════════════════════════════════════════

function DecisionsTab() {
  const [decisions, setDecisions] = useState<Array<{
    id: string; ticker: string; direction: string; action_type: string;
    confidence: number; reasoning: string; status: string;
    outcome: { pnl_pct: number; pnl_dollars: number; correct: boolean } | null;
    created_at: string;
    portfolio_id?: string; portfolio_name?: string;
  }>>([]);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState("all");
  const [fetchError, setFetchError] = useState<string | null>(null);

  useEffect(() => {
    setFetchError(null);
    api.getAIPortfolioDecisions(filter, 100)
      .then(setDecisions)
      .catch((e) => setFetchError(e instanceof Error ? e.message : "Failed to load decisions"))
      .finally(() => setLoading(false));
  }, [filter]);

  if (loading) return <div className="space-y-2">{[1,2,3,4,5].map((i) => <Skeleton key={i} className="h-20" />)}</div>;

  const taken = decisions.filter((d) => d.status === "approved" && d.action_type !== "SKIP").length;
  const skipped = decisions.length - taken;
  const withOutcome = decisions.filter((d) => d.outcome);
  const hitRate = withOutcome.length > 0
    ? (withOutcome.filter((d) => d.outcome?.correct).length / withOutcome.length * 100) : 0;

  return (
    <div>
      {/* Stats bar */}
      <div className="flex items-center gap-4 mb-4 text-[11px] font-mono">
        <span className="text-gray-500">Signals: <span className="text-white">{decisions.length}</span></span>
        <span className="text-gray-500">Taken: <span className="text-profit">{taken}</span></span>
        <span className="text-gray-500">Skipped: <span className="text-gray-400">{skipped}</span></span>
        {withOutcome.length > 0 && (
          <span className="text-gray-500">Hit rate: <span className={hitRate >= 50 ? "text-profit" : "text-loss"}>{hitRate.toFixed(0)}%</span></span>
        )}
        <div className="ml-auto flex gap-1">
          {["all", "taken", "skipped"].map((f) => (
            <button key={f} onClick={() => setFilter(f)}
              className={`px-2 py-0.5 rounded text-[10px] transition ${filter === f ? "bg-[#6366f1]/20 text-[#6366f1]" : "text-gray-500 hover:text-gray-300"}`}>
              {f}
            </button>
          ))}
        </div>
      </div>

      {fetchError ? (
        <p className="text-loss text-sm text-center py-12">Couldn&apos;t load decisions: {fetchError}</p>
      ) : decisions.length === 0 ? (
        <p className="text-gray-500 text-sm text-center py-12">
          No decisions yet — enable AI evaluation on a portfolio (or create one with Henry as manager) and wait for the next scan cycle.
        </p>
      ) : (
        <div className="space-y-2">
          {decisions.map((d) => (
            <div key={d.id} className="rounded-lg border border-border/50 p-3 hover:border-border transition">
              <div className="flex items-center gap-2 mb-1.5">
                <span className="text-sm font-bold text-white" style={FONT_OUTFIT}>{d.ticker}</span>
                <Badge className={`text-[8px] px-1.5 py-0 ${d.direction === "long" ? "bg-profit/15 text-profit" : "bg-loss/15 text-loss"}`}>
                  {(d.direction || "long").toUpperCase()}
                </Badge>
                <Badge className={`text-[8px] px-1.5 py-0 ${
                  d.action_type === "BUY" ? "bg-profit/15 text-profit"
                    : d.action_type === "SKIP" ? "bg-gray-700/50 text-gray-400"
                    : d.action_type === "CLOSE" ? "bg-loss/15 text-loss"
                    : "bg-[#6366f1]/15 text-[#6366f1]"
                }`}>{d.action_type}</Badge>
                {/* Confidence dots */}
                <div className="flex gap-px ml-1">
                  {Array.from({ length: 10 }).map((_, i) => (
                    <div key={i} className={`w-1.5 h-1 rounded-sm ${i < d.confidence ? "bg-[#6366f1]" : "bg-gray-800"}`} />
                  ))}
                </div>
                {d.portfolio_name && (
                  <span
                    className="text-[9px] text-gray-500 font-mono ml-1 px-1.5 py-0 rounded bg-[#1f2937]/40"
                    style={FONT_MONO}
                    title={`Portfolio: ${d.portfolio_name}`}
                  >
                    {d.portfolio_name}
                  </span>
                )}
                <span className="text-[9px] text-gray-600 font-mono ml-auto">{formatTimeAgo(d.created_at)}</span>
                {d.outcome && (
                  <Badge className={`text-[8px] px-1.5 py-0 ${d.outcome.correct ? "bg-profit/15 text-profit" : "bg-loss/15 text-loss"}`}>
                    {d.outcome.pnl_pct > 0 ? "+" : ""}{d.outcome.pnl_pct.toFixed(1)}%
                  </Badge>
                )}
              </div>
              <p className="text-[11px] text-gray-400 leading-relaxed line-clamp-2">{d.reasoning}</p>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ══════════════════════════════════════════════════════════════════════
// MEMORY TAB — Henry's stored memories
// ══════════════════════════════════════════════════════════════════════

interface Memory {
  id: string; type: string; ticker: string | null; strategy: string | null;
  content: string; importance: number; validated: boolean; source: string; created_at: string;
}

function MemoryTab() {
  const [memories, setMemories] = useState<Memory[]>([]);
  const [loading, setLoading] = useState(true);
  const [typeFilter, setTypeFilter] = useState("");
  const [sourceFilter, setSourceFilter] = useState("");

  const fetchMemories = useCallback(async () => {
    try {
      const params: { type?: string; source?: string } = {};
      if (typeFilter) params.type = typeFilter;
      if (sourceFilter) params.source = sourceFilter;
      const data = await api.getMemories(params);
      setMemories(data);
    } catch {}
    setLoading(false);
  }, [typeFilter, sourceFilter]);

  useEffect(() => { fetchMemories(); }, [fetchMemories]);

  const types = Array.from(new Set(memories.map((m) => m.type))).sort();
  const sources = Array.from(new Set(memories.map((m) => m.source))).sort();

  const handleDelete = async (id: string) => {
    if (!confirm("Delete this memory?")) return;
    try { await api.deleteMemory(id); fetchMemories(); } catch {}
  };

  const handleUpdateImportance = async (id: string, importance: number) => {
    try { await api.updateMemory(id, { importance }); fetchMemories(); } catch {}
  };

  return (
    <div>
      {/* Filters */}
      <div className="flex flex-wrap items-center gap-2 mb-4">
        <select value={typeFilter} onChange={(e) => setTypeFilter(e.target.value)}
          className="h-8 text-[11px] font-mono bg-[#1f2937]/50 border border-border rounded-lg px-2 text-white">
          <option value="">All types</option>
          {types.map((t) => <option key={t} value={t}>{t}</option>)}
        </select>
        <select value={sourceFilter} onChange={(e) => setSourceFilter(e.target.value)}
          className="h-8 text-[11px] font-mono bg-[#1f2937]/50 border border-border rounded-lg px-2 text-white">
          <option value="">All sources</option>
          {sources.map((s) => <option key={s} value={s}>{s}</option>)}
        </select>
        <span className="text-[10px] text-gray-600 ml-auto" style={FONT_MONO}>{memories.length} memories</span>
      </div>

      {loading ? (
        <div className="space-y-2">{[1,2,3,4,5].map((i) => <Skeleton key={i} className="h-16" />)}</div>
      ) : memories.length === 0 ? (
        <p className="text-gray-500 text-sm text-center py-12">No memories stored</p>
      ) : (
        <div className="space-y-1.5">
          {memories.map((m) => (
            <div key={m.id} className="flex items-start gap-3 px-3 py-2.5 rounded-lg border border-border/30 hover:border-border/60 transition group">
              <div className="flex-1 min-w-0">
                <p className="text-[12px] text-gray-300 leading-relaxed">{m.content}</p>
                <div className="flex items-center gap-2 mt-1.5">
                  <Badge className="text-[8px] px-1.5 py-0 bg-[#1f2937] text-gray-500">{m.type}</Badge>
                  {m.ticker && <Badge className="text-[8px] px-1.5 py-0 bg-[#6366f1]/10 text-[#6366f1]">{m.ticker}</Badge>}
                  {m.strategy && <Badge className="text-[8px] px-1.5 py-0 bg-amber-500/10 text-amber-400">{m.strategy}</Badge>}
                  <span className="text-[9px] text-gray-600 font-mono">{m.source}</span>
                  <span className="text-[9px] text-gray-600 font-mono ml-auto">{formatTimeAgo(m.created_at)}</span>
                </div>
              </div>
              <div className="flex items-center gap-1.5 shrink-0">
                {/* Importance */}
                <div className="flex gap-px">
                  {Array.from({ length: 10 }).map((_, i) => (
                    <button key={i} onClick={() => handleUpdateImportance(m.id, i + 1)}
                      className={`w-1.5 h-3 rounded-sm transition ${i < m.importance ? "bg-[#6366f1]" : "bg-gray-800 hover:bg-gray-700"}`} />
                  ))}
                </div>
                <button onClick={() => handleDelete(m.id)}
                  className="text-gray-700 hover:text-loss transition opacity-0 group-hover:opacity-100 ml-1">
                  <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
                  </svg>
                </button>
              </div>
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

// Whitelist of tab values — guards against arbitrary strings in ?tab=.
const VALID_TABS = new Set(["chat", "activity", "decisions", "memory", "memory-3d"]);

// Wrapper provides the Suspense boundary required by Next 14 App Router
// when a client component uses useSearchParams() — without this, the
// /henry page fails static prerender at build time with
// "useSearchParams() should be wrapped in a suspense boundary".
export default function HenryPage() {
  return (
    <Suspense fallback={<div className="h-screen" />}>
      <HenryPageInner />
    </Suspense>
  );
}

function HenryPageInner() {
  useFonts();
  // Read ?tab=... from URL so the 3D Map's "Open in Memory tab" click and
  // other deep-links land on the right tab. Controlled <Tabs value=...>
  // so the default is only the initial URL — user clicks after that work
  // locally without a navigation.
  const searchParams = useSearchParams();
  const urlTab = searchParams?.get("tab");
  const initialTab =
    urlTab && VALID_TABS.has(urlTab) ? urlTab : "chat";
  const [activeTab, setActiveTab] = useState(initialTab);
  // Re-sync when the URL changes (back button, external link).
  useEffect(() => {
    if (urlTab && VALID_TABS.has(urlTab) && urlTab !== activeTab) {
      setActiveTab(urlTab);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [urlTab]);

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center gap-4">
        <div className="w-12 h-12 rounded-xl bg-[#6366f1]/10 flex items-center justify-center">
          <svg className="w-6 h-6 text-[#6366f1]" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M9.813 15.904L9 18.75l-.813-2.846a4.5 4.5 0 00-3.09-3.09L2.25 12l2.846-.813a4.5 4.5 0 003.09-3.09L9 5.25l.813 2.846a4.5 4.5 0 003.09 3.09L15.75 12l-2.846.813a4.5 4.5 0 00-3.09 3.09zM18.259 8.715L18 9.75l-.259-1.035a3.375 3.375 0 00-2.455-2.456L14.25 6l1.036-.259a3.375 3.375 0 002.455-2.456L18 2.25l.259 1.035a3.375 3.375 0 002.455 2.456L21.75 6l-1.036.259a3.375 3.375 0 00-2.455 2.456z" />
          </svg>
        </div>
        <div>
          <h1 className="text-2xl font-bold text-white tracking-tight" style={FONT_OUTFIT}>Henry</h1>
          <p className="text-xs text-gray-500" style={FONT_OUTFIT}>AI trader, analyst, and portfolio manager</p>
        </div>
      </div>

      {/* Tabs */}
      <Tabs value={activeTab} onValueChange={setActiveTab} className="w-full">
        <TabsList className="bg-[#1f2937]/30 border border-border p-1 rounded-lg">
          <TabsTrigger value="chat" className="text-xs data-[state=active]:bg-[#6366f1]/20 data-[state=active]:text-[#6366f1]">Chat</TabsTrigger>
          <TabsTrigger value="activity" className="text-xs data-[state=active]:bg-[#6366f1]/20 data-[state=active]:text-[#6366f1]">Activity</TabsTrigger>
          <TabsTrigger value="decisions" className="text-xs data-[state=active]:bg-[#6366f1]/20 data-[state=active]:text-[#6366f1]">Decisions</TabsTrigger>
          <TabsTrigger value="memory" className="text-xs data-[state=active]:bg-[#6366f1]/20 data-[state=active]:text-[#6366f1]">Memory</TabsTrigger>
          <TabsTrigger value="memory-3d" className="text-xs data-[state=active]:bg-[#6366f1]/20 data-[state=active]:text-[#6366f1]">3D Map</TabsTrigger>
        </TabsList>

        <TabsContent value="chat" className="mt-4">
          <ChatTab />
        </TabsContent>

        <TabsContent value="activity" className="mt-4">
          <ActivityTab />
        </TabsContent>

        <TabsContent value="decisions" className="mt-4">
          <DecisionsTab />
        </TabsContent>

        <TabsContent value="memory" className="mt-4">
          <MemoryTab />
        </TabsContent>

        <TabsContent value="memory-3d" className="mt-4">
          <MemoryMap3D />
        </TabsContent>
      </Tabs>
    </div>
  );
}
