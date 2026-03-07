"use client";
import Link from "next/link";
import { usePortfolios } from "@/hooks/usePortfolio";
import { formatCurrency, formatPercent, pnlColor } from "@/lib/formatters";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";

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
          {portfolios?.map((p) => (
            <Link key={p.id} href={`/portfolios/${p.id}`}>
              <Card className="hover:border-primary/50 transition cursor-pointer h-full">
                <CardContent>
                  <div className="flex items-start justify-between mb-3">
                    <div>
                      <h3 className="font-bold text-white">{p.name}</h3>
                      <p className="text-xs text-gray-500 mt-0.5 line-clamp-2">{p.description}</p>
                    </div>
                    <span className={`stat-value text-lg ${pnlColor(p.total_return_pct)}`}>
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
          ))}
        </div>
      )}
    </div>
  );
}
