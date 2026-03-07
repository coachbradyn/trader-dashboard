"use client";

import { useState } from "react";
import { usePolling } from "@/hooks/usePolling";
import { api } from "@/lib/api";
import { formatTimeAgo, formatDateTime } from "@/lib/formatters";
import type { ConflictResolution } from "@/lib/types";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Collapsible, CollapsibleTrigger, CollapsibleContent } from "@/components/ui/collapsible";
import { Separator } from "@/components/ui/separator";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";

const FILTER_OPTIONS = [
  { label: "1 Day", value: 1 },
  { label: "7 Days", value: 7 },
  { label: "30 Days", value: 30 },
] as const;

function ConfidenceGauge({ confidence }: { confidence: number }) {
  const segments = 10;
  const colorClass =
    confidence >= 7
      ? "confidence-high"
      : confidence >= 4
        ? "confidence-mid"
        : "confidence-low";

  const textColor =
    confidence >= 7
      ? "text-profit"
      : confidence >= 4
        ? "text-yellow-500"
        : "text-loss";

  return (
    <div className="flex items-center gap-2">
      <div className="flex gap-0.5">
        {Array.from({ length: segments }, (_, i) => (
          <div
            key={i}
            className={`w-2.5 h-3 rounded-sm transition-all duration-300 ${
              i < confidence ? colorClass : "bg-surface-light/60"
            }`}
            style={{
              animationDelay: `${i * 30}ms`,
            }}
          />
        ))}
      </div>
      <span className={`text-xs font-mono font-bold ${textColor}`}>
        {confidence}/10
      </span>
    </div>
  );
}

function StrategyBadge({
  trader,
  direction,
}: {
  trader: string;
  direction: string;
}) {
  const label = trader
    .replace("henry-", "")
    .toUpperCase();
  const isLong = direction.toLowerCase() === "long";

  return (
    <span
      className={`inline-flex items-center gap-1 px-2 py-0.5 rounded text-xs font-mono font-medium ${
        isLong
          ? "bg-profit/12 text-profit"
          : "bg-loss/12 text-loss"
      }`}
    >
      {label}{" "}
      <span className="opacity-70">{isLong ? "LONG" : "SHORT"}</span>
    </span>
  );
}

function ConflictRow({ conflict }: { conflict: ConflictResolution }) {
  const [expanded, setExpanded] = useState(false);

  const isRecent =
    Date.now() - new Date(conflict.created_at).getTime() < 24 * 60 * 60 * 1000;

  // Map strategies to their directions from signals
  const strategyDirections: Record<string, string> = {};
  if (conflict.signals) {
    conflict.signals.forEach((s) => {
      strategyDirections[s.trader] = s.dir;
    });
  }

  return (
    <Collapsible open={expanded} onOpenChange={setExpanded}>
      <div
        className={`rounded-lg border transition-colors ${
          expanded
            ? "border-ai-blue/30 bg-surface-light/20"
            : "border-border/50 hover:border-border"
        }`}
      >
        <CollapsibleTrigger asChild>
          <button className="w-full px-4 py-3 text-left">
            <div className="flex items-center gap-4">
              {/* Timestamp */}
              <span className="text-xs text-gray-500 font-mono w-24 flex-shrink-0">
                {isRecent
                  ? formatTimeAgo(conflict.created_at)
                  : formatDateTime(conflict.created_at)}
              </span>

              {/* Ticker */}
              <span className="text-sm font-mono font-bold text-white w-16 flex-shrink-0">
                {conflict.ticker}
              </span>

              {/* Strategy badges */}
              <div className="flex items-center gap-1.5 flex-shrink-0">
                {conflict.strategies.map((s, i) => (
                  <span key={s} className="flex items-center gap-1.5">
                    <StrategyBadge
                      trader={s}
                      direction={strategyDirections[s] || "long"}
                    />
                    {i < conflict.strategies.length - 1 && (
                      <span className="text-xs text-gray-600">vs</span>
                    )}
                  </span>
                ))}
              </div>

              {/* Confidence gauge */}
              <div className="ml-auto flex items-center gap-4">
                <ConfidenceGauge confidence={conflict.confidence} />

                {/* Short recommendation */}
                <span className="text-xs text-gray-400 max-w-[240px] truncate hidden lg:block">
                  &ldquo;{conflict.reasoning.slice(0, 80)}
                  {conflict.reasoning.length > 80 ? "..." : ""}&rdquo;
                </span>

                {/* Expand arrow */}
                <svg
                  className={`w-4 h-4 text-gray-500 transition-transform ${
                    expanded ? "rotate-180" : ""
                  }`}
                  fill="none"
                  viewBox="0 0 24 24"
                  stroke="currentColor"
                  strokeWidth={2}
                >
                  <path
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    d="M19 9l-7 7-7-7"
                  />
                </svg>
              </div>
            </div>
          </button>
        </CollapsibleTrigger>

        {/* Expanded reasoning */}
        <CollapsibleContent>
          <div className="px-4 pb-4 animate-fade-in">
            <div className="rounded-lg bg-terminal p-4 mt-1 border border-border/30">
              <p className="text-sm text-gray-300 leading-relaxed font-mono">
                {conflict.reasoning}
              </p>

              {/* Signal details */}
              {conflict.signals && conflict.signals.length > 0 && (
                <div className="mt-3 pt-3 border-t border-gray-800 space-y-1">
                  {conflict.signals.map((s, i) => (
                    <div key={i} className="text-xs font-mono text-gray-500">
                      {s.trader.replace("henry-", "").toUpperCase()}: {s.dir.toUpperCase()}{" "}
                      @ ${s.price.toFixed(2)} | sig={s.sig.toFixed(1)} adx=
                      {s.adx.toFixed(1)}
                    </div>
                  ))}
                </div>
              )}

              {/* Recommendation */}
              <div className="mt-3 pt-3 border-t border-gray-800 flex items-center gap-2">
                <span className="text-xs text-gray-500 font-mono">
                  Recommendation:
                </span>
                <span
                  className={`text-xs font-mono font-bold ${
                    conflict.recommendation === "LONG"
                      ? "text-profit"
                      : conflict.recommendation === "SHORT"
                        ? "text-loss"
                        : "text-yellow-500"
                  }`}
                >
                  {conflict.recommendation}
                </span>
              </div>
            </div>
          </div>
        </CollapsibleContent>
      </div>
    </Collapsible>
  );
}

export default function ConflictLog() {
  const [daysBack, setDaysBack] = useState(7);

  const { data, loading } = usePolling(
    () => api.getConflicts(daysBack),
    30000
  );

  const conflicts = data || [];

  return (
    <div className="ai-card">
      {/* Header */}
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-3">
          <div className="w-8 h-8 rounded-lg bg-yellow-500/10 flex items-center justify-center">
            <svg className="w-4 h-4 text-yellow-500" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M13 10V3L4 14h7v7l9-11h-7z" />
            </svg>
          </div>
          <h3 className="text-base font-semibold text-white">Conflict Log</h3>
          {conflicts.length > 0 && (
            <Badge variant="ai">{conflicts.length}</Badge>
          )}
        </div>

        {/* Filter */}
        <Tabs
          value={String(daysBack)}
          onValueChange={(v) => setDaysBack(Number(v))}
        >
          <TabsList>
            {FILTER_OPTIONS.map((opt) => (
              <TabsTrigger key={opt.value} value={String(opt.value)}>
                {opt.label}
              </TabsTrigger>
            ))}
          </TabsList>
        </Tabs>
      </div>

      {/* Separator */}
      <Separator className="-mx-5 mb-4" />

      {/* Content */}
      <div className="space-y-2">
        {loading && conflicts.length === 0 && (
          <div className="space-y-2">
            {[1, 2, 3].map((i) => (
              <Skeleton key={i} variant="ai" className="h-14 rounded-lg" />
            ))}
          </div>
        )}

        {!loading && conflicts.length === 0 && (
          <div className="flex items-center justify-center gap-2 py-8">
            <svg className="w-5 h-5 text-ai-blue/50" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
            </svg>
            <span className="text-sm text-gray-500">
              No conflicts detected — strategies are aligned
            </span>
          </div>
        )}

        {conflicts.map((c) => (
          <ConflictRow key={c.id} conflict={c} />
        ))}
      </div>
    </div>
  );
}
