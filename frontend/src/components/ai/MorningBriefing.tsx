"use client";

import { useState, useEffect, useCallback } from "react";
import { api } from "@/lib/api";
import { renderMarkdown } from "@/lib/markdown";
import type { BriefingResponse } from "@/lib/types";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Badge } from "@/components/ui/badge";

function SkeletonLines() {
  return (
    <div className="space-y-3 py-2">
      {[1, 2, 3, 4, 5, 6].map((i) => (
        <Skeleton
          key={i}
          variant="ai"
          className="h-4 rounded"
          style={{
            width: `${65 + Math.random() * 30}%`,
            animationDelay: `${i * 0.15}s`,
          }}
        />
      ))}
    </div>
  );
}

export default function MorningBriefing() {
  const [data, setData] = useState<BriefingResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [visibleSections, setVisibleSections] = useState(0);

  const fetchBriefing = useCallback(async (isRefresh = false) => {
    try {
      if (isRefresh) setRefreshing(true);
      else setLoading(true);

      setError(null);
      const result = await api.getBriefing();
      setData(result);
      setVisibleSections(0);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load briefing");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    fetchBriefing();
  }, [fetchBriefing]);

  // Stagger sections into view
  useEffect(() => {
    if (!data) return;
    const timer = setInterval(() => {
      setVisibleSections((prev) => {
        if (prev >= 10) {
          clearInterval(timer);
          return prev;
        }
        return prev + 1;
      });
    }, 150);
    return () => clearInterval(timer);
  }, [data]);

  const now = new Date();
  const timeStr = now.toLocaleString("en-US", {
    hour: "numeric",
    minute: "2-digit",
    hour12: true,
    timeZoneName: "short",
  });
  const dateStr = now.toLocaleDateString("en-US", {
    weekday: "long",
    month: "long",
    day: "numeric",
    year: "numeric",
  });

  return (
    <div className="ai-gradient-border">
      <div className="ai-card">
        {/* Header */}
        <div className="flex items-center justify-between mb-4">
          <div className="flex items-center gap-3">
            <div className="flex items-center gap-2">
              <span className="w-2 h-2 rounded-full bg-profit animate-pulse" />
              <span className="text-xs font-mono font-medium text-profit uppercase tracking-wider">
                Live
              </span>
            </div>
            <h2 className="text-lg font-semibold text-white">
              Today&apos;s Briefing
            </h2>
            {data && (
              <Badge variant="ai">
                {data.open_positions} open position{data.open_positions !== 1 ? "s" : ""}
              </Badge>
            )}
          </div>
          <div className="flex items-center gap-3">
            <span className="text-xs text-gray-500 font-mono">
              {dateStr} &middot; {timeStr}
            </span>
            <Button
              variant="ai-ghost"
              size="sm"
              onClick={() => fetchBriefing(true)}
              disabled={refreshing}
            >
              <svg
                className={`w-3.5 h-3.5 ${refreshing ? "animate-spin" : ""}`}
                fill="none"
                viewBox="0 0 24 24"
                stroke="currentColor"
                strokeWidth={2}
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"
                />
              </svg>
              {refreshing ? "Refreshing..." : "Refresh"}
            </Button>
          </div>
        </div>

        {/* Content */}
        {loading && <SkeletonLines />}

        {error && (
          <div className="flex items-center gap-2 py-4 px-4 rounded-lg border border-loss/30 bg-loss/5">
            <svg className="w-4 h-4 text-loss flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-2.5L13.732 4c-.77-.833-1.964-.833-2.732 0L4.082 16.5c-.77.833.192 2.5 1.732 2.5z" />
            </svg>
            <span className="text-sm text-loss">Briefing unavailable — </span>
            <Button
              variant="link"
              className="text-sm text-loss underline hover:text-loss/80 p-0 h-auto"
              onClick={() => fetchBriefing()}
            >
              retry
            </Button>
          </div>
        )}

        {data && !loading && (
          <div
            className="ai-prose"
            style={{
              opacity: visibleSections > 0 ? 1 : 0,
              transition: "opacity 0.3s ease-out",
            }}
            dangerouslySetInnerHTML={{ __html: renderMarkdown(data.briefing) }}
          />
        )}
      </div>
    </div>
  );
}
