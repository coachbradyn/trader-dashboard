"use client";

import { useState, useEffect, useCallback } from "react";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";
import { formatCurrency, formatTimeAgo, formatDate, pnlColor } from "@/lib/formatters";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import type { ScannerOpportunity, ScannerStats, FmpUsage } from "@/lib/types";

const FONT_OUTFIT = { fontFamily: "'Outfit', sans-serif" } as const;
const FONT_MONO = { fontFamily: "'JetBrains Mono', monospace" } as const;

// ── Archetype badge ────────────────────────────────────────────────

function archetypeBadge(archetype?: string) {
  const map: Record<string, { bg: string; text: string }> = {
    momentum: { bg: "bg-amber-500/15 border-amber-500/30", text: "text-amber-400" },
    accumulation: { bg: "bg-ai-blue/15 border-ai-blue/30", text: "text-ai-blue" },
    catalyst: { bg: "bg-ai-purple/15 border-ai-purple/30", text: "text-ai-purple" },
    conviction: { bg: "bg-profit/15 border-profit/30", text: "text-profit" },
  };
  const style = map[archetype || ""] || { bg: "bg-gray-700/30 border-gray-600/30", text: "text-gray-400" };
  return style;
}

// ── Controls Section ───────────────────────────────────────────────

function ScannerControls({
  criteria,
  fmpUsage,
  running,
  onRunScan,
  stats,
}: {
  criteria: Record<string, unknown> | null;
  fmpUsage: FmpUsage | null;
  running: boolean;
  onRunScan: () => void;
  stats: ScannerStats | null;
}) {
  return (
    <div className="flex flex-wrap items-center gap-4 px-4 py-3 rounded-xl bg-surface-light/30 border border-border/50 mb-6">
      <Button
        onClick={onRunScan}
        disabled={running}
        className="bg-ai-blue/20 text-ai-blue border border-ai-blue/30 hover:bg-ai-blue/30"
        size="sm"
      >
        {running ? (
          <svg className="animate-spin h-3.5 w-3.5 mr-1" viewBox="0 0 24 24" fill="none">
            <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
            <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
          </svg>
        ) : (
          <svg className="w-3.5 h-3.5 mr-1" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M21 21l-5.197-5.197m0 0A7.5 7.5 0 105.196 5.196a7.5 7.5 0 0010.607 10.607z" />
          </svg>
        )}
        {running ? "Scanning..." : "Run Scan Now"}
      </Button>

      {/* Criteria summary */}
      {criteria && (
        <div className="flex items-center gap-3 text-[10px] text-gray-500 font-mono">
          {criteria.min_market_cap != null && Number(criteria.min_market_cap) > 0 && (
            <span>Cap: ${(Number(criteria.min_market_cap) / 1e9).toFixed(0)}B+</span>
          )}
          {criteria.min_volume != null && Number(criteria.min_volume) > 0 && (
            <span>Vol: {(Number(criteria.min_volume) / 1000).toFixed(0)}K+</span>
          )}
          {criteria.min_price != null && Number(criteria.min_price) > 0 && (
            <span>Price: ${Number(criteria.min_price)}+</span>
          )}
        </div>
      )}

      <div className="w-px h-4 bg-border hidden sm:block" />

      {/* Stats */}
      {stats && (
        <div className="flex items-center gap-3 text-[10px] text-gray-500 font-mono">
          <span>Total: {stats.total_opportunities ?? 0}</span>
          <span>Hit rate: <span className={(stats.hit_rate ?? 0) >= 50 ? "text-profit" : "text-loss"}>{(stats.hit_rate ?? 0).toFixed(0)}%</span></span>
          <span>Avg conf: {(stats.avg_confidence ?? 0).toFixed(1)}/10</span>
        </div>
      )}

      {/* FMP usage */}
      {fmpUsage && (
        <div className="flex items-center gap-2 ml-auto text-[10px] font-mono">
          <span className={fmpUsage.throttled ? "text-loss" : "text-gray-500"}>
            FMP: {fmpUsage.calls_today}/{fmpUsage.limit}
          </span>
          <div className="w-16 h-1.5 rounded-full bg-surface-light/40 overflow-hidden">
            <div
              className={`h-full rounded-full transition-all ${
                fmpUsage.remaining < 50 ? "bg-loss" : fmpUsage.remaining < 200 ? "bg-amber-400" : "bg-profit"
              }`}
              style={{ width: `${Math.min(100, (fmpUsage.calls_today / fmpUsage.limit) * 100)}%` }}
            />
          </div>
        </div>
      )}
    </div>
  );
}

// ── Opportunity Card ───────────────────────────────────────────────

function OpportunityCard({
  opp,
  onAddWatchlist,
  onViewDetail,
}: {
  opp: ScannerOpportunity;
  onAddWatchlist: (ticker: string) => void;
  onViewDetail: (ticker: string) => void;
}) {
  const badge = archetypeBadge(opp.position_archetype);
  const [expanded, setExpanded] = useState(false);

  const hasLevels = opp.entry_level != null && opp.stop_level != null && opp.target_level != null;

  return (
    <Card className="border-border/40 hover:border-ai-blue/30 transition">
      <CardContent className="p-5">
        {/* Header */}
        <div className="flex items-start justify-between mb-3">
          <div>
            <div className="flex items-center gap-2 mb-1">
              <span className="text-lg font-bold text-white" style={FONT_OUTFIT}>{opp.ticker}</span>
              <Badge className={`text-[9px] ${
                opp.direction === "long" ? "bg-profit/15 text-profit" : "bg-loss/15 text-loss"
              }`}>{opp.direction.toUpperCase()}</Badge>
              <Badge className={`text-[9px] ${badge.bg} ${badge.text} border`}>
                {opp.position_archetype || opp.action_type}
              </Badge>
            </div>
          </div>
          {opp.expires_at && (
            <span className="text-[9px] text-amber-400/60 font-mono shrink-0">
              Expires {formatTimeAgo(opp.expires_at)}
            </span>
          )}
        </div>

        {/* Thesis */}
        <p className="text-xs text-gray-400 leading-relaxed mb-3">
          {expanded ? opp.reasoning : opp.reasoning.slice(0, 200) + (opp.reasoning.length > 200 ? "..." : "")}
          {opp.reasoning.length > 200 && (
            <button onClick={() => setExpanded(!expanded)} className="text-ai-blue/70 hover:text-ai-blue ml-1">
              {expanded ? "less" : "more"}
            </button>
          )}
        </p>

        {/* Entry / Stop / Target bar */}
        {hasLevels && (
          <div className="mb-3">
            <div className="flex items-center justify-between text-[9px] font-mono text-gray-500 mb-1">
              <span className="text-loss">Stop ${opp.stop_level!.toFixed(2)}</span>
              <span className="text-white">Entry ${opp.entry_level!.toFixed(2)}</span>
              <span className="text-profit">Target ${opp.target_level!.toFixed(2)}</span>
            </div>
            <div className="relative h-2 rounded-full bg-surface-light/40 overflow-hidden">
              {(() => {
                const range = opp.target_level! - opp.stop_level!;
                const entryPct = range > 0 ? ((opp.entry_level! - opp.stop_level!) / range) * 100 : 50;
                return (
                  <>
                    <div className="absolute left-0 h-full bg-loss/30 rounded-l-full" style={{ width: `${entryPct}%` }} />
                    <div className="absolute h-full bg-profit/30 rounded-r-full" style={{ left: `${entryPct}%`, width: `${100 - entryPct}%` }} />
                    <div className="absolute top-0 h-full w-0.5 bg-white" style={{ left: `${entryPct}%` }} />
                  </>
                );
              })()}
            </div>
          </div>
        )}

        {/* Confidence bar */}
        <div className="flex items-center gap-2 mb-3">
          <span className="text-[10px] text-gray-500 font-mono w-20">Conf {opp.confidence}/10</span>
          <div className="flex-1 h-1.5 rounded-full bg-surface-light/40 overflow-hidden">
            <div
              className={`h-full rounded-full transition-all ${
                opp.confidence >= 8 ? "bg-profit" : opp.confidence >= 5 ? "bg-amber-400" : "bg-loss"
              }`}
              style={{ width: `${opp.confidence * 10}%` }}
            />
          </div>
        </div>

        {/* Price info */}
        <div className="flex items-center gap-3 text-[10px] text-gray-500 font-mono mb-3">
          {opp.current_price != null && <span>Current: ${opp.current_price.toFixed(2)}</span>}
          {opp.suggested_price != null && <span>Suggested: ${opp.suggested_price.toFixed(2)}</span>}
          <span className="text-gray-600">{formatTimeAgo(opp.created_at)}</span>
        </div>

        {/* Actions */}
        <div className="flex items-center gap-2">
          <Button
            size="sm"
            onClick={() => onAddWatchlist(opp.ticker)}
            className="text-[10px] h-7 bg-amber-500/15 text-amber-400 border border-amber-500/30 hover:bg-amber-500/25"
          >
            + Watchlist
          </Button>
          <Button
            size="sm"
            onClick={() => onViewDetail(opp.ticker)}
            className="text-[10px] h-7 bg-ai-blue/15 text-ai-blue border border-ai-blue/30 hover:bg-ai-blue/25"
          >
            View Detail
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

// ── History Section ────────────────────────────────────────────────

function ScannerHistory({ history }: { history: ScannerOpportunity[] }) {
  const [expanded, setExpanded] = useState(false);

  if (history.length === 0) return null;

  return (
    <div className="mt-6">
      <button
        onClick={() => setExpanded(!expanded)}
        className="flex items-center gap-2 text-sm font-semibold text-gray-400 hover:text-white transition mb-3"
        style={FONT_OUTFIT}
      >
        <svg className={`w-4 h-4 transition ${expanded ? "rotate-180" : ""}`} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
          <path strokeLinecap="round" strokeLinejoin="round" d="M19.5 8.25l-7.5 7.5-7.5-7.5" />
        </svg>
        Scanner History ({history.length})
      </button>

      {expanded && (
        <div className="space-y-2 max-h-96 overflow-y-auto">
          {history.map((h) => (
            <div key={h.id} className="flex items-center gap-3 p-3 rounded-lg border border-border/30 bg-surface-light/10">
              <span className="text-sm font-bold text-white font-mono">{h.ticker}</span>
              <Badge className={`text-[9px] ${
                h.direction === "long" ? "bg-profit/15 text-profit" : "bg-loss/15 text-loss"
              }`}>{h.direction}</Badge>
              <span className="text-[10px] text-gray-500 font-mono">conf {h.confidence}/10</span>
              <Badge className={`text-[9px] ${
                h.status === "approved" ? "bg-profit/15 text-profit" :
                h.status === "rejected" ? "bg-loss/15 text-loss" :
                "bg-gray-600/15 text-gray-400"
              }`}>{h.status}</Badge>
              <span className="text-[10px] text-gray-600 ml-auto">{formatDate(h.created_at)}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ── Main Page ──────────────────────────────────────────────────────

export default function ScannerPage() {
  const router = useRouter();
  const [results, setResults] = useState<ScannerOpportunity[]>([]);
  const [history, setHistory] = useState<ScannerOpportunity[]>([]);
  const [criteria, setCriteria] = useState<Record<string, unknown> | null>(null);
  const [stats, setStats] = useState<ScannerStats | null>(null);
  const [fmpUsage, setFmpUsage] = useState<FmpUsage | null>(null);
  const [loading, setLoading] = useState(true);
  const [running, setRunning] = useState(false);

  const fetchAll = useCallback(async () => {
    try {
      const [r, h, c, s, f] = await Promise.all([
        api.getScannerResults().catch(() => []),
        api.getScannerHistory().catch(() => []),
        api.getScannerCriteria().catch(() => null),
        api.getScannerStats().catch(() => null),
        api.getFmpUsage().catch(() => null),
      ]);
      setResults(r);
      setHistory(h);
      setCriteria(c);
      setStats(s);
      setFmpUsage(f);
    } catch {}
    setLoading(false);
  }, []);

  useEffect(() => { fetchAll(); }, [fetchAll]);

  // Poll every 60s
  useEffect(() => {
    const interval = setInterval(fetchAll, 60000);
    return () => clearInterval(interval);
  }, [fetchAll]);

  const handleRunScan = async () => {
    setRunning(true);
    try {
      await api.runScanner();
      // Refresh after a brief delay
      setTimeout(fetchAll, 3000);
    } catch {}
    setRunning(false);
  };

  const handleAddWatchlist = async (ticker: string) => {
    try {
      await api.addWatchlistTickers([ticker]);
    } catch {}
  };

  return (
    <div>
      {/* Header */}
      <div className="mb-6">
        <div className="flex items-center gap-3 mb-1">
          <div className="w-8 h-8 rounded-lg bg-ai-blue/10 flex items-center justify-center">
            <svg className="w-4 h-4 text-ai-blue" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M21 21l-5.197-5.197m0 0A7.5 7.5 0 105.196 5.196a7.5 7.5 0 0010.607 10.607z" />
            </svg>
          </div>
          <div>
            <h1 className="text-xl font-bold text-white" style={FONT_OUTFIT}>Scanner</h1>
            <p className="text-xs text-gray-500">
              Henry&apos;s proactive market scanning - {results.length} active opportunit{results.length !== 1 ? "ies" : "y"}
            </p>
          </div>
        </div>
      </div>

      {/* Controls */}
      <ScannerControls
        criteria={criteria}
        fmpUsage={fmpUsage}
        running={running}
        onRunScan={handleRunScan}
        stats={stats}
      />

      {/* Loading */}
      {loading && (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {[1, 2, 3, 4].map((i) => <Skeleton key={i} className="h-64 rounded-xl" />)}
        </div>
      )}

      {/* Empty */}
      {!loading && results.length === 0 && (
        <div className="flex flex-col items-center justify-center py-20 text-center">
          <div className="w-16 h-16 rounded-full bg-ai-blue/10 flex items-center justify-center mb-4">
            <svg className="w-8 h-8 text-ai-blue/40" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M21 21l-5.197-5.197m0 0A7.5 7.5 0 105.196 5.196a7.5 7.5 0 0010.607 10.607z" />
            </svg>
          </div>
          <h2 className="text-lg font-semibold text-white mb-2" style={FONT_OUTFIT}>No scan results yet</h2>
          <p className="text-sm text-gray-500 max-w-md">
            Click &quot;Run Scan Now&quot; to have Henry analyze the market for opportunities.
          </p>
        </div>
      )}

      {/* Opportunity Grid */}
      {!loading && results.length > 0 && (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {results.map((opp) => (
            <OpportunityCard
              key={opp.id}
              opp={opp}
              onAddWatchlist={handleAddWatchlist}
              onViewDetail={(ticker) => router.push(`/screener/${ticker}`)}
            />
          ))}
        </div>
      )}

      {/* History */}
      {!loading && <ScannerHistory history={history} />}
    </div>
  );
}
