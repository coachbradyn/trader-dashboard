"use client";
import { use } from "react";
import { usePolling } from "@/hooks/usePolling";
import { useTrades } from "@/hooks/useTrades";
import { api } from "@/lib/api";
import { formatDate } from "@/lib/formatters";
import TradeCard from "@/components/dashboard/TradeCard";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Badge } from "@/components/ui/badge";

export default function TraderDetailPage({ params }: { params: Promise<{ traderId: string }> }) {
  const { traderId } = use(params);

  const { data: trader, loading } = usePolling(() => api.getTrader(traderId), 60000);
  const { data: trades } = useTrades({ trader_id: traderId, limit: 50 }, 10000);

  if (loading) {
    return <Skeleton className="h-48 rounded-xl" />;
  }

  if (!trader) {
    return (
      <Card>
        <CardContent className="text-loss text-center py-12">
          Trader not found
        </CardContent>
      </Card>
    );
  }

  return (
    <div className="space-y-4">
      <Card>
        <CardContent>
          <h1 className="text-2xl font-bold text-white">{trader.display_name}</h1>
          <p className="text-sm text-accent mt-1">{trader.strategy_name}</p>
          <p className="text-sm text-gray-400 mt-2">{trader.description}</p>

          <div className="flex gap-4 mt-4 text-sm">
            <div>
              <span className="text-gray-500">Status: </span>
              <span className={trader.is_active ? "text-profit" : "text-loss"}>
                {trader.is_active ? "Active" : "Inactive"}
              </span>
            </div>
            <div>
              <span className="text-gray-500">Since: </span>
              <span className="text-gray-300">{formatDate(trader.created_at)}</span>
            </div>
          </div>

          {trader.portfolios.length > 0 && (
            <div className="mt-4">
              <span className="text-xs text-gray-500 uppercase tracking-wider">Linked Portfolios</span>
              <div className="flex gap-2 mt-1">
                {trader.portfolios.map((name) => (
                  <Badge key={name} variant="open">{name}</Badge>
                ))}
              </div>
            </div>
          )}
        </CardContent>
      </Card>

      <div>
        <h2 className="text-lg font-bold text-white mb-3">Recent Trades</h2>
        {trades && trades.length > 0 ? (
          <div className="space-y-3">
            {trades.map((t) => (
              <TradeCard key={t.id} trade={t} />
            ))}
          </div>
        ) : (
          <Card>
            <CardContent className="text-gray-500 text-center py-8">
              No trades yet
            </CardContent>
          </Card>
        )}
      </div>
    </div>
  );
}
