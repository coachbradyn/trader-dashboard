"use client";
import { use } from "react";
import { usePortfolio, usePerformance, useEquityHistory, useDailyStats } from "@/hooks/usePortfolio";
import { usePositions } from "@/hooks/usePositions";
import { useTrades } from "@/hooks/useTrades";
import PortfolioHeader from "@/components/portfolio/PortfolioHeader";
import PerformanceStats from "@/components/portfolio/PerformanceStats";
import OpenPositions from "@/components/portfolio/OpenPositions";
import EquityCurve from "@/components/portfolio/EquityCurve";
import DrawdownChart from "@/components/portfolio/DrawdownChart";
import MonthlyHeatmap from "@/components/portfolio/MonthlyHeatmap";
import TradeHistory from "@/components/portfolio/TradeHistory";
import { Skeleton } from "@/components/ui/skeleton";
import { Card, CardContent } from "@/components/ui/card";

export default function PortfolioDetailPage({ params }: { params: Promise<{ portfolioId: string }> }) {
  const { portfolioId } = use(params);

  const { data: portfolio, loading: loadingPortfolio } = usePortfolio(portfolioId);
  const { data: performance, loading: loadingPerf } = usePerformance(portfolioId);
  const { data: positions } = usePositions(portfolioId);
  const { data: equity } = useEquityHistory(portfolioId);
  const { data: dailyStats } = useDailyStats(portfolioId);
  const { data: trades } = useTrades({ portfolio_id: portfolioId, limit: 200 }, 15000);

  if (loadingPortfolio) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-32 rounded-xl" />
        <Skeleton className="h-64 rounded-xl" />
      </div>
    );
  }

  if (!portfolio) {
    return (
      <Card>
        <CardContent className="text-loss text-center py-12">
          Portfolio not found
        </CardContent>
      </Card>
    );
  }

  return (
    <div className="space-y-4">
      <PortfolioHeader portfolio={portfolio} />

      {performance && <PerformanceStats perf={performance} />}

      {positions && <OpenPositions positions={positions} />}

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {equity && <EquityCurve data={equity} />}
        {equity && <DrawdownChart data={equity} />}
      </div>

      {dailyStats && <MonthlyHeatmap data={dailyStats} />}

      {trades && <TradeHistory trades={trades} />}
    </div>
  );
}
