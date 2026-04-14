"use client";

import { useState, useEffect, useMemo, useCallback } from "react";
import { useRouter } from "next/navigation";
import { Treemap, ResponsiveContainer, Tooltip } from "recharts";
import { api } from "@/lib/api";
import { Search, Plus, Eye, X } from "lucide-react";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import type { WatchlistTickerData } from "@/lib/types";

const FONT_OUTFIT = { fontFamily: "'Outfit', sans-serif" } as const;
const FONT_MONO = { fontFamily: "'JetBrains Mono', monospace" } as const;

type DirFilter = "all" | "positive" | "negative";

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

// ── Quote shape (matches GET /watchlist/quotes) ─────────────────────────
type Quote = {
  price: number | null;
  change_pct: number | null;
  change: number | null;
  volume: number | null;
  day_high: number | null;
  day_low: number | null;
};

// ── Tile data shape fed to Recharts <Treemap> ───────────────────────────
type TileDatum = {
  name: string;      // ticker symbol
  size: number;      // weight passed to Recharts (|change_pct| with floor)
  changePct: number; // real daily %change (signed)
  price: number | null;
  totalSignals: number;
  direction: string;
};

// ── Color mapping ────────────────────────────────────────────────────────
// Map magnitude of change_pct onto an opacity ramp so bigger moves feel
// bolder. Tiles without quote data render gray so users see they're
// unknown, not neutral.
function fillForTile(d: TileDatum): string {
  if (d.changePct == null || d.price == null) return "#374151"; // neutral gray
  const mag = Math.min(10, Math.abs(d.changePct));
  const alpha = 0.22 + (mag / 10) * 0.68; // 0.22–0.90
  if (d.changePct > 0.1) return `rgba(34, 197, 94, ${alpha.toFixed(2)})`;   // profit
  if (d.changePct < -0.1) return `rgba(239, 68, 68, ${alpha.toFixed(2)})`;  // loss
  return "rgba(156, 163, 175, 0.35)"; // near-flat = gray
}

function textColorForTile(d: TileDatum): string {
  if (d.changePct == null || d.price == null) return "#9ca3af";
  if (Math.abs(d.changePct) >= 3) return "#ffffff";
  return "#e5e7eb";
}

// ── Custom tile content ─────────────────────────────────────────────────
// Recharts <Treemap> passes each node's computed {x,y,width,height} plus
// all the fields from the data. Render a bordered rect with the ticker
// and %change, scaling label size to the tile to keep things readable on
// both big and small cells.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
function TreemapTile(props: any) {
  const {
    x, y, width, height, name, changePct, price,
  } = props;

  if (!name || width <= 0 || height <= 0) return null;

  const fill = fillForTile(props as TileDatum);
  const textColor = textColorForTile(props as TileDatum);

  // Don't render labels on tiles too small to fit them
  const area = width * height;
  const showTicker = width >= 34 && height >= 22;
  const showChange = width >= 48 && height >= 40;
  const showPrice = width >= 72 && height >= 62;

  // Scale the ticker font with tile size, capped so small tiles don't crush
  // and huge tiles don't get absurdly big
  const tickerSize = Math.max(11, Math.min(34, Math.sqrt(area) / 5.5));
  const changeSize = Math.max(9, Math.min(18, tickerSize * 0.55));
  const priceSize = Math.max(9, Math.min(13, tickerSize * 0.42));

  const pctLabel =
    changePct == null
      ? "—"
      : `${changePct > 0 ? "+" : ""}${changePct.toFixed(2)}%`;

  return (
    <g>
      <rect
        x={x}
        y={y}
        width={width}
        height={height}
        style={{
          fill,
          stroke: "#0a0a0f",
          strokeWidth: 1,
          cursor: "pointer",
        }}
      />
      {showTicker && (
        <text
          x={x + width / 2}
          y={y + height / 2 - (showChange ? changeSize * 0.55 : 0)}
          textAnchor="middle"
          dominantBaseline="middle"
          style={{
            fill: textColor,
            fontFamily: "'Outfit', sans-serif",
            fontWeight: 700,
            fontSize: tickerSize,
            letterSpacing: "-0.02em",
            pointerEvents: "none",
          }}
        >
          {name}
        </text>
      )}
      {showChange && (
        <text
          x={x + width / 2}
          y={y + height / 2 + tickerSize * 0.6}
          textAnchor="middle"
          dominantBaseline="middle"
          style={{
            fill: textColor,
            fontFamily: "'JetBrains Mono', monospace",
            fontSize: changeSize,
            fontWeight: 500,
            pointerEvents: "none",
          }}
        >
          {pctLabel}
        </text>
      )}
      {showPrice && price != null && (
        <text
          x={x + width / 2}
          y={y + height / 2 + tickerSize * 0.6 + changeSize + 4}
          textAnchor="middle"
          dominantBaseline="middle"
          style={{
            fill: textColor,
            opacity: 0.75,
            fontFamily: "'JetBrains Mono', monospace",
            fontSize: priceSize,
            pointerEvents: "none",
          }}
        >
          ${price.toFixed(2)}
        </text>
      )}
    </g>
  );
}

// ── Custom tooltip ───────────────────────────────────────────────────────
// eslint-disable-next-line @typescript-eslint/no-explicit-any
function TreemapTooltip({ active, payload }: any) {
  if (!active || !payload || !payload.length) return null;
  const d = payload[0]?.payload as TileDatum | undefined;
  if (!d || !d.name) return null;
  return (
    <div
      className="rounded-lg border border-[#374151] bg-[#111827] px-3 py-2 shadow-xl text-xs"
      style={FONT_OUTFIT}
    >
      <div className="flex items-center gap-2 mb-1">
        <span className="text-sm font-bold text-white" style={FONT_OUTFIT}>{d.name}</span>
        {d.changePct != null && (
          <span
            className={
              "text-[11px] font-mono " +
              (d.changePct > 0 ? "text-profit" : d.changePct < 0 ? "text-loss" : "text-gray-400")
            }
            style={FONT_MONO}
          >
            {d.changePct > 0 ? "+" : ""}{d.changePct.toFixed(2)}%
          </span>
        )}
      </div>
      {d.price != null && (
        <div className="text-[10px] text-gray-400 font-mono" style={FONT_MONO}>
          ${d.price.toFixed(2)}
        </div>
      )}
      <div className="text-[10px] text-gray-500 mt-1" style={FONT_OUTFIT}>
        {d.totalSignals} signal{d.totalSignals !== 1 ? "s" : ""} · {d.direction}
      </div>
    </div>
  );
}

// ── Page ─────────────────────────────────────────────────────────────────
export default function WatchlistTreemapPage() {
  useFonts();
  const router = useRouter();

  const [watchlist, setWatchlist] = useState<WatchlistTickerData[]>([]);
  const [quotes, setQuotes] = useState<Record<string, Quote>>({});
  const [loading, setLoading] = useState(true);

  const [search, setSearch] = useState("");
  const [dirFilter, setDirFilter] = useState<DirFilter>("all");

  const [addInput, setAddInput] = useState("");
  const [adding, setAdding] = useState(false);

  const fetchAll = useCallback(async () => {
    try {
      const [wl, q] = await Promise.all([
        api.getWatchlist().catch(() => [] as WatchlistTickerData[]),
        api.getWatchlistQuotes().catch(() => ({} as Record<string, Quote>)),
      ]);
      setWatchlist(wl);
      setQuotes(q || {});
    } catch {
      /* ignore — keep last successful state */
    }
  }, []);

  useEffect(() => {
    fetchAll().finally(() => setLoading(false));
  }, [fetchAll]);

  useEffect(() => {
    const iv = setInterval(fetchAll, 30000);
    return () => clearInterval(iv);
  }, [fetchAll]);

  const handleAdd = async () => {
    const raw = addInput.trim();
    if (!raw || adding) return;
    setAdding(true);
    try {
      const tickers = raw
        .split(",")
        .map((t) => t.trim().toUpperCase())
        .filter(Boolean);
      if (tickers.length > 0) {
        await api.addWatchlistTickers(tickers);
        setAddInput("");
        await fetchAll();
      }
    } catch {
      /* ignore */
    }
    setAdding(false);
  };

  // Build treemap data from watchlist + quotes
  const tiles: TileDatum[] = useMemo(() => {
    const q = search.trim().toUpperCase();
    return watchlist
      .filter((w) => !q || w.ticker.toUpperCase().includes(q))
      .map<TileDatum>((w) => {
        const quote = quotes[w.ticker];
        const change = quote?.change_pct ?? null;
        const price = quote?.price ?? null;
        // Recharts sizes tiles by `size` — use |%change| with a small floor
        // so tickers with no quote (gray) still appear, and flat movers
        // (±0.1%) render as small neutral cells instead of disappearing.
        const baseWeight = change != null ? Math.abs(change) : 0;
        const size = Math.max(0.25, baseWeight);
        return {
          name: w.ticker,
          size,
          changePct: change ?? 0,
          price,
          totalSignals: w.consensus?.total_signals ?? 0,
          direction: w.consensus?.direction ?? "no-data",
        };
      })
      .filter((d) => {
        if (dirFilter === "all") return true;
        if (dirFilter === "positive") return d.changePct > 0;
        return d.changePct < 0;
      });
  }, [watchlist, quotes, search, dirFilter]);

  const counts = useMemo(() => {
    let up = 0, down = 0, flat = 0;
    for (const t of tiles) {
      if (t.changePct > 0.1) up++;
      else if (t.changePct < -0.1) down++;
      else flat++;
    }
    return { up, down, flat };
  }, [tiles]);

  const handleTileClick = useCallback(
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    (node: any) => {
      const name = node?.name || node?.payload?.name;
      if (name) router.push(`/screener/${name}`);
    },
    [router]
  );

  return (
    <div className="flex flex-col h-[calc(100vh-7rem)] min-h-[520px]">
      {/* Header */}
      <div className="flex items-baseline gap-3 mb-3">
        <h1 className="text-xl font-bold text-white tracking-tight" style={FONT_OUTFIT}>
          Watchlist
        </h1>
        <span className="text-[11px] text-gray-500" style={FONT_OUTFIT}>
          Tiles sized by today&apos;s % move. Click to open ticker.
        </span>
        <div className="ml-auto flex items-center gap-3 text-[11px] font-mono" style={FONT_MONO}>
          <span className="text-profit">▲ {counts.up}</span>
          <span className="text-loss">▼ {counts.down}</span>
          <span className="text-gray-500">· {counts.flat} flat</span>
          <span className="text-gray-600">/ {watchlist.length} total</span>
        </div>
      </div>

      {/* Controls */}
      <div className="flex flex-col md:flex-row items-stretch md:items-center gap-2 mb-3">
        <div className="relative flex-1 min-w-0">
          <Search
            className="absolute left-3 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-gray-500 pointer-events-none"
            strokeWidth={2}
          />
          <Input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Filter tickers..."
            className="h-9 pl-8 bg-[#1f2937]/40 border-[#374151] text-sm"
            style={FONT_MONO}
          />
          {search && (
            <button
              onClick={() => setSearch("")}
              className="absolute right-2 top-1/2 -translate-y-1/2 p-1 text-gray-500 hover:text-gray-300"
              aria-label="Clear search"
            >
              <X className="w-3.5 h-3.5" strokeWidth={2} />
            </button>
          )}
        </div>

        {/* Direction filter */}
        <div className="flex rounded-lg overflow-hidden border border-[#374151] shrink-0">
          {(
            [
              { value: "all", label: "All" },
              { value: "positive", label: "Positive" },
              { value: "negative", label: "Negative" },
            ] as const
          ).map((opt) => (
            <button
              key={opt.value}
              onClick={() => setDirFilter(opt.value)}
              className={
                "px-3 py-1.5 text-xs font-medium transition whitespace-nowrap " +
                (dirFilter === opt.value
                  ? opt.value === "positive"
                    ? "bg-profit/20 text-profit"
                    : opt.value === "negative"
                    ? "bg-loss/20 text-loss"
                    : "bg-ai-blue/20 text-ai-blue"
                  : "bg-[#1f2937]/40 text-gray-500 hover:text-gray-300")
              }
              style={FONT_OUTFIT}
            >
              {opt.label}
            </button>
          ))}
        </div>

        {/* Add tickers */}
        <div className="flex items-center gap-1 shrink-0">
          <Input
            type="text"
            value={addInput}
            onChange={(e) => setAddInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                handleAdd();
              }
            }}
            placeholder="Add (NVDA, AAPL…)"
            className="h-9 w-44 bg-[#1f2937]/40 border-[#374151] text-sm font-mono placeholder:text-gray-600"
          />
          <Button
            onClick={handleAdd}
            disabled={adding || !addInput.trim()}
            className="h-9 px-3 bg-ai-blue/15 text-ai-blue border border-ai-blue/30 hover:bg-ai-blue/25"
            aria-label="Add tickers"
          >
            <Plus className="w-4 h-4" strokeWidth={2} />
          </Button>
        </div>
      </div>

      {/* Treemap — fills remaining viewport height */}
      <div className="flex-1 min-h-[320px] rounded-xl overflow-hidden border border-[#1f2937] bg-[#0a0a0f]">
        {loading ? (
          <div className="w-full h-full grid grid-cols-4 md:grid-cols-6 gap-1 p-1">
            {Array.from({ length: 18 }).map((_, i) => (
              <Skeleton key={i} className="w-full h-full rounded-md" />
            ))}
          </div>
        ) : tiles.length === 0 ? (
          <EmptyState watchlistSize={watchlist.length} />
        ) : (
          <ResponsiveContainer width="100%" height="100%">
            <Treemap
              data={tiles}
              dataKey="size"
              aspectRatio={16 / 9}
              stroke="#0a0a0f"
              fill="#111827"
              isAnimationActive={false}
              // eslint-disable-next-line @typescript-eslint/no-explicit-any
              content={<TreemapTile /> as any}
              onClick={handleTileClick}
            >
              <Tooltip content={<TreemapTooltip />} />
            </Treemap>
          </ResponsiveContainer>
        )}
      </div>

      {/* Legend */}
      <div className="flex items-center justify-center gap-4 mt-3 text-[10px] text-gray-500" style={FONT_OUTFIT}>
        <LegendSwatch color="rgba(239, 68, 68, 0.85)" label="Large loss" />
        <LegendSwatch color="rgba(239, 68, 68, 0.35)" label="Small loss" />
        <LegendSwatch color="rgba(156, 163, 175, 0.35)" label="Flat" />
        <LegendSwatch color="rgba(34, 197, 94, 0.35)" label="Small gain" />
        <LegendSwatch color="rgba(34, 197, 94, 0.85)" label="Large gain" />
        <span className="text-gray-600">· Size = |% change|</span>
      </div>
    </div>
  );
}

function LegendSwatch({ color, label }: { color: string; label: string }) {
  return (
    <span className="inline-flex items-center gap-1.5">
      <span
        className="inline-block w-3 h-3 rounded-sm"
        style={{ backgroundColor: color, border: "1px solid #0a0a0f" }}
      />
      {label}
    </span>
  );
}

function EmptyState({ watchlistSize }: { watchlistSize: number }) {
  if (watchlistSize === 0) {
    return (
      <div className="h-full flex flex-col items-center justify-center text-center px-6">
        <div className="w-12 h-12 rounded-full bg-[#1f2937]/70 flex items-center justify-center mb-3">
          <Eye className="w-5 h-5 text-gray-500" strokeWidth={1.75} />
        </div>
        <div className="text-sm font-semibold text-white mb-1" style={FONT_OUTFIT}>
          No tickers on your watchlist
        </div>
        <div className="text-xs text-gray-500 max-w-sm" style={FONT_OUTFIT}>
          Add tickers above to see live moves. Signals and holdings sync here automatically.
        </div>
      </div>
    );
  }
  return (
    <div className="h-full flex flex-col items-center justify-center text-center px-6">
      <div className="text-sm font-semibold text-white mb-1" style={FONT_OUTFIT}>
        No tickers match the current filter
      </div>
      <div className="text-xs text-gray-500" style={FONT_OUTFIT}>
        Try clearing the search or switching direction filter.
      </div>
    </div>
  );
}
