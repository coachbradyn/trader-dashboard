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
import { MetricTooltip } from "@/app/layout-shell";
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

  // Fundamentals state
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const [fundamentals, setFundamentals] = useState<Record<string, any> | null>(null);

  // Price targets state
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const [priceTargets, setPriceTargets] = useState<Record<string, any> | null>(null);
  const [ptLoading, setPtLoading] = useState(false);

  // Chart range state
  const [chartDays, setChartDays] = useState(90);

  const fetchAll = useCallback(async () => {
    try {
      const [d, c, allBt, news, thesis, fund, pt] = await Promise.all([
        api.getWatchlistDetail(ticker).catch(() => null),
        api.getScreenerChart(ticker, chartDays).catch(() => []),
        api.getBacktestImports().catch(() => []),
        api.getTickerNews(ticker).catch(() => null),
        api.getTickerThesis(ticker).catch(() => null),
        api.getTickerFundamentals(ticker).catch(() => null),
        api.getHenryPriceTargets(ticker).catch(() => null),
      ]);
      setDetail(d);
      setChartData(c);
      setBacktests(allBt.filter((b) => b.ticker === ticker));
      if (fund) setFundamentals(fund);
      if (pt && !pt.error) setPriceTargets(pt);
      setNewsData(news);
      if (thesis?.thesis) {
        setThesisData(thesis.thesis);
        setThesisCached(thesis.cached ?? false);
      }
    } catch {}
  }, [ticker, chartDays]);

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

  // Derived values for hero
  const lastPrice = priceChartData.length > 0 ? priceChartData[priceChartData.length - 1]?.close : null;
  const prevPrice = priceChartData.length > 1 ? priceChartData[priceChartData.length - 2]?.close : null;
  const dailyChange = lastPrice && prevPrice ? lastPrice - prevPrice : null;
  const dailyChangePct = lastPrice && prevPrice ? ((lastPrice - prevPrice) / prevPrice) * 100 : null;
  const isUp = (dailyChangePct ?? 0) >= 0;

  const fmtMcap = (v: number) => v >= 1e12 ? `$${(v/1e12).toFixed(2)}T` : v >= 1e9 ? `$${(v/1e9).toFixed(2)}B` : `$${(v/1e6).toFixed(0)}M`;

  return (
    <div className="space-y-4">
      {/* ═══ HERO ═══ */}
      <div>
        <div className="flex items-center gap-2 mb-1">
          <button onClick={() => router.push("/screener")} className="text-gray-500 hover:text-white transition">
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M15.75 19.5L8.25 12l7.5-7.5" />
            </svg>
          </button>
          <span className="text-xs text-gray-500" style={FONT_OUTFIT}>Watchlist</span>
        </div>
        <div className="flex items-start justify-between">
          <div>
            <div className="flex items-center gap-3">
              <h1 className="text-3xl font-bold text-white" style={FONT_OUTFIT}>{ticker}</h1>
              {fundamentals?.company_name && <span className="text-sm text-gray-400" style={FONT_OUTFIT}>{fundamentals.company_name}</span>}
            </div>
            <div className="flex items-baseline gap-3 mt-1">
              {lastPrice != null && (
                <span className="text-4xl font-bold text-white" style={FONT_MONO}>${lastPrice.toFixed(2)}</span>
              )}
              {dailyChange != null && dailyChangePct != null && (
                <span className={`text-lg font-semibold ${isUp ? "text-profit" : "text-loss"}`} style={FONT_MONO}>
                  {isUp ? "+" : ""}{dailyChange.toFixed(2)} ({isUp ? "+" : ""}{dailyChangePct.toFixed(2)}%)
                </span>
              )}
            </div>
            {fundamentals?.description && (
              <p className="text-xs text-gray-500 mt-2 max-w-2xl" style={FONT_OUTFIT}>{fundamentals.description}</p>
            )}
            <div className="flex flex-wrap items-center gap-2 mt-2">
              {fundamentals?.sector && <Badge variant="outline" className="text-[9px]">{fundamentals.sector}</Badge>}
              {fundamentals?.industry && <Badge variant="outline" className="text-[9px]">{fundamentals.industry}</Badge>}
              {fundamentals?.market_cap != null && <Badge variant="outline" className="text-[9px] font-mono">{fmtMcap(fundamentals.market_cap)}</Badge>}
              {cons && <Badge className={`text-[9px] ${cons.color === "text-profit" ? "bg-profit/15 text-profit" : cons.color === "text-loss" ? "bg-loss/15 text-loss" : "bg-amber-500/15 text-amber-400"}`}>{cons.label}</Badge>}
            </div>
          </div>
          <Button variant="outline" size="sm" onClick={handleRemove} className="text-loss/70 border-loss/20 hover:text-loss shrink-0">
            Remove
          </Button>
        </div>
      </div>

      {/* ═══ CHART + TIME RANGE ═══ */}
      {priceChartData.length >= 2 && (
        <div>
          <CandlestickChart data={priceChartData} />
          <div className="flex items-center gap-1 mt-2">
            {[{label: "1W", days: 7}, {label: "1M", days: 30}, {label: "3M", days: 90}, {label: "6M", days: 180}, {label: "1Y", days: 365}].map(r => (
              <button key={r.label} onClick={() => setChartDays(r.days)}
                className={`px-3 py-1 rounded text-[10px] font-mono transition ${chartDays === r.days ? "bg-ai-blue/20 text-ai-blue border border-ai-blue/30" : "text-gray-500 hover:text-gray-300"}`}>
                {r.label}
              </button>
            ))}
          </div>
        </div>
      )}

      {/* ═══ KEY STATS BAR ═══ */}
      <div className="flex gap-3 overflow-x-auto pb-1 -mx-1 px-1">
        {[
          priceChartData.length > 0 && ["Open", `$${priceChartData[priceChartData.length-1]?.open?.toFixed(2)}`],
          priceChartData.length > 0 && ["High", `$${priceChartData[priceChartData.length-1]?.high?.toFixed(2)}`],
          priceChartData.length > 0 && ["Low", `$${priceChartData[priceChartData.length-1]?.low?.toFixed(2)}`],
          priceChartData.length > 0 && ["Volume", `${((priceChartData[priceChartData.length-1]?.volume || 0) / 1e6).toFixed(1)}M`],
          fundamentals?.market_cap != null && ["Mkt Cap", fmtMcap(fundamentals.market_cap)],
          fundamentals?.pe_ratio != null && ["P/E", fundamentals.pe_ratio.toFixed(1)],
          fundamentals?.beta != null && ["Beta", fundamentals.beta.toFixed(2)],
          fundamentals?.dividend_yield != null && fundamentals.dividend_yield > 0 && ["Div Yield", `${fundamentals.dividend_yield.toFixed(2)}%`],
          fundamentals?.short_interest_pct != null && fundamentals.short_interest_pct > 3 && ["Short %", `${fundamentals.short_interest_pct.toFixed(1)}%`],
          fundamentals?.profit_margin != null && ["Margin", `${(fundamentals.profit_margin * 100).toFixed(1)}%`],
          fundamentals?.roe != null && ["ROE", `${(fundamentals.roe * 100).toFixed(1)}%`],
          fundamentals?.debt_to_equity != null && ["D/E", fundamentals.debt_to_equity.toFixed(2)],
        ].filter(Boolean).map((stat, i) => {
          const [label, value] = stat as [string, string];
          const tipMap: Record<string, string> = {
            "P/E": "Price relative to earnings. Lower may indicate value",
            "Beta": "Volatility vs market. Below 1 = less volatile, above 1 = more volatile",
          };
          const tip = tipMap[label];
          const labelEl = <div className="text-[8px] text-gray-500 uppercase tracking-wider" style={FONT_OUTFIT}>{label}</div>;
          return (
            <div key={i} className="shrink-0 px-3 py-1.5 rounded-lg bg-surface-light/20 border border-border/20">
              {tip ? <MetricTooltip tip={tip}>{labelEl}</MetricTooltip> : labelEl}
              <div className="text-xs font-mono text-white">{value}</div>
            </div>
          );
        })}
      </div>

      {/* ═══ ANALYST & VALUATION ═══ */}
      {fundamentals && (fundamentals.analyst_rating || fundamentals.analyst_target_consensus || fundamentals.dcf_value || fundamentals.earnings_date) && (
        <Card>
          <CardContent className="p-5">
            <h2 className="text-sm font-semibold text-white mb-4" style={FONT_OUTFIT}>Analyst & Valuation</h2>
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
              {/* Rating */}
              {fundamentals.analyst_rating && (
                <div className="text-center p-3 rounded-lg bg-surface-light/20">
                  <div className="text-[9px] text-gray-500 mb-1">Analyst Rating</div>
                  <div className={`text-lg font-bold ${
                    fundamentals.analyst_rating.toLowerCase().includes("buy") ? "text-profit" :
                    fundamentals.analyst_rating.toLowerCase().includes("sell") ? "text-loss" : "text-amber-400"
                  }`}>{fundamentals.analyst_rating}</div>
                  {fundamentals.analyst_count && <div className="text-[9px] text-gray-500 font-mono">{fundamentals.analyst_count} analysts</div>}
                </div>
              )}
              {/* Price Target */}
              {fundamentals.analyst_target_consensus != null && (
                <div className="p-3 rounded-lg bg-surface-light/20">
                  <div className="text-[9px] text-gray-500 mb-1">Price Target</div>
                  <div className="text-lg font-bold font-mono text-white">${fundamentals.analyst_target_consensus.toFixed(2)}</div>
                  {fundamentals.analyst_target_low != null && fundamentals.analyst_target_high != null && (
                    <>
                      <div className="relative h-2 rounded-full bg-surface-light/30 mt-2 mb-1">
                        {(() => {
                          const lo = fundamentals.analyst_target_low, hi = fundamentals.analyst_target_high, cons2 = fundamentals.analyst_target_consensus;
                          const range = hi - lo || 1;
                          const consPct = ((cons2 - lo) / range) * 100;
                          const pricePct = lastPrice ? ((lastPrice - lo) / range) * 100 : null;
                          return (<>
                            <div className="absolute h-full bg-ai-blue/30 rounded-full" style={{ width: "100%" }} />
                            <div className="absolute top-0 h-full w-0.5 bg-ai-blue" style={{ left: `${Math.min(100, Math.max(0, consPct))}%` }} title="Consensus" />
                            {pricePct != null && <div className="absolute top-0 h-full w-0.5 bg-amber-400" style={{ left: `${Math.min(100, Math.max(0, pricePct))}%` }} title="Current price" />}
                          </>);
                        })()}
                      </div>
                      <div className="flex justify-between text-[8px] font-mono text-gray-600">
                        <span>${fundamentals.analyst_target_low.toFixed(0)}</span>
                        <span>${fundamentals.analyst_target_high.toFixed(0)}</span>
                      </div>
                    </>
                  )}
                </div>
              )}
              {/* DCF */}
              {fundamentals.dcf_value != null && (
                <div className="p-3 rounded-lg bg-surface-light/20">
                  <MetricTooltip tip="Discounted Cash Flow estimate of fair value"><div className="text-[9px] text-gray-500 mb-1">DCF Value</div></MetricTooltip>
                  <div className="text-lg font-bold font-mono text-white">${fundamentals.dcf_value.toFixed(2)}</div>
                  {fundamentals.dcf_diff_pct != null && (
                    <Badge className={`text-[9px] mt-1 ${fundamentals.dcf_diff_pct > 0 ? "bg-profit/15 text-profit" : "bg-loss/15 text-loss"}`}>
                      {fundamentals.dcf_diff_pct > 0 ? `${fundamentals.dcf_diff_pct.toFixed(0)}% undervalued` : `${Math.abs(fundamentals.dcf_diff_pct).toFixed(0)}% overvalued`}
                    </Badge>
                  )}
                </div>
              )}
              {/* Earnings */}
              {fundamentals.earnings_date && (
                <div className="p-3 rounded-lg bg-surface-light/20">
                  <div className="text-[9px] text-gray-500 mb-1">Next Earnings</div>
                  <div className="text-sm font-mono text-white">{fundamentals.earnings_date}</div>
                  {fundamentals.earnings_time && <span className="text-[9px] text-gray-500"> {fundamentals.earnings_time.toUpperCase()}</span>}
                  {(() => { const d = Math.ceil((new Date(fundamentals.earnings_date).getTime() - Date.now()) / 86400000); return d >= 0 ? <div className={`text-[10px] font-mono mt-1 ${d <= 7 ? "text-amber-400" : "text-gray-400"}`}>{d} days away</div> : null; })()}
                  {fundamentals.eps_estimate_current != null && <div className="text-[9px] text-gray-500 mt-1">EPS Est: <span className="text-white">${fundamentals.eps_estimate_current.toFixed(2)}</span></div>}
                  {fundamentals.eps_surprise_last != null && <div className={`text-[9px] ${fundamentals.eps_surprise_last >= 0 ? "text-profit" : "text-loss"}`}>Last surprise: {fundamentals.eps_surprise_last >= 0 ? "+" : ""}{fundamentals.eps_surprise_last.toFixed(1)}%</div>}
                </div>
              )}
            </div>
            {/* Insider + Institutional row */}
            {(fundamentals.insider_net_90d || fundamentals.institutional_ownership_pct) && (
              <div className="flex flex-wrap gap-4 mt-3 text-[10px] font-mono">
                {fundamentals.insider_net_90d != null && fundamentals.insider_net_90d !== 0 && (
                  <span className={fundamentals.insider_net_90d > 0 ? "text-profit" : "text-loss"}>
                    Insider 90d: {fundamentals.insider_net_90d > 0 ? "Net buying" : "Net selling"} ${Math.abs(fundamentals.insider_net_90d / 1e6).toFixed(1)}M
                  </span>
                )}
                {fundamentals.institutional_ownership_pct != null && <span className="text-gray-400">Institutional: {fundamentals.institutional_ownership_pct.toFixed(1)}%</span>}
              </div>
            )}
          </CardContent>
        </Card>
      )}

      {/* ═══ PRICE TARGETS ═══ */}
      <Card>
        <CardContent className="p-5">
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-sm font-semibold text-white" style={FONT_OUTFIT}>Price Targets</h2>
            {!priceTargets && !ptLoading && (
              <Button variant="outline" size="sm" onClick={async () => {
                setPtLoading(true);
                try {
                  const pt = await api.getHenryPriceTargets(ticker);
                  if (pt && !(pt as Record<string, unknown>).error) setPriceTargets(pt);
                } catch {}
                setPtLoading(false);
              }} className="text-[10px] h-7 text-ai-blue border-ai-blue/30">
                Generate Targets
              </Button>
            )}
            {ptLoading && <span className="text-[10px] text-ai-blue/60 font-mono animate-pulse">Analyzing...</span>}
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
            {/* Analyst targets (from FMP) */}
            {fundamentals?.analyst_target_consensus != null && (
              <div className="p-4 rounded-lg bg-surface-light/10 border border-border/20">
                <div className="text-[9px] text-gray-500 uppercase tracking-wider mb-2" style={FONT_OUTFIT}>Analyst Consensus</div>
                <div className="text-2xl font-bold font-mono text-white">${fundamentals.analyst_target_consensus.toFixed(2)}</div>
                {fundamentals.analyst_target_low != null && fundamentals.analyst_target_high != null && (
                  <div className="text-[10px] font-mono text-gray-500 mt-1">
                    Range: ${fundamentals.analyst_target_low.toFixed(2)} — ${fundamentals.analyst_target_high.toFixed(2)}
                  </div>
                )}
                {fundamentals.analyst_count && <div className="text-[9px] text-gray-600 mt-1">{fundamentals.analyst_count} analysts</div>}
                {lastPrice != null && fundamentals.analyst_target_consensus != null && (
                  <div className={`text-xs font-mono mt-2 ${fundamentals.analyst_target_consensus > lastPrice ? "text-profit" : "text-loss"}`}>
                    {fundamentals.analyst_target_consensus > lastPrice ? "▲" : "▼"} {Math.abs(((fundamentals.analyst_target_consensus - lastPrice) / lastPrice) * 100).toFixed(1)}% {fundamentals.analyst_target_consensus > lastPrice ? "upside" : "downside"}
                  </div>
                )}
              </div>
            )}
            {/* Henry's targets */}
            {priceTargets ? (
              <>
                {[
                  { key: "short_term", label: "1 Week", color: "text-amber-400", border: "border-amber-500/20" },
                  { key: "medium_term", label: "1 Month", color: "text-ai-blue", border: "border-ai-blue/20" },
                  { key: "long_term", label: "6 Months", color: "text-ai-purple", border: "border-ai-purple/20" },
                ].map(({ key, label, color, border }) => {
                  // eslint-disable-next-line @typescript-eslint/no-explicit-any
                  const target = (priceTargets as Record<string, any>)[key];
                  if (!target?.target) return null;
                  const changePct = lastPrice ? ((target.target - lastPrice) / lastPrice) * 100 : null;
                  return (
                    <div key={key} className={`p-4 rounded-lg bg-surface-light/10 border ${border}`}>
                      <div className="flex items-center gap-2 mb-2">
                        <span className="text-[9px] text-gray-500 uppercase tracking-wider" style={FONT_OUTFIT}>{label}</span>
                        <span className="w-2 h-2 rounded-full bg-ai-blue animate-pulse" title="Henry's estimate" />
                      </div>
                      <div className={`text-2xl font-bold font-mono ${color}`}>${target.target.toFixed(2)}</div>
                      {changePct != null && (
                        <div className={`text-xs font-mono mt-1 ${changePct >= 0 ? "text-profit" : "text-loss"}`}>
                          {changePct >= 0 ? "▲" : "▼"} {Math.abs(changePct).toFixed(1)}%
                        </div>
                      )}
                      <p className="text-[10px] text-gray-500 mt-2 leading-relaxed">{target.reason}</p>
                      <Badge className={`text-[8px] mt-1 ${
                        target.confidence === "high" ? "bg-profit/10 text-profit" :
                        target.confidence === "low" ? "bg-loss/10 text-loss" :
                        "bg-amber-500/10 text-amber-400"
                      }`}>{target.confidence} confidence</Badge>
                    </div>
                  );
                })}
              </>
            ) : !ptLoading && !fundamentals?.analyst_target_consensus ? (
              <div className="col-span-3 text-center py-6 text-gray-500 text-xs">
                Click &quot;Generate Targets&quot; for Henry&apos;s 1-week, 1-month, and 6-month price predictions
              </div>
            ) : null}
          </div>
        </CardContent>
      </Card>

      {/* ═══ TWO COLUMN LAYOUT ═══ */}
      <div className="grid grid-cols-1 lg:grid-cols-5 gap-4">

        {/* LEFT COLUMN (3/5) — Henry + Thesis + News */}
        <div className="lg:col-span-3 space-y-4">

          {/* Henry's Analysis */}
          <Card className="border-ai-blue/20">
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
                <div className="ai-prose text-sm text-gray-300 leading-relaxed" dangerouslySetInnerHTML={{ __html: renderMarkdown(detail.cached_summary.summary) }} />
              ) : (
                <p className="text-sm text-gray-500">No analysis yet. Click Refresh to generate.</p>
              )}
            </CardContent>
          </Card>

          {/* Bull/Bear Thesis */}
          <Card>
            <CardContent className="pt-5">
              <div className="flex items-center justify-between mb-3">
                <h2 className="text-sm font-semibold text-white" style={FONT_OUTFIT}>Bull / Bear Thesis</h2>
                <Button variant="outline" size="sm" onClick={generateThesis} disabled={thesisLoading}
                  className="text-[10px] h-7 text-ai-blue border-ai-blue/30 hover:bg-ai-blue/10">
                  {thesisLoading ? "Generating..." : thesisData ? "Regenerate" : "Generate"}
                </Button>
              </div>
              {thesisData ? (
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                  <div className="p-3 rounded-lg bg-profit/5 border border-profit/20">
                    <h3 className="text-xs font-semibold text-profit mb-2">Bull Case</h3>
                    <p className="text-xs text-gray-400 leading-relaxed">{thesisData.bull_case}</p>
                    {thesisData.key_catalysts?.length > 0 && (
                      <div className="mt-2">
                        <span className="text-[9px] text-gray-500">Catalysts:</span>
                        <div className="flex flex-wrap gap-1 mt-1">
                          {thesisData.key_catalysts.map((c: string, i: number) => (
                            <Badge key={i} className="text-[8px] bg-profit/10 text-profit">{c}</Badge>
                          ))}
                        </div>
                      </div>
                    )}
                  </div>
                  <div className="p-3 rounded-lg bg-loss/5 border border-loss/20">
                    <h3 className="text-xs font-semibold text-loss mb-2">Bear Case</h3>
                    <p className="text-xs text-gray-400 leading-relaxed">{thesisData.bear_case}</p>
                    {thesisData.risk_factors?.length > 0 && (
                      <div className="mt-2">
                        <span className="text-[9px] text-gray-500">Risks:</span>
                        <div className="flex flex-wrap gap-1 mt-1">
                          {thesisData.risk_factors.map((r: string, i: number) => (
                            <Badge key={i} className="text-[8px] bg-loss/10 text-loss">{r}</Badge>
                          ))}
                        </div>
                      </div>
                    )}
                  </div>
                </div>
              ) : (
                <p className="text-xs text-gray-600 text-center py-4">Click Generate for Henry&apos;s bull/bear analysis</p>
              )}
            </CardContent>
          </Card>

          {/* News Headlines */}
          {newsData?.headlines && newsData.headlines.length > 0 && (
            <Card>
              <CardContent className="pt-5">
                <h2 className="text-sm font-semibold text-white mb-3" style={FONT_OUTFIT}>News</h2>
                <div className="space-y-2 max-h-80 overflow-y-auto">
                  {newsData.headlines.slice(0, 10).map((article: import("@/lib/types").NewsArticle, i: number) => (
                    <a key={i} href={article.url || "#"} target="_blank" rel="noopener noreferrer"
                      className="block p-2 rounded-lg hover:bg-surface-light/20 transition">
                      <div className="text-xs text-white">{article.headline}</div>
                      <div className="flex items-center gap-2 mt-1 text-[9px] text-gray-500">
                        {article.source && <span>{article.source}</span>}
                        {article.published_at && <span>{formatTimeAgo(article.published_at)}</span>}
                        {article.sentiment_score != null && Math.abs(article.sentiment_score) > 0.05 && (
                          <Badge className={`text-[8px] ${
                            article.sentiment_score > 0.05 ? "bg-profit/10 text-profit" :
                            article.sentiment_score < -0.05 ? "bg-loss/10 text-loss" :
                            "bg-gray-700/30 text-gray-400"
                          }`}>{article.sentiment_score > 0.05 ? "Bullish" : "Bearish"}</Badge>
                        )}
                      </div>
                    </a>
                  ))}
                </div>
              </CardContent>
            </Card>
          )}
        </div>

        {/* RIGHT COLUMN (2/5) — About + Signals + Positions */}
        <div className="lg:col-span-2 space-y-4">

          {/* About */}
          {(fundamentals?.company_description || fundamentals?.description) && (
            <Card>
              <CardContent className="pt-5">
                <h2 className="text-sm font-semibold text-white mb-2" style={FONT_OUTFIT}>About</h2>
                <CompanyDescription text={fundamentals.company_description || fundamentals.description || ""} />
              </CardContent>
            </Card>
          )}

          {/* Indicator Signals */}
          {detail && detail.latest_signals.length > 0 && (
            <Card>
              <CardContent className="pt-5">
                <h2 className="text-sm font-semibold text-white mb-3" style={FONT_OUTFIT}>Signals</h2>
                <div className="space-y-1.5 max-h-60 overflow-y-auto">
                  {detail.latest_signals.map((s, i) => (
                    <div key={i} className="flex items-center gap-2 text-xs">
                      <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${signalDot(s.signal)}`} />
                      <span className="text-gray-400 font-mono truncate flex-1">{formatIndicator(s.indicator)}</span>
                      <span className={s.signal === "bullish" ? "text-profit" : s.signal === "bearish" ? "text-loss" : "text-gray-500"}>{s.signal}</span>
                      <span className="text-[9px] text-gray-600">{formatTimeAgo(s.created_at)}</span>
                    </div>
                  ))}
                </div>
              </CardContent>
            </Card>
          )}

          {/* Strategy Positions */}
          {detail && detail.strategy_positions.length > 0 && (
            <Card>
              <CardContent className="pt-5">
                <h2 className="text-sm font-semibold text-white mb-3" style={FONT_OUTFIT}>Strategy Positions</h2>
                <div className="space-y-2">
                  {detail.strategy_positions.map((p, i) => (
                    <div key={i} className="flex items-center justify-between text-xs">
                      <div className="flex items-center gap-2">
                        <Badge className={`text-[9px] ${p.direction === "long" ? "bg-profit/15 text-profit" : "bg-loss/15 text-loss"}`}>{p.direction}</Badge>
                        <span className="text-gray-300 font-mono">{p.strategy_name}</span>
                      </div>
                      <div className="text-right font-mono">
                        <span className="text-gray-400">${p.entry_price.toFixed(2)}</span>
                        {p.pnl_pct != null && <span className={`ml-2 ${pnlColor(p.pnl_pct)}`}>{formatPercent(p.pnl_pct)}</span>}
                      </div>
                    </div>
                  ))}
                </div>
              </CardContent>
            </Card>
          )}

          {/* Trade History */}
          {detail && detail.trade_history && detail.trade_history.length > 0 && (
            <Card>
              <CardContent className="pt-5">
                <h2 className="text-sm font-semibold text-white mb-3" style={FONT_OUTFIT}>Recent Trades</h2>
                <div className="space-y-1.5 max-h-48 overflow-y-auto">
                  {detail.trade_history.slice(0, 8).map((t, i) => (
                    <div key={i} className="flex items-center gap-2 text-[10px] font-mono">
                      <Badge className={`text-[8px] ${t.direction === "long" ? "bg-profit/10 text-profit" : "bg-loss/10 text-loss"}`}>{t.direction}</Badge>
                      <span className="text-gray-400">${t.entry_price.toFixed(2)} → ${t.exit_price?.toFixed(2) ?? "?"}</span>
                      <span className={`ml-auto ${pnlColor(t.pnl_pct ?? 0)}`}>{formatPercent(t.pnl_pct ?? 0)}</span>
                    </div>
                  ))}
                </div>
              </CardContent>
            </Card>
          )}
        </div>
      </div>

      {/* ═══ BOTTOM TABS — Monte Carlo + Backtests ═══ */}
      {(backtests.length > 0 || true) && (
        <Card>
          <CardContent className="pt-5">
            <div className="flex items-center gap-4 mb-4 border-b border-border/30 pb-3">
              {["monte-carlo", "backtests"].map(tab => (
                <button key={tab} onClick={() => setMcSource(tab === "monte-carlo" ? mcSource : mcSource)}
                  className="text-xs font-medium text-gray-400 hover:text-white transition" style={FONT_OUTFIT}>
                  {tab === "monte-carlo" ? "Monte Carlo" : "Backtests"}
                </button>
              ))}
            </div>

            {/* Monte Carlo */}
            <div className="space-y-4">
              <div className="flex flex-wrap gap-3 items-end">
                <div>
                  <label className="text-[9px] text-gray-500 block mb-0.5">Source</label>
                  <select value={mcSource} onChange={(e) => setMcSource(e.target.value as typeof mcSource)}
                    className="h-8 rounded border border-border bg-transparent px-2 text-xs text-white">
                    <option value="combined">Combined</option>
                    <option value="live">Live</option>
                    <option value="backtest">Backtest</option>
                  </select>
                </div>
                <div>
                  <label className="text-[9px] text-gray-500 block mb-0.5">Sims</label>
                  <select value={mcSims} onChange={(e) => setMcSims(Number(e.target.value))}
                    className="h-8 rounded border border-border bg-transparent px-2 text-xs text-white">
                    {[500, 1000, 5000].map(n => <option key={n} value={n}>{n}</option>)}
                  </select>
                </div>
                <div>
                  <label className="text-[9px] text-gray-500 block mb-0.5">Trades</label>
                  <select value={mcTrades} onChange={(e) => setMcTrades(Number(e.target.value))}
                    className="h-8 rounded border border-border bg-transparent px-2 text-xs text-white">
                    {[50, 100, 200].map(n => <option key={n} value={n}>{n}</option>)}
                  </select>
                </div>
                <div>
                  <label className="text-[9px] text-gray-500 block mb-0.5">Capital</label>
                  <input type="number" value={mcCapital} onChange={(e) => setMcCapital(Number(e.target.value))}
                    className="h-8 w-24 rounded border border-border bg-transparent px-2 text-xs text-white font-mono" />
                </div>
                <Button size="sm" onClick={runMC} disabled={mcLoading} className="h-8 text-xs bg-ai-blue/20 text-ai-blue border border-ai-blue/30">
                  {mcLoading ? "Running..." : "Run Simulation"}
                </Button>
              </div>

              {mcResults && (
                <div className="space-y-4">
                  {/* Probability Cone */}
                  <div style={{ height: 280 }}>
                    <ResponsiveContainer width="100%" height="100%">
                      <AreaChart data={mcConeData}>
                        <defs>
                          <linearGradient id="mc5" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor="#6366f1" stopOpacity={0.06} /><stop offset="100%" stopColor="#6366f1" stopOpacity={0} /></linearGradient>
                          <linearGradient id="mc25" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor="#6366f1" stopOpacity={0.12} /><stop offset="100%" stopColor="#6366f1" stopOpacity={0} /></linearGradient>
                        </defs>
                        <CartesianGrid strokeDasharray="3 3" stroke="#1f2937" />
                        <XAxis dataKey="trade" tick={{ fill: "#6b7280", fontSize: 9 }} />
                        <YAxis tick={{ fill: "#6b7280", fontSize: 9, ...FONT_MONO }} tickFormatter={(v: number) => `$${(v/1000).toFixed(0)}k`} />
                        <Tooltip contentStyle={CHART_TOOLTIP} />
                        <Area type="monotone" dataKey="p5" stackId="a" stroke="none" fill="url(#mc5)" />
                        <Area type="monotone" dataKey="p25" stackId="b" stroke="none" fill="url(#mc25)" />
                        <Line type="monotone" dataKey="p50" stroke="#fbbf24" strokeWidth={2} dot={false} />
                        <Area type="monotone" dataKey="p75" stackId="c" stroke="none" fill="url(#mc25)" />
                        <Area type="monotone" dataKey="p95" stackId="d" stroke="none" fill="url(#mc5)" />
                        <ReferenceLine y={mcCapital} stroke="#374151" strokeDasharray="4 4" />
                      </AreaChart>
                    </ResponsiveContainer>
                  </div>
                  {/* MC Summary Stats */}
                  <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3">
                    {[
                      ["P(Profit)", `${mcResults.summary?.probability_of_profit?.toFixed(0) ?? "?"}%`, (mcResults.summary?.probability_of_profit ?? 0) >= 50],
                      ["Median Return", `${mcResults.summary?.median_return_pct?.toFixed(1) ?? "?"}%`, (mcResults.summary?.median_return_pct ?? 0) >= 0],
                      ["Median DD", `${mcResults.summary?.median_max_drawdown_pct?.toFixed(1) ?? "?"}%`, false],
                      ["Worst (5th)", `$${mcResults.percentile_bands?.p5?.[mcResults.percentile_bands.p5.length - 1]?.toFixed(0) ?? "?"}`, false],
                      ["Best (95th)", `$${mcResults.percentile_bands?.p95?.[mcResults.percentile_bands.p95.length - 1]?.toFixed(0) ?? "?"}`, true],
                      ["P(Ruin)", `${mcResults.summary?.probability_of_ruin?.toFixed(1) ?? "?"}%`, (mcResults.summary?.probability_of_ruin ?? 100) < 10],
                    ].map(([label, value, good], i) => (
                      <div key={i} className="p-2 rounded-lg bg-surface-light/20 text-center">
                        <div className="text-[8px] text-gray-500 uppercase">{String(label)}</div>
                        <div className={`text-sm font-mono ${good ? "text-profit" : "text-white"}`}>{String(value)}</div>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* Backtests */}
              {backtests.length > 0 && (
                <div className="mt-4 pt-4 border-t border-border/30">
                  <h3 className="text-sm font-semibold text-white mb-3" style={FONT_OUTFIT}>Backtest Data</h3>
                  <div className="space-y-2">
                    {backtests.map((bt) => (
                      <div key={bt.id}>
                        <button onClick={() => loadBtTrades(bt.id)}
                          className="w-full text-left p-3 rounded-lg border border-border/30 hover:border-border/60 transition">
                          <div className="flex items-center justify-between">
                            <span className="text-xs text-white font-medium">{bt.strategy_name}</span>
                            <span className="text-[10px] font-mono text-gray-400">
                              {bt.trade_count} trades | WR {bt.win_rate?.toFixed(1)}% | PF {bt.profit_factor?.toFixed(2)}
                            </span>
                          </div>
                        </button>
                        {expandedBt === bt.id && btTrades[bt.id] && (
                          <div className="mt-1 ml-4 space-y-1 max-h-40 overflow-y-auto">
                            {btTrades[bt.id].map((t, i) => (
                              <div key={i} className="flex items-center gap-2 text-[9px] font-mono text-gray-500">
                                <span>{t.trade_date}</span>
                                <span className={(t.net_pnl_pct ?? 0) >= 0 ? "text-profit" : "text-loss"}>{(t.net_pnl_pct ?? 0) >= 0 ? "+" : ""}{(t.net_pnl_pct ?? 0).toFixed(2)}%</span>
                                <span>{t.type}</span>
                              </div>
                            ))}
                          </div>
                        )}
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
