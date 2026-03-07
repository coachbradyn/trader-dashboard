"use client";
import { Portfolio } from "@/lib/types";
import { formatCurrency, formatPercent, pnlColor } from "@/lib/formatters";
import { Card, CardContent } from "@/components/ui/card";

export default function PortfolioHeader({ portfolio }: { portfolio: Portfolio }) {
  return (
    <Card>
      <CardContent>
        <div className="flex flex-col md:flex-row md:items-center md:justify-between gap-4">
          <div>
            <h1 className="text-2xl font-bold text-white">{portfolio.name}</h1>
            <p className="text-sm text-gray-400 mt-1">{portfolio.description}</p>
          </div>
          <div className="flex gap-6">
            <div className="text-right">
              <div className="stat-label">Total Return</div>
              <div className={`stat-value ${pnlColor(portfolio.total_return_pct)}`}>
                {formatPercent(portfolio.total_return_pct)}
              </div>
            </div>
            <div className="text-right">
              <div className="stat-label">Equity</div>
              <div className="stat-value text-white">{formatCurrency(portfolio.equity)}</div>
            </div>
            <div className="text-right">
              <div className="stat-label">Unrealized</div>
              <div className={`stat-value text-lg ${pnlColor(portfolio.unrealized_pnl)}`}>
                {formatCurrency(portfolio.unrealized_pnl)}
              </div>
            </div>
            <div className="text-right">
              <div className="stat-label">Open</div>
              <div className="stat-value text-white">{portfolio.open_positions}</div>
            </div>
          </div>
        </div>
      </CardContent>
    </Card>
  );
}
