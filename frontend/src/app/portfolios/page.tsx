"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import { ResponsiveContainer, AreaChart, Area } from "recharts";
import { usePortfolios } from "@/hooks/usePortfolio";
import { api } from "@/lib/api";
import { formatCurrency, formatPercent, pnlColor } from "@/lib/formatters";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { chartColors } from "@/components/ui/chart-config";

const FONT_OUTFIT = { fontFamily: "'Outfit', sans-serif" } as const;
const FONT_MONO = { fontFamily: "'JetBrains Mono', monospace" } as const;

function useFonts() {
  useEffect(() => {
    if (document.getElementById("__portfolios-fonts")) return;
    const link = document.createElement("link");
    link.id = "__portfolios-fonts";
    link.rel = "stylesheet";
    link.href = "https://fonts.googleapis.com/css2?family=Outfit:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500;600;700&display=swap";
    document.head.appendChild(link);
  }, []);
}

function ExecutionBadge({ mode }: { mode?: string }) {
  if (!mode || mode === "local")
    return <span className="px-1.5 py-0.5 rounded text-[9px] uppercase tracking-wider text-gray-500 bg-gray-800 font-mono">Manual</span>;
  if (mode === "paper")
    return <span className="px-1.5 py-0.5 rounded text-[9px] uppercase tracking-wider text-amber-400 bg-amber-400/10 font-mono">Paper</span>;
  return <span className="px-1.5 py-0.5 rounded text-[9px] uppercase tracking-wider text-profit bg-profit/10 font-mono">Live</span>;
}

function PortfolioSparkline({ portfolioId, isUp }: { portfolioId: string; isUp: boolean }) {
  const [data, setData] = useState<{ t: string; v: number }[] | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    api
      .getEquityHistory(portfolioId)
      .then((points) => {
        if (cancelled) return;
        const mapped = (points ?? []).map((p) => ({ t: p.time, v: p.equity }));
        setData(mapped.slice(-30));
      })
      .catch(() => {
        if (!cancelled) setData([]);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [portfolioId]);

  if (loading) {
    return <Skeleton className="h-[72px] w-full rounded-md" />;
  }

  if (!data || data.length === 0) {
    return <div className="h-[72px] w-full" aria-hidden />;
  }

  const color = isUp ? chartColors.profit : chartColors.loss;
  const gradId = `sparkline-${portfolioId}`;

  return (
    <div className="h-[72px] w-full">
      <ResponsiveContainer width="100%" height={72}>
        <AreaChart data={data} margin={{ top: 2, right: 0, bottom: 2, left: 0 }}>
          <defs>
            <linearGradient id={gradId} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={color} stopOpacity={0.35} />
              <stop offset="100%" stopColor={color} stopOpacity={0} />
            </linearGradient>
          </defs>
          <Area
            type="monotone"
            dataKey="v"
            stroke={color}
            strokeWidth={1.5}
            fill={`url(#${gradId})`}
            dot={false}
            isAnimationActive={false}
          />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}

export default function PortfoliosPage() {
  useFonts();
  const { data: portfolios, loading } = usePortfolios();

  return (
    <div>
      <div className="mb-8">
        <h1 className="text-3xl font-bold text-white tracking-tight" style={FONT_OUTFIT}>Portfolios</h1>
        <p className="text-gray-500 mt-1 text-sm" style={FONT_OUTFIT}>
          Themed strategy groupings — each with their own capital and performance tracking
        </p>
      </div>

      {loading ? (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {Array.from({ length: 3 }).map((_, i) => (
            <Skeleton key={i} className="h-52 rounded-xl" />
          ))}
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {portfolios?.map((p) => {
            const isAI = p.name?.toLowerCase().includes("ai") || !!(p as unknown as Record<string, unknown>).is_ai_managed;
            const returnPct = p.total_return_pct ?? 0;
            const isUp = returnPct >= 0;
            return (
              <Link key={p.id} href={`/portfolios/${p.id}`}>
                <Card className={`group hover:border-gray-500 transition-all duration-200 cursor-pointer h-full bg-[#1f2937]/60 border-[#374151] ${isAI ? "border-[#6366f1]/40 hover:border-[#6366f1]/70" : ""}`}>
                  <CardContent className="p-5">
                    {/* Name + badges */}
                    <div className="flex items-center gap-2 mb-1">
                      <h3 className="font-bold text-white text-lg truncate" style={FONT_OUTFIT}>{p.name}</h3>
                      {isAI && <Badge variant="ai" className="text-[9px] shrink-0">AI</Badge>}
                      <div className="ml-auto shrink-0"><ExecutionBadge mode={p.execution_mode} /></div>
                    </div>
                    {p.description && (
                      <p className="text-xs text-gray-500 line-clamp-1 mb-4" style={FONT_OUTFIT}>{p.description}</p>
                    )}

                    {/* Hero number: equity */}
                    <div className="mb-3">
                      <div className="text-2xl font-bold text-white" style={FONT_MONO}>{formatCurrency(p.equity)}</div>
                      <span className={`text-sm font-semibold ${pnlColor(returnPct)}`} style={FONT_MONO}>
                        {formatPercent(returnPct)}
                      </span>
                    </div>

                    {/* Sparkline (30-day equity curve) */}
                    <div className="mb-3 -mx-1">
                      <PortfolioSparkline portfolioId={p.id} isUp={isUp} />
                    </div>

                    {/* Stat row */}
                    <div className="flex items-center gap-4 pt-3 border-t border-[#374151]">
                      <div className="flex-1">
                        <div className="text-[9px] text-gray-500 uppercase tracking-wider" style={FONT_OUTFIT}>Cash</div>
                        <div className="text-xs font-mono text-gray-300">{formatCurrency(p.cash)}</div>
                      </div>
                      <div className="flex-1">
                        <div className="text-[9px] text-gray-500 uppercase tracking-wider" style={FONT_OUTFIT}>Unrealized</div>
                        <div className={`text-xs font-mono ${pnlColor(p.unrealized_pnl)}`}>{formatCurrency(p.unrealized_pnl)}</div>
                      </div>
                      <div className="flex-1">
                        <div className="text-[9px] text-gray-500 uppercase tracking-wider" style={FONT_OUTFIT}>Positions</div>
                        <div className="text-xs font-mono text-gray-300">{p.open_positions}</div>
                      </div>
                    </div>
                  </CardContent>
                </Card>
              </Link>
            );
          })}
        </div>
      )}
    </div>
  );
}
