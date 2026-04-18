"use client";

import { useState, useEffect, useCallback, useMemo } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  Gauge, FileText, Briefcase, PieChart as PieIcon, Newspaper,
  TrendingUp, TrendingDown, Target, Zap, Brain, RefreshCw,
  CheckCircle2, type LucideIcon,
} from "lucide-react";
import { api } from "@/lib/api";
import { useVisibilityPoll } from "@/hooks/useVisibilityPoll";
import { formatCurrency, formatPercent, formatTimeAgo, pnlColor } from "@/lib/formatters";
import { renderMarkdown } from "@/lib/markdown";
import { Skeleton } from "@/components/ui/skeleton";
import { Badge } from "@/components/ui/badge";
import OnboardingWizard from "@/components/OnboardingWizard";
import { NewsDigestCard, UpcomingEventsCard, SectorAnalysisCard } from "@/components/ai/MarketContextCards";
import DottedGlowBackground from "@/components/ui/dotted-glow-background";
import CardSpotlight from "@/components/ui/card-spotlight";
import {
  chartColors, chartAnimation,
} from "@/components/ui/chart-config";
import {
  ResponsiveContainer, AreaChart, Area,
} from "recharts";
import type {
  Portfolio, PortfolioAction, ActionStats,
  BriefingResponse, Trade, NewsArticle, MarketIntel,
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

function getMarketStatus() {
  const now = new Date();
  const etStr = now.toLocaleString("en-US", { timeZone: "America/New_York", hour: "numeric", minute: "numeric", hour12: false });
  const [h, m] = etStr.split(":").map(Number);
  const mins = h * 60 + m;
  const dayOfWeek = now.toLocaleString("en-US", { weekday: "short", timeZone: "America/New_York" });
  const isWeekend = dayOfWeek === "Sat" || dayOfWeek === "Sun";
  if (isWeekend) return { label: "CLOSED", dot: "bg-gray-500", text: "text-gray-400", open: false };
  if (mins >= 570 && mins < 960) return { label: "MARKET OPEN", dot: "bg-profit", text: "text-profit", open: true };
  if (mins >= 240 && mins < 570) return { label: "PRE-MARKET", dot: "bg-amber-500", text: "text-amber-400", open: false };
  if (mins >= 960 && mins < 1200) return { label: "AFTER-HOURS", dot: "bg-amber-500", text: "text-amber-400", open: false };
  return { label: "CLOSED", dot: "bg-gray-500", text: "text-gray-400", open: false };
}

// ── Card shell ────────────────────────────────────────────────────
function CardHeader({
  icon: Icon, title, action,
}: { icon: LucideIcon; title: string; action?: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between mb-4">
      <div className="flex items-center gap-2">
        <Icon className="w-4 h-4 text-ai-blue" strokeWidth={1.75} />
        <h2 className="text-[13px] font-semibold text-white uppercase tracking-wider" style={FONT_OUTFIT}>{title}</h2>
      </div>
      {action}
    </div>
  );
}

// ── Card 1: Market Sentiment ──────────────────────────────────────
function SentimentCard({ briefing, portfolios }: { briefing: string | null; portfolios: Portfolio[] }) {
  const { score, label, note } = useMemo(() => {
    const text = (briefing || "").toLowerCase();
    let s = 5;
    const bullKw = (text.match(/bullish|risk[- ]on|constructive|rally|breakout/g) || []).length;
    const bearKw = (text.match(/bearish|risk[- ]off|selloff|decline|weakness|caution/g) || []).length;
    s += Math.min(3, bullKw);
    s -= Math.min(3, bearKw);
    const totalPnL = portfolios.reduce((a, p) => a + (p.unrealized_pnl || 0), 0);
    if (totalPnL > 0) s += 1;
    else if (totalPnL < 0) s -= 1;
    s = Math.max(1, Math.min(10, s));
    let label = "Neutral";
    if (s >= 8) label = "Strongly bullish";
    else if (s >= 6.5) label = "Cautiously bullish";
    else if (s >= 4.5) label = "Mixed";
    else if (s >= 3) label = "Defensive";
    else label = "Risk-off posture";
    const firstSentence = (briefing || "").split(/[.\n]/).filter((x) => x.trim().length > 30)[0];
    const note = firstSentence
      ? firstSentence.replace(/[#*_`]/g, "").trim() + "."
      : "Sentiment derived from portfolio posture and briefing tone.";
    return { score: s, label, note };
  }, [briefing, portfolios]);

  const pct = ((score - 1) / 9) * 100;

  return (
    <CardSpotlight className="lg:col-span-2">
      <div className="p-5">
        <CardHeader icon={Gauge} title="Market Sentiment" action={
          <span className="text-[11px] text-gray-500 font-mono" style={FONT_MONO}>{score.toFixed(1)} / 10</span>
        } />
        <div className="flex flex-col sm:flex-row sm:items-center gap-4">
          <div className="flex-1 min-w-0">
            <div className="relative h-3 rounded-full overflow-hidden bg-[#0a0a0f] border border-[#1f2937]">
              <div className="absolute inset-0 bg-gradient-to-r from-loss via-amber-400 to-profit opacity-70" />
              <div
                className="absolute top-1/2 -translate-y-1/2 w-4 h-4 rounded-full bg-white border-2 border-[#0a0a0f] shadow-lg transition-[left] duration-500"
                style={{ left: `calc(${pct}% - 8px)` }}
              />
            </div>
            <div className="mt-2 flex justify-between text-[10px] text-gray-600 font-mono" style={FONT_MONO}>
              <span>BEARISH</span><span>NEUTRAL</span><span>BULLISH</span>
            </div>
          </div>
          <div className="sm:w-[46%]">
            <div className="text-lg font-bold text-white" style={FONT_OUTFIT}>{label}</div>
            <p className="text-xs text-gray-400 mt-1 leading-relaxed line-clamp-3">{note}</p>
          </div>
        </div>
      </div>
    </CardSpotlight>
  );
}

// ── Card 2: Briefing ──────────────────────────────────────────────
function BriefingCard({
  data, loading, error, onRefresh, refreshing, marketOpen,
}: {
  data: BriefingResponse | null; loading: boolean; error: string | null;
  onRefresh: () => void; refreshing: boolean; marketOpen: boolean;
}) {
  return (
    <CardSpotlight className="lg:col-span-2">
      <div className="p-5">
        <CardHeader icon={FileText} title="Today's Briefing" action={
          <div className="flex items-center gap-3">
            {marketOpen && (
              <span className="flex items-center gap-1 text-[10px] font-mono text-profit" style={FONT_MONO}>
                <span className="w-1.5 h-1.5 rounded-full bg-profit animate-pulse" /> LIVE
              </span>
            )}
            {data?.generated_at && (
              <span className="text-[10px] text-gray-500 font-mono" style={FONT_MONO}>{formatTimeAgo(data.generated_at)}</span>
            )}
            <button
              onClick={onRefresh}
              disabled={refreshing}
              className="flex items-center gap-1 text-[11px] text-ai-blue hover:text-white transition px-2 py-1 rounded border border-ai-blue/30 bg-ai-blue/10 disabled:opacity-50"
              style={FONT_OUTFIT}
            >
              <RefreshCw className={`w-3 h-3 ${refreshing ? "animate-spin" : ""}`} strokeWidth={2} />
              Refresh
            </button>
          </div>
        } />
        {loading ? (
          <div className="space-y-2">
            {[1,2,3,4,5,6].map((i) => <Skeleton key={i} className="h-3 rounded" style={{ width: `${60 + Math.random()*35}%` }} />)}
          </div>
        ) : error ? (
          <div className="text-xs text-loss">{error}</div>
        ) : data?.briefing ? (
          <div
            className="ai-prose max-h-[420px] overflow-y-auto pr-2"
            dangerouslySetInnerHTML={{ __html: renderMarkdown(data.briefing) }}
          />
        ) : (
          <div className="text-xs text-gray-500">No briefing available yet.</div>
        )}
      </div>
    </CardSpotlight>
  );
}

// ── Card 3: Portfolio Commentary ──────────────────────────────────
function PortfolioCommentaryCard({ portfolios }: { portfolios: Portfolio[] }) {
  const totalEquity = portfolios.reduce((s, p) => s + p.equity, 0);
  const totalPnL = portfolios.reduce((s, p) => s + p.unrealized_pnl, 0);
  const totalPositions = portfolios.reduce((s, p) => s + p.open_positions, 0);
  const active = portfolios.filter((p) => p.is_active).slice(0, 3);

  return (
    <CardSpotlight>
      <div className="p-5">
        <CardHeader icon={Briefcase} title="Portfolio Commentary" action={
          <Link href="/portfolios" className="text-[11px] text-ai-blue hover:text-white transition">View all →</Link>
        } />
        <div className="grid grid-cols-3 gap-3 mb-4">
          <Metric label="Total Equity" value={formatCurrency(totalEquity)} />
          <Metric label="Unrealized" value={formatCurrency(totalPnL)} tone={pnlColor(totalPnL)} />
          <Metric label="Positions" value={String(totalPositions)} />
        </div>
        <div className="space-y-2">
          {active.length === 0 ? (
            <p className="text-xs text-gray-500 py-4 text-center">No active portfolios.</p>
          ) : active.map((p) => (
            <PortfolioRow key={p.id} p={p} />
          ))}
        </div>
      </div>
    </CardSpotlight>
  );
}

function PortfolioRow({ p }: { p: Portfolio }) {
  const [data, setData] = useState<{ t: string; v: number }[] | null>(null);
  const isUp = (p.total_return_pct ?? 0) >= 0;
  const color = isUp ? chartColors.profit : chartColors.loss;
  const gradId = `pf-home-${p.id}`;
  useEffect(() => {
    let mounted = true;
    api.getEquityHistory(p.id)
      .then((pts) => {
        if (!mounted) return;
        const mapped = pts.slice(-30).map((x) => ({ t: x.time, v: x.equity }));
        // Portfolios without trade activity may have 0 or 1 snapshots.
        // Recharts won't render an <Area> with <2 points, so synthesise
        // a flat line at the current equity — the sparkline is a visual
        // anchor, not a precise history in that state.
        if (mapped.length < 2 && p.equity > 0) {
          const t = new Date().toISOString();
          setData([
            { t, v: p.equity },
            { t, v: p.equity },
          ]);
        } else {
          setData(mapped);
        }
      })
      .catch(() => {
        if (!mounted) return;
        if (p.equity > 0) {
          const t = new Date().toISOString();
          setData([{ t, v: p.equity }, { t, v: p.equity }]);
        } else {
          setData([]);
        }
      });
    return () => { mounted = false; };
  }, [p.id, p.equity]);

  return (
    <Link href={`/portfolios/${p.id}`} className="flex items-center gap-3 px-3 py-2 rounded-lg hover:bg-[#1f2937]/40 transition">
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span className="text-xs font-semibold text-white truncate" style={FONT_OUTFIT}>{p.name}</span>
        </div>
        <div className="text-[10px] text-gray-500 font-mono" style={FONT_MONO}>
          {formatCurrency(p.equity)} · <span className={pnlColor(p.total_return_pct ?? 0)}>{formatPercent(p.total_return_pct ?? 0)}</span>
        </div>
      </div>
      <div className="w-[80px] h-[32px] shrink-0">
        {data && data.length > 1 && (
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart data={data}>
              <defs>
                <linearGradient id={gradId} x1="0" x2="0" y1="0" y2="1">
                  <stop offset="0%" stopColor={color} stopOpacity={0.4} />
                  <stop offset="100%" stopColor={color} stopOpacity={0} />
                </linearGradient>
              </defs>
              <Area
                type="monotone" dataKey="v" stroke={color} strokeWidth={1.5}
                fill={`url(#${gradId})`} dot={false}
                animationDuration={chartAnimation.duration}
                animationEasing={chartAnimation.easing}
              />
            </AreaChart>
          </ResponsiveContainer>
        )}
      </div>
    </Link>
  );
}

function Metric({ label, value, tone }: { label: string; value: string; tone?: string }) {
  return (
    <div className="rounded-lg bg-[#0a0a0f]/60 border border-[#1f2937] px-3 py-2">
      <div className="text-[9px] uppercase tracking-wider text-gray-500" style={FONT_OUTFIT}>{label}</div>
      <div className={`text-sm font-semibold mt-0.5 ${tone || "text-white"}`} style={FONT_MONO}>{value}</div>
    </div>
  );
}

// ── Card 4: Sector Analysis (Gemini-grounded) ─────────────────────
function SectorCard({ intel, loading }: { intel: MarketIntel | null; loading: boolean }) {
  const sectors = intel?.sectors ?? [];
  const isFallback = intel?.source === "fallback";
  return (
    <CardSpotlight>
      <div className="p-5">
        <CardHeader icon={PieIcon} title="Sector Analysis" />
        {loading && !intel ? (
          <div className="space-y-2">
            {Array.from({ length: 6 }).map((_, i) => (
              <Skeleton key={i} className="h-4 rounded" />
            ))}
          </div>
        ) : sectors.length === 0 ? (
          <p className="text-xs text-gray-500 py-6 text-center">
            Sector intel unavailable. Gemini will refresh shortly.
          </p>
        ) : (
          <div className="space-y-2">
            {sectors.map((s) => {
              const tone = s.score > 0 ? "bg-profit" : s.score < 0 ? "bg-loss" : "bg-gray-500";
              const width = s.score !== 0 ? 70 : 30;
              const rowOpacity = s.score !== 0 ? "opacity-100" : "opacity-60";
              return (
                <div
                  key={s.name}
                  className={`group relative flex items-center gap-3 ${rowOpacity}`}
                  title={s.summary || s.name}
                >
                  <span className="text-[11px] text-gray-300 w-28 truncate" style={FONT_OUTFIT}>
                    {s.name}
                  </span>
                  <div className="flex-1 h-1.5 rounded-full bg-[#0a0a0f] border border-[#1f2937] overflow-hidden">
                    <div
                      className={`h-full ${tone} transition-all duration-500`}
                      style={{
                        width: `${width}%`,
                        marginLeft: s.score < 0 ? `${100 - width}%` : 0,
                      }}
                    />
                  </div>
                  <span
                    className={`text-[10px] font-mono w-8 text-right ${
                      s.score > 0 ? "text-profit" : s.score < 0 ? "text-loss" : "text-gray-500"
                    }`}
                    style={FONT_MONO}
                  >
                    {s.score > 0 ? "↑" : s.score < 0 ? "↓" : "—"}
                  </span>
                  {/* Hover detail: leaders + summary */}
                  {(s.summary || s.leaders.length > 0) && (
                    <div className="absolute left-0 right-0 -bottom-1 translate-y-full z-10 hidden group-hover:block p-2 rounded-md bg-[#0a0a0f] border border-[#1f2937] shadow-lg">
                      {s.summary && (
                        <p className="text-[10px] text-gray-300 leading-relaxed" style={FONT_OUTFIT}>
                          {s.summary}
                        </p>
                      )}
                      {s.leaders.length > 0 && (
                        <div className="flex gap-1 mt-1">
                          {s.leaders.map((t) => (
                            <span
                              key={t}
                              className="text-[9px] font-mono text-ai-blue bg-ai-blue/10 px-1.5 py-0.5 rounded"
                              style={FONT_MONO}
                            >
                              {t}
                            </span>
                          ))}
                        </div>
                      )}
                    </div>
                  )}
                </div>
              );
            })}
            {isFallback && (
              <p className="text-[9px] text-gray-600 mt-2 text-center" style={FONT_MONO}>
                Awaiting Gemini refresh
              </p>
            )}
          </div>
        )}
      </div>
    </CardSpotlight>
  );
}

// ── Card 5: News / Macro (Gemini-grounded with raw news fallback) ──
function NewsCard({ intel, loading }: { intel: MarketIntel | null; loading: boolean }) {
  // Raw news kept only as a fallback when Gemini intel is absent — the
  // primary path is Gemini's curated macro headlines with impact tags.
  const [rawNews, setRawNews] = useState<NewsArticle[] | null>(null);
  const macro = intel?.macro ?? [];
  useEffect(() => {
    if (macro.length > 0) return;
    api.getNews({ limit: 6, hours: 24 })
      .then((d) => setRawNews(Array.isArray(d) ? d : []))
      .catch(() => setRawNews([]));
  }, [macro.length]);

  return (
    <CardSpotlight>
      <div className="p-5">
        <CardHeader icon={Newspaper} title="News / Macro" />
        {loading && !intel && rawNews === null ? (
          <div className="space-y-2">
            {[1, 2, 3, 4].map((i) => <Skeleton key={i} className="h-6 rounded" />)}
          </div>
        ) : macro.length > 0 ? (
          <ul className="space-y-2.5">
            {macro.slice(0, 6).map((m, idx) => {
              const tone =
                m.impact === "bullish" ? "bg-profit"
                : m.impact === "bearish" ? "bg-loss"
                : "bg-gray-500";
              return (
                <li key={`${m.headline}-${idx}`} className="flex items-start gap-2.5">
                  <span className={`w-1.5 h-1.5 rounded-full shrink-0 mt-1.5 ${tone}`} />
                  <div className="min-w-0 flex-1">
                    {m.url ? (
                      <a
                        href={m.url}
                        target="_blank"
                        rel="noreferrer"
                        className="text-[12px] text-gray-200 hover:text-white line-clamp-1 leading-snug"
                        style={FONT_OUTFIT}
                      >
                        {m.headline}
                      </a>
                    ) : (
                      <span className="text-[12px] text-gray-200 line-clamp-1 leading-snug" style={FONT_OUTFIT}>
                        {m.headline}
                      </span>
                    )}
                    {m.summary && (
                      <p className="text-[10px] text-gray-500 line-clamp-2 mt-0.5 leading-snug" style={FONT_OUTFIT}>
                        {m.summary}
                      </p>
                    )}
                  </div>
                </li>
              );
            })}
          </ul>
        ) : rawNews && rawNews.length > 0 ? (
          <ul className="space-y-2.5">
            {rawNews.slice(0, 6).map((a, idx) => {
              const s = a.sentiment_score ?? 0;
              const tone = s > 0.2 ? "bg-profit" : s < -0.2 ? "bg-loss" : "bg-gray-500";
              const rowKey = a.id || a.url || `${a.headline}-${idx}`;
              return (
                <li key={rowKey} className="flex items-start gap-2.5">
                  <span className={`w-1.5 h-1.5 rounded-full shrink-0 mt-1.5 ${tone}`} />
                  <div className="min-w-0 flex-1">
                    <a
                      href={a.url || "#"} target="_blank" rel="noreferrer"
                      className="text-[12px] text-gray-200 hover:text-white line-clamp-1 leading-snug"
                      style={FONT_OUTFIT}
                    >
                      {a.headline}
                    </a>
                    <div className="flex gap-2 text-[9px] text-gray-600 font-mono mt-0.5" style={FONT_MONO}>
                      <span>{a.source}</span>
                      <span>·</span>
                      <span>{formatTimeAgo(a.published_at)}</span>
                    </div>
                  </div>
                </li>
              );
            })}
          </ul>
        ) : (
          <p className="text-xs text-gray-500 py-4 text-center">No recent headlines.</p>
        )}
      </div>
    </CardSpotlight>
  );
}

// ── Card 6: Wins / Losses ────────────────────────────────────────
function WinsLossesCard() {
  const [trades, setTrades] = useState<Trade[] | null>(null);
  useEffect(() => {
    api.getTrades({ status: "closed", limit: 60 } as { status?: string; limit?: number })
      .then(setTrades)
      .catch(() => setTrades([]));
  }, []);

  const closed = (trades || []).filter((t) => t.status === "closed" && t.pnl_percent != null);
  const wins = [...closed].sort((a, b) => (b.pnl_percent || 0) - (a.pnl_percent || 0)).slice(0, 4);
  const losses = [...closed].sort((a, b) => (a.pnl_percent || 0) - (b.pnl_percent || 0)).slice(0, 4);

  return (
    <CardSpotlight>
      <div className="p-5">
        <CardHeader icon={TrendingUp} title="What Worked / Didn't" />
        {trades === null ? (
          <div className="space-y-2">{[1,2,3].map((i) => <Skeleton key={i} className="h-8 rounded" />)}</div>
        ) : closed.length === 0 ? (
          <p className="text-xs text-gray-500 py-4 text-center">No closed trades yet.</p>
        ) : (
          <div className="grid grid-cols-2 gap-4">
            <TradeColumn tone="profit" icon={TrendingUp} label="Wins" trades={wins} />
            <TradeColumn tone="loss" icon={TrendingDown} label="Losses" trades={losses} />
          </div>
        )}
      </div>
    </CardSpotlight>
  );
}

function TradeColumn({
  tone, icon: Icon, label, trades,
}: {
  tone: "profit" | "loss";
  icon: LucideIcon;
  label: string; trades: Trade[];
}) {
  const accent = tone === "profit" ? "border-l-profit" : "border-l-loss";
  const headColor = tone === "profit" ? "text-profit" : "text-loss";
  return (
    <div className={`border-l-2 ${accent} pl-2.5`}>
      <div className={`flex items-center gap-1.5 mb-2 text-[10px] font-semibold uppercase tracking-wider ${headColor}`} style={FONT_OUTFIT}>
        <Icon className="w-3 h-3" strokeWidth={2} />
        {label}
      </div>
      <ul className="space-y-1.5">
        {trades.map((t) => (
          <li key={t.id} className="flex items-center gap-2">
            <span className="text-xs font-semibold text-white truncate" style={FONT_OUTFIT}>{t.ticker}</span>
            <span className="text-[9px] text-gray-600 font-mono truncate" style={FONT_MONO}>{t.trader_name}</span>
            <span className={`ml-auto text-[11px] font-mono ${pnlColor(t.pnl_percent || 0)}`} style={FONT_MONO}>
              {formatPercent(t.pnl_percent || 0)}
            </span>
          </li>
        ))}
        {trades.length === 0 && <li className="text-[10px] text-gray-600">—</li>}
      </ul>
    </div>
  );
}

// ── Card 7: The Play (pending actions first, Gemini intel fallback) ─
function PlayCard({
  actions,
  intel,
}: {
  actions: PortfolioAction[];
  intel: MarketIntel | null;
}) {
  const play = useMemo(() => {
    // Primary: highest-confidence pending go-long action from Henry.
    const candidates = actions.filter(
      (a) =>
        a.action_type === "BUY" ||
        a.action_type === "ADD" ||
        a.action_type === "OPPORTUNITY"
    );
    if (candidates.length > 0) {
      const top = [...candidates].sort((a, b) => b.confidence - a.confidence)[0];
      return {
        ticker: top.ticker,
        direction: top.direction || "long",
        reasoning: top.reasoning,
        confidence: top.confidence,
        action_type: top.action_type,
        current_price: top.current_price,
        suggested_price: top.suggested_price,
        source: "henry_action" as const,
      };
    }
    // Fallback: Gemini's pick from market-intel
    if (intel?.play) {
      return {
        ticker: intel.play.ticker,
        direction: intel.play.direction,
        reasoning: intel.play.rationale,
        confidence: intel.play.confidence,
        action_type: "GEMINI",
        current_price: intel.play.current_price ?? null,
        suggested_price: intel.play.suggested_price ?? null,
        source: "gemini" as const,
      };
    }
    return null;
  }, [actions, intel]);

  return (
    <CardSpotlight>
      <div className="p-5">
        <CardHeader icon={Target} title="The Play" />
        {!play ? (
          <div className="py-8 text-center">
            <p className="text-xs text-gray-500">No active play — Henry is observing.</p>
          </div>
        ) : (
          <div>
            <div className="flex items-center gap-2 mb-3">
              <span className="text-2xl font-bold text-white" style={FONT_OUTFIT}>{play.ticker}</span>
              <Badge className={`text-[9px] ${play.direction === "long" ? "bg-profit/15 text-profit" : "bg-loss/15 text-loss"}`}>
                {play.direction.toUpperCase()}
              </Badge>
              <Badge
                className={`text-[9px] ${
                  play.source === "gemini"
                    ? "bg-purple-500/15 text-purple-300"
                    : "bg-ai-blue/15 text-ai-blue"
                }`}
              >
                {play.source === "gemini" ? "GEMINI" : play.action_type}
              </Badge>
            </div>
            <p className="text-xs text-gray-400 leading-relaxed line-clamp-3 mb-3">{play.reasoning}</p>
            <div className="grid grid-cols-3 gap-2 text-[10px] font-mono mb-3" style={FONT_MONO}>
              <div className="text-center bg-[#0a0a0f]/60 rounded py-1.5 border border-[#1f2937]">
                <div className="text-gray-500 text-[9px]">Entry</div>
                <div className="text-white">{play.current_price != null ? `$${play.current_price.toFixed(2)}` : "—"}</div>
              </div>
              <div className="text-center bg-[#0a0a0f]/60 rounded py-1.5 border border-[#1f2937]">
                <div className="text-gray-500 text-[9px]">Target</div>
                <div className="text-profit">{play.suggested_price != null ? `$${play.suggested_price.toFixed(2)}` : "—"}</div>
              </div>
              <div className="text-center bg-[#0a0a0f]/60 rounded py-1.5 border border-[#1f2937]">
                <div className="text-gray-500 text-[9px]">Conf</div>
                <div className="text-ai-blue">{play.confidence}/10</div>
              </div>
            </div>
            <div className="h-1.5 rounded-full bg-[#0a0a0f] border border-[#1f2937] overflow-hidden">
              <div
                className="h-full bg-gradient-to-r from-ai-blue to-ai-purple transition-all duration-500"
                style={{ width: `${play.confidence * 10}%` }}
              />
            </div>
          </div>
        )}
      </div>
    </CardSpotlight>
  );
}

// ── Card 8: Actions Queue ─────────────────────────────────────────
function ActionsQueueCard({
  actions, onApprove,
}: { actions: PortfolioAction[]; onApprove: (id: string) => void }) {
  const router = useRouter();
  return (
    <CardSpotlight>
      <div className="p-5">
        <CardHeader icon={Zap} title="Actions Queue" action={
          <Link href="/portfolio-manager" className="text-[11px] text-ai-blue hover:text-white transition">View all →</Link>
        } />
        {actions.length === 0 ? (
          <div className="py-8 text-center">
            <CheckCircle2 className="w-6 h-6 text-gray-600 mx-auto mb-2" strokeWidth={1.5} />
            <p className="text-xs text-gray-500">No pending actions.</p>
          </div>
        ) : (
          <ul className="space-y-2">
            {actions.slice(0, 5).map((a) => (
              <li key={a.id}
                onClick={() => router.push(`/screener/${a.ticker}`)}
                className="group flex items-center gap-2 px-2.5 py-2 rounded-lg bg-[#0a0a0f]/50 border border-[#1f2937] hover:border-ai-blue/30 cursor-pointer transition">
                <Badge className={`text-[9px] shrink-0 ${
                  a.action_type === "BUY" || a.action_type === "ADD" ? "bg-profit/15 text-profit" :
                  a.action_type === "CLOSE" || a.action_type === "SELL" ? "bg-loss/15 text-loss" :
                  "bg-amber-500/15 text-amber-400"
                }`}>{a.action_type}</Badge>
                <span className="text-xs font-semibold text-white" style={FONT_OUTFIT}>{a.ticker}</span>
                <div className="flex gap-[2px] ml-1">
                  {Array.from({ length: 10 }).map((_, i) => (
                    <span key={i} className={`w-1 h-2 rounded-sm ${i < a.confidence ? "bg-ai-blue" : "bg-[#1f2937]"}`} />
                  ))}
                </div>
                <span className="ml-auto text-[9px] text-gray-600 font-mono" style={FONT_MONO}>
                  {a.expires_at ? `${formatTimeAgo(a.expires_at)}` : formatTimeAgo(a.created_at)}
                </span>
                <button
                  onClick={(e) => { e.stopPropagation(); onApprove(a.id); }}
                  className="shrink-0 text-[10px] px-2 py-0.5 rounded bg-profit/15 text-profit border border-profit/20 hover:bg-profit/25 opacity-0 group-hover:opacity-100 transition"
                >
                  Approve
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </CardSpotlight>
  );
}

// ── Card 9: Henry's Sentiment ─────────────────────────────────────
function HenrySentimentCard({ stats }: { stats: ActionStats | null }) {
  const { mood, tone, note } = useMemo(() => {
    const hit = stats?.hit_rate;
    if (hit == null) return { mood: "Learning", tone: "text-gray-400", note: "Insufficient history to calibrate confidence." };
    if (hit >= 60) return { mood: "Calibrated", tone: "text-profit", note: "Signals are firing reliably — press edges when confidence is high." };
    if (hit >= 40) return { mood: "Steady", tone: "text-amber-400", note: "Mixed results — stick to confluence setups only." };
    return { mood: "Cautious", tone: "text-loss", note: "Recent hit rate is low — tighten risk and reduce size." };
  }, [stats]);

  return (
    <CardSpotlight>
      <div className="p-5">
        <CardHeader icon={Brain} title="Henry's Sentiment" />
        <div className="flex items-center gap-3 mb-4">
          <span className={`w-2 h-2 rounded-full ${tone.replace("text-", "bg-")} animate-pulse`} />
          <span className={`text-lg font-bold ${tone}`} style={FONT_OUTFIT}>{mood}</span>
        </div>
        <div className="grid grid-cols-3 gap-2 mb-3">
          <Metric
            label="Hit rate"
            value={stats?.hit_rate != null ? `${stats.hit_rate.toFixed(0)}%` : "—"}
            tone={stats?.hit_rate != null ? pnlColor((stats.hit_rate ?? 50) - 50) : "text-gray-400"}
          />
          <Metric label="Pending" value={String(stats?.pending_count ?? 0)} />
          <Metric label="Approved today" value={String(stats?.approved_today ?? 0)} />
        </div>
        <p className="text-[11px] text-gray-400 leading-relaxed">{note}</p>
      </div>
    </CardSpotlight>
  );
}

// ── Main page ─────────────────────────────────────────────────────
export default function HomePage() {
  useFonts();
  const router = useRouter();

  const [portfolios, setPortfolios] = useState<Portfolio[]>([]);
  const [actions, setActions] = useState<PortfolioAction[]>([]);
  const [actionStats, setActionStats] = useState<ActionStats | null>(null);
  const [briefing, setBriefing] = useState<BriefingResponse | null>(null);
  const [briefingLoading, setBriefingLoading] = useState(true);
  const [briefingError, setBriefingError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [traders, setTraders] = useState<{ id: string }[] | null>(null);
  const [intel, setIntel] = useState<MarketIntel | null>(null);
  const [intelLoading, setIntelLoading] = useState(true);

  const market = getMarketStatus();
  const now = new Date();
  const dateStr = now.toLocaleDateString("en-US", { weekday: "long", month: "long", day: "numeric", timeZone: "America/New_York" });

  const fetchBriefing = useCallback(async (isRefresh = false) => {
    try {
      if (isRefresh) setRefreshing(true);
      else setBriefingLoading(true);
      setBriefingError(null);
      const r = isRefresh ? await api.refreshBriefing() : await api.getBriefing();
      setBriefing(r);
    } catch (e) {
      setBriefingError(e instanceof Error ? e.message : "Failed to load briefing");
    } finally {
      setBriefingLoading(false);
      setRefreshing(false);
    }
  }, []);

  // Pull portfolios + actions + action stats in one round-trip. Replaces
  // three separate parallel fetches with one call that the backend fans
  // out internally — one auth middleware pass, one TLS handshake, and
  // paired with useVisibilityPoll below, no polling happens while the
  // tab is hidden.
  const refreshHomeSnapshot = useCallback(async () => {
    try {
      const snap = await api.getHomeSnapshot();
      setPortfolios(snap.portfolios || []);
      setActions(snap.actions || []);
      setActionStats(snap.action_stats || null);
    } catch {
      /* swallow — keep last good state */
    }
  }, []);

  const refreshIntel = useCallback(async () => {
    try {
      const d = await api.getMarketIntel();
      setIntel(d);
    } catch {
      /* ignore */
    } finally {
      setIntelLoading(false);
    }
  }, []);

  useEffect(() => {
    // One-time fetches (no interval needed)
    api.getTraders().then(setTraders).catch(() => setTraders([]));
    fetchBriefing();
    // Prime the polling caches once; useVisibilityPoll will take over
    // the interval side of things.
    refreshHomeSnapshot();
    refreshIntel();
  }, [fetchBriefing, refreshHomeSnapshot, refreshIntel]);

  // 30s poll for portfolios/actions/stats — paused when tab hidden,
  // re-fires on visibility return so the user sees fresh data instantly.
  useVisibilityPoll(refreshHomeSnapshot, 30_000);
  // 5-min poll for market intel — backend caches 15m so most calls hit cache.
  useVisibilityPoll(refreshIntel, 5 * 60_000);

  const handleApprove = useCallback(async (id: string) => {
    try {
      await api.approveAction(id);
      const next = await api.getActions("pending");
      setActions(next);
    } catch {}
  }, []);

  if (traders !== null && traders.length === 0 && portfolios.length === 0) {
    return <OnboardingWizard />;
  }

  return (
    <div className="relative max-w-7xl mx-auto">
      <DottedGlowBackground />

      {/* Greeting */}
      <div className="flex flex-col sm:flex-row sm:items-end sm:justify-between gap-2 mb-6">
        <div>
          <h1 className="text-2xl sm:text-3xl font-bold text-white tracking-tight" style={FONT_OUTFIT}>
            {dateStr}
          </h1>
          <p className="text-sm text-gray-500 mt-0.5" style={FONT_OUTFIT}>
            Command center · {portfolios.length} portfolio{portfolios.length !== 1 ? "s" : ""} tracked
          </p>
        </div>
        <div className="flex items-center gap-2">
          <span className="relative flex h-2 w-2">
            <span className={`absolute inline-flex h-full w-full rounded-full ${market.dot} ${market.open ? "animate-ping opacity-75" : "opacity-50"}`} />
            <span className={`relative inline-flex rounded-full h-2 w-2 ${market.dot}`} />
          </span>
          <span className={`text-xs font-semibold tracking-wider uppercase ${market.text}`} style={FONT_MONO}>
            {market.label}
          </span>
        </div>
      </div>

      {/* Card grid */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <SentimentCard briefing={briefing?.briefing ?? null} portfolios={portfolios} />
        <BriefingCard
          data={briefing} loading={briefingLoading} error={briefingError}
          onRefresh={() => fetchBriefing(true)} refreshing={refreshing}
          marketOpen={market.open}
        />
        <PortfolioCommentaryCard portfolios={portfolios} />
        <SectorCard intel={intel} loading={intelLoading} />
        <NewsCard intel={intel} loading={intelLoading} />
        <NewsDigestCard />
        <UpcomingEventsCard />
        <SectorAnalysisCard />
        <WinsLossesCard />
        <PlayCard actions={actions} intel={intel} />
        <ActionsQueueCard actions={actions} onApprove={handleApprove} />
        <HenrySentimentCard stats={actionStats} />
      </div>

      {/* Unused router guard against linting */}
      <span className="hidden" aria-hidden>{router ? "" : ""}</span>
    </div>
  );
}
