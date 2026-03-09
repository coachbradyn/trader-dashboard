"use client";
import { useState } from "react";
import { useLeaderboard } from "@/hooks/useLeaderboard";
import LeaderboardTable from "@/components/leaderboard/LeaderboardTable";
import { Skeleton } from "@/components/ui/skeleton";

export default function LeaderboardPage() {
  const [sortBy, setSortBy] = useState("total_return_pct");
  const { data: entries, loading } = useLeaderboard(sortBy);

  return (
    <div>
      <div className="mb-6">
        <h1 className="text-xl sm:text-2xl font-bold">Strategy Leaderboard</h1>
        <p className="text-gray-400 mt-1 text-sm">
          Real-time multi-strategy performance ranking
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
