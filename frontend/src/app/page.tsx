"use client";
import { useState } from "react";
import { useLeaderboard } from "@/hooks/useLeaderboard";
import LeaderboardTable from "@/components/leaderboard/LeaderboardTable";
import { Skeleton } from "@/components/ui/skeleton";

export default function HomePage() {
  const [sortBy, setSortBy] = useState("total_return_pct");
  const { data: entries, loading } = useLeaderboard(sortBy);

  return (
    <div>
      <div className="mb-6">
        <h1 className="text-3xl font-bold">Live Trading Dashboard</h1>
        <p className="text-gray-400 mt-1">
          Real-time multi-strategy portfolio tracking — powered by TradingView webhooks
        </p>
      </div>

      {loading ? (
        <Skeleton className="h-64 rounded-xl" />
      ) : (
        <LeaderboardTable
          entries={entries || []}
          sortBy={sortBy}
          onSortChange={setSortBy}
        />
      )}
    </div>
  );
}
