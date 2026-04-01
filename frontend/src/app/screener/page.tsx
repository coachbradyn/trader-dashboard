"use client";

import { useState, useEffect, useCallback, useMemo } from "react";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";
import { formatTimeAgo, formatIndicator } from "@/lib/formatters";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Badge } from "@/components/ui/badge";
import type {
  WatchlistTickerData,
  ChartDataPoint,
} from "@/lib/types";

const FONT_OUTFIT = { fontFamily: "'Outfit', sans-serif" } as const;
const FONT_MONO = { fontFamily: "'JetBrains Mono', monospace" } as const;

// ── Fonts ────────────────────────────────────────────────────────────────
function useFonts() {
  useEffect(() => {
    if (document.getElementById("__watchlist-fonts")) return;
    const link = document.createElement("link");
    link.id = "__watchlist-fonts";
    link.rel = "stylesheet";
    link.href =
      "https://fonts.googleapis.com/css2?family=Outfit:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500;600;700&display=swap";
    document.head.appendChild(link);
  }, []);
}

// ── Helpers ──────────────────────────────────────────────────────────────
function consensusBadge(direction: string) {
  switch (direction) {
    case "bullish":
      return { bg: "bg-profit/15 border-profit/30", text: "text-profit", label: "Bullish" };
    case "bearish":
      return { bg: "bg-loss/15 border-loss/30", text: "text-loss", label: "Bearish" };
    case "mixed":
      return { bg: "bg-yellow-500/15 border-yellow-500/30", text: "text-yellow-400", label: "Mixed" };
    default:
      return { bg: "bg-gray-700/30 border-gray-600/30", text: "text-gray-500", label: "No Data" };
  }
}

function signalDot(signal: string) {
  const s = signal.toLowerCase();
  if (s === "bullish") return "bg-profit";
  if (s === "bearish") return "bg-loss";
  return "bg-gray-500";
}

// ── Sort options ────────────────────────────────────────────────────────
type SortMode = "recent" | "consensus" | "name";

function sortWatchlist(items: WatchlistTickerData[], mode: SortMode): WatchlistTickerData[] {
  return [...items].sort((a, b) => {
    if (mode === "name") return a.ticker.localeCompare(b.ticker);

    if (mode === "consensus") {
      // Sort by total signals desc, then ticker name
      if (b.consensus.total_signals !== a.consensus.total_signals)
        return b.consensus.total_signals - a.consensus.total_signals;
      return a.ticker.localeCompare(b.ticker);
    }

    // Default: recent activity
    const aTime = a.last_alert_at ? new Date(a.last_alert_at).getTime() : 0;
    const bTime = b.last_alert_at ? new Date(b.last_alert_at).getTime() : 0;
    const now = Date.now();
    const aRecent = now - aTime < 3600000;
    const bRecent = now - bTime < 3600000;
    if (aRecent && !bRecent) return -1;
    if (!aRecent && bRecent) return 1;
    if (b.consensus.total_signals !== a.consensus.total_signals)
      return b.consensus.total_signals - a.consensus.total_signals;
    if (b.strategy_positions.length !== a.strategy_positions.length)
      return b.strategy_positions.length - a.strategy_positions.length;
    return a.ticker.localeCompare(b.ticker);
  });
}

// ── Mini Sparkline ─────────────────────────────────────────────────────
function MiniSparkline({
  events,
}: {
  events: Array<{ date: string; signal: string }>;
}) {
  if (!events || events.length < 2) return null;

  // Build a simple trend line from signal events
  const W = 64;
  const H = 24;
  const len = Math.min(events.length, 20);
  const recent = events.slice(-len);

  // Map signals to values: bullish = up, bearish = down
  let val = 50;
  const points: number[] = [];
  for (const ev of recent) {
    if (ev.signal === "bullish") val = Math.min(100, val + 10);
    else if (ev.signal === "bearish") val = Math.max(0, val - 10);
    points.push(val);
  }

  const min = Math.min(...points);
  const max = Math.max(...points);
  const range = max - min || 1;

  const pts = points
    .map((v, i) => {
      const x = (i / (points.length - 1)) * W;
      const y = H - ((v - min) / range) * (H - 4) - 2;
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");

  const up = points[points.length - 1] >= points[0];
  const stroke = up ? "#22c55e" : "#ef4444";

  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-16 h-6 flex-shrink-0" preserveAspectRatio="none">
      <polyline
        points={pts}
        fill="none"
        stroke={stroke}
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

// ── Fundamentals Line ────────────────────────────────────────────────────
function FundamentalsLine({ item }: { item: WatchlistTickerData }) {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const f = (item as any).fundamentals as Record<string, unknown> | undefined;
  if (!f) return null;

  const parts: string[] = [];
  if (f.pe_ratio != null) parts.push(`PE ${Number(f.pe_ratio).toFixed(1)}`);
  if (f.analyst_rating) parts.push(String(f.analyst_rating));
  if (f.market_cap != null) {
    const mc = Number(f.market_cap);
    parts.push(mc >= 1e12 ? `$${(mc/1e12).toFixed(1)}T` : mc >= 1e9 ? `$${(mc/1e9).toFixed(1)}B` : `$${(mc/1e6).toFixed(0)}M`);
  }
  const earningsDate = f.earnings_date as string | null;
  if (earningsDate) {
    const daysUntil = Math.ceil((new Date(earningsDate).getTime() - Date.now()) / 86400000);
    if (daysUntil >= 0 && daysUntil <= 30) parts.push(`ER ${daysUntil}d`);
  }
  const dcfDiff = f.dcf_diff_pct as number | null;
  if (dcfDiff != null && Math.abs(dcfDiff) > 10) {
    parts.push(dcfDiff > 0 ? "UNDERVAL" : "OVERVAL");
  }
  if (parts.length === 0) return null;

  return (
    <span className="text-[10px] font-mono text-gray-500">
      {parts.map((p, i) => (
        <span key={i}>
          {i > 0 && <span className="mx-1 text-gray-600">&middot;</span>}
          <span className={
            p === "UNDERVAL" ? "text-profit" :
            p === "OVERVAL" ? "text-loss" :
            p.startsWith("ER ") ? "text-amber-400" :
            "text-gray-400"
          }>
            {p}
          </span>
        </span>
      ))}
    </span>
  );
}

// ── Ticker Row ─────────────────────────────────────────────────────────
function TickerRow({
  item,
  onClick,
}: {
  item: WatchlistTickerData;
  onClick: () => void;
}) {
  const badge = consensusBadge(item.consensus.direction);
  const isRecent = item.last_alert_at
    ? Date.now() - new Date(item.last_alert_at).getTime() < 3600000
    : false;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const companyName = ((item as any).fundamentals as Record<string, unknown> | undefined)?.company_name as string | undefined;

  return (
    <button
      onClick={onClick}
      className={`w-full text-left px-4 sm:px-5 py-4 flex items-center gap-4 border-b border-border/30 transition-all duration-150 hover:bg-[#1f2937]/50 group ${
        isRecent ? "bg-[#1f2937]/30" : ""
      }`}
    >
      {/* Left: Ticker + Company */}
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span
            className="text-base font-bold text-white tracking-tight"
            style={FONT_OUTFIT}
          >
            {item.ticker}
          </span>
          {isRecent && (
            <span className="w-1.5 h-1.5 rounded-full bg-profit animate-pulse flex-shrink-0" />
          )}
        </div>
        {companyName && (
          <div className="text-xs text-gray-500 truncate mt-0.5" style={FONT_OUTFIT}>
            {companyName}
          </div>
        )}
        {/* Mobile: fundamentals + time below ticker */}
        <div className="sm:hidden mt-1.5 flex flex-wrap items-center gap-2">
          <FundamentalsLine item={item} />
          {item.last_alert_at && (
            <span className="text-[10px] text-gray-600 font-mono">{formatTimeAgo(item.last_alert_at)}</span>
          )}
        </div>
      </div>

      {/* Sparkline */}
      <div className="hidden sm:block">
        <MiniSparkline events={item.signal_events} />
      </div>

      {/* Consensus Badge */}
      <div className="flex-shrink-0">
        <span
          className={`text-[10px] font-semibold px-2.5 py-1 rounded-full border ${badge.bg} ${badge.text}`}
        >
          {badge.label}
          {item.consensus.total_signals > 0 && (
            <span className="ml-1 opacity-70">
              {item.consensus.bullish_count}/{item.consensus.bearish_count}
            </span>
          )}
        </span>
      </div>

      {/* Fundamentals - desktop */}
      <div className="hidden sm:block flex-shrink-0 text-right min-w-[140px]">
        <FundamentalsLine item={item} />
      </div>

      {/* Last signal time - desktop */}
      <div className="hidden sm:block flex-shrink-0 w-20 text-right">
        {item.last_alert_at ? (
          <span className="text-[10px] text-gray-500 font-mono">{formatTimeAgo(item.last_alert_at)}</span>
        ) : (
          <span className="text-[10px] text-gray-600 font-mono">--</span>
        )}
      </div>

      {/* Chevron */}
      <svg
        className="w-4 h-4 text-gray-600 group-hover:text-gray-400 transition flex-shrink-0"
        fill="none"
        viewBox="0 0 24 24"
        stroke="currentColor"
        strokeWidth={2}
      >
        <path strokeLinecap="round" strokeLinejoin="round" d="M8.25 4.5l7.5 7.5-7.5 7.5" />
      </svg>
    </button>
  );
}

// ── Main Page ───────────────────────────────────────────────────────────
export default function WatchlistPage() {
  useFonts();
  const router = useRouter();

  const [watchlist, setWatchlist] = useState<WatchlistTickerData[]>([]);
  const [loading, setLoading] = useState(true);
  const [addInput, setAddInput] = useState("");
  const [adding, setAdding] = useState(false);
  const [sortMode, setSortMode] = useState<SortMode>("recent");

  const fetchWatchlist = useCallback(async () => {
    try {
      const [data, fundData] = await Promise.all([
        api.getWatchlist(),
        api.getWatchlistFundamentals().catch(() => ({} as Record<string, unknown>)),
      ]);
      // Inject fundamentals into each watchlist item for display
      const enriched = data.map((item) => {
        const f = fundData[item.ticker];
        if (f) {
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          (item as any).fundamentals = f;
        }
        return item;
      });
      setWatchlist(enriched);
    } catch {}
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    fetchWatchlist().finally(() => setLoading(false));
  }, [fetchWatchlist]);

  useEffect(() => {
    const interval = setInterval(fetchWatchlist, 30000);
    return () => clearInterval(interval);
  }, [fetchWatchlist]);

  const sortedWatchlist = useMemo(() => sortWatchlist(watchlist, sortMode), [watchlist, sortMode]);

  const handleAdd = async () => {
    if (!addInput.trim() || adding) return;
    setAdding(true);
    try {
      const tickers = addInput.split(",").map((t) => t.trim().toUpperCase()).filter(Boolean);
      if (tickers.length > 0) {
        await api.addWatchlistTickers(tickers);
        setAddInput("");
        await fetchWatchlist();
      }
    } catch {}
    setAdding(false);
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter") { e.preventDefault(); handleAdd(); }
  };

  return (
    <div className="max-w-4xl mx-auto">
      {/* Header */}
      <div className="mb-6">
        <div className="flex items-center gap-3 mb-1">
          <h1
            className="text-3xl font-bold text-white tracking-tight"
            style={FONT_OUTFIT}
          >
            Watchlist
          </h1>
          {watchlist.length > 0 && (
            <Badge className="text-xs bg-[#1f2937]/60 text-gray-300 border-border/50">
              {watchlist.length}
            </Badge>
          )}
        </div>
        <p className="text-sm text-gray-500" style={FONT_OUTFIT}>
          Track tickers and monitor signals. Click any row for full analysis.
        </p>
      </div>

      {/* Add Tickers Bar + Sort */}
      <div className="flex flex-col sm:flex-row items-stretch sm:items-center gap-3 mb-6">
        <div className="flex items-center gap-2 flex-1">
          <Input
            type="text"
            value={addInput}
            onChange={(e) => setAddInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Add tickers (e.g. NVDA, AAPL, TSLA)"
            className="flex-1 h-10 bg-[#1f2937]/40 border-border/50 text-sm font-mono placeholder:text-gray-600"
          />
          <Button
            onClick={handleAdd}
            disabled={adding || !addInput.trim()}
            className="bg-ai-blue/15 text-ai-blue border border-ai-blue/30 hover:bg-ai-blue/25 h-10 px-5 font-semibold"
          >
            {adding ? (
              <span className="w-1.5 h-1.5 rounded-full bg-ai-blue animate-pulse" />
            ) : (
              <svg className="w-4 h-4 mr-1" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M12 4.5v15m7.5-7.5h-15" />
              </svg>
            )}
            Add
          </Button>
        </div>

        {/* Sort dropdown */}
        <div className="flex items-center gap-2">
          <span className="text-[10px] text-gray-500 uppercase tracking-wider whitespace-nowrap" style={FONT_OUTFIT}>
            Sort by
          </span>
          <div className="flex rounded-lg overflow-hidden border border-border/50">
            {([
              { value: "recent" as SortMode, label: "Recent" },
              { value: "consensus" as SortMode, label: "Signals" },
              { value: "name" as SortMode, label: "Name" },
            ]).map((opt) => (
              <button
                key={opt.value}
                onClick={() => setSortMode(opt.value)}
                className={`px-3 py-1.5 text-xs font-medium transition ${
                  sortMode === opt.value
                    ? "bg-ai-blue/20 text-ai-blue"
                    : "bg-[#1f2937]/40 text-gray-500 hover:text-gray-300"
                }`}
              >
                {opt.label}
              </button>
            ))}
          </div>
        </div>
      </div>

      {/* Loading State */}
      {loading && (
        <div className="rounded-xl border border-border/50 overflow-hidden">
          {[1, 2, 3, 4, 5, 6].map((i) => (
            <div key={i} className="px-5 py-4 border-b border-border/30">
              <Skeleton className="h-5 w-20 rounded mb-2" />
              <Skeleton className="h-3 w-40 rounded" />
            </div>
          ))}
        </div>
      )}

      {/* Empty State */}
      {!loading && watchlist.length === 0 && (
        <div className="flex flex-col items-center justify-center py-24 text-center">
          <div className="w-16 h-16 rounded-full bg-[#1f2937]/60 flex items-center justify-center mb-5">
            <svg className="w-8 h-8 text-gray-600" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M2.036 12.322a1.012 1.012 0 010-.639C3.423 7.51 7.36 4.5 12 4.5c4.64 0 8.577 3.007 9.963 7.178.07.207.07.431 0 .639C20.577 16.49 16.64 19.5 12 19.5c-4.64 0-8.577-3.007-9.963-7.178z" />
              <path strokeLinecap="round" strokeLinejoin="round" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
            </svg>
          </div>
          <h2
            className="text-xl font-bold text-white mb-2"
            style={FONT_OUTFIT}
          >
            No tickers on your watchlist
          </h2>
          <p className="text-sm text-gray-500 max-w-md leading-relaxed">
            Add tickers above to start monitoring. Tickers are automatically added
            when you receive trade signals or add holdings.
          </p>
        </div>
      )}

      {/* Watchlist Rows */}
      {!loading && watchlist.length > 0 && (
        <div className="rounded-xl border border-border/50 overflow-hidden bg-[#111827]/30">
          {/* Table header - desktop */}
          <div className="hidden sm:flex items-center gap-4 px-5 py-2.5 border-b border-border/40 bg-[#1f2937]/20">
            <span className="flex-1 text-[10px] text-gray-600 uppercase tracking-wider" style={FONT_OUTFIT}>Ticker</span>
            <span className="w-16 text-[10px] text-gray-600 uppercase tracking-wider text-center" style={FONT_OUTFIT}>Trend</span>
            <span className="text-[10px] text-gray-600 uppercase tracking-wider" style={FONT_OUTFIT}>Consensus</span>
            <span className="min-w-[140px] text-[10px] text-gray-600 uppercase tracking-wider text-right" style={FONT_OUTFIT}>Fundamentals</span>
            <span className="w-20 text-[10px] text-gray-600 uppercase tracking-wider text-right" style={FONT_OUTFIT}>Last Signal</span>
            <span className="w-4" />
          </div>

          {sortedWatchlist.map((item) => (
            <TickerRow
              key={item.id}
              item={item}
              onClick={() => router.push(`/screener/${item.ticker}`)}
            />
          ))}
        </div>
      )}
    </div>
  );
}
