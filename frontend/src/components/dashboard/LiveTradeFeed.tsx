"use client";
import { useTrades } from "@/hooks/useTrades";
import TradeCard from "./TradeCard";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";

interface Props {
  portfolioId?: string;
  limit?: number;
}

export default function LiveTradeFeed({ portfolioId, limit = 50 }: Props) {
  const { data: trades, loading, error } = useTrades(
    { portfolio_id: portfolioId, limit },
    5000
  );

  if (loading) {
    return (
      <div className="space-y-3">
        {Array.from({ length: 5 }).map((_, i) => (
          <Skeleton key={i} className="h-20 rounded-xl" />
        ))}
      </div>
    );
  }

  if (error) {
    return (
      <Card>
        <CardContent className="text-loss text-center py-8">
          Failed to load trades
        </CardContent>
      </Card>
    );
  }

  if (!trades || trades.length === 0) {
    return (
      <Card>
        <CardContent className="text-gray-500 text-center py-8">
          No trades yet — waiting for signals
        </CardContent>
      </Card>
    );
  }

  return (
    <div className="space-y-3">
      {trades.map((trade) => (
        <TradeCard key={trade.id} trade={trade} />
      ))}
    </div>
  );
}
