"use client";

import { useState, useEffect, useMemo, useCallback } from "react";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";
import { Search, Plus, ArrowUpDown, Eye, TrendingUp, TrendingDown, Minus } from "lucide-react";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import type { WatchlistTickerData } from "@/lib/types";

const FONT_OUTFIT = { fontFamily: "'Outfit', sans-serif" } as const;
const FONT_MONO = { fontFamily: "'JetBrains Mono', monospace" } as const;

type SizeMetric = "alerts" | "signal" | "mcap";
type DirFilter = "all" | "bullish" | "bearish";

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

// ── Types ────────────────────────────────────────────────────────────────
type Fundamentals = {
  company_name?: string;
  market_cap?: number;
  pe_ratio?: number;
  price?: number;
  change_pct?: number;
  daily_change_pct?: number;
};

type EnrichedItem = WatchlistTickerData & { fundamentals?: Fundamentals };

// ── Helpers ──────────────────────────────────────────────────────────────
function getFundamentals(item: WatchlistTickerData): Fundamentals | undefined {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  return (item as any).fundamentals as Fundamentals | undefined;
}

function consensusGradient(direction: string): string {
  switch (direction) {
    case "bullish":
      return "bg-gradient-to-br from-profit/25 via-profit/10 to-transparent";
    case "bearish":
      return "bg-gradient-to-br from-loss/25 via-loss/10 to-transparent";
    case "mixed":
      return "bg-gradient-to-br from-amber-500/25 via-amber-500/10 to-transparent";
    default:
      return "bg-gradient-to-br from-gray-500/10 via-gray-500/5 to-transparent";
  }
}

function consensusAccent(direction: string): { text: string; ring: string; dot: string } {
  switch (direction) {
    case "bullish":
      return { text: "text-profit", ring: "ring-profit/30", dot: "bg-profit" };
    case "bearish":
      return { text: "text-loss", ring: "ring-loss/30", dot: "bg-loss" };
    case "mixed":
      return { text: "text-screener-amber", ring: "ring-amber-500/30", dot: "bg-amber-500" };
    default:
      return { text: "text-gray-500", ring: "ring-gray-600/30", dot: "bg-gray-600" };
  }
}

function formatMarketCap(n: number | undefined): string {
  if (n == null || !isFinite(n)) return "—";
  if (n >= 1e12) return `$${(n / 1e12).toFixed(1)}T`;
  if (n >= 1e9) return `$${(n / 1e9).toFixed(1)}B`;
  if (n >= 1e6) return `$${(n / 1e6).toFixed(0)}M`;
  return `$${n.toFixed(0)}`;
}

function formatPct(n: number | undefined): string {
  if (n == null || !isFinite(n)) return "—";
  const sign = n >= 0 ? "+" : "";
  return `${sign}${n.toFixed(2)}%`;
}

function formatPrice(n: number | undefined): string {
  if (n == null || !isFinite(n)) return "—";
  return `$${n.toFixed(2)}`;
}

function getWeight(item: EnrichedItem, metric: SizeMetric): number {
  const f = getFundamentals(item);
  switch (metric) {
    case "alerts":
      return Math.max(1, item.consensus.total_signals);
    case "signal":
      return Math.max(
        1,
        item.consensus.bullish_count + item.consensus.bearish_count,
      );
    case "mcap":
      return Math.max(1, f?.market_cap ?? 1);
  }
}

// ── Tile span: bucket weight into 4 size classes ─────────────────────────
function tileSpan(
  weight: number,
  sorted: number[],
): { cols: number; rows: number } {
  if (sorted.length === 0) return { cols: 1, rows: 1 };
  const idx = sorted.findIndex((w) => w <= weight);
  const rank = idx === -1 ? sorted.length - 1 : idx;
  const pct = 1 - rank / Math.max(1, sorted.length - 1); // 1 = largest
  if (pct >= 0.85) return { cols: 3, rows: 2 }; // xlarge
  if (pct >= 0.6) return { cols: 2, rows: 2 }; // large
  if (pct >= 0.3) return { cols: 2, rows: 1 }; // medium
  return { cols: 1, rows: 1 }; // small
}

// ── Sparkline ────────────────────────────────────────────────────────────
function TileSparkline({
  events,
  direction,
}: {
  events: Array<{ date: string; signal: string }>;
  direction: string;
}) {
  if (!events || events.length < 2) return null;
  const W = 120;
  const H = 32;
  const len = Math.min(events.length, 24);
  const recent = events.slice(-len);

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

  const stroke =
    direction === "bullish"
      ? "#22c55e"
      : direction === "bearish"
      ? "#ef4444"
      : direction === "mixed"
      ? "#fbbf24"
      : "#6b7280";

  return (
    <svg
      viewBox={`0 0 ${W} ${H}`}
      className="w-full h-8 opacity-80"
      preserveAspectRatio="none"
    >
      <polyline
        points={pts}
        fill="none"
        stroke={stroke}
        strokeWidth="1.75"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

// ── Tooltip ──────────────────────────────────────────────────────────────
function TileTooltip({ item }: { item: EnrichedItem }) {
  const f = getFundamentals(item);
  const accent = consensusAccent(item.consensus.direction);
  return (
    <div
      role="tooltip"
      className="pointer-events-none absolute left-1/2 -translate-x-1/2 top-full mt-2 z-30 w-56 rounded-lg border border-border/70 bg-[#0b1120]/95 backdrop-blur-sm shadow-xl p-3 opacity-0 group-hover:opacity-100 transition-opacity duration-150"
    >
      <div className="flex items-center justify-between mb-2">
        <span
          className="text-sm font-bold text-white"
          style={FONT_OUTFIT}
        >
          {item.ticker}
        </span>
        <span
          className={`text-[10px] font-semibold uppercase tracking-wider ${accent.text}`}
          style={FONT_OUTFIT}
        >
          {item.consensus.direction}
        </span>
      </div>
      {f?.company_name && (
        <div className="text-[11px] text-gray-400 mb-2 truncate" style={FONT_OUTFIT}>
          {f.company_name}
        </div>
      )}
      <div className="grid grid-cols-2 gap-2 text-[10px]" style={FONT_MONO}>
        <div className="flex flex-col">
          <span className="text-gray-500">Bullish</span>
          <span className="text-profit font-semibold">
            {item.consensus.bullish_count}
          </span>
        </div>
        <div className="flex flex-col">
          <span className="text-gray-500">Bearish</span>
          <span className="text-loss font-semibold">
            {item.consensus.bearish_count}
          </span>
        </div>
        <div className="flex flex-col">
          <span className="text-gray-500">Total Signals</span>
          <span className="text-white font-semibold">
            {item.consensus.total_signals}
          </span>
        </div>
        <div className="flex flex-col">
          <span className="text-gray-500">Mkt Cap</span>
          <span className="text-white font-semibold">
            {formatMarketCap(f?.market_cap)}
          </span>
        </div>
      </div>
    </div>
  );
}

// ── Tile ─────────────────────────────────────────────────────────────────
function Tile({
  item,
  cols,
  rows,
  onClick,
}: {
  item: EnrichedItem;
  cols: number;
  rows: number;
  onClick: () => void;
}) {
  const accent = consensusAccent(item.consensus.direction);
  const gradient = consensusGradient(item.consensus.direction);
  const f = getFundamentals(item);
  const price = f?.price;
  const changePct = f?.daily_change_pct ?? f?.change_pct;
  const isLarge = cols >= 2 && rows >= 2;
  const isXLarge = cols >= 3;

  const DirIcon =
    item.consensus.direction === "bullish"
      ? TrendingUp
      : item.consensus.direction === "bearish"
      ? TrendingDown
      : Minus;

  return (
    <div
      className="relative group"
      style={{
        gridColumn: `span ${cols} / span ${cols}`,
        gridRow: `span ${rows} / span ${rows}`,
      }}
    >
      <button
        onClick={onClick}
        aria-label={`${item.ticker} — ${item.consensus.direction}`}
        className={`relative w-full h-full rounded-xl border border-border/50 bg-[#0f1522] overflow-hidden
          transition-all duration-300 ease-out
          hover:scale-[1.02] hover:ring-1 hover:ring-ai-blue/40 hover:border-ai-blue/30
          focus:outline-none focus:ring-1 focus:ring-ai-blue/60`}
      >
        {/* Gradient overlay */}
        <div className={`absolute inset-0 ${gradient} pointer-events-none`} />

        {/* Content */}
        <div className="relative h-full flex flex-col justify-between p-3">
          {/* Top row: ticker + direction icon */}
          <div className="flex items-start justify-between gap-2">
            <div className="min-w-0 flex-1">
              <div
                className={`font-bold text-white tracking-tight leading-none truncate ${
                  isXLarge ? "text-3xl" : isLarge ? "text-2xl" : cols >= 2 ? "text-xl" : "text-base"
                }`}
                style={FONT_OUTFIT}
              >
                {item.ticker}
              </div>
              {(isLarge || isXLarge) && f?.company_name && (
                <div
                  className="text-[10px] text-gray-400 truncate mt-1"
                  style={FONT_OUTFIT}
                >
                  {f.company_name}
                </div>
              )}
            </div>
            <DirIcon className={`w-3.5 h-3.5 flex-shrink-0 ${accent.text}`} />
          </div>

          {/* Middle: sparkline (only large tiles) */}
          {isLarge && item.signal_events && item.signal_events.length > 1 && (
            <div className="flex-1 flex items-center justify-center px-1 py-2">
              <TileSparkline
                events={item.signal_events}
                direction={item.consensus.direction}
              />
            </div>
          )}

          {/* Bottom: price + change */}
          <div className="flex items-end justify-between gap-2">
            <div className="min-w-0">
              <div
                className={`font-semibold text-white truncate ${
                  isLarge ? "text-sm" : "text-xs"
                }`}
                style={FONT_MONO}
              >
                {formatPrice(price)}
              </div>
              <div
                className={`text-[10px] font-medium truncate ${
                  changePct == null
                    ? "text-gray-500"
                    : changePct >= 0
                    ? "text-profit"
                    : "text-loss"
                }`}
                style={FONT_MONO}
              >
                {formatPct(changePct)}
              </div>
            </div>
            <div className="flex items-center gap-1 flex-shrink-0">
              <span className={`w-1.5 h-1.5 rounded-full ${accent.dot}`} />
              <span
                className={`text-[10px] font-semibold ${accent.text}`}
                style={FONT_MONO}
              >
                {item.consensus.total_signals}
              </span>
            </div>
          </div>
        </div>
      </button>

      <TileTooltip item={item} />
    </div>
  );
}

// ── Main Page ───────────────────────────────────────────────────────────
export default function WatchlistTreemapPage() {
  useFonts();
  const router = useRouter();

  const [watchlist, setWatchlist] = useState<EnrichedItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [addInput, setAddInput] = useState("");
  const [adding, setAdding] = useState(false);
  const [search, setSearch] = useState("");
  const [sizeMetric, setSizeMetric] = useState<SizeMetric>("alerts");
  const [dirFilter, setDirFilter] = useState<DirFilter>("all");

  const fetchWatchlist = useCallback(async () => {
    try {
      const [data, fundData] = await Promise.all([
        api.getWatchlist(),
        api
          .getWatchlistFundamentals()
          .catch(() => ({} as Record<string, Fundamentals>)),
      ]);
      const enriched = data.map((item) => {
        const f = (fundData as Record<string, Fundamentals>)[item.ticker];
        if (f) {
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          (item as any).fundamentals = f;
        }
        return item as EnrichedItem;
      });
      setWatchlist(enriched);
    } catch {}
  }, []);

  useEffect(() => {
    fetchWatchlist().finally(() => setLoading(false));
  }, [fetchWatchlist]);

  useEffect(() => {
    const interval = setInterval(fetchWatchlist, 30000);
    return () => clearInterval(interval);
  }, [fetchWatchlist]);

  // Filter + sort
  const visible = useMemo(() => {
    const needle = search.trim().toUpperCase();
    const filtered = watchlist.filter((it) => {
      if (needle && !it.ticker.toUpperCase().includes(needle)) return false;
      if (dirFilter === "bullish" && it.consensus.direction !== "bullish") return false;
      if (dirFilter === "bearish" && it.consensus.direction !== "bearish") return false;
      return true;
    });
    return [...filtered].sort(
      (a, b) => getWeight(b, sizeMetric) - getWeight(a, sizeMetric),
    );
  }, [watchlist, search, dirFilter, sizeMetric]);

  const sortedWeights = useMemo(
    () =>
      [...visible]
        .map((it) => getWeight(it, sizeMetric))
        .sort((a, b) => b - a),
    [visible, sizeMetric],
  );

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

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter") {
      e.preventDefault();
      handleAdd();
    }
  };

  const sizeOptions: Array<{ value: SizeMetric; label: string }> = [
    { value: "alerts", label: "Alert count" },
    { value: "signal", label: "Signal strength" },
    { value: "mcap", label: "Market cap" },
  ];

  const dirOptions: Array<{ value: DirFilter; label: string }> = [
    { value: "all", label: "All" },
    { value: "bullish", label: "Bullish" },
    { value: "bearish", label: "Bearish" },
  ];

  return (
    <div>
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
            <span
              className="text-xs bg-[#1f2937]/60 text-gray-300 border border-border/50 px-2 py-0.5 rounded-md"
              style={FONT_MONO}
            >
              {watchlist.length}
            </span>
          )}
        </div>
        <p className="text-sm text-gray-500" style={FONT_OUTFIT}>
          Tile size reflects{" "}
          <span className="text-gray-300">
            {sizeOptions.find((s) => s.value === sizeMetric)?.label.toLowerCase()}
          </span>
          . Color reflects consensus direction. Click any tile for full analysis.
        </p>
      </div>

      {/* Controls row 1: add + search */}
      <div className="flex flex-col lg:flex-row items-stretch lg:items-center gap-3 mb-3">
        <div className="flex items-center gap-2 flex-1">
          <div className="relative flex-1">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-500 pointer-events-none" />
            <Input
              type="text"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Filter by ticker..."
              className="h-10 pl-9 bg-[#1f2937]/40 border-border/50 text-sm font-mono placeholder:text-gray-600"
            />
          </div>
          <div className="flex items-center gap-2 flex-1 max-w-md">
            <Input
              type="text"
              value={addInput}
              onChange={(e) => setAddInput(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder="Add tickers (e.g. NVDA, AAPL)"
              className="flex-1 h-10 bg-[#1f2937]/40 border-border/50 text-sm font-mono placeholder:text-gray-600"
            />
            <Button
              onClick={handleAdd}
              disabled={adding || !addInput.trim()}
              className="bg-ai-blue/15 text-ai-blue border border-ai-blue/30 hover:bg-ai-blue/25 h-10 px-4 font-semibold"
            >
              {adding ? (
                <span className="w-1.5 h-1.5 rounded-full bg-ai-blue animate-pulse" />
              ) : (
                <Plus className="w-4 h-4 mr-1" />
              )}
              Add
            </Button>
          </div>
        </div>
      </div>

      {/* Controls row 2: size metric + direction filter */}
      <div className="flex flex-col sm:flex-row items-stretch sm:items-center gap-3 mb-6">
        <div className="flex items-center gap-2">
          <ArrowUpDown className="w-3.5 h-3.5 text-gray-500" />
          <span
            className="text-[10px] text-gray-500 uppercase tracking-wider whitespace-nowrap"
            style={FONT_OUTFIT}
          >
            Size by
          </span>
          <div className="flex rounded-lg overflow-hidden border border-border/50">
            {sizeOptions.map((opt) => (
              <button
                key={opt.value}
                onClick={() => setSizeMetric(opt.value)}
                className={`px-3 py-1.5 text-xs font-medium transition ${
                  sizeMetric === opt.value
                    ? "bg-ai-blue/20 text-ai-blue"
                    : "bg-[#1f2937]/40 text-gray-500 hover:text-gray-300"
                }`}
                style={FONT_OUTFIT}
              >
                {opt.label}
              </button>
            ))}
          </div>
        </div>

        <div className="flex items-center gap-2">
          <span
            className="text-[10px] text-gray-500 uppercase tracking-wider whitespace-nowrap"
            style={FONT_OUTFIT}
          >
            Filter
          </span>
          <div className="flex rounded-lg overflow-hidden border border-border/50">
            {dirOptions.map((opt) => (
              <button
                key={opt.value}
                onClick={() => setDirFilter(opt.value)}
                className={`px-3 py-1.5 text-xs font-medium transition ${
                  dirFilter === opt.value
                    ? opt.value === "bullish"
                      ? "bg-profit/20 text-profit"
                      : opt.value === "bearish"
                      ? "bg-loss/20 text-loss"
                      : "bg-ai-blue/20 text-ai-blue"
                    : "bg-[#1f2937]/40 text-gray-500 hover:text-gray-300"
                }`}
                style={FONT_OUTFIT}
              >
                {opt.label}
              </button>
            ))}
          </div>
        </div>
      </div>

      {/* Loading State */}
      {loading && (
        <div className="grid grid-cols-3 md:grid-cols-6 auto-rows-[84px] md:auto-rows-[110px] gap-2">
          <Skeleton className="rounded-xl col-span-3 row-span-2" />
          <Skeleton className="rounded-xl col-span-2 row-span-2" />
          <Skeleton className="rounded-xl col-span-1 row-span-1" />
          <Skeleton className="rounded-xl col-span-1 row-span-1" />
          <Skeleton className="rounded-xl col-span-2 row-span-1" />
          <Skeleton className="rounded-xl col-span-2 row-span-1" />
        </div>
      )}

      {/* Empty State */}
      {!loading && watchlist.length === 0 && (
        <div className="flex flex-col items-center justify-center py-24 text-center">
          <div className="w-16 h-16 rounded-full bg-[#1f2937]/60 flex items-center justify-center mb-5">
            <Eye className="w-8 h-8 text-gray-600" />
          </div>
          <h2
            className="text-xl font-bold text-white mb-2"
            style={FONT_OUTFIT}
          >
            No tickers on your watchlist
          </h2>
          <p
            className="text-sm text-gray-500 max-w-md leading-relaxed"
            style={FONT_OUTFIT}
          >
            Add tickers using the input above to start monitoring. The treemap
            will size each ticker by signal activity and color by consensus
            direction.
          </p>
        </div>
      )}

      {/* Empty filter state */}
      {!loading && watchlist.length > 0 && visible.length === 0 && (
        <div className="flex flex-col items-center justify-center py-16 text-center rounded-xl border border-border/50 bg-[#0f1522]/40">
          <Search className="w-6 h-6 text-gray-600 mb-3" />
          <p className="text-sm text-gray-400" style={FONT_OUTFIT}>
            No tickers match the current filter.
          </p>
        </div>
      )}

      {/* Treemap */}
      {!loading && visible.length > 0 && (
        <div className="grid grid-cols-3 md:grid-cols-6 auto-rows-[84px] md:auto-rows-[110px] gap-2 transition-all duration-300">
          {visible.map((item) => {
            const weight = getWeight(item, sizeMetric);
            const { cols, rows } = tileSpan(weight, sortedWeights);
            return (
              <Tile
                key={item.id}
                item={item}
                cols={cols}
                rows={rows}
                onClick={() => router.push(`/screener/${item.ticker}`)}
              />
            );
          })}
        </div>
      )}
    </div>
  );
}
