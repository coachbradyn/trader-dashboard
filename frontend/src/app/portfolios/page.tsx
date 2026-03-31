"use client";
import Link from "next/link";
import { usePortfolios } from "@/hooks/usePortfolio";
import { formatCurrency, formatPercent, pnlColor } from "@/lib/formatters";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";

function ExecutionBadge({ mode }: { mode?: string }) {
  if (!mode || mode === "local")
    return <span className="text-[10px] uppercase tracking-wider text-gray-500 font-mono">Manual</span>;
  if (mode === "paper")
    return <span className="text-[10px] uppercase tracking-wider text-amber-400 font-mono">Paper</span>;
  return <span className="text-[10px] uppercase tracking-wider text-profit font-mono">Live</span>;
}

export default function PortfoliosPage() {
  const { data: portfolios, loading } = usePortfolios();

  return (
    <div>
      <div className="mb-6">
        <h1 className="text-2xl font-bold">Portfolios</h1>
        <p className="text-gray-400 mt-1 text-sm">
          Themed strategy groupings — each with their own capital and performance tracking
        </p>
      </div>

      {loading ? (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {Array.from({ length: 3 }).map((_, i) => (
            <Skeleton key={i} className="h-48 rounded-xl" />
          ))}
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {portfolios?.map((p) => {
            const isAI = p.name?.toLowerCase().includes("ai") || !!(p as unknown as Record<string, unknown>).is_ai_managed;
            return (
              <Link key={p.id} href={`/portfolios/${p.id}`}>
                <Card className={`hover:border-primary/50 transition cursor-pointer h-full ${isAI ? "border-ai-blue/40" : ""}`}>
                  <CardContent>
                    <div className="flex items-start justify-between mb-3">
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2">
                          <h3 className="font-bold text-white truncate">{p.name}</h3>
                          {isAI && (
                            <Badge variant="ai" className="text-[10px] shrink-0">AI</Badge>
                          )}
                        </div>
                        <div className="flex items-center gap-2 mt-0.5">
                          <p className="text-xs text-gray-500 line-clamp-1 flex-1">{p.description}</p>
                          <ExecutionBadge mode={p.execution_mode} />
                        </div>
                      </div>
                      <span className={`stat-value text-lg ml-2 shrink-0 ${pnlColor(p.total_return_pct)}`}>
                        {formatPercent(p.total_return_pct)}
                      </span>
                    </div>

                    <div className="grid grid-cols-3 gap-3 mt-4">
                      <div>
                        <div className="stat-label">Equity</div>
                        <div className="text-sm font-mono text-white">{formatCurrency(p.equity)}</div>
                      </div>
                      <div>
                        <div className="stat-label">Unrealized</div>
                        <div className={`text-sm font-mono ${pnlColor(p.unrealized_pnl)}`}>
                          {formatCurrency(p.unrealized_pnl)}
                        </div>
                      </div>
                      <div>
                        <div className="stat-label">Positions</div>
                        <div className="text-sm font-mono text-white">{p.open_positions}</div>
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
