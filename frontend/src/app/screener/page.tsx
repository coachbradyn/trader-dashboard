"use client";

import { useState, useEffect, useMemo, useCallback, useRef } from "react";
import { api } from "@/lib/api";
import { usePolling } from "@/hooks/usePolling";
import { formatTimeAgo } from "@/lib/formatters";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  Table, TableHeader, TableBody,
  TableHead, TableRow, TableCell,
} from "@/components/ui/table";
import { TooltipProvider } from "@/components/ui/tooltip";
import type {
  TickerAggregation, ChartDataPoint, TickerAnalysis,
} from "@/lib/types";

// ── Fonts ────────────────────────────────────────────────────────────────
function useFonts() {
  useEffect(() => {
    if (document.getElementById("__screener-fonts")) return;
    const link = document.createElement("link");
    link.id = "__screener-fonts";
    link.rel = "stylesheet";
    link.href =
      "https://fonts.googleapis.com/css2?family=Outfit:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500;600;700&display=swap";
    document.head.appendChild(link);
  }, []);
}

// ── Helpers ──────────────────────────────────────────────────────────────
const HOUR_MAP: Record<string, number> = {
  "1H": 1, "4H": 4, "12H": 12, "24H": 24, "7D": 168,
};

function signalBg(signal: string) {
  const s = signal.toLowerCase();
  if (s === "bullish") return "bg-profit/15 text-profit";
  if (s === "bearish") return "bg-loss/15 text-loss";
  return "bg-gray-700/50 text-gray-400";
}

function confidenceColor(c: number) {
  if (c >= 70) return "bg-profit";
  if (c >= 45) return "bg-yellow-500";
  return "bg-loss";
}

function heatLevel(count: number): "cold" | "warm" | "hot" | "fire" {
  if (count >= 10) return "fire";
  if (count >= 6) return "hot";
  if (count >= 3) return "warm";
  return "cold";
}

// ── Heatmap Layout Hook ─────────────────────────────────────────────────
interface HeatmapItem {
  ticker: TickerAggregation;
  colSpan: number;
  rowSpan: number;
  minHeight: number;
  heat: "cold" | "warm" | "hot" | "fire";
  heatIntensity: number;
  tickerSize: string;
  sparklineHeight: number;
  maxIndicators: number;
}

function useHeatmapLayout(tickers: TickerAggregation[]): HeatmapItem[] {
  return useMemo(() => {
    if (!tickers.length) return [];

    const maxAlerts = Math.max(...tickers.map((t) => t.alert_count));
    const minAlerts = Math.min(...tickers.map((t) => t.alert_count));

    return tickers.map((ticker) => {
      const normalized =
        maxAlerts === minAlerts
          ? 0.5
          : (ticker.alert_count - minAlerts) / (maxAlerts - minAlerts);

      const colSpan = ticker.alert_count >= 6 ? 2 : 1;
      const rowSpan = ticker.alert_count >= 10 ? 2 : 1;
      const minHeight = Math.round(120 + normalized * 200);
      const heat = heatLevel(ticker.alert_count);
      const heatIntensity = Math.min(1, normalized * 1.2);

      // Scale typography and chart size with heat
      const tickerSize =
        heat === "fire"
          ? "text-3xl"
          : heat === "hot"
          ? "text-2xl"
          : heat === "warm"
          ? "text-xl"
          : "text-lg";
      const sparklineHeight =
        heat === "fire" ? 72 : heat === "hot" ? 56 : heat === "warm" ? 44 : 36;
      const maxIndicators =
        heat === "fire" ? 10 : heat === "hot" ? 7 : heat === "warm" ? 5 : 3;

      return {
        ticker,
        colSpan,
        rowSpan,
        minHeight,
        heat,
        heatIntensity,
        tickerSize,
        sparklineHeight,
        maxIndicators,
      };
    });
  }, [tickers]);
}

// ── Sparkline ────────────────────────────────────────────────────────────
function Sparkline({
  data,
  height = 48,
}: {
  data: ChartDataPoint[];
  height?: number;
}) {
  if (!data || data.length < 2) return null;
  const closes = data.map((d) => d.close);
  const min = Math.min(...closes);
  const max = Math.max(...closes);
  const range = max - min || 1;
  const w = 200;
  const pts = closes
    .map((c, i) => {
      const x = (i / (closes.length - 1)) * w;
      const y = height - ((c - min) / range) * (height - 4) - 2;
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");
  const up = closes[closes.length - 1] >= closes[0];
  const stroke = up ? "#22c55e" : "#ef4444";
  const gradId = `spark-${up ? "up" : "dn"}-${Math.random().toString(36).slice(2, 6)}`;

  return (
    <svg
      viewBox={`0 0 ${w} ${height}`}
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
        points={`0,${height} ${pts} ${w},${height}`}
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
    </svg>
  );
}

// ── Candlestick Chart ────────────────────────────────────────────────────
function CandlestickChart({
  data,
  alerts,
}: {
  data: ChartDataPoint[];
  alerts: TickerAggregation["alerts"];
}) {
  if (!data || data.length < 2) {
    return (
      <div className="chart-container flex items-center justify-center h-[300px] text-gray-500 text-sm font-mono">
        No chart data
      </div>
    );
  }

  const W = 700;
  const H = 300;
  const pad = { top: 16, right: 16, bottom: 28, left: 56 };
  const iw = W - pad.left - pad.right;
  const ih = H - pad.top - pad.bottom;

  const allPrices = data.flatMap((d) => [d.high, d.low]);
  const pMin = Math.min(...allPrices);
  const pMax = Math.max(...allPrices);
  const pRange = pMax - pMin || 1;

  const yOf = (p: number) => pad.top + ih - ((p - pMin) / pRange) * ih;
  const candleW = Math.max(2, Math.min(10, iw / data.length - 2));

  const alertDates = new Set(alerts.map((a) => a.created_at.slice(0, 10)));

  const yTicks: number[] = [];
  const step = pRange / 5;
  for (let i = 0; i <= 5; i++) yTicks.push(pMin + step * i);

  return (
    <div className="chart-container p-3 rounded-lg">
      <svg
        viewBox={`0 0 ${W} ${H}`}
        className="w-full"
        style={{ height: H }}
      >
        {yTicks.map((t) => (
          <g key={t}>
            <line
              x1={pad.left}
              y1={yOf(t)}
              x2={W - pad.right}
              y2={yOf(t)}
              stroke="rgba(255,255,255,0.04)"
              strokeWidth={1}
            />
            <text
              x={pad.left - 6}
              y={yOf(t) + 3}
              textAnchor="end"
              fill="rgba(255,255,255,0.3)"
              fontSize={9}
              fontFamily="'JetBrains Mono', monospace"
            >
              {t.toFixed(t >= 100 ? 0 : 2)}
            </text>
          </g>
        ))}

        {data.map((d, i) => {
          const x = pad.left + (i / (data.length - 1)) * iw;
          const up = d.close >= d.open;
          const color = up ? "#22c55e" : "#ef4444";
          const bodyTop = yOf(Math.max(d.open, d.close));
          const bodyBot = yOf(Math.min(d.open, d.close));
          const bodyH = Math.max(1, bodyBot - bodyTop);
          const dateStr = d.date.slice(0, 10);
          const hasAlert = alertDates.has(dateStr);

          return (
            <g key={i}>
              <line
                x1={x}
                y1={yOf(d.high)}
                x2={x}
                y2={yOf(d.low)}
                stroke={color}
                strokeWidth={1}
              />
              <rect
                x={x - candleW / 2}
                y={bodyTop}
                width={candleW}
                height={bodyH}
                fill={color}
                rx={1}
                opacity={0.9}
              />
              {hasAlert && (
                <polygon
                  points={`${x},${yOf(d.high) - 10} ${x - 4},${yOf(d.high) - 4} ${x + 4},${yOf(d.high) - 4}`}
                  fill="#fbbf24"
                  opacity={0.85}
                />
              )}
            </g>
          );
        })}

        {data.map((d, i) => {
          if (
            data.length <= 10 ||
            i % Math.ceil(data.length / 7) === 0
          ) {
            const x = pad.left + (i / (data.length - 1)) * iw;
            return (
              <text
                key={`xl-${i}`}
                x={x}
                y={H - 6}
                textAnchor="middle"
                fill="rgba(255,255,255,0.25)"
                fontSize={8}
                fontFamily="'JetBrains Mono', monospace"
              >
                {new Date(d.date).toLocaleDateString("en-US", {
                  month: "short",
                  day: "numeric",
                })}
              </text>
            );
          }
          return null;
        })}
      </svg>
    </div>
  );
}

// ── Heatmap Card ─────────────────────────────────────────────────────────
function HeatmapCard({
  item,
  index,
  isSelected,
  onClick,
}: {
  item: HeatmapItem;
  index: number;
  isSelected: boolean;
  onClick: () => void;
}) {
  const { ticker, colSpan, rowSpan, minHeight, heat, heatIntensity, tickerSize, sparklineHeight, maxIndicators } = item;
  const [chartData, setChartData] = useState<ChartDataPoint[] | null>(null);
  const [hovered, setHovered] = useState(false);
  const fetchedRef = useRef(false);

  // Lazy-load sparkline: immediately for hot/fire, on hover for others
  useEffect(() => {
    if ((heat === "hot" || heat === "fire") && !fetchedRef.current) {
      fetchedRef.current = true;
      api
        .getScreenerChart(ticker.ticker, 30)
        .then(setChartData)
        .catch(() => {});
    }
  }, [ticker.ticker, heat]);

  useEffect(() => {
    if (hovered && !fetchedRef.current) {
      fetchedRef.current = true;
      api
        .getScreenerChart(ticker.ticker, 30)
        .then(setChartData)
        .catch(() => {});
    }
  }, [hovered, ticker.ticker]);

  const alertsToShow = ticker.alerts.slice(0, maxIndicators);
  const remaining = ticker.alerts.length - alertsToShow.length;

  return (
    <div
      className={`heatmap-card heatmap-card-grow cursor-pointer group ${
        isSelected ? "" : ""
      }`}
      data-heat={isSelected ? undefined : heat}
      data-selected={isSelected ? "true" : undefined}
      style={
        {
          gridColumn: `span ${colSpan}`,
          gridRow: `span ${rowSpan}`,
          minHeight: `${minHeight}px`,
          "--heat-intensity": heatIntensity,
          animationDelay: `${index * 50}ms`,
          fontFamily: "'Outfit', sans-serif",
        } as React.CSSProperties
      }
      onClick={onClick}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => e.key === "Enter" && onClick()}
    >
      <div className="p-4 flex flex-col h-full relative z-10">
        {/* Top: ticker + signal + count */}
        <div className="flex items-center justify-between mb-2">
          <div className="flex items-center gap-2.5">
            <span
              className={`${tickerSize} font-bold tracking-tight text-white`}
              style={{ fontFamily: "'JetBrains Mono', monospace" }}
            >
              {ticker.ticker}
            </span>
            <Badge className={signalBg(ticker.latest_signal)}>
              {ticker.latest_signal}
            </Badge>
          </div>
          <div className="flex items-center gap-1.5">
            <div
              className={`flex items-center justify-center rounded-full ${
                heat === "fire"
                  ? "w-10 h-10 bg-screener-amber/15 ring-2 ring-screener-amber/30"
                  : heat === "hot"
                  ? "w-9 h-9 bg-screener-amber/10 ring-1 ring-screener-amber/20"
                  : "w-8 h-8"
              }`}
            >
              <span
                className={`font-bold tabular-nums text-screener-amber ${
                  heat === "fire" ? "text-xl" : heat === "hot" ? "text-lg" : "text-base"
                }`}
                style={{ fontFamily: "'JetBrains Mono', monospace" }}
              >
                {ticker.alert_count}
              </span>
            </div>
          </div>
        </div>

        {/* Indicators */}
        <div className="space-y-1 flex-1 mb-2">
          {heat === "cold" ? (
            // Compact single-line for cold cards
            <div className="flex items-center gap-2 text-xs">
              <span className="font-mono text-amber-300/70">
                {ticker.alerts[0]?.indicator}
              </span>
              <span className="font-mono tabular-nums text-gray-300">
                {ticker.alerts[0]?.value.toFixed(2)}
              </span>
              <span className="text-gray-600 ml-auto font-mono text-[10px]">
                {formatTimeAgo(ticker.latest_alert_at)}
              </span>
            </div>
          ) : (
            // Stacked rows for warm/hot/fire
            <>
              {alertsToShow.map((a) => (
                <div
                  key={a.id}
                  className="flex items-center gap-2 text-xs py-1 px-2 rounded-md bg-surface-light/20 hover:bg-surface-light/40 transition"
                >
                  <span className="font-mono text-amber-300/70 w-28 truncate">
                    {a.indicator}
                  </span>
                  <span
                    className="font-mono tabular-nums text-gray-200"
                    style={{ fontFamily: "'JetBrains Mono', monospace" }}
                  >
                    {a.value.toFixed(2)}
                  </span>
                  <Badge
                    className={`${signalBg(a.signal)} text-[10px] px-1.5 py-0`}
                  >
                    {a.signal}
                  </Badge>
                  {a.timeframe && (
                    <span className="text-gray-600 text-[10px]">
                      {a.timeframe}
                    </span>
                  )}
                  <span className="text-gray-600 ml-auto text-[10px] font-mono">
                    {formatTimeAgo(a.created_at)}
                  </span>
                </div>
              ))}
              {remaining > 0 && (
                <p className="text-[10px] text-gray-600 font-mono pl-2">
                  +{remaining} more
                </p>
              )}
            </>
          )}
        </div>

        {/* Sparkline */}
        <div className="overflow-hidden rounded-md bg-surface-light/10 mt-auto">
          {chartData ? (
            <Sparkline data={chartData} height={sparklineHeight} />
          ) : (
            <div
              className="w-full bg-surface-light/5"
              style={{ height: sparklineHeight }}
            />
          )}
        </div>

        {/* Indicator badge strip */}
        <div className="flex flex-wrap gap-1 mt-2">
          {ticker.indicators.slice(0, heat === "cold" ? 3 : 6).map((ind) => (
            <span
              key={ind}
              className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-amber-500/8 text-amber-400/60 border border-amber-500/10"
            >
              {ind}
            </span>
          ))}
          {/* Timeframe dots */}
          <div className="flex items-center gap-0.5 ml-auto">
            {ticker.alerts
              .reduce((tfs: string[], a) => {
                if (a.timeframe && !tfs.includes(a.timeframe))
                  tfs.push(a.timeframe);
                return tfs;
              }, [])
              .slice(0, 4)
              .map((tf) => (
                <span
                  key={tf}
                  className="text-[8px] font-mono text-gray-600 px-1"
                >
                  {tf}
                </span>
              ))}
          </div>
        </div>
      </div>
    </div>
  );
}

// ── Analysis Loading Skeleton ────────────────────────────────────────────
function AnalysisLoadingSkeleton() {
  return (
    <div className="analysis-panel p-6 space-y-6 animate-slide-up-panel">
      {/* Header */}
      <div className="flex items-center gap-4">
        <div className="analysis-skeleton h-7 w-24 rounded-md" />
        <div className="analysis-skeleton h-8 w-20 rounded-md" />
        <div className="analysis-skeleton h-6 w-16 rounded-md" />
        <div className="analysis-skeleton h-5 w-48 ml-auto rounded-md" />
      </div>
      {/* Thesis */}
      <div className="space-y-2">
        <div className="analysis-skeleton h-4 w-full rounded" />
        <div className="analysis-skeleton h-4 w-3/4 rounded" />
      </div>
      {/* Levels */}
      <div className="grid grid-cols-4 gap-3">
        {[0, 1, 2, 3].map((i) => (
          <div
            key={i}
            className="analysis-skeleton h-20 rounded-lg"
            style={{ animationDelay: `${i * 150}ms` }}
          />
        ))}
      </div>
      {/* Chart */}
      <div className="analysis-skeleton h-[300px] w-full rounded-lg" />
      {/* Strategy grid */}
      <div className="grid grid-cols-4 gap-3">
        {[0, 1, 2, 3].map((i) => (
          <div
            key={i}
            className="analysis-skeleton h-24 rounded-lg"
            style={{ animationDelay: `${i * 100}ms` }}
          />
        ))}
      </div>
    </div>
  );
}

// ── Analysis Panel ───────────────────────────────────────────────────────
function AnalysisPanel({
  analysis,
  tickerData,
  onClose,
}: {
  analysis: TickerAnalysis;
  tickerData: TickerAggregation;
  onClose: () => void;
}) {
  const [chartData, setChartData] = useState<ChartDataPoint[] | null>(null);
  const [chartLoading, setChartLoading] = useState(true);

  useEffect(() => {
    setChartLoading(true);
    api
      .getScreenerChart(analysis.ticker, 60)
      .then(setChartData)
      .catch(() => setChartData(null))
      .finally(() => setChartLoading(false));
  }, [analysis.ticker]);

  const playBadgeClass =
    analysis.play_type === "WEEKLY" ? "play-badge-weekly" : "play-badge-daily";

  return (
    <div
      className="animate-slide-up-panel"
      style={{ fontFamily: "'Outfit', sans-serif" }}
    >
      <div className="ai-gradient-border">
        <div className="analysis-panel p-6 space-y-6">
          {/* ── Header ─────────────────────────────── */}
          <div className="flex items-center justify-between flex-wrap gap-3">
            <div className="flex items-center gap-3">
              <span className={playBadgeClass}>{analysis.play_type} Play</span>
              <h2
                className="text-2xl font-extrabold tracking-tight text-white"
                style={{ fontFamily: "'JetBrains Mono', monospace" }}
              >
                {analysis.ticker}
              </h2>
              <Badge
                className={
                  analysis.direction === "LONG"
                    ? "bg-profit/20 text-profit font-semibold text-sm px-3"
                    : "bg-loss/20 text-loss font-semibold text-sm px-3"
                }
              >
                {analysis.direction}
              </Badge>
            </div>
            <div className="flex items-center gap-4">
              {/* Confidence gauge */}
              <div className="flex items-center gap-3">
                <span className="text-[10px] text-gray-500 uppercase tracking-wider font-semibold">
                  Confidence
                </span>
                <div className="w-32 h-2 bg-surface-light rounded-full overflow-hidden">
                  <div
                    className={`h-full rounded-full ${confidenceColor(analysis.confidence)} animate-gauge-fill`}
                    style={
                      {
                        "--gauge-width": `${analysis.confidence}%`,
                        width: `${analysis.confidence}%`,
                      } as React.CSSProperties
                    }
                  />
                </div>
                <span
                  className="text-sm font-bold font-mono text-white"
                  style={{ fontFamily: "'JetBrains Mono', monospace" }}
                >
                  {analysis.confidence}%
                </span>
              </div>
              {/* Close */}
              <button
                onClick={onClose}
                className="w-8 h-8 rounded-lg bg-surface-light/60 hover:bg-surface-light flex items-center justify-center text-gray-400 hover:text-white transition"
              >
                <svg
                  width={14}
                  height={14}
                  viewBox="0 0 14 14"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth={2}
                >
                  <path d="M1 1l12 12M13 1L1 13" />
                </svg>
              </button>
            </div>
          </div>

          {/* ── Thesis ─────────────────────────────── */}
          <p className="text-sm text-gray-300 leading-relaxed max-w-3xl">
            {analysis.thesis}
          </p>

          {/* ── Signal breakdown strip ─────────────── */}
          <div className="flex items-center gap-3 text-xs">
            <span className="text-gray-500 uppercase tracking-wider font-semibold text-[10px]">
              Signals
            </span>
            <div className="flex items-center gap-1.5">
              <span className="px-2 py-0.5 rounded bg-profit/15 text-profit font-mono font-semibold">
                {analysis.signal_breakdown.bullish} bullish
              </span>
              <span className="px-2 py-0.5 rounded bg-loss/15 text-loss font-mono font-semibold">
                {analysis.signal_breakdown.bearish} bearish
              </span>
              {analysis.signal_breakdown.neutral > 0 && (
                <span className="px-2 py-0.5 rounded bg-gray-700/50 text-gray-400 font-mono">
                  {analysis.signal_breakdown.neutral} neutral
                </span>
              )}
            </div>
            <span className="text-gray-600 mx-1">|</span>
            <span className="text-gray-400 font-mono">
              {analysis.alert_timeline_summary}
            </span>
          </div>

          {/* ── Levels Grid ────────────────────────── */}
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            <div className="rounded-lg bg-surface-light/20 border border-border/30 p-4 text-center">
              <p className="text-[10px] text-gray-500 uppercase tracking-wider mb-1">
                Entry Zone
              </p>
              <p
                className="text-base font-semibold text-white font-mono"
                style={{ fontFamily: "'JetBrains Mono', monospace" }}
              >
                {analysis.entry_zone}
              </p>
            </div>
            <div className="rounded-lg bg-surface-light/20 border border-profit/15 p-4 text-center">
              <p className="text-[10px] text-profit/70 uppercase tracking-wider mb-1">
                Target
              </p>
              <p
                className="text-base font-semibold text-profit font-mono"
                style={{ fontFamily: "'JetBrains Mono', monospace" }}
              >
                {analysis.price_target}
              </p>
            </div>
            <div className="rounded-lg bg-surface-light/20 border border-loss/15 p-4 text-center">
              <p className="text-[10px] text-loss/70 uppercase tracking-wider mb-1">
                Stop Loss
              </p>
              <p
                className="text-base font-semibold text-loss font-mono"
                style={{ fontFamily: "'JetBrains Mono', monospace" }}
              >
                {analysis.stop_loss}
              </p>
            </div>
            <div className="rounded-lg bg-surface-light/20 border border-border/30 p-4 text-center">
              <p className="text-[10px] text-gray-500 uppercase tracking-wider mb-1">
                Risk / Reward
              </p>
              <p
                className="text-base font-semibold text-white font-mono"
                style={{ fontFamily: "'JetBrains Mono', monospace" }}
              >
                {analysis.risk_reward}
              </p>
            </div>
          </div>

          {/* ── Candlestick Chart ──────────────────── */}
          {chartLoading ? (
            <Skeleton className="h-[300px] w-full rounded-lg" />
          ) : chartData && chartData.length > 0 ? (
            <CandlestickChart data={chartData} alerts={tickerData.alerts} />
          ) : (
            <div className="chart-container h-[300px] flex items-center justify-center text-gray-600 text-sm">
              Chart data unavailable
            </div>
          )}

          {/* ── Historical Patterns ────────────────── */}
          {analysis.historical_matches.length > 0 && (
            <div className="space-y-3">
              <h3
                className="text-sm font-semibold text-gray-300 uppercase tracking-wider"
                style={{ fontFamily: "'Outfit', sans-serif" }}
              >
                Historical Patterns
              </h3>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                {analysis.historical_matches.map((match, i) => (
                  <div
                    key={i}
                    className="rounded-lg bg-surface-light/20 border border-border/30 p-4 space-y-2 animate-scale-in"
                    style={{ animationDelay: `${i * 80 + 200}ms` }}
                  >
                    <p className="text-xs font-mono text-amber-300/70 leading-relaxed">
                      {match.pattern}
                    </p>
                    <div className="grid grid-cols-4 gap-2 text-center">
                      <div>
                        <p className="stat-label text-[9px]">Occurrences</p>
                        <p
                          className="text-sm font-mono font-bold text-white"
                          style={{
                            fontFamily: "'JetBrains Mono', monospace",
                          }}
                        >
                          {match.occurrences}
                        </p>
                      </div>
                      <div>
                        <p className="stat-label text-[9px]">Avg Return</p>
                        <p
                          className={`text-sm font-mono font-bold ${
                            match.avg_return_pct >= 0
                              ? "text-profit"
                              : "text-loss"
                          }`}
                          style={{
                            fontFamily: "'JetBrains Mono', monospace",
                          }}
                        >
                          {match.avg_return_pct >= 0 ? "+" : ""}
                          {match.avg_return_pct.toFixed(1)}%
                        </p>
                      </div>
                      <div>
                        <p className="stat-label text-[9px]">Win Rate</p>
                        <p
                          className="text-sm font-mono font-bold text-white"
                          style={{
                            fontFamily: "'JetBrains Mono', monospace",
                          }}
                        >
                          {match.win_rate.toFixed(0)}%
                        </p>
                      </div>
                      <div>
                        <p className="stat-label text-[9px]">Avg Hold</p>
                        <p
                          className="text-sm font-mono font-bold text-gray-300"
                          style={{
                            fontFamily: "'JetBrains Mono', monospace",
                          }}
                        >
                          {match.avg_bars_held}b
                        </p>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* ── Strategy Alignment ─────────────────── */}
          {analysis.strategy_alignment.length > 0 && (
            <div className="space-y-3">
              <h3
                className="text-sm font-semibold text-gray-300 uppercase tracking-wider"
                style={{ fontFamily: "'Outfit', sans-serif" }}
              >
                Strategy Alignment
              </h3>
              <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
                {analysis.strategy_alignment.map((sa, i) => (
                  <div
                    key={sa.strategy_id}
                    className={`rounded-lg p-3 space-y-1.5 animate-scale-in ${
                      sa.signal_agrees
                        ? "strategy-agrees"
                        : sa.has_active_position
                        ? "strategy-disagrees"
                        : "strategy-neutral"
                    }`}
                    style={{ animationDelay: `${i * 60 + 300}ms` }}
                  >
                    <div className="flex items-center justify-between">
                      <span className="text-xs font-semibold">
                        {sa.strategy_name}
                      </span>
                      {sa.has_active_position ? (
                        <span className="w-2 h-2 rounded-full bg-current animate-pulse" />
                      ) : (
                        <span className="w-2 h-2 rounded-full bg-gray-600" />
                      )}
                    </div>
                    {sa.has_active_position && sa.position_direction && (
                      <p className="text-[10px] font-mono uppercase tracking-wider opacity-80">
                        {sa.position_direction}
                      </p>
                    )}
                    <p className="text-[10px] opacity-70 leading-relaxed">
                      {sa.notes}
                    </p>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* ── Alert Timeline ─────────────────────── */}
          <div className="space-y-3">
            <div className="flex items-center justify-between">
              <h3
                className="text-sm font-semibold text-gray-300 uppercase tracking-wider"
                style={{ fontFamily: "'Outfit', sans-serif" }}
              >
                Alert History
              </h3>
              <div className="flex flex-wrap gap-1">
                {analysis.timeframes_represented.map((tf) => (
                  <span
                    key={tf}
                    className="text-[9px] font-mono px-1.5 py-0.5 rounded bg-surface-light/40 text-gray-400"
                  >
                    {tf}
                  </span>
                ))}
              </div>
            </div>
            <div className="rounded-lg border border-border overflow-hidden">
              <Table>
                <TableHeader>
                  <TableRow className="bg-surface-light/30">
                    <TableHead>Indicator</TableHead>
                    <TableHead>Value</TableHead>
                    <TableHead>Signal</TableHead>
                    <TableHead>Timeframe</TableHead>
                    <TableHead>Time</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {tickerData.alerts.map((a) => (
                    <TableRow key={a.id}>
                      <TableCell className="font-mono text-xs text-amber-300/80">
                        {a.indicator}
                      </TableCell>
                      <TableCell className="font-mono text-xs tabular-nums">
                        {a.value.toFixed(2)}
                      </TableCell>
                      <TableCell>
                        <Badge className={signalBg(a.signal)}>
                          {a.signal}
                        </Badge>
                      </TableCell>
                      <TableCell className="text-xs text-gray-500">
                        {a.timeframe || "--"}
                      </TableCell>
                      <TableCell className="text-xs text-gray-500 font-mono">
                        {formatTimeAgo(a.created_at)}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          </div>

          {/* ── Footer ─────────────────────────────── */}
          <div className="flex items-center justify-between text-[10px] text-gray-600 pt-2 border-t border-border/30">
            <span className="font-mono">
              {analysis.indicators_firing.length} indicators |{" "}
              {tickerData.alert_count} alerts analyzed
            </span>
            <span className="font-mono">
              Generated {formatTimeAgo(analysis.generated_at)}
            </span>
          </div>
        </div>
      </div>
    </div>
  );
}

// ── Filter Bar ───────────────────────────────────────────────────────────
function FilterBar({
  timeRange,
  setTimeRange,
  signalFilter,
  setSignalFilter,
  search,
  setSearch,
}: {
  timeRange: string;
  setTimeRange: (v: string) => void;
  signalFilter: string;
  setSignalFilter: (v: string) => void;
  search: string;
  setSearch: (v: string) => void;
}) {
  return (
    <div
      className="sticky top-14 z-40 bg-background/80 backdrop-blur-lg border-b border-border/50 -mx-4 px-4 py-3 animate-fade-in"
      style={{ fontFamily: "'Outfit', sans-serif" }}
    >
      <div className="flex flex-wrap items-center gap-4">
        <Tabs value={timeRange} onValueChange={setTimeRange}>
          <TabsList className="bg-surface/80">
            {Object.keys(HOUR_MAP).map((k) => (
              <TabsTrigger
                key={k}
                value={k}
                className="data-[state=active]:bg-screener-amber data-[state=active]:text-black text-[11px]"
              >
                {k}
              </TabsTrigger>
            ))}
          </TabsList>
        </Tabs>

        <Tabs value={signalFilter} onValueChange={setSignalFilter}>
          <TabsList className="bg-surface/80">
            <TabsTrigger value="all" className="text-[11px]">
              All
            </TabsTrigger>
            <TabsTrigger
              value="bullish"
              className="data-[state=active]:bg-profit data-[state=active]:text-black text-[11px]"
            >
              Bullish
            </TabsTrigger>
            <TabsTrigger
              value="bearish"
              className="data-[state=active]:bg-loss data-[state=active]:text-white text-[11px]"
            >
              Bearish
            </TabsTrigger>
          </TabsList>
        </Tabs>

        <div className="relative ml-auto w-56">
          <svg
            className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-gray-500"
            fill="none"
            viewBox="0 0 24 24"
            stroke="currentColor"
            strokeWidth={2.5}
          >
            <circle cx={11} cy={11} r={7} />
            <path d="M21 21l-4.35-4.35" strokeLinecap="round" />
          </svg>
          <Input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search ticker..."
            className="pl-8 h-8 text-xs font-mono bg-surface/60 border-border/50"
          />
        </div>
      </div>
    </div>
  );
}

// ── Loading Skeleton ─────────────────────────────────────────────────────
function LoadingSkeleton() {
  return (
    <div className="space-y-6">
      <div className="flex items-center gap-4 py-3">
        <Skeleton className="h-8 w-48" />
        <Skeleton className="h-8 w-36" />
        <Skeleton className="h-8 w-56 ml-auto" />
      </div>
      <div
        className="grid gap-3"
        style={{
          gridTemplateColumns: "repeat(auto-fill, minmax(280px, 1fr))",
          gridAutoRows: "minmax(120px, auto)",
        }}
      >
        {Array.from({ length: 8 }).map((_, i) => (
          <div
            key={i}
            className="heatmap-card p-4 space-y-3"
            data-heat="cold"
            style={{
              animationDelay: `${i * 60}ms`,
              gridColumn: i === 0 ? "span 2" : "span 1",
              minHeight: i === 0 ? "280px" : "140px",
            }}
          >
            <div className="flex items-center justify-between">
              <Skeleton className="h-7 w-20" />
              <Skeleton className="h-6 w-10 rounded-full" />
            </div>
            <Skeleton className="h-4 w-full" />
            <Skeleton className="h-4 w-3/4" />
            <Skeleton className="h-10 w-full mt-auto" />
          </div>
        ))}
      </div>
    </div>
  );
}

// ── Empty State ──────────────────────────────────────────────────────────
function EmptyState() {
  return (
    <div
      className="flex flex-col items-center justify-center py-24 text-center animate-fade-in"
      style={{ fontFamily: "'Outfit', sans-serif" }}
    >
      <div className="w-16 h-16 rounded-2xl bg-surface-light/30 flex items-center justify-center mb-5">
        <svg
          className="w-8 h-8 text-gray-600"
          fill="none"
          viewBox="0 0 24 24"
          stroke="currentColor"
          strokeWidth={1.5}
        >
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            d="M3.75 3v11.25A2.25 2.25 0 006 16.5h2.25M3.75 3h-1.5m1.5 0h16.5m0 0h1.5m-1.5 0v11.25A2.25 2.25 0 0118 16.5h-2.25m-7.5 0h7.5m-7.5 0l-1 3m8.5-3l1 3m0 0l.5 1.5m-.5-1.5h-9.5m0 0l-.5 1.5M9 11.25v1.5M12 9v3.75m3-6v6"
          />
        </svg>
      </div>
      <h3 className="text-lg font-semibold text-gray-300 mb-1">
        No alerts detected
      </h3>
      <p className="text-sm text-gray-600 max-w-sm">
        The screener is listening for indicator alerts. When TradingView signals
        fire, ticker cards will appear here.
      </p>
    </div>
  );
}

// ── Page Header ──────────────────────────────────────────────────────────
function PageHeader({
  tickerCount,
  alertCount,
  loading,
}: {
  tickerCount: number;
  alertCount: number;
  loading?: boolean;
}) {
  return (
    <div
      className="flex items-center gap-3 animate-fade-in"
      style={{ fontFamily: "'Outfit', sans-serif" }}
    >
      <div className="w-10 h-10 rounded-xl bg-screener-amber/10 flex items-center justify-center">
        <svg
          className="w-5 h-5 text-screener-amber"
          fill="none"
          viewBox="0 0 24 24"
          stroke="currentColor"
          strokeWidth={2}
        >
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            d="M3.75 3v11.25A2.25 2.25 0 006 16.5h2.25M3.75 3h-1.5m1.5 0h16.5m0 0h1.5m-1.5 0v11.25A2.25 2.25 0 0118 16.5h-2.25m-7.5 0h7.5m-7.5 0l-1 3m8.5-3l1 3m0 0l.5 1.5m-.5-1.5h-9.5m0 0l-.5 1.5M9 11.25v1.5M12 9v3.75m3-6v6"
          />
        </svg>
      </div>
      <div>
        <h1 className="text-xl font-bold text-white tracking-tight">
          Screener
        </h1>
        <p className="text-xs text-gray-500">
          {loading ? (
            <span className="inline-block w-40 h-3 bg-surface-light rounded animate-pulse" />
          ) : (
            <>
              <span className="font-mono text-screener-amber font-semibold">
                {tickerCount}
              </span>{" "}
              ticker{tickerCount !== 1 ? "s" : ""} alerting
              <span className="mx-2 text-gray-700">|</span>
              <span className="font-mono text-gray-400">{alertCount}</span>{" "}
              signals
            </>
          )}
        </p>
      </div>
      <div className="ml-auto flex items-center gap-2">
        <span className="w-2 h-2 rounded-full bg-profit animate-pulse" />
        <span
          className="text-[10px] text-gray-500 uppercase tracking-wider font-mono"
          style={{ fontFamily: "'JetBrains Mono', monospace" }}
        >
          Live
        </span>
      </div>
    </div>
  );
}

// ═════════════════════════════════════════════════════════════════════════
// MAIN PAGE
// ═════════════════════════════════════════════════════════════════════════
export default function ScreenerPage() {
  useFonts();

  // ── State ──
  const [timeRange, setTimeRange] = useState("24H");
  const [signalFilter, setSignalFilter] = useState("all");
  const [search, setSearch] = useState("");
  const [expandedTicker, setExpandedTicker] = useState<string | null>(null);
  const [analysis, setAnalysis] = useState<TickerAnalysis | null>(null);
  const [analysisLoading, setAnalysisLoading] = useState(false);
  const [analysisError, setAnalysisError] = useState<string | null>(null);
  const analysisPanelRef = useRef<HTMLDivElement>(null);
  const abortRef = useRef<AbortController | null>(null);

  // ── Data fetching ──
  const hours = HOUR_MAP[timeRange] || 24;

  const tickerFetcher = useCallback(
    () => api.getScreenerTickers(hours),
    [hours]
  );

  const {
    data: tickers,
    loading: tickersLoading,
    error: tickersError,
  } = usePolling<TickerAggregation[]>(tickerFetcher, 30_000);

  // ── Filtering ──
  const filteredTickers = useMemo(() => {
    if (!tickers) return [];
    let list = [...tickers];

    if (signalFilter !== "all") {
      list = list.filter(
        (t) => t.latest_signal.toLowerCase() === signalFilter
      );
    }

    if (search.trim()) {
      const q = search.trim().toUpperCase();
      list = list.filter((t) => t.ticker.toUpperCase().includes(q));
    }

    list.sort((a, b) => b.alert_count - a.alert_count);
    return list;
  }, [tickers, signalFilter, search]);

  // ── Heatmap layout ──
  const heatmapItems = useHeatmapLayout(filteredTickers);

  // ── Totals ──
  const totalAlerts = useMemo(
    () => filteredTickers.reduce((sum, t) => sum + t.alert_count, 0),
    [filteredTickers]
  );

  // ── Card click → trigger analysis ──
  const handleCardClick = useCallback(
    (tickerSymbol: string) => {
      // Toggle off if same card
      if (expandedTicker === tickerSymbol) {
        setExpandedTicker(null);
        setAnalysis(null);
        setAnalysisError(null);
        return;
      }

      // Cancel any in-flight request
      if (abortRef.current) {
        abortRef.current.abort();
      }
      const controller = new AbortController();
      abortRef.current = controller;

      setExpandedTicker(tickerSymbol);
      setAnalysis(null);
      setAnalysisLoading(true);
      setAnalysisError(null);

      api
        .analyzeScreenerTicker(tickerSymbol, hours)
        .then((result) => {
          if (!controller.signal.aborted) {
            setAnalysis(result);
          }
        })
        .catch((err) => {
          if (!controller.signal.aborted) {
            setAnalysisError(err.message || "Analysis failed");
          }
        })
        .finally(() => {
          if (!controller.signal.aborted) {
            setAnalysisLoading(false);
          }
        });
    },
    [expandedTicker, hours]
  );

  // ── Scroll to analysis panel on open ──
  useEffect(() => {
    if (expandedTicker && analysisPanelRef.current) {
      setTimeout(() => {
        analysisPanelRef.current?.scrollIntoView({
          behavior: "smooth",
          block: "start",
        });
      }, 100);
    }
  }, [expandedTicker, analysis, analysisLoading]);

  // ── Render ──
  if (tickersLoading && !tickers) {
    return (
      <div
        className="space-y-6"
        style={{ fontFamily: "'Outfit', sans-serif" }}
      >
        <PageHeader tickerCount={0} alertCount={0} loading />
        <LoadingSkeleton />
      </div>
    );
  }

  return (
    <TooltipProvider>
      <div
        className="space-y-5"
        style={{ fontFamily: "'Outfit', sans-serif" }}
      >
        {/* Page Header */}
        <PageHeader
          tickerCount={filteredTickers.length}
          alertCount={totalAlerts}
        />

        {/* Filter Bar */}
        <FilterBar
          timeRange={timeRange}
          setTimeRange={setTimeRange}
          signalFilter={signalFilter}
          setSignalFilter={setSignalFilter}
          search={search}
          setSearch={setSearch}
        />

        {/* Card Grid or Empty */}
        {filteredTickers.length === 0 ? (
          <EmptyState />
        ) : (
          <>
            {/* Heatmap Grid */}
            <div
              className="grid gap-3"
              style={{
                gridTemplateColumns:
                  "repeat(auto-fill, minmax(280px, 1fr))",
                gridAutoRows: "minmax(120px, auto)",
                gridAutoFlow: "dense",
              }}
            >
              {heatmapItems.map((item, i) => (
                <HeatmapCard
                  key={item.ticker.ticker}
                  item={item}
                  index={i}
                  isSelected={expandedTicker === item.ticker.ticker}
                  onClick={() => handleCardClick(item.ticker.ticker)}
                />
              ))}
            </div>

            {/* Analysis Panel */}
            {expandedTicker && (
              <div ref={analysisPanelRef} className="mt-2">
                {analysisLoading ? (
                  <AnalysisLoadingSkeleton />
                ) : analysisError ? (
                  <div
                    className="analysis-panel p-6 animate-slide-up-panel"
                    style={{ fontFamily: "'Outfit', sans-serif" }}
                  >
                    <div className="flex items-center justify-between">
                      <div className="flex items-center gap-3">
                        <div className="w-9 h-9 rounded-lg bg-loss/15 flex items-center justify-center">
                          <svg
                            className="w-5 h-5 text-loss"
                            fill="none"
                            viewBox="0 0 24 24"
                            stroke="currentColor"
                            strokeWidth={2}
                          >
                            <path
                              strokeLinecap="round"
                              strokeLinejoin="round"
                              d="M12 9v3.75m9-.75a9 9 0 11-18 0 9 9 0 0118 0zm-9 3.75h.008v.008H12v-.008z"
                            />
                          </svg>
                        </div>
                        <div>
                          <p className="text-sm font-semibold text-white">
                            Analysis unavailable
                          </p>
                          <p className="text-xs text-gray-500">
                            {analysisError}
                          </p>
                        </div>
                      </div>
                      <div className="flex items-center gap-2">
                        <button
                          onClick={() => handleCardClick(expandedTicker)}
                          className="text-xs px-3 py-1.5 rounded-lg bg-surface-light/60 hover:bg-surface-light text-gray-300 hover:text-white transition font-mono"
                        >
                          Retry
                        </button>
                        <button
                          onClick={() => {
                            setExpandedTicker(null);
                            setAnalysisError(null);
                          }}
                          className="w-8 h-8 rounded-lg bg-surface-light/60 hover:bg-surface-light flex items-center justify-center text-gray-400 hover:text-white transition"
                        >
                          <svg
                            width={14}
                            height={14}
                            viewBox="0 0 14 14"
                            fill="none"
                            stroke="currentColor"
                            strokeWidth={2}
                          >
                            <path d="M1 1l12 12M13 1L1 13" />
                          </svg>
                        </button>
                      </div>
                    </div>
                  </div>
                ) : analysis ? (
                  <AnalysisPanel
                    analysis={analysis}
                    tickerData={
                      filteredTickers.find(
                        (t) => t.ticker === expandedTicker
                      )!
                    }
                    onClose={() => {
                      setExpandedTicker(null);
                      setAnalysis(null);
                    }}
                  />
                ) : null}
              </div>
            )}
          </>
        )}

        {/* Error state */}
        {tickersError && (
          <div className="text-center py-8 animate-fade-in">
            <p className="text-sm text-loss/80 font-mono">
              Failed to load screener data. Retrying...
            </p>
          </div>
        )}
      </div>
    </TooltipProvider>
  );
}
