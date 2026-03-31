"use client";

import { useState, useEffect, useCallback, useMemo } from "react";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";
import { formatTimeAgo, formatIndicator } from "@/lib/formatters";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import type {
  WatchlistTickerData,
  ChartDataPoint,
} from "@/lib/types";

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

// ── Sort watchlist tickers ──────────────────────────────────────────────
function sortWatchlist(items: WatchlistTickerData[]): WatchlistTickerData[] {
  return [...items].sort((a, b) => {
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

// ── Sparkline with Signal Overlays ──────────────────────────────────────
function Sparkline({
  data,
  signalEvents,
  tradeEvents,
  height = 48,
}: {
  data: ChartDataPoint[];
  signalEvents?: Array<{ date: string; signal: string }>;
  tradeEvents?: Array<{ date: string; direction: string }>;
  height?: number;
}) {
  if (!data || data.length < 2) return null;

  const closes = data.map((d) => d.close);
  const dates = data.map((d) => d.date);
  const min = Math.min(...closes);
  const max = Math.max(...closes);
  const range = max - min || 1;
  const W = 200;

  const pts = closes
    .map((c, i) => {
      const x = (i / (closes.length - 1)) * W;
      const y = height - ((c - min) / range) * (height - 4) - 2;
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");

  const up = closes[closes.length - 1] >= closes[0];
  const stroke = up ? "#22c55e" : "#ef4444";
  const gradId = `spark-${up ? "up" : "dn"}-${Math.random().toString(36).slice(2, 6)}`;

  // Map signal events to x positions
  const signalMarkers: Array<{ x: number; y: number; color: string }> = [];
  if (signalEvents) {
    const dateSet = new Map(dates.map((d, i) => [d, i]));
    for (const ev of signalEvents) {
      const idx = dateSet.get(ev.date);
      if (idx !== undefined) {
        const x = (idx / (closes.length - 1)) * W;
        const y = height - ((closes[idx] - min) / range) * (height - 4) - 2;
        signalMarkers.push({
          x, y,
          color: ev.signal === "bullish" ? "#22c55e" : ev.signal === "bearish" ? "#ef4444" : "#6b7280",
        });
      }
    }
  }

  // Map trade events to x positions
  const tradeMarkers: Array<{ x: number; y: number; color: string }> = [];
  if (tradeEvents) {
    const dateSet = new Map(dates.map((d, i) => [d, i]));
    for (const ev of tradeEvents) {
      const idx = dateSet.get(ev.date);
      if (idx !== undefined) {
        const x = (idx / (closes.length - 1)) * W;
        const y = height - ((closes[idx] - min) / range) * (height - 4) - 2;
        tradeMarkers.push({
          x, y,
          color: ev.direction === "long" ? "#6366f1" : "#f59e0b",
        });
      }
    }
  }

  return (
    <svg
      viewBox={`0 0 ${W} ${height}`}
      preserveAspectRatio="none"
      className="w-full"
      style={{ height }}
    >
      <defs>
        <linearGradient id={gradId} x1="0" x2="0" y1="0" y2="1">
          <stop offset="0%" stopColor={stroke} stopOpacity={0.25} />
          <stop offset="100%" stopColor={stroke} stopOpacity={0} />
        </linearGradient>
      </defs>
      <polygon
        points={`0,${height} ${pts} ${W},${height}`}
        fill={`url(#${gradId})`}
      />
      <polyline
        points={pts}
        fill="none"
        stroke={stroke}
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      {/* Signal event markers (small dots) */}
      {signalMarkers.map((m, i) => (
        <circle key={`s${i}`} cx={m.x} cy={m.y} r="2.5" fill={m.color} fillOpacity={0.8} />
      ))}
      {/* Trade event markers (diamonds) */}
      {tradeMarkers.map((m, i) => (
        <polygon
          key={`t${i}`}
          points={`${m.x},${m.y - 4} ${m.x + 3},${m.y} ${m.x},${m.y + 4} ${m.x - 3},${m.y}`}
          fill={m.color}
          fillOpacity={0.9}
        />
      ))}
    </svg>
  );
}

// ── Ticker Card ─────────────────────────────────────────────────────────
function TickerCard({
  item,
  chartData,
  onClick,
}: {
  item: WatchlistTickerData;
  chartData: ChartDataPoint[] | null;
  onClick: () => void;
}) {
  const badge = consensusBadge(item.consensus.direction);
  const hasSignals = item.latest_signals.length > 0 || item.strategy_positions.length > 0;
  const isRecent = item.last_alert_at
    ? Date.now() - new Date(item.last_alert_at).getTime() < 3600000
    : false;

  return (
    <button
      onClick={onClick}
      className={`w-full text-left rounded-xl border transition-all duration-200 p-4
        ${isRecent
          ? "border-accent/30 bg-surface-light/60 hover:border-accent/50 hover:bg-surface-light/80"
          : hasSignals
          ? "border-border/60 bg-surface-light/30 hover:border-border hover:bg-surface-light/50"
          : "border-border/30 bg-surface-light/10 hover:border-border/50 hover:bg-surface-light/20 opacity-60"
        }
      `}
    >
      {/* Top row: ticker + consensus */}
      <div className="flex items-center justify-between mb-2">
        <span
          className="text-xl font-bold text-white tracking-tight"
          style={{ fontFamily: "'Outfit', sans-serif" }}
        >
          {item.ticker}
        </span>
        <span
          className={`text-xs font-semibold px-2.5 py-1 rounded-full border ${badge.bg} ${badge.text}`}
        >
          {badge.label}
          {item.consensus.total_signals > 0 && (
            <span className="ml-1 opacity-70">
              {item.consensus.bullish_count}B/{item.consensus.bearish_count}B
            </span>
          )}
        </span>
      </div>

      {/* Sparkline removed from listing for faster load times — available on detail page */}

      {/* Signal counts + last update */}
      <div className="flex items-center gap-3 mb-2 text-xs text-gray-500 font-mono">
        {item.last_alert_at ? (
          <span>{formatTimeAgo(item.last_alert_at)}</span>
        ) : (
          <span className="text-gray-600">Awaiting signals</span>
        )}
        {item.latest_signals.length > 0 && (
          <span>{item.latest_signals.length} indicator{item.latest_signals.length !== 1 ? "s" : ""}</span>
        )}
      </div>

      {/* Strategy position badges */}
      {item.strategy_positions.length > 0 && (
        <div className="flex flex-wrap gap-1.5 mb-2">
          {item.strategy_positions.map((p) => (
            <span
              key={p.strategy_id}
              className={`text-[10px] font-mono px-2 py-0.5 rounded-full border ${
                p.direction === "long"
                  ? "bg-profit/10 border-profit/20 text-profit"
                  : "bg-loss/10 border-loss/20 text-loss"
              }`}
            >
              {p.strategy_name} {p.direction.toUpperCase()}
            </span>
          ))}
        </div>
      )}

      {/* Fundamentals quick line (if available from cached data) */}
      <FundamentalsLine item={item} />

      {/* Recent indicator signals (up to 3) */}
      {item.latest_signals.length > 0 && (
        <div className="space-y-1">
          {item.latest_signals.slice(0, 3).map((s, i) => (
            <div key={i} className="flex items-center gap-2 text-xs">
              <span className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${signalDot(s.signal)}`} />
              <span className="text-gray-400 font-mono truncate">{formatIndicator(s.indicator)}</span>
              <span className={`ml-auto ${
                s.signal === "bullish" ? "text-profit" : s.signal === "bearish" ? "text-loss" : "text-gray-500"
              }`}>
                {s.signal}
              </span>
            </div>
          ))}
          {item.latest_signals.length > 3 && (
            <span className="text-[10px] text-gray-600 font-mono">
              +{item.latest_signals.length - 3} more
            </span>
          )}
        </div>
      )}

      {/* No signals state */}
      {!hasSignals && (
        <div className="text-center py-2">
          <span className="text-xs text-gray-600">Awaiting signals</span>
        </div>
      )}
    </button>
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
    <div className="flex flex-wrap gap-1.5 mb-2">
      {parts.map((p, i) => (
        <span key={i} className={`text-[10px] font-mono px-1.5 py-0.5 rounded border ${
          p === "UNDERVAL" ? "border-profit/30 text-profit bg-profit/5" :
          p === "OVERVAL" ? "border-loss/30 text-loss bg-loss/5" :
          p.startsWith("ER ") ? "border-amber-500/30 text-amber-400 bg-amber-500/5" :
          "border-gray-600/30 text-gray-400 bg-gray-700/20"
        }`}>
          {p}
        </span>
      ))}
    </div>
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

  const fetchWatchlist = useCallback(async () => {
    try {
      const data = await api.getWatchlist();
      setWatchlist(data);
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

  const sortedWatchlist = useMemo(() => sortWatchlist(watchlist), [watchlist]);

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
    <div>
      {/* Header */}
      <div className="mb-6">
        <div className="flex items-center gap-3 mb-1">
          <div className="w-8 h-8 rounded-lg bg-amber-500/10 flex items-center justify-center">
            <svg className="w-4 h-4 text-amber-500" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M2.036 12.322a1.012 1.012 0 010-.639C3.423 7.51 7.36 4.5 12 4.5c4.64 0 8.577 3.007 9.963 7.178.07.207.07.431 0 .639C20.577 16.49 16.64 19.5 12 19.5c-4.64 0-8.577-3.007-9.963-7.178z" />
              <path strokeLinecap="round" strokeLinejoin="round" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
            </svg>
          </div>
          <div>
            <h1 className="text-xl font-bold text-white" style={{ fontFamily: "'Outfit', sans-serif" }}>
              Watchlist
            </h1>
            <p className="text-xs text-gray-500">
              {watchlist.length} ticker{watchlist.length !== 1 ? "s" : ""} monitored
              {" "}&middot; click any card for full analysis
            </p>
          </div>
        </div>
      </div>

      {/* Add Tickers Bar */}
      <div className="flex items-center gap-2 mb-6">
        <Input
          type="text"
          value={addInput}
          onChange={(e) => setAddInput(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="Add tickers (e.g. NVDA, AAPL, TSLA)"
          className="flex-1 h-9 bg-surface-light/30 border-border/50 text-sm font-mono"
        />
        <Button
          onClick={handleAdd}
          disabled={adding || !addInput.trim()}
          size="sm"
          className="bg-amber-500/15 text-amber-400 border border-amber-500/30 hover:bg-amber-500/25 h-9 px-4"
        >
          {adding ? (
            <span className="w-1.5 h-1.5 rounded-full bg-amber-400 animate-pulse" />
          ) : (
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M12 4.5v15m7.5-7.5h-15" />
            </svg>
          )}
          Add
        </Button>
      </div>

      {/* Sparkline legend */}
      <div className="flex items-center gap-4 mb-4 text-[10px] text-gray-600">
        <div className="flex items-center gap-1">
          <span className="w-2 h-2 rounded-full bg-profit" /> Bullish signal
        </div>
        <div className="flex items-center gap-1">
          <span className="w-2 h-2 rounded-full bg-loss" /> Bearish signal
        </div>
        <div className="flex items-center gap-1">
          <svg width="8" height="8" viewBox="0 0 8 8"><polygon points="4,0 7,4 4,8 1,4" fill="#6366f1" /></svg> Strategy trade
        </div>
      </div>

      {/* Loading State */}
      {loading && (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-3">
          {[1, 2, 3, 4, 5, 6].map((i) => (
            <Skeleton key={i} className="h-52 rounded-xl" />
          ))}
        </div>
      )}

      {/* Empty State */}
      {!loading && watchlist.length === 0 && (
        <div className="flex flex-col items-center justify-center py-20 text-center">
          <div className="w-16 h-16 rounded-full bg-amber-500/10 flex items-center justify-center mb-4">
            <svg className="w-8 h-8 text-amber-500/40" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M2.036 12.322a1.012 1.012 0 010-.639C3.423 7.51 7.36 4.5 12 4.5c4.64 0 8.577 3.007 9.963 7.178.07.207.07.431 0 .639C20.577 16.49 16.64 19.5 12 19.5c-4.64 0-8.577-3.007-9.963-7.178z" />
              <path strokeLinecap="round" strokeLinejoin="round" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
            </svg>
          </div>
          <h2 className="text-lg font-semibold text-white mb-2" style={{ fontFamily: "'Outfit', sans-serif" }}>
            No tickers on your watchlist
          </h2>
          <p className="text-sm text-gray-500 max-w-md">
            Add tickers above to start monitoring. Tickers are auto-added when you receive trade signals or add holdings.
          </p>
        </div>
      )}

      {/* Ticker Card Grid */}
      {!loading && watchlist.length > 0 && (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-3">
          {sortedWatchlist.map((item) => (
            <TickerCard
              key={item.id}
              item={item}
              chartData={null}
              onClick={() => router.push(`/screener/${item.ticker}`)}
            />
          ))}
        </div>
      )}
    </div>
  );
}
