"use client";

import { useState, useEffect, useCallback, useMemo } from "react";
import { api } from "@/lib/api";
import { formatTimeAgo } from "@/lib/formatters";
import { renderMarkdown } from "@/lib/markdown";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import type {
  WatchlistTickerData,
  WatchlistTickerDetail,
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
    // 1. Tickers with new signals first (by recency)
    const aTime = a.last_alert_at ? new Date(a.last_alert_at).getTime() : 0;
    const bTime = b.last_alert_at ? new Date(b.last_alert_at).getTime() : 0;
    const now = Date.now();
    const aRecent = now - aTime < 3600000; // within 1 hour
    const bRecent = now - bTime < 3600000;
    if (aRecent && !bRecent) return -1;
    if (!aRecent && bRecent) return 1;

    // 2. By consensus strength (total signals)
    if (b.consensus.total_signals !== a.consensus.total_signals) {
      return b.consensus.total_signals - a.consensus.total_signals;
    }

    // 3. By active strategy positions
    if (b.strategy_positions.length !== a.strategy_positions.length) {
      return b.strategy_positions.length - a.strategy_positions.length;
    }

    // 4. Alphabetically
    return a.ticker.localeCompare(b.ticker);
  });
}

// ── Ticker Card ─────────────────────────────────────────────────────────
function TickerCard({
  item,
  onClick,
}: {
  item: WatchlistTickerData;
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
      <div className="flex items-center justify-between mb-3">
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

      {/* Signal counts + last update */}
      <div className="flex items-center gap-3 mb-3 text-xs text-gray-500 font-mono">
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
        <div className="flex flex-wrap gap-1.5 mb-3">
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

      {/* Recent indicator signals (up to 3) */}
      {item.latest_signals.length > 0 && (
        <div className="space-y-1">
          {item.latest_signals.slice(0, 3).map((s, i) => (
            <div key={i} className="flex items-center gap-2 text-xs">
              <span className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${signalDot(s.signal)}`} />
              <span className="text-gray-400 font-mono truncate">{s.indicator}</span>
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

// ── Detail Panel ────────────────────────────────────────────────────────
function DetailPanel({
  ticker,
  onClose,
  onRemove,
}: {
  ticker: string;
  onClose: () => void;
  onRemove: (t: string) => void;
}) {
  const [detail, setDetail] = useState<WatchlistTickerDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);

  useEffect(() => {
    setLoading(true);
    api.getWatchlistDetail(ticker)
      .then(setDetail)
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [ticker]);

  const handleRefreshSummary = async () => {
    setRefreshing(true);
    try {
      await api.refreshWatchlistSummary(ticker);
      // Poll for updated data after a delay
      setTimeout(async () => {
        try {
          const updated = await api.getWatchlistDetail(ticker);
          setDetail(updated);
        } catch {}
        setRefreshing(false);
      }, 3000);
    } catch {
      setRefreshing(false);
    }
  };

  const badge = detail ? consensusBadge(detail.consensus.direction) : consensusBadge("no_data");

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center pt-8 pb-8 px-4 overflow-y-auto">
      {/* Backdrop */}
      <div className="fixed inset-0 bg-black/60 backdrop-blur-sm" onClick={onClose} />

      {/* Panel */}
      <div className="relative w-full max-w-2xl bg-surface border border-border rounded-2xl shadow-2xl animate-fade-in">
        {/* Header */}
        <div className="flex items-center justify-between p-5 border-b border-border">
          <div className="flex items-center gap-3">
            <span
              className="text-2xl font-bold text-white"
              style={{ fontFamily: "'Outfit', sans-serif" }}
            >
              {ticker}
            </span>
            {detail && (
              <span className={`text-xs font-semibold px-2.5 py-1 rounded-full border ${badge.bg} ${badge.text}`}>
                {badge.label}
              </span>
            )}
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={() => { onRemove(ticker); onClose(); }}
              className="text-xs text-loss/70 hover:text-loss px-2 py-1 rounded transition"
            >
              Remove
            </button>
            <button
              onClick={onClose}
              className="text-gray-500 hover:text-white p-1 rounded transition"
            >
              <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
              </svg>
            </button>
          </div>
        </div>

        {loading ? (
          <div className="p-6 space-y-4">
            {[1, 2, 3, 4].map((i) => (
              <Skeleton key={i} className="h-16 rounded-lg" />
            ))}
          </div>
        ) : detail ? (
          <div className="p-5 space-y-5">
            {/* Henry's AI Summary */}
            <section>
              <div className="flex items-center justify-between mb-2">
                <h4 className="text-sm font-semibold text-white flex items-center gap-2">
                  <svg className="w-4 h-4 text-ai-blue" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M9.75 17L9 20l-1 1h8l-1-1-.75-3M3 13h18M5 17h14a2 2 0 002-2V5a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z" />
                  </svg>
                  Henry&apos;s Analysis
                </h4>
                <button
                  onClick={handleRefreshSummary}
                  disabled={refreshing}
                  className="text-xs text-ai-blue/70 hover:text-ai-blue flex items-center gap-1 transition disabled:opacity-50"
                >
                  <svg className={`w-3 h-3 ${refreshing ? "animate-spin" : ""}`} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
                  </svg>
                  {refreshing ? "Generating..." : "Refresh"}
                </button>
              </div>
              {detail.cached_summary ? (
                <div className={`rounded-lg border p-3 ${
                  detail.cached_summary.is_stale
                    ? "border-yellow-500/20 bg-yellow-500/5"
                    : "border-ai-blue/20 bg-ai-blue/5"
                }`}>
                  {detail.cached_summary.is_stale && (
                    <div className="flex items-center gap-1.5 mb-2 text-[10px] text-yellow-400">
                      <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                        <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v2m0 4h.01" />
                      </svg>
                      Stale — new data available since {formatTimeAgo(detail.cached_summary.generated_at)}
                    </div>
                  )}
                  <div
                    className="text-sm text-gray-300 ai-prose"
                    dangerouslySetInnerHTML={{ __html: renderMarkdown(detail.cached_summary.summary) }}
                  />
                  <div className="text-[10px] text-gray-600 mt-2 font-mono">
                    Generated {formatTimeAgo(detail.cached_summary.generated_at)}
                  </div>
                </div>
              ) : (
                <div className="rounded-lg border border-border/30 bg-surface-light/10 p-4 text-center">
                  <p className="text-xs text-gray-500 mb-2">No analysis generated yet</p>
                  <button
                    onClick={handleRefreshSummary}
                    disabled={refreshing}
                    className="text-xs text-ai-blue hover:underline disabled:opacity-50"
                  >
                    {refreshing ? "Generating..." : "Generate now"}
                  </button>
                </div>
              )}
            </section>

            {/* Indicator Signals */}
            <section>
              <h4 className="text-sm font-semibold text-white mb-2">Indicator Signals</h4>
              {detail.all_signals.length > 0 ? (
                <div className="space-y-1 max-h-48 overflow-y-auto">
                  {detail.all_signals.map((s) => (
                    <div key={s.id} className="flex items-center gap-2 text-xs py-1.5 px-2 rounded bg-surface-light/20">
                      <span className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${signalDot(s.signal)}`} />
                      <span className="text-gray-300 font-mono flex-1">{s.indicator}</span>
                      <span className="text-gray-500 font-mono">{s.value.toFixed(2)}</span>
                      <span className={`font-mono ${
                        s.signal === "bullish" ? "text-profit" : s.signal === "bearish" ? "text-loss" : "text-gray-500"
                      }`}>
                        {s.signal}
                      </span>
                      <span className="text-gray-600">{s.timeframe || ""}</span>
                      <span className="text-gray-600">{formatTimeAgo(s.created_at)}</span>
                    </div>
                  ))}
                </div>
              ) : (
                <p className="text-xs text-gray-600">No indicator signals recorded</p>
              )}
            </section>

            {/* Strategy Positions */}
            <section>
              <h4 className="text-sm font-semibold text-white mb-2">Strategy Positions</h4>
              {detail.strategy_positions.length > 0 ? (
                <div className="space-y-1.5">
                  {detail.strategy_positions.map((p) => (
                    <div key={p.strategy_id} className="flex items-center gap-3 text-xs py-2 px-3 rounded-lg bg-surface-light/20 border border-border/30">
                      <span className="text-gray-300 font-medium">{p.strategy_name}</span>
                      <span className={`font-mono font-bold ${
                        p.direction === "long" ? "text-profit" : "text-loss"
                      }`}>
                        {p.direction.toUpperCase()}
                      </span>
                      <span className="text-gray-500 font-mono ml-auto">
                        @ ${p.entry_price.toFixed(2)}
                      </span>
                      {p.current_price && (
                        <span className="text-gray-500 font-mono">
                          now ${p.current_price.toFixed(2)}
                        </span>
                      )}
                      {p.pnl_pct != null && (
                        <span className={`font-mono font-bold ${
                          p.pnl_pct >= 0 ? "text-profit" : "text-loss"
                        }`}>
                          {p.pnl_pct >= 0 ? "+" : ""}{p.pnl_pct.toFixed(2)}%
                        </span>
                      )}
                    </div>
                  ))}
                </div>
              ) : (
                <p className="text-xs text-gray-600">No active strategy positions</p>
              )}
            </section>

            {/* Trade History */}
            {detail.trade_history.length > 0 && (
              <section>
                <h4 className="text-sm font-semibold text-white mb-2">Recent Trade History</h4>
                <div className="space-y-1 max-h-40 overflow-y-auto">
                  {detail.trade_history.map((t, i) => (
                    <div key={i} className="flex items-center gap-2 text-xs py-1.5 px-2 rounded bg-surface-light/20">
                      <span className="text-gray-400">{t.strategy_name}</span>
                      <span className={`font-mono ${t.direction === "long" ? "text-profit" : "text-loss"}`}>
                        {t.direction.toUpperCase()}
                      </span>
                      <span className="text-gray-500 font-mono">
                        ${t.entry_price.toFixed(2)} → ${t.exit_price?.toFixed(2) ?? "?"}
                      </span>
                      <span className={`font-mono ml-auto font-bold ${
                        t.pnl_pct >= 0 ? "text-profit" : "text-loss"
                      }`}>
                        {t.pnl_pct >= 0 ? "+" : ""}{t.pnl_pct.toFixed(2)}%
                      </span>
                      {t.exit_time && (
                        <span className="text-gray-600">{formatTimeAgo(t.exit_time)}</span>
                      )}
                    </div>
                  ))}
                </div>
              </section>
            )}
          </div>
        ) : (
          <div className="p-6 text-center text-gray-500 text-sm">Failed to load details</div>
        )}
      </div>
    </div>
  );
}

// ── Main Page ───────────────────────────────────────────────────────────
export default function WatchlistPage() {
  useFonts();

  const [watchlist, setWatchlist] = useState<WatchlistTickerData[]>([]);
  const [loading, setLoading] = useState(true);
  const [addInput, setAddInput] = useState("");
  const [adding, setAdding] = useState(false);
  const [selectedTicker, setSelectedTicker] = useState<string | null>(null);

  const fetchWatchlist = useCallback(async () => {
    try {
      const data = await api.getWatchlist();
      setWatchlist(data);
    } catch {}
  }, []);

  // Initial load
  useEffect(() => {
    fetchWatchlist().finally(() => setLoading(false));
  }, [fetchWatchlist]);

  // Auto-refresh every 30 seconds
  useEffect(() => {
    const interval = setInterval(fetchWatchlist, 30000);
    return () => clearInterval(interval);
  }, [fetchWatchlist]);

  const sortedWatchlist = useMemo(() => sortWatchlist(watchlist), [watchlist]);

  const handleAdd = async () => {
    if (!addInput.trim() || adding) return;
    setAdding(true);
    try {
      const tickers = addInput
        .split(",")
        .map((t) => t.trim().toUpperCase())
        .filter(Boolean);
      if (tickers.length > 0) {
        await api.addWatchlistTickers(tickers);
        setAddInput("");
        await fetchWatchlist();
      }
    } catch {}
    setAdding(false);
  };

  const handleRemove = async (ticker: string) => {
    try {
      await api.removeWatchlistTicker(ticker);
      setWatchlist((prev) => prev.filter((w) => w.ticker !== ticker));
    } catch {}
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter") {
      e.preventDefault();
      handleAdd();
    }
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
            <h1
              className="text-xl font-bold text-white"
              style={{ fontFamily: "'Outfit', sans-serif" }}
            >
              Watchlist
            </h1>
            <p className="text-xs text-gray-500">
              {watchlist.length} ticker{watchlist.length !== 1 ? "s" : ""} monitored
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

      {/* Loading State */}
      {loading && (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-3">
          {[1, 2, 3, 4, 5, 6].map((i) => (
            <Skeleton key={i} className="h-40 rounded-xl" />
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
            Add tickers above to start monitoring them. Henry will track indicator signals,
            strategy positions, and generate AI analysis for each ticker.
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
              onClick={() => setSelectedTicker(item.ticker)}
            />
          ))}
        </div>
      )}

      {/* Detail Panel */}
      {selectedTicker && (
        <DetailPanel
          ticker={selectedTicker}
          onClose={() => setSelectedTicker(null)}
          onRemove={handleRemove}
        />
      )}
    </div>
  );
}
