"use client";

import { useState } from "react";
import { api } from "@/lib/api";
import { renderMarkdown } from "@/lib/markdown";
import type { ReviewResponse } from "@/lib/types";

const DAY_OPTIONS = [
  { label: "1D", value: 1 },
  { label: "3D", value: 3 },
  { label: "5D", value: 5 },
] as const;

function SkeletonReview() {
  return (
    <div className="space-y-3 pt-2">
      {[1, 2, 3, 4, 5, 6, 7, 8].map((i) => (
        <div
          key={i}
          className="ai-skeleton h-3.5 rounded"
          style={{
            width: `${50 + Math.random() * 45}%`,
            animationDelay: `${i * 0.12}s`,
          }}
        />
      ))}
    </div>
  );
}

export default function TradeReview() {
  const [daysBack, setDaysBack] = useState(1);
  const [data, setData] = useState<ReviewResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const runAnalysis = async () => {
    try {
      setLoading(true);
      setError(null);
      const result = await api.postReview(daysBack);
      setData(result);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Analysis failed");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="ai-card h-full flex flex-col">
      {/* Header */}
      <div className="flex items-center gap-3 mb-4">
        <div className="w-8 h-8 rounded-lg bg-ai-blue/10 flex items-center justify-center">
          <svg className="w-4 h-4 text-ai-blue" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z" />
          </svg>
        </div>
        <h3 className="text-base font-semibold text-white">Trade Review</h3>
      </div>

      {/* Day selector */}
      <div className="flex items-center gap-2 mb-4">
        <div className="flex rounded-lg overflow-hidden border border-border">
          {DAY_OPTIONS.map((opt) => (
            <button
              key={opt.value}
              onClick={() => setDaysBack(opt.value)}
              className={`px-4 py-1.5 text-xs font-mono font-medium transition-colors ${
                daysBack === opt.value
                  ? "bg-ai-blue text-white"
                  : "bg-surface-light/50 text-gray-400 hover:text-white hover:bg-surface-light"
              }`}
            >
              {opt.label}
            </button>
          ))}
        </div>

        <button
          onClick={runAnalysis}
          disabled={loading}
          className="ml-auto flex items-center gap-2 px-4 py-1.5 rounded-lg text-sm font-medium
                     bg-ai-blue text-white hover:bg-ai-blue/90 transition-colors
                     disabled:opacity-50 disabled:cursor-not-allowed"
        >
          {loading ? (
            <>
              <span className="w-1.5 h-1.5 rounded-full bg-white animate-pulse" />
              Analyzing...
            </>
          ) : (
            <>
              <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M14.752 11.168l-3.197-2.132A1 1 0 0010 9.87v4.263a1 1 0 001.555.832l3.197-2.132a1 1 0 000-1.664z" />
                <path strokeLinecap="round" strokeLinejoin="round" d="M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
              </svg>
              Run Analysis
            </>
          )}
        </button>
      </div>

      {/* Content area */}
      <div className="flex-1 min-h-0 overflow-y-auto">
        {loading && <SkeletonReview />}

        {error && !loading && (
          <div className="flex items-center gap-2 py-3 px-3 rounded-lg border border-loss/30 bg-loss/5">
            <svg className="w-4 h-4 text-loss flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-2.5L13.732 4c-.77-.833-1.964-.833-2.732 0L4.082 16.5c-.77.833.192 2.5 1.732 2.5z" />
            </svg>
            <span className="text-sm text-loss">{error}</span>
          </div>
        )}

        {!data && !loading && !error && (
          <div className="flex flex-col items-center justify-center py-12 text-center">
            <div className="w-12 h-12 rounded-full bg-ai-blue/10 flex items-center justify-center mb-3">
              <svg className="w-6 h-6 text-ai-blue/50" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z" />
              </svg>
            </div>
            <p className="text-sm text-gray-500">
              Select a time range and run analysis
            </p>
            <p className="text-xs text-gray-600 mt-1">
              Reviews patterns across strategies and exit reasons
            </p>
          </div>
        )}

        {data && !loading && (
          <div className="animate-fade-in">
            <div className="flex items-center gap-2 mb-3 pb-3 border-b border-border/50">
              <span className="text-xs text-gray-500 font-mono">
                {data.trades_analyzed} signals analyzed
              </span>
            </div>
            <div
              className="ai-prose"
              dangerouslySetInnerHTML={{ __html: renderMarkdown(data.review) }}
            />
          </div>
        )}
      </div>
    </div>
  );
}
