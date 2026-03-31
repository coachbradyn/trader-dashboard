"use client";

import { useState, useEffect, useCallback } from "react";
import { useParams, useRouter } from "next/navigation";
import { api } from "@/lib/api";
import { formatTimeAgo, formatCurrency, formatPercent, pnlColor, formatIndicator } from "@/lib/formatters";
import { renderMarkdown } from "@/lib/markdown";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Badge } from "@/components/ui/badge";
import {
  ResponsiveContainer, AreaChart, Area, Line,
  XAxis, YAxis, Tooltip, CartesianGrid, ReferenceLine,
} from "recharts";
import type {
  WatchlistTickerDetail, ChartDataPoint, BacktestImportData,
  BacktestTradeData, MonteCarloResponse, MonteCarloRequest,
  TickerNewsResponse,
} from "@/lib/types";

const FONT_OUTFIT = { fontFamily: "'Outfit', sans-serif" } as const;
const FONT_MONO = { fontFamily: "'JetBrains Mono', monospace" } as const;

function useFonts() {
  useEffect(() => {
    if (document.getElementById("__ticker-fonts")) return;
    const link = document.createElement("link");
    link.id = "__ticker-fonts";
    link.rel = "stylesheet";
    link.href = "https://fonts.googleapis.com/css2?family=Outfit:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500;600;700&display=swap";
    document.head.appendChild(link);
  }, []);
}

function signalDot(signal: string) {
  const s = signal.toLowerCase();
  if (s === "bullish") return "bg-profit";
  if (s === "bearish") return "bg-loss";
  return "bg-gray-500";
}

function consensusLabel(dir: string) {
  if (dir === "bullish") return { label: "Bullish", color: "text-profit" };
  if (dir === "bearish") return { label: "Bearish", color: "text-loss" };
  if (dir === "mixed") return { label: "Mixed", color: "text-yellow-400" };
  return { label: "No Data", color: "text-gray-500" };
}

const CHART_TOOLTIP = { background: "#1f2937", border: "1px solid #374151", borderRadius: 8 };

// ── Candlestick Chart (SVG) ────────────────────────────────────────
function CandlestickChart({ data }: { data: Array<{ date: string; open: number; high: number; low: number; close: number; volume: number; bullish: boolean }> }) {
  const [hovered, setHovered] = useState<number | null>(null);
  const [dims, setDims] = useState({ w: 600, h: 250 });
  const containerRef = useCallback((node: HTMLDivElement | null) => {
    if (node) {
      const ro = new ResizeObserver(([entry]) => {
        setDims({ w: entry.contentRect.width, h: 250 });
      });
      ro.observe(node);
      setDims({ w: node.offsetWidth, h: 250 });
    }
  }, []);

  if (data.length < 2) return null;

  const margin = { top: 10, right: 50, bottom: 22, left: 0 };
  const innerW = dims.w - margin.left - margin.right;
  const innerH = dims.h - margin.top - margin.bottom;

  const allLows = data.map((d) => d.low);
  const allHighs = data.map((d) => d.high);
  const minPrice = Math.min(...allLows) * 0.998;
  const maxPrice = Math.max(...allHighs) * 1.002;
  const priceRange = maxPrice - minPrice || 1;

  const candleW = Math.max(2, Math.min(8, (innerW / data.length) * 0.7));
  const gap = innerW / data.length;

  const yScale = (v: number) => margin.top + innerH - ((v - minPrice) / priceRange) * innerH;

  // Y-axis ticks
  const tickCount = 5;
  const yTicks = Array.from({ length: tickCount }, (_, i) => minPrice + (priceRange * i) / (tickCount - 1));

  // X-axis labels (show every ~15th label)
  const labelInterval = Math.max(1, Math.floor(data.length / 6));

  const hoveredCandle = hovered != null ? data[hovered] : null;

  return (
    <div ref={containerRef} className="relative" style={{ height: dims.h }}>
      <svg width={dims.w} height={dims.h} className="select-none">
        {/* Grid lines */}
        {yTicks.map((tick, i) => (
          <g key={i}>
            <line x1={margin.left} x2={dims.w - margin.right} y1={yScale(tick)} y2={yScale(tick)} stroke="#1f2937" strokeDasharray="3 3" />
            <text x={dims.w - margin.right + 4} y={yScale(tick) + 3} fill="#6b7280" fontSize={9} style={{ fontFamily: "'JetBrains Mono', monospace" }}>
              ${tick.toFixed(tick >= 100 ? 0 : 2)}
            </text>
          </g>
        ))}

        {/* Candles */}
        {data.map((d, i) => {
          const x = margin.left + i * gap + gap / 2;
          const bodyTop = yScale(Math.max(d.open, d.close));
          const bodyBottom = yScale(Math.min(d.open, d.close));
          const bodyH = Math.max(1, bodyBottom - bodyTop);
          const wickTop = yScale(d.high);
          const wickBottom = yScale(d.low);
          const color = d.bullish ? "#22c55e" : "#ef4444";
          const isHovered = hovered === i;

          return (
            <g key={i}
              onMouseEnter={() => setHovered(i)}
              onMouseLeave={() => setHovered(null)}
              style={{ cursor: "crosshair" }}
            >
              {/* Hover highlight */}
              {isHovered && (
                <rect x={x - gap / 2} y={margin.top} width={gap} height={innerH} fill="white" fillOpacity={0.03} />
              )}
              {/* Wick */}
              <line x1={x} x2={x} y1={wickTop} y2={wickBottom} stroke={color} strokeWidth={1} />
              {/* Body */}
              <rect x={x - candleW / 2} y={bodyTop} width={candleW} height={bodyH}
                fill={d.bullish ? color : color} stroke={color} strokeWidth={0.5}
                fillOpacity={d.bullish ? 0.9 : 0.9}
              />
            </g>
          );
        })}

        {/* X-axis labels */}
        {data.map((d, i) => (
          i % labelInterval === 0 ? (
            <text key={i} x={margin.left + i * gap + gap / 2} y={dims.h - 4} textAnchor="middle"
              fill="#6b7280" fontSize={9} style={{ fontFamily: "'JetBrains Mono', monospace" }}>
              {d.date.slice(5)}
            </text>
          ) : null
        ))}
      </svg>

      {/* Tooltip */}
      {hoveredCandle && hovered != null && (
        <div
          className="absolute pointer-events-none bg-surface-light/95 border border-border rounded-lg px-3 py-2 text-[10px] font-mono shadow-xl z-10"
          style={{
            left: Math.min(dims.w - 160, Math.max(8, margin.left + hovered * gap + gap / 2 - 70)),
            top: 8,
          }}
        >
          <div className="text-gray-400 mb-1">{hoveredCandle.date}</div>
          <div className="grid grid-cols-2 gap-x-4 gap-y-0.5">
            <span className="text-gray-500">O</span><span className="text-white">${hoveredCandle.open.toFixed(2)}</span>
            <span className="text-gray-500">H</span><span className="text-white">${hoveredCandle.high.toFixed(2)}</span>
            <span className="text-gray-500">L</span><span className="text-white">${hoveredCandle.low.toFixed(2)}</span>
            <span className="text-gray-500">C</span><span className={hoveredCandle.bullish ? "text-profit" : "text-loss"}>${hoveredCandle.close.toFixed(2)}</span>
          </div>
          <div className="text-gray-500 mt-1">Vol: {(hoveredCandle.volume / 1e6).toFixed(1)}M</div>
        </div>
      )}
    </div>
  );
}

function CompanyDescription({ text }: { text: string }) {
  const [expanded, setExpanded] = useState(false);
  const truncLen = 200;
  const needsTruncation = text.length > truncLen;
  const displayed = expanded ? text : text.slice(0, truncLen) + (needsTruncation ? "..." : "");

  return (
    <div className="mt-2">
      <p className="text-xs text-gray-400 leading-relaxed" style={FONT_OUTFIT}>
        {displayed}
      </p>
      {needsTruncation && (
        <button
          onClick={() => setExpanded(!expanded)}
          className="text-[10px] text-ai-blue/70 hover:text-ai-blue mt-1 transition"
          style={FONT_OUTFIT}
        >
          {expanded ? "Show less" : "Read more"}
        </button>
      )}
    </div>
  );
}

export default function TickerDetailPage() {
  useFonts();
  const params = useParams();
  const router = useRouter();
  const ticker = (params.ticker as string || "").toUpperCase();

  const [detail, setDetail] = useState<WatchlistTickerDetail | null>(null);
  const [chartData, setChartData] = useState<ChartDataPoint[]>([]);
  const [backtests, setBacktests] = useState<BacktestImportData[]>([]);
  const [btTrades, setBtTrades] = useState<Record<string, BacktestTradeData[]>>({});
  const [expandedBt, setExpandedBt] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [newsData, setNewsData] = useState<TickerNewsResponse | null>(null);

  // Thesis state
  const [thesisData, setThesisData] = useState<{ bull_case: string; bear_case: string; key_catalysts: string[]; risk_factors: string[]; sentiment_summary: string } | null>(null);
  const [thesisLoading, setThesisLoading] = useState(false);
  const [thesisCached, setThesisCached] = useState(false);

  // Monte Carlo state
  const [mcResults, setMcResults] = useState<MonteCarloResponse | null>(null);
  const [mcLoading, setMcLoading] = useState(false);
  const [mcSource, setMcSource] = useState<"combined" | "live" | "backtest">("combined");
  const [mcSims, setMcSims] = useState(1000);
  const [mcTrades, setMcTrades] = useState(100);
  const [mcCapital, setMcCapital] = useState(10000);

  const fetchAll = useCallback(async () => {
    try {
      const [d, c, allBt, news, thesis] = await Promise.all([
        api.getWatchlistDetail(ticker).catch(() => null),
        api.getScreenerChart(ticker, 90).catch(() => []),
        api.getBacktestImports().catch(() => []),
        api.getTickerNews(ticker).catch(() => null),
        api.getTickerThesis(ticker).catch(() => null),
      ]);
      setDetail(d);
      setChartData(c);
      setBacktests(allBt.filter((b) => b.ticker === ticker));
      setNewsData(news);
      if (thesis?.thesis) {
        setThesisData(thesis.thesis);
        setThesisCached(thesis.cached ?? false);
      }
    } catch {}
  }, [ticker]);

  const generateThesis = async () => {
    setThesisLoading(true);
    try {
      const result = await api.generateTickerThesis(ticker);
      if (result.thesis) {
        setThesisData(result.thesis);
        setThesisCached(false);
      }
    } catch {}
    setThesisLoading(false);
  };

  useEffect(() => {
    fetchAll().finally(() => setLoading(false));
  }, [fetchAll]);

  const handleRefreshSummary = async () => {
    setRefreshing(true);
    try {
      await api.refreshWatchlistSummary(ticker);
      setTimeout(async () => {
        const updated = await api.getWatchlistDetail(ticker).catch(() => null);
        if (updated) setDetail(updated);
        setRefreshing(false);
      }, 3000);
    } catch { setRefreshing(false); }
  };

  const handleRemove = async () => {
    if (!confirm(`Remove ${ticker} from watchlist?`)) return;
    try {
      await api.removeWatchlistTicker(ticker);
      router.push("/screener");
    } catch {}
  };

  const loadBtTrades = async (importId: string) => {
    if (btTrades[importId]) { setExpandedBt(expandedBt === importId ? null : importId); return; }
    try {
      const trades = await api.getBacktestTrades(importId);
      setBtTrades((prev) => ({ ...prev, [importId]: trades }));
      setExpandedBt(importId);
    } catch {}
  };

  const runMC = async () => {
    setMcLoading(true);
    try {
      const params: MonteCarloRequest = {
        source: mcSource,
        ticker,
        num_simulations: mcSims,
        forward_trades: mcTrades,
        initial_capital: mcCapital,
        position_size_pct: 100,
      };
      const r = await api.runMonteCarlo(params);
      setMcResults(r);
    } catch {}
    setMcLoading(false);
  };

  const cons = detail ? consensusLabel(detail.consensus.direction) : null;

  // Chart data for price chart (candlestick)
  const priceChartData = chartData.map((d) => ({
    date: d.date,
    open: d.open,
    high: d.high,
    low: d.low,
    close: d.close,
    volume: d.volume,
    // For the Bar component: bar spans open→close, wick spans low→high
    barBottom: Math.min(d.open, d.close),
    barHeight: Math.abs(d.close - d.open) || 0.01,
    bullish: d.close >= d.open,
  }));

  // MC cone data
  const mcConeData = mcResults
    ? mcResults.trade_indices.map((t, i) => {
        const p: Record<string, number> = {
          trade: t,
          p5: mcResults.percentile_bands.p5[i],
          p25: mcResults.percentile_bands.p25[i],
          p50: mcResults.percentile_bands.p50[i],
          p75: mcResults.percentile_bands.p75[i],
          p95: mcResults.percentile_bands.p95[i],
        };
        if (mcResults.buyhold) {
          const bh = mcResults.buyhold.percentile_bands;
          if (bh.p50?.[i] !== undefined) p.bh_p50 = bh.p50[i];
          if (bh.p25?.[i] !== undefined) p.bh_p25 = bh.p25[i];
          if (bh.p75?.[i] !== undefined) p.bh_p75 = bh.p75[i];
        }
        return p;
      })
    : [];

  if (loading) return (
    <div className="space-y-4">
      <Skeleton className="h-12 w-48 rounded-lg" />
      <Skeleton className="h-64 rounded-xl" />
      <div className="grid grid-cols-2 gap-4">
        <Skeleton className="h-40 rounded-xl" />
        <Skeleton className="h-40 rounded-xl" />
      </div>
    </div>
  );

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <button onClick={() => router.push("/screener")} className="text-gray-500 hover:text-white transition">
            <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M15.75 19.5L8.25 12l7.5-7.5" />
            </svg>
          </button>
          <h1 className="text-2xl font-bold text-white" style={FONT_OUTFIT}>{ticker}</h1>
          {cons && <span className={`text-sm font-semibold ${cons.color}`}>{cons.label}</span>}
          {detail && detail.consensus.total_signals > 0 && (
            <span className="text-xs text-gray-500 font-mono">
              {detail.consensus.bullish_count}B / {detail.consensus.bearish_count}B
            </span>
          )}
        </div>
        <div className="flex items-center gap-2">
          <Button variant="outline" size="sm" onClick={handleRemove} className="text-loss/70 border-loss/20 hover:text-loss">
            Remove
          </Button>
        </div>
      </div>

      {/* Company Info + Sentiment + Headlines */}
      {newsData && (
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
          {/* Company Description */}
          {newsData.company && (
            <Card className="lg:col-span-2">
              <CardContent className="pt-5">
                <div className="flex items-start justify-between mb-2">
                  <div>
                    <h2 className="text-sm font-semibold text-white" style={FONT_OUTFIT}>
                      {newsData.company.name || ticker}
                    </h2>
                    <div className="flex items-center gap-2 mt-1">
                      {newsData.company.sector && (
                        <Badge className="text-[9px] bg-ai-blue/10 text-ai-blue px-1.5 py-0">{newsData.company.sector}</Badge>
                      )}
                      {newsData.company.industry && (
                        <span className="text-[10px] text-gray-500">{newsData.company.industry}</span>
                      )}
                    </div>
                  </div>
                  {newsData.company.market_cap && (
                    <div className="text-right">
                      <div className="text-[9px] text-gray-500 uppercase tracking-wider" style={FONT_OUTFIT}>Mkt Cap</div>
                      <div className="text-sm font-mono text-white">
                        ${newsData.company.market_cap >= 1e12
                          ? (newsData.company.market_cap / 1e12).toFixed(2) + "T"
                          : newsData.company.market_cap >= 1e9
                          ? (newsData.company.market_cap / 1e9).toFixed(1) + "B"
                          : (newsData.company.market_cap / 1e6).toFixed(0) + "M"}
                      </div>
                    </div>
                  )}
                </div>
                {newsData.company.description && <CompanyDescription text={newsData.company.description} />}
                <div className="flex items-center gap-6 mt-3 pt-3 border-t border-border">
                  {newsData.company.high_52w != null && (
                    <div>
                      <span className="text-[9px] text-gray-500 uppercase">52W High</span>
                      <span className="text-xs font-mono text-white ml-2">${newsData.company.high_52w.toFixed(2)}</span>
                    </div>
                  )}
                  {newsData.company.low_52w != null && (
                    <div>
                      <span className="text-[9px] text-gray-500 uppercase">52W Low</span>
                      <span className="text-xs font-mono text-white ml-2">${newsData.company.low_52w.toFixed(2)}</span>
                    </div>
                  )}
                </div>
              </CardContent>
            </Card>
          )}

          {/* Sentiment + Headlines */}
          <Card>
            <CardContent className="pt-5">
              {/* Sentiment Gauge */}
              <div className="flex items-center justify-between mb-3">
                <h2 className="text-sm font-semibold text-white" style={FONT_OUTFIT}>News Sentiment</h2>
                <div className="flex items-center gap-2">
                  <span className={`text-sm font-bold font-mono ${
                    newsData.sentiment.label === "Bullish" ? "text-profit" :
                    newsData.sentiment.label === "Bearish" ? "text-loss" :
                    "text-gray-400"
                  }`}>
                    {newsData.sentiment.label}
                  </span>
                  <span className="text-[10px] text-gray-500 font-mono">
                    ({newsData.sentiment.score >= 0 ? "+" : ""}{newsData.sentiment.score.toFixed(2)})
                  </span>
                </div>
              </div>

              {/* Sentiment bar */}
              <div className="h-2 rounded-full bg-gray-800 mb-4 overflow-hidden">
                <div
                  className={`h-full rounded-full transition-all ${
                    newsData.sentiment.score > 0.1 ? "bg-profit" :
                    newsData.sentiment.score < -0.1 ? "bg-loss" :
                    "bg-gray-500"
                  }`}
                  style={{ width: `${Math.min(100, Math.max(5, (newsData.sentiment.score + 1) / 2 * 100))}%` }}
                />
              </div>

              <div className="text-[10px] text-gray-500 mb-3">
                Based on {newsData.sentiment.article_count} article{newsData.sentiment.article_count !== 1 ? "s" : ""}
              </div>

              {/* Headlines */}
              <div className="space-y-2">
                {newsData.headlines.slice(0, 3).map((article, i) => (
                  <a
                    key={i}
                    href={article.url || "#"}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="block group"
                  >
                    <div className="text-xs text-gray-300 group-hover:text-white transition leading-snug" style={FONT_OUTFIT}>
                      {article.headline}
                    </div>
                    <div className="flex items-center gap-2 mt-0.5">
                      <span className="text-[9px] text-gray-600">{article.source}</span>
                      <span className="text-[9px] text-gray-600">
                        {new Date(article.published_at).toLocaleDateString("en-US", { month: "short", day: "numeric" })}
                      </span>
                      {article.sentiment_score != null && (
                        <span className={`text-[9px] font-mono ${
                          article.sentiment_score > 0.1 ? "text-profit/60" :
                          article.sentiment_score < -0.1 ? "text-loss/60" :
                          "text-gray-600"
                        }`}>
                          {article.sentiment_score > 0 ? "+" : ""}{article.sentiment_score.toFixed(1)}
                        </span>
                      )}
                    </div>
                  </a>
                ))}
                {newsData.headlines.length === 0 && (
                  <p className="text-xs text-gray-600 text-center py-3">No recent headlines</p>
                )}
              </div>
            </CardContent>
          </Card>
        </div>
      )}

      {/* Bull/Bear Thesis */}
      <Card>
        <CardContent className="pt-5">
          <div className="flex items-center justify-between mb-3">
            <h2 className="text-sm font-semibold text-white" style={FONT_OUTFIT}>
              Bull / Bear Thesis
            </h2>
            <Button
              size="sm"
              onClick={generateThesis}
              disabled={thesisLoading}
              className="h-6 text-[10px] bg-ai-blue/10 text-ai-blue hover:bg-ai-blue/20 border border-ai-blue/20"
            >
              {thesisLoading ? "Generating..." : thesisData ? "Refresh" : "Generate"}
            </Button>
          </div>

          {thesisData ? (
            <div className="space-y-3">
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                {/* Bull Case */}
                <div className="rounded-lg bg-profit/5 border border-profit/10 p-3">
                  <div className="flex items-center gap-1.5 mb-1.5">
                    <svg className="w-3.5 h-3.5 text-profit" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                      <path strokeLinecap="round" strokeLinejoin="round" d="M2.25 18L9 11.25l4.306 4.307a11.95 11.95 0 015.814-5.519l2.74-1.22m0 0l-5.94-2.28m5.94 2.28l-2.28 5.941" />
                    </svg>
                    <span className="text-[10px] font-semibold text-profit uppercase tracking-wider" style={FONT_OUTFIT}>Bull Case</span>
                  </div>
                  <p className="text-xs text-gray-300 leading-relaxed">{thesisData.bull_case}</p>
                  {thesisData.key_catalysts && thesisData.key_catalysts.length > 0 && (
                    <div className="mt-2 flex flex-wrap gap-1">
                      {thesisData.key_catalysts.map((c, i) => (
                        <span key={i} className="text-[9px] px-1.5 py-0.5 rounded bg-profit/10 text-profit/80">{c}</span>
                      ))}
                    </div>
                  )}
                </div>

                {/* Bear Case */}
                <div className="rounded-lg bg-loss/5 border border-loss/10 p-3">
                  <div className="flex items-center gap-1.5 mb-1.5">
                    <svg className="w-3.5 h-3.5 text-loss" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                      <path strokeLinecap="round" strokeLinejoin="round" d="M2.25 6L9 12.75l4.286-4.286a11.948 11.948 0 014.306 6.43l.776 2.898m0 0l3.182-5.511m-3.182 5.51l-5.511-3.181" />
                    </svg>
                    <span className="text-[10px] font-semibold text-loss uppercase tracking-wider" style={FONT_OUTFIT}>Bear Case</span>
                  </div>
                  <p className="text-xs text-gray-300 leading-relaxed">{thesisData.bear_case}</p>
                  {thesisData.risk_factors && thesisData.risk_factors.length > 0 && (
                    <div className="mt-2 flex flex-wrap gap-1">
                      {thesisData.risk_factors.map((r, i) => (
                        <span key={i} className="text-[9px] px-1.5 py-0.5 rounded bg-loss/10 text-loss/80">{r}</span>
                      ))}
                    </div>
                  )}
                </div>
              </div>

              {thesisData.sentiment_summary && (
                <p className="text-[10px] text-gray-500 italic">{thesisData.sentiment_summary}</p>
              )}
              {thesisCached && (
                <span className="text-[9px] text-gray-600">Cached — click Refresh for updated analysis</span>
              )}
            </div>
          ) : (
            <p className="text-xs text-gray-600 text-center py-4">
              Click Generate for Henry&apos;s bull/bear analysis
            </p>
          )}
        </CardContent>
      </Card>

      {/* Price Chart (Candlestick) */}
      {priceChartData.length >= 2 && (
        <Card>
          <CardContent className="pt-5">
            <h2 className="text-sm font-semibold text-white mb-3" style={FONT_OUTFIT}>Price (90d)</h2>
            <CandlestickChart data={priceChartData} />
          </CardContent>
        </Card>
      )}

      {/* Henry's AI Summary */}
      <Card>
        <CardContent className="pt-5">
          <div className="flex items-center justify-between mb-2">
            <h2 className="text-sm font-semibold text-white flex items-center gap-2" style={FONT_OUTFIT}>
              <span className="w-2 h-2 rounded-full bg-ai-blue animate-pulse" />
              Henry&apos;s Analysis
            </h2>
            <button onClick={handleRefreshSummary} disabled={refreshing}
              className="text-xs text-ai-blue/70 hover:text-ai-blue flex items-center gap-1 transition disabled:opacity-50">
              <svg className={`w-3 h-3 ${refreshing ? "animate-spin" : ""}`} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
              </svg>
              {refreshing ? "Generating..." : "Refresh"}
            </button>
          </div>
          {detail?.cached_summary ? (
            <div className={`rounded-lg border p-3 ${detail.cached_summary.is_stale ? "border-yellow-500/20 bg-yellow-500/5" : "border-ai-blue/20 bg-ai-blue/5"}`}>
              {detail.cached_summary.is_stale && (
                <div className="flex items-center gap-1.5 mb-2 text-[10px] text-yellow-400">Stale — new data since {formatTimeAgo(detail.cached_summary.generated_at)}</div>
              )}
              <div className="text-sm text-gray-300 ai-prose" dangerouslySetInnerHTML={{ __html: renderMarkdown(detail.cached_summary.summary) }} />
              <div className="text-[10px] text-gray-600 mt-2 font-mono">Generated {formatTimeAgo(detail.cached_summary.generated_at)}</div>
            </div>
          ) : (
            <div className="text-center py-4">
              <button onClick={handleRefreshSummary} disabled={refreshing} className="text-xs text-ai-blue hover:underline">{refreshing ? "Generating..." : "Generate analysis"}</button>
            </div>
          )}
        </CardContent>
      </Card>

      {/* Indicator Signals + Strategy Positions side by side */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {/* Signals */}
        <Card>
          <CardContent className="pt-5">
            <h2 className="text-sm font-semibold text-white mb-3" style={FONT_OUTFIT}>Indicator Signals</h2>
            {detail && detail.all_signals.length > 0 ? (
              <div className="space-y-1 max-h-64 overflow-y-auto">
                {detail.all_signals.map((s) => (
                  <div key={s.id} className="flex items-center gap-2 text-xs py-1.5 px-2 rounded bg-surface-light/20">
                    <span className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${signalDot(s.signal)}`} />
                    <span className="text-gray-300 font-mono flex-1">{formatIndicator(s.indicator)}</span>
                    <span className="text-gray-500 font-mono">{s.value.toFixed(2)}</span>
                    <span className={s.signal === "bullish" ? "text-profit" : s.signal === "bearish" ? "text-loss" : "text-gray-500"}>{s.signal}</span>
                    <span className="text-gray-600">{formatTimeAgo(s.created_at)}</span>
                  </div>
                ))}
              </div>
            ) : (
              <p className="text-xs text-gray-600 py-4 text-center">No indicator signals</p>
            )}
          </CardContent>
        </Card>

        {/* Strategy Positions + Trade History */}
        <Card>
          <CardContent className="pt-5">
            <h2 className="text-sm font-semibold text-white mb-3" style={FONT_OUTFIT}>Positions &amp; History</h2>
            {detail && detail.strategy_positions.length > 0 && (
              <div className="space-y-1.5 mb-4">
                {detail.strategy_positions.map((p) => (
                  <div key={p.strategy_id} className="flex items-center gap-3 text-xs py-2 px-3 rounded-lg bg-surface-light/20 border border-border/30">
                    <span className="text-gray-300">{p.strategy_name}</span>
                    <span className={`font-mono font-bold ${p.direction === "long" ? "text-profit" : "text-loss"}`}>{p.direction.toUpperCase()}</span>
                    <span className="text-gray-500 font-mono ml-auto">@ ${p.entry_price.toFixed(2)}</span>
                    {p.pnl_pct != null && (
                      <span className={`font-mono font-bold ${(p.pnl_pct ?? 0) >= 0 ? "text-profit" : "text-loss"}`}>{(p.pnl_pct ?? 0) >= 0 ? "+" : ""}{p.pnl_pct?.toFixed(2)}%</span>
                    )}
                  </div>
                ))}
              </div>
            )}
            {detail && detail.trade_history.length > 0 ? (
              <div className="space-y-1 max-h-48 overflow-y-auto">
                <div className="text-[10px] text-gray-600 uppercase tracking-wider mb-1">Recent Trades</div>
                {detail.trade_history.map((t, i) => (
                  <div key={i} className="flex items-center gap-2 text-xs py-1.5 px-2 rounded bg-surface-light/20">
                    <span className="text-gray-400">{t.strategy_name}</span>
                    <span className={t.direction === "long" ? "text-profit" : "text-loss"}>{t.direction.toUpperCase()}</span>
                    <span className="text-gray-500 font-mono">${t.entry_price.toFixed(2)} → ${t.exit_price?.toFixed(2) ?? "?"}</span>
                    <span className={`font-mono ml-auto font-bold ${t.pnl_pct >= 0 ? "text-profit" : "text-loss"}`}>{t.pnl_pct >= 0 ? "+" : ""}{t.pnl_pct.toFixed(2)}%</span>
                  </div>
                ))}
              </div>
            ) : (
              <p className="text-xs text-gray-600 py-2 text-center">No trade history</p>
            )}
          </CardContent>
        </Card>
      </div>

      {/* Backtest Data */}
      <Card>
        <CardContent className="pt-5">
          <h2 className="text-sm font-semibold text-white mb-3" style={FONT_OUTFIT}>Backtest Data — {ticker}</h2>
          {backtests.length > 0 ? (
            <div className="space-y-2">
              {backtests.map((bt) => (
                <div key={bt.id} className="border border-border/40 rounded-lg overflow-hidden">
                  <button onClick={() => loadBtTrades(bt.id)} className="w-full flex items-center gap-3 p-3 text-left hover:bg-surface-light/20 transition">
                    <span className="text-sm text-white font-medium">{bt.strategy_name}</span>
                    <Badge variant="outline" className="text-[10px]">{bt.trade_count} trades</Badge>
                    {bt.win_rate != null && <span className="text-xs text-gray-400 font-mono">WR {bt.win_rate.toFixed(1)}%</span>}
                    {bt.profit_factor != null && <span className="text-xs text-gray-400 font-mono">PF {bt.profit_factor.toFixed(2)}</span>}
                    {bt.total_pnl_pct != null && (
                      <span className={`text-xs font-mono ml-auto font-bold ${bt.total_pnl_pct >= 0 ? "text-profit" : "text-loss"}`}>
                        {bt.total_pnl_pct >= 0 ? "+" : ""}{bt.total_pnl_pct.toFixed(2)}%
                      </span>
                    )}
                    <svg className={`w-4 h-4 text-gray-500 transition ${expandedBt === bt.id ? "rotate-180" : ""}`} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                      <path strokeLinecap="round" strokeLinejoin="round" d="M19.5 8.25l-7.5 7.5-7.5-7.5" />
                    </svg>
                  </button>
                  {expandedBt === bt.id && btTrades[bt.id] && (
                    <div className="border-t border-border/30 p-3 max-h-48 overflow-y-auto">
                      <div className="space-y-1">
                        {btTrades[bt.id].map((t) => (
                          <div key={t.id} className="flex items-center gap-2 text-xs py-1 px-2 rounded bg-surface-light/10">
                            <span className="text-gray-500 font-mono w-16">{t.trade_date?.slice(0, 10)}</span>
                            <span className={t.direction === "long" ? "text-profit" : "text-loss"}>{t.type}</span>
                            <span className="text-gray-400 font-mono">${t.price.toFixed(2)}</span>
                            {t.net_pnl_pct != null && (
                              <span className={`font-mono ml-auto ${t.net_pnl_pct >= 0 ? "text-profit" : "text-loss"}`}>
                                {t.net_pnl_pct >= 0 ? "+" : ""}{t.net_pnl_pct.toFixed(2)}%
                              </span>
                            )}
                          </div>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              ))}
            </div>
          ) : (
            <p className="text-xs text-gray-600 py-4 text-center">No backtest data for {ticker}. Upload CSV files in Settings.</p>
          )}
        </CardContent>
      </Card>

      {/* Monte Carlo Simulation */}
      <Card>
        <CardContent className="pt-5">
          <h2 className="text-sm font-semibold text-white mb-3" style={FONT_OUTFIT}>Monte Carlo — {ticker}</h2>
          <div className="flex flex-wrap items-end gap-3 mb-4">
            <div className="space-y-1">
              <label className="text-[10px] text-gray-500 uppercase tracking-wider" style={FONT_OUTFIT}>Source</label>
              <div className="flex rounded-lg overflow-hidden border border-border">
                {(["combined", "live", "backtest"] as const).map((s) => (
                  <button key={s} onClick={() => setMcSource(s)}
                    className={`px-3 py-1.5 text-xs font-medium capitalize ${mcSource === s ? "bg-indigo-500 text-white" : "bg-surface-light/30 text-gray-400 hover:text-white"}`}>{s}</button>
                ))}
              </div>
            </div>
            <div className="space-y-1">
              <label className="text-[10px] text-gray-500 uppercase tracking-wider" style={FONT_OUTFIT}>Sims</label>
              <select value={mcSims} onChange={(e) => setMcSims(Number(e.target.value))}
                className="bg-surface-light/30 border border-border rounded-lg px-3 py-1.5 text-xs text-white" style={FONT_MONO}>
                {[500, 1000, 2500, 5000].map((n) => <option key={n} value={n}>{n.toLocaleString()}</option>)}
              </select>
            </div>
            <div className="space-y-1">
              <label className="text-[10px] text-gray-500 uppercase tracking-wider" style={FONT_OUTFIT}>Trades</label>
              <select value={mcTrades} onChange={(e) => setMcTrades(Number(e.target.value))}
                className="bg-surface-light/30 border border-border rounded-lg px-3 py-1.5 text-xs text-white" style={FONT_MONO}>
                {[50, 100, 200].map((n) => <option key={n} value={n}>{n}</option>)}
              </select>
            </div>
            <div className="space-y-1">
              <label className="text-[10px] text-gray-500 uppercase tracking-wider" style={FONT_OUTFIT}>Capital</label>
              <input type="number" value={mcCapital} onChange={(e) => setMcCapital(Number(e.target.value))}
                className="bg-surface-light/30 border border-border rounded-lg px-3 py-1.5 text-xs text-white w-24" style={FONT_MONO} />
            </div>
            <button onClick={runMC} disabled={mcLoading}
              className="bg-indigo-500 hover:bg-indigo-600 disabled:opacity-50 text-white px-5 py-1.5 rounded-lg text-xs font-semibold flex items-center gap-2" style={FONT_OUTFIT}>
              {mcLoading && <svg className="animate-spin h-3.5 w-3.5" viewBox="0 0 24 24" fill="none"><circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" /><path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" /></svg>}
              Run
            </button>
          </div>

          {mcResults && (
            <div className="space-y-4">
              {/* MC Cone Chart */}
              <div style={{ height: 300 }}>
                <ResponsiveContainer width="100%" height="100%">
                  <AreaChart data={mcConeData}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#1f2937" vertical={false} />
                    <XAxis dataKey="trade" tick={{ fill: "#6b7280", fontSize: 10, ...FONT_MONO }} axisLine={{ stroke: "#374151" }} tickLine={false} />
                    <YAxis tick={{ fill: "#6b7280", fontSize: 10, ...FONT_MONO }} axisLine={{ stroke: "#374151" }} tickLine={false} tickFormatter={(v: number) => `$${(v / 1000).toFixed(0)}k`} />
                    <Tooltip contentStyle={CHART_TOOLTIP} />
                    <ReferenceLine y={mcCapital} stroke="rgba(255,255,255,0.2)" strokeDasharray="4 4" />
                    <Area type="monotone" dataKey="p95" fill="#6366f1" fillOpacity={0.05} stroke="none" isAnimationActive={false} />
                    <Area type="monotone" dataKey="p5" fill="#111827" fillOpacity={1} stroke="none" isAnimationActive={false} />
                    <Area type="monotone" dataKey="p75" fill="#6366f1" fillOpacity={0.1} stroke="none" isAnimationActive={false} />
                    <Area type="monotone" dataKey="p25" fill="#111827" fillOpacity={1} stroke="none" isAnimationActive={false} />
                    <Line type="monotone" dataKey="p50" stroke="#fbbf24" strokeWidth={2.5} dot={false} isAnimationActive={false} />
                    {mcResults.buyhold && (
                      <>
                        <Area type="monotone" dataKey="bh_p75" fill="#22c55e" fillOpacity={0.06} stroke="none" isAnimationActive={false} />
                        <Area type="monotone" dataKey="bh_p25" fill="#111827" fillOpacity={1} stroke="none" isAnimationActive={false} />
                        <Line type="monotone" dataKey="bh_p50" stroke="#22c55e" strokeWidth={2.5} dot={false} isAnimationActive={false} />
                      </>
                    )}
                  </AreaChart>
                </ResponsiveContainer>
              </div>

              {/* MC Summary Stats */}
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
                <div className="bg-surface-light/30 rounded-xl p-3 border border-border">
                  <div className="text-[10px] text-gray-500 uppercase" style={FONT_OUTFIT}>Prob. Profit</div>
                  <div className={`text-lg font-mono font-semibold ${mcResults.summary.probability_of_profit > 60 ? "text-profit" : mcResults.summary.probability_of_profit >= 40 ? "text-amber-400" : "text-loss"}`}>
                    {mcResults.summary.probability_of_profit.toFixed(1)}%
                  </div>
                </div>
                <div className="bg-surface-light/30 rounded-xl p-3 border border-border">
                  <div className="text-[10px] text-gray-500 uppercase" style={FONT_OUTFIT}>Median Return</div>
                  <div className={`text-lg font-mono font-semibold ${pnlColor(mcResults.summary.median_return_pct)}`}>
                    {formatPercent(mcResults.summary.median_return_pct)}
                  </div>
                </div>
                <div className="bg-surface-light/30 rounded-xl p-3 border border-border">
                  <div className="text-[10px] text-gray-500 uppercase" style={FONT_OUTFIT}>Best (P95)</div>
                  <div className="text-lg font-mono font-semibold text-profit">{formatCurrency(mcResults.summary.best_case_p95)}</div>
                </div>
                <div className="bg-surface-light/30 rounded-xl p-3 border border-border">
                  <div className="text-[10px] text-gray-500 uppercase" style={FONT_OUTFIT}>Worst (P5)</div>
                  <div className="text-lg font-mono font-semibold text-loss">{formatCurrency(mcResults.summary.worst_case_p5)}</div>
                </div>
              </div>

              {/* Buy & Hold comparison if available */}
              {mcResults.buyhold && (
                <div className="flex items-center gap-4 text-xs px-3 py-2 rounded-lg bg-surface-light/20 border border-border/30">
                  <span className="text-gray-400">Strategy median:</span>
                  <span className={`font-mono font-bold ${pnlColor(mcResults.summary.median_return_pct)}`}>{formatPercent(mcResults.summary.median_return_pct)}</span>
                  <span className="text-gray-600">vs</span>
                  <span className="text-gray-400">Buy &amp; Hold:</span>
                  <span className={`font-mono font-bold ${pnlColor(mcResults.buyhold.summary.median_return_pct)}`}>{formatPercent(mcResults.buyhold.summary.median_return_pct)}</span>
                  {(() => {
                    const edge = mcResults.summary.median_return_pct - mcResults.buyhold.summary.median_return_pct;
                    return <span className={`font-mono font-bold ml-auto ${edge >= 0 ? "text-profit" : "text-loss"}`}>Edge: {edge >= 0 ? "+" : ""}{edge.toFixed(1)}%</span>;
                  })()}
                </div>
              )}

              {/* Legend */}
              <div className="flex items-center gap-4 text-[10px] text-gray-600 justify-center">
                <div className="flex items-center gap-1"><div className="w-4 h-0.5 bg-amber-400 rounded" /> Strategy</div>
                <div className="flex items-center gap-1"><div className="w-3 h-3 rounded bg-indigo-500/10 border border-indigo-500/20" /> Probability bands</div>
                {mcResults.buyhold && <div className="flex items-center gap-1"><div className="w-4 h-0.5 bg-green-500 rounded" /> Buy &amp; Hold</div>}
              </div>
            </div>
          )}

          {!mcResults && !mcLoading && (
            <p className="text-xs text-gray-600 text-center py-4">Configure parameters and run a simulation</p>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
