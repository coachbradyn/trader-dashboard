"use client";
import LiveTradeFeed from "@/components/dashboard/LiveTradeFeed";

export default function FeedPage() {
  return (
    <div>
      <div className="mb-6">
        <h1 className="text-2xl font-bold">Live Trade Feed</h1>
        <p className="text-gray-400 mt-1 text-sm">
          Real-time entries and exits across all portfolios — updates every 5 seconds
        </p>
      </div>
      <LiveTradeFeed limit={100} />
    </div>
  );
}
