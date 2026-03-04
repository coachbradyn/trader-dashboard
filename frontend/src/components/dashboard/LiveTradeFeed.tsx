"use client";
import { useTrades } from "@/hooks/useTrades";
import TradeCard from "./TradeCard";

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
          <div key={i} className="card animate-pulse h-20" />
        ))}
      </div>
    );
  }

  if (error) {
    return <div className="card text-loss text-center py-8">Failed to load trades</div>;
  }

  if (!trades || trades.length === 0) {
    return <div className="card text-gray-500 text-center py-8">No trades yet — waiting for signals</div>;
  }

  return (
    <div className="space-y-3">
      {trades.map((trade) => (
        <TradeCard key={trade.id} trade={trade} />
      ))}
    </div>
  );
}
