"use client";

import { useState, useEffect, useCallback, useMemo, useRef } from "react";
import { useRouter } from "next/navigation";
import {
  Play, RefreshCw, Download, ChevronUp, ChevronDown,
  Radar, Plus, ExternalLink,
} from "lucide-react";
import { api } from "@/lib/api";
import { formatTimeAgo, formatDate } from "@/lib/formatters";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import type {
  ScannerOpportunity, ScanProfile, ScannerStats, FmpUsage,
} from "@/lib/types";

const FONT_OUTFIT = { fontFamily: "'Outfit', sans-serif" } as const;
const FONT_MONO = { fontFamily: "'JetBrains Mono', monospace" } as const;

type SortKey = "ticker" | "confidence" | "current_price" | "suggested_price" | "created_at" | "expires_at";
type SortDir = "asc" | "desc";
type DirFilter = "all" | "long" | "short";
type ArchFilter = "all" | "momentum" | "accumulation" | "catalyst" | "conviction";

const ARCHETYPE_STYLE: Record<string, { bg: string; text: string }> = {
  momentum: { bg: "bg-amber-500/15 border-amber-500/30", text: "text-amber-400" },
  accumulation: { bg: "bg-ai-blue/15 border-ai-blue/30", text: "text-ai-blue" },
  catalyst: { bg: "bg-ai-purple/15 border-ai-purple/30", text: "text-ai-purple" },
  conviction: { bg: "bg-profit/15 border-profit/30", text: "text-profit" },
};

function archetypeClass(a?: string) {
  return ARCHETYPE_STYLE[a || ""] || { bg: "bg-gray-700/30 border-gray-600/30", text: "text-gray-400" };
}

// ── CSV export ────────────────────────────────────────────────────
function exportCsv(rows: ScannerOpportunity[]) {
  const headers = ["ticker", "direction", "archetype", "confidence", "current_price", "suggested_price", "entry", "stop", "target", "status", "expires_at", "created_at", "reasoning"];
  const lines = [headers.join(",")];
  for (const r of rows) {
    const vals = [
      r.ticker, r.direction, r.position_archetype || r.action_type,
      String(r.confidence),
      r.current_price ?? "", r.suggested_price ?? "",
      r.entry_level ?? "", r.stop_level ?? "", r.target_level ?? "",
      r.status,
      r.expires_at ?? "", r.created_at,
      `"${(r.reasoning || "").replace(/"/g, '""').replace(/\s+/g, " ").slice(0, 300)}"`,
    ];
    lines.push(vals.join(","));
  }
  const blob = new Blob([lines.join("\n")], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `scanner-${new Date().toISOString().slice(0, 10)}.csv`;
  document.body.appendChild(a); a.click(); a.remove();
  URL.revokeObjectURL(url);
}

// ── Status bar ────────────────────────────────────────────────────
function StatusBar({
  running, onRun, lastScanAt, stats, fmpUsage, activeProfileName,
}: {
  running: boolean; onRun: () => void; lastScanAt: string | null;
  stats: ScannerStats | null; fmpUsage: FmpUsage | null;
  activeProfileName: string;
}) {
  return (
    <div className="flex flex-wrap items-center gap-3 px-4 py-3 rounded-xl bg-[#111827]/60 border border-[#1f2937] mb-4">
      <button
        onClick={onRun}
        disabled={running}
        className="flex items-center gap-1.5 bg-ai-blue/15 text-ai-blue border border-ai-blue/30 hover:bg-ai-blue/25 px-3 py-1.5 rounded-lg text-xs font-semibold transition disabled:opacity-50"
        style={FONT_OUTFIT}
      >
        {running ? <RefreshCw className="w-3.5 h-3.5 animate-spin" /> : <Play className="w-3.5 h-3.5" strokeWidth={2} />}
        {running ? `Scanning ${activeProfileName}…` : "Scan now"}
      </button>

      {lastScanAt && (
        <span className="text-[10px] text-gray-500 font-mono" style={FONT_MONO}>
          Last: {formatTimeAgo(lastScanAt)}
        </span>
      )}

      {stats && (
        <div className="flex items-center gap-3 text-[10px] font-mono text-gray-500" style={FONT_MONO}>
          <span>Total: <span className="text-white">{stats.total_opportunities ?? 0}</span></span>
          <span>Hit: <span className={(stats.hit_rate ?? 0) >= 50 ? "text-profit" : "text-loss"}>{(stats.hit_rate ?? 0).toFixed(0)}%</span></span>
          <span>Conf: <span className="text-white">{(stats.avg_confidence ?? 0).toFixed(1)}/10</span></span>
        </div>
      )}

      {fmpUsage && (
        <div className="flex items-center gap-2 ml-auto text-[10px] font-mono" style={FONT_MONO}>
          <span className={fmpUsage.throttled ? "text-loss" : "text-gray-500"}>
            FMP {fmpUsage.calls_today}/{fmpUsage.limit}
          </span>
          <div className="w-16 h-1.5 rounded-full bg-[#1f2937]/50 overflow-hidden">
            <div className={`h-full ${fmpUsage.remaining < 50 ? "bg-loss" : fmpUsage.remaining < 200 ? "bg-amber-400" : "bg-profit"}`}
              style={{ width: `${Math.min(100, (fmpUsage.calls_today / fmpUsage.limit) * 100)}%` }} />
          </div>
        </div>
      )}
    </div>
  );
}

// ── Profile tabs ──────────────────────────────────────────────────
function ProfileTabs({
  profiles, activeId, onSelect, running, results,
}: {
  profiles: ScanProfile[]; activeId: string;
  onSelect: (id: string) => void;
  running: boolean; results: ScannerOpportunity[];
}) {
  const profileCount = (profileName: string) => {
    const n = profileName.toLowerCase();
    return results.filter((r) => (r.position_archetype || "").toLowerCase() === n).length;
  };
  return (
    <div className="flex gap-1 mb-4 overflow-x-auto pb-1">
      <ProfileTab
        label="All" count={results.length}
        active={activeId === "all"} onClick={() => onSelect("all")}
        statusDot={running ? "amber" : "gray"}
      />
      {profiles.map((p) => (
        <ProfileTab
          key={p.id} label={p.name} count={profileCount(p.name)}
          active={activeId === p.id} onClick={() => onSelect(p.id)}
          statusDot={running && activeId === p.id ? "amber" : p.enabled ? "green" : "gray"}
        />
      ))}
    </div>
  );
}

function ProfileTab({
  label, count, active, onClick, statusDot,
}: {
  label: string; count: number; active: boolean; onClick: () => void;
  statusDot: "green" | "amber" | "gray";
}) {
  const dotColor = statusDot === "green" ? "bg-profit" : statusDot === "amber" ? "bg-amber-400 animate-pulse" : "bg-gray-600";
  return (
    <button
      onClick={onClick}
      className={`flex items-center gap-2 px-3 py-1.5 rounded-lg text-xs whitespace-nowrap transition ${
        active ? "bg-ai-blue/20 text-ai-blue border border-ai-blue/30" : "bg-[#1f2937]/40 text-gray-400 hover:text-white border border-transparent"
      }`}
      style={FONT_OUTFIT}
    >
      <span className={`w-1.5 h-1.5 rounded-full ${dotColor}`} />
      <span className="capitalize">{label}</span>
      <span className="text-[9px] text-gray-500 font-mono" style={FONT_MONO}>{count}</span>
    </button>
  );
}

// ── Filters ───────────────────────────────────────────────────────
function FiltersRow({
  minConf, setMinConf, dirFilter, setDirFilter, archFilter, setArchFilter, onExport, totalFiltered,
}: {
  minConf: number; setMinConf: (n: number) => void;
  dirFilter: DirFilter; setDirFilter: (d: DirFilter) => void;
  archFilter: ArchFilter; setArchFilter: (a: ArchFilter) => void;
  onExport: () => void; totalFiltered: number;
}) {
  return (
    <div className="flex flex-wrap items-center gap-3 mb-3 px-4 py-2.5 rounded-xl bg-[#0f1522]/60 border border-[#1f2937]">
      {/* Min confidence */}
      <div className="flex items-center gap-2">
        <span className="text-[10px] text-gray-500 uppercase tracking-wider" style={FONT_OUTFIT}>Min conf</span>
        <input
          type="range" min={0} max={10} step={1} value={minConf}
          onChange={(e) => setMinConf(parseInt(e.target.value))}
          className="w-24 accent-ai-blue"
          aria-label="Minimum confidence"
        />
        <span className="text-[11px] text-ai-blue font-mono w-6" style={FONT_MONO}>{minConf}</span>
      </div>

      {/* Direction */}
      <div className="flex rounded-lg overflow-hidden border border-[#1f2937]">
        {(["all", "long", "short"] as const).map((d) => (
          <button key={d}
            onClick={() => setDirFilter(d)}
            className={`px-2.5 py-1 text-[10px] capitalize transition ${
              dirFilter === d ? "bg-ai-blue/20 text-ai-blue" : "bg-[#111827]/40 text-gray-500 hover:text-gray-300"
            }`}
            style={FONT_OUTFIT}
          >{d}</button>
        ))}
      </div>

      {/* Archetype */}
      <div className="flex rounded-lg overflow-hidden border border-[#1f2937]">
        {(["all", "momentum", "accumulation", "catalyst", "conviction"] as const).map((a) => (
          <button key={a}
            onClick={() => setArchFilter(a)}
            className={`px-2.5 py-1 text-[10px] capitalize transition ${
              archFilter === a ? "bg-ai-blue/20 text-ai-blue" : "bg-[#111827]/40 text-gray-500 hover:text-gray-300"
            }`}
            style={FONT_OUTFIT}
          >{a}</button>
        ))}
      </div>

      <span className="text-[10px] text-gray-600 font-mono ml-auto" style={FONT_MONO}>
        {totalFiltered} result{totalFiltered !== 1 ? "s" : ""}
      </span>

      <button
        onClick={onExport}
        className="flex items-center gap-1 text-[11px] text-gray-400 hover:text-white border border-[#1f2937] bg-[#111827]/40 hover:bg-[#1f2937]/60 px-2.5 py-1 rounded-lg transition"
        style={FONT_OUTFIT}
      >
        <Download className="w-3 h-3" strokeWidth={2} />
        CSV
      </button>
    </div>
  );
}

// ── Table ─────────────────────────────────────────────────────────
function SortHeader({
  label, sortKey, currentKey, dir, onSort, align,
}: {
  label: string; sortKey: SortKey; currentKey: SortKey; dir: SortDir;
  onSort: (k: SortKey) => void; align?: "left" | "right" | "center";
}) {
  const active = sortKey === currentKey;
  return (
    <th
      onClick={() => onSort(sortKey)}
      className={`px-3 py-2 text-[10px] uppercase tracking-wider text-gray-500 font-semibold cursor-pointer select-none hover:text-gray-200 ${
        align === "right" ? "text-right" : align === "center" ? "text-center" : "text-left"
      }`}
      style={FONT_OUTFIT}
    >
      <span className="inline-flex items-center gap-1">
        {label}
        {active && (dir === "asc" ? <ChevronUp className="w-3 h-3" /> : <ChevronDown className="w-3 h-3" />)}
      </span>
    </th>
  );
}

function OpportunityRow({
  opp, onNav, onAdd,
}: {
  opp: ScannerOpportunity;
  onNav: (ticker: string) => void;
  onAdd: (ticker: string) => void;
}) {
  const arch = archetypeClass(opp.position_archetype);
  const confColor = opp.confidence >= 8 ? "bg-profit" : opp.confidence >= 5 ? "bg-amber-400" : "bg-loss";
  const expiryStr = opp.expires_at ? formatTimeAgo(opp.expires_at) : "—";
  const expiryTone = opp.expires_at && (new Date(opp.expires_at).getTime() - Date.now()) < 3600_000 * 6
    ? "text-amber-400" : "text-gray-500";

  return (
    <tr
      onClick={() => onNav(opp.ticker)}
      className="cursor-pointer hover:bg-[#1f2937]/40 odd:bg-[#0f1522]/30 transition-colors"
    >
      <td className="px-3 py-2 text-[13px] font-bold text-white" style={FONT_OUTFIT}>{opp.ticker}</td>
      <td className="px-3 py-2">
        <Badge className={`text-[9px] ${opp.direction === "long" ? "bg-profit/15 text-profit" : "bg-loss/15 text-loss"}`}>
          {opp.direction.toUpperCase()}
        </Badge>
      </td>
      <td className="px-3 py-2">
        <Badge className={`text-[9px] border ${arch.bg} ${arch.text}`}>
          {opp.position_archetype || opp.action_type}
        </Badge>
      </td>
      <td className="px-3 py-2">
        <div className="flex items-center gap-2">
          <div className="w-12 h-1 rounded-full bg-[#1f2937] overflow-hidden">
            <div className={`h-full ${confColor}`} style={{ width: `${opp.confidence * 10}%` }} />
          </div>
          <span className="text-[10px] text-gray-300 font-mono" style={FONT_MONO}>{opp.confidence}/10</span>
        </div>
      </td>
      <td className="px-3 py-2 text-[11px] text-white font-mono text-right" style={FONT_MONO}>
        {opp.current_price != null ? `$${opp.current_price.toFixed(2)}` : "—"}
      </td>
      <td className="px-3 py-2 text-[11px] text-profit font-mono text-right" style={FONT_MONO}>
        {opp.suggested_price != null ? `$${opp.suggested_price.toFixed(2)}` : "—"}
      </td>
      <td className={`px-3 py-2 text-[10px] font-mono text-right ${expiryTone}`} style={FONT_MONO}>
        {expiryStr}
      </td>
      <td className="px-3 py-2 text-[10px] text-gray-500 font-mono text-right" style={FONT_MONO}>
        {formatTimeAgo(opp.created_at)}
      </td>
      <td className="px-3 py-2 text-right">
        <div className="flex items-center gap-1 justify-end">
          <button
            onClick={(e) => { e.stopPropagation(); onAdd(opp.ticker); }}
            className="text-[10px] px-2 py-0.5 rounded bg-amber-500/15 text-amber-400 border border-amber-500/30 hover:bg-amber-500/25 transition"
            aria-label={`Add ${opp.ticker} to watchlist`}
          >
            <Plus className="w-3 h-3" strokeWidth={2} />
          </button>
          <button
            onClick={(e) => { e.stopPropagation(); onNav(opp.ticker); }}
            className="text-[10px] px-2 py-0.5 rounded bg-ai-blue/15 text-ai-blue border border-ai-blue/30 hover:bg-ai-blue/25 transition"
            aria-label={`View ${opp.ticker}`}
          >
            <ExternalLink className="w-3 h-3" strokeWidth={2} />
          </button>
        </div>
      </td>
    </tr>
  );
}

// ── History ───────────────────────────────────────────────────────
function HistorySection({ history }: { history: ScannerOpportunity[] }) {
  const [expanded, setExpanded] = useState(false);
  if (history.length === 0) return null;
  return (
    <div className="mt-6">
      <button
        onClick={() => setExpanded(!expanded)}
        className="flex items-center gap-2 text-sm font-semibold text-gray-400 hover:text-white transition mb-3"
        style={FONT_OUTFIT}
      >
        <ChevronDown className={`w-4 h-4 transition ${expanded ? "rotate-180" : ""}`} />
        Scanner History ({history.length})
      </button>
      {expanded && (
        <div className="space-y-1 max-h-96 overflow-y-auto">
          {history.map((h) => (
            <div key={h.id} className="flex items-center gap-3 px-3 py-1.5 rounded border border-[#1f2937] bg-[#0f1522]/30">
              <span className="text-[12px] font-bold text-white font-mono" style={FONT_MONO}>{h.ticker}</span>
              <Badge className={`text-[9px] ${h.direction === "long" ? "bg-profit/15 text-profit" : "bg-loss/15 text-loss"}`}>
                {h.direction}
              </Badge>
              <span className="text-[10px] text-gray-500 font-mono" style={FONT_MONO}>conf {h.confidence}/10</span>
              <Badge className={`text-[9px] ${
                h.status === "approved" ? "bg-profit/15 text-profit" :
                h.status === "rejected" ? "bg-loss/15 text-loss" :
                "bg-gray-600/15 text-gray-400"
              }`}>{h.status}</Badge>
              <span className="text-[10px] text-gray-600 ml-auto" style={FONT_MONO}>{formatDate(h.created_at)}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ── Main Page ─────────────────────────────────────────────────────
export default function ScannerPage() {
  const router = useRouter();
  const [results, setResults] = useState<ScannerOpportunity[]>([]);
  const [history, setHistory] = useState<ScannerOpportunity[]>([]);
  const [profiles, setProfiles] = useState<ScanProfile[]>([]);
  const [stats, setStats] = useState<ScannerStats | null>(null);
  const [fmpUsage, setFmpUsage] = useState<FmpUsage | null>(null);
  const [loading, setLoading] = useState(true);
  const [running, setRunning] = useState(false);
  const [scanMessage, setScanMessage] = useState<string | null>(null);

  const [activeProfile, setActiveProfile] = useState<string>("all");

  const [sortKey, setSortKey] = useState<SortKey>("confidence");
  const [sortDir, setSortDir] = useState<SortDir>("desc");

  const [minConf, setMinConf] = useState<number>(0);
  const [dirFilter, setDirFilter] = useState<DirFilter>("all");
  const [archFilter, setArchFilter] = useState<ArchFilter>("all");

  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const fetchAll = useCallback(async () => {
    try {
      const [r, h, s, f, p] = await Promise.all([
        api.getScannerResults().catch(() => []),
        api.getScannerHistory().catch(() => []),
        api.getScannerStats().catch(() => null),
        api.getFmpUsage().catch(() => null),
        api.getScannerProfiles().catch(() => ({ profiles: [] as ScanProfile[] })),
      ]);
      setResults(r);
      setHistory(h);
      setStats(s);
      setFmpUsage(f);
      setProfiles(p?.profiles || []);
    } catch {}
    setLoading(false);
  }, []);

  useEffect(() => { fetchAll(); }, [fetchAll]);
  useEffect(() => {
    const iv = setInterval(fetchAll, 60000);
    return () => clearInterval(iv);
  }, [fetchAll]);

  const handleRun = async () => {
    setRunning(true);
    setScanMessage("Scanner running...");
    try {
      if (activeProfile === "all") {
        await api.runScanner();
      } else {
        await api.runScannerWithProfile(activeProfile);
      }
      if (pollRef.current) clearInterval(pollRef.current);
      pollRef.current = setInterval(async () => {
        try {
          const status = await api.getScannerRunStatus();
          if (!status.running) {
            if (pollRef.current) clearInterval(pollRef.current);
            pollRef.current = null;
            setScanMessage(status.last_result?.message || "Scan complete.");
            setRunning(false);
            await fetchAll();
          }
        } catch {}
      }, 5000);
      setTimeout(() => {
        if (pollRef.current) {
          clearInterval(pollRef.current);
          pollRef.current = null;
          setRunning(false);
          setScanMessage("Scanner timed out — check results.");
          fetchAll();
        }
      }, 180000);
    } catch (e) {
      setScanMessage(`Scan failed: ${e instanceof Error ? e.message : "unknown"}`);
      setRunning(false);
    }
  };

  useEffect(() => () => { if (pollRef.current) clearInterval(pollRef.current); }, []);

  const handleAddWatchlist = async (ticker: string) => {
    try { await api.addWatchlistTickers([ticker]); } catch {}
  };

  const handleSort = (k: SortKey) => {
    if (k === sortKey) setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    else { setSortKey(k); setSortDir("desc"); }
  };

  // Apply filters + sort
  const filtered = useMemo(() => {
    let out = results;
    if (activeProfile !== "all") {
      const prof = profiles.find((p) => p.id === activeProfile);
      if (prof) {
        const pname = prof.name.toLowerCase();
        out = out.filter((r) => (r.position_archetype || "").toLowerCase() === pname);
      }
    }
    out = out.filter((r) => r.confidence >= minConf);
    if (dirFilter !== "all") out = out.filter((r) => r.direction === dirFilter);
    if (archFilter !== "all") out = out.filter((r) => (r.position_archetype || "").toLowerCase() === archFilter);
    const sorted = [...out].sort((a, b) => {
      const getVal = (r: ScannerOpportunity) => {
        switch (sortKey) {
          case "ticker": return r.ticker;
          case "confidence": return r.confidence;
          case "current_price": return r.current_price ?? -Infinity;
          case "suggested_price": return r.suggested_price ?? -Infinity;
          case "expires_at": return r.expires_at ? new Date(r.expires_at).getTime() : 0;
          case "created_at": return new Date(r.created_at).getTime();
        }
      };
      const va = getVal(a);
      const vb = getVal(b);
      if (typeof va === "string" && typeof vb === "string") {
        return sortDir === "asc" ? va.localeCompare(vb) : vb.localeCompare(va);
      }
      return sortDir === "asc" ? (va as number) - (vb as number) : (vb as number) - (va as number);
    });
    return sorted;
  }, [results, profiles, activeProfile, minConf, dirFilter, archFilter, sortKey, sortDir]);

  const lastScanAt = results.length > 0
    ? results.reduce((max, r) => r.created_at > max ? r.created_at : max, results[0].created_at)
    : null;

  const activeProfileName = activeProfile === "all"
    ? "All"
    : (profiles.find((p) => p.id === activeProfile)?.name || "Profile");

  return (
    <div>
      {/* Header */}
      <div className="flex items-center gap-3 mb-5">
        <div className="w-9 h-9 rounded-lg bg-ai-blue/15 border border-ai-blue/30 flex items-center justify-center">
          <Radar className="w-4 h-4 text-ai-blue" strokeWidth={2} />
        </div>
        <div>
          <h1 className="text-xl font-bold text-white" style={FONT_OUTFIT}>Scanner</h1>
          <p className="text-xs text-gray-500" style={FONT_OUTFIT}>
            Henry&apos;s proactive market scanning · {results.length} opportunit{results.length !== 1 ? "ies" : "y"}
          </p>
        </div>
      </div>

      <StatusBar
        running={running} onRun={handleRun} lastScanAt={lastScanAt}
        stats={stats} fmpUsage={fmpUsage} activeProfileName={activeProfileName}
      />

      {scanMessage && (
        <div className={`text-xs font-mono px-3 py-2 rounded-lg mb-3 ${
          scanMessage.includes("fail") || scanMessage.includes("error")
            ? "bg-loss/10 text-loss border border-loss/20"
            : scanMessage.includes("timed out") || scanMessage.includes("0 opportunities")
            ? "bg-amber-500/10 text-amber-400 border border-amber-500/20"
            : "bg-profit/10 text-profit border border-profit/20"
        }`} style={FONT_MONO}>
          {scanMessage}
        </div>
      )}

      <ProfileTabs
        profiles={profiles} activeId={activeProfile}
        onSelect={setActiveProfile} running={running} results={results}
      />

      <FiltersRow
        minConf={minConf} setMinConf={setMinConf}
        dirFilter={dirFilter} setDirFilter={setDirFilter}
        archFilter={archFilter} setArchFilter={setArchFilter}
        onExport={() => exportCsv(filtered)}
        totalFiltered={filtered.length}
      />

      {loading ? (
        <div className="space-y-2">
          {[1,2,3,4,5,6].map((i) => <Skeleton key={i} className="h-10 rounded" />)}
        </div>
      ) : filtered.length === 0 ? (
        <div className="text-center py-16 text-sm text-gray-500">
          No opportunities match the current filters.
        </div>
      ) : (
        <div className="overflow-x-auto rounded-xl border border-[#1f2937]">
          <table className="w-full text-xs">
            <thead className="bg-[#111827]/60 sticky top-0 z-10 border-b border-[#1f2937]">
              <tr>
                <SortHeader label="Ticker" sortKey="ticker" currentKey={sortKey} dir={sortDir} onSort={handleSort} />
                <th className="px-3 py-2 text-left text-[10px] uppercase tracking-wider text-gray-500" style={FONT_OUTFIT}>Dir</th>
                <th className="px-3 py-2 text-left text-[10px] uppercase tracking-wider text-gray-500" style={FONT_OUTFIT}>Archetype</th>
                <SortHeader label="Conf" sortKey="confidence" currentKey={sortKey} dir={sortDir} onSort={handleSort} />
                <SortHeader label="Price" sortKey="current_price" currentKey={sortKey} dir={sortDir} onSort={handleSort} align="right" />
                <SortHeader label="Target" sortKey="suggested_price" currentKey={sortKey} dir={sortDir} onSort={handleSort} align="right" />
                <SortHeader label="Expires" sortKey="expires_at" currentKey={sortKey} dir={sortDir} onSort={handleSort} align="right" />
                <SortHeader label="Scanned" sortKey="created_at" currentKey={sortKey} dir={sortDir} onSort={handleSort} align="right" />
                <th className="px-3 py-2" />
              </tr>
            </thead>
            <tbody className="divide-y divide-[#1f2937]">
              {filtered.map((o) => (
                <OpportunityRow
                  key={o.id} opp={o}
                  onNav={(t) => router.push(`/screener/${t}`)}
                  onAdd={handleAddWatchlist}
                />
              ))}
            </tbody>
          </table>
        </div>
      )}

      <HistorySection history={history} />
    </div>
  );
}
