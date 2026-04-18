"use client";

import { useCallback, useEffect, useState } from "react";
import { Newspaper, CalendarClock, PieChart as PieIcon, RefreshCw, type LucideIcon } from "lucide-react";
import { api } from "@/lib/api";
import { renderMarkdown } from "@/lib/markdown";
import { Skeleton } from "@/components/ui/skeleton";
import CardSpotlight from "@/components/ui/card-spotlight";

const FONT_OUTFIT = { fontFamily: "'Outfit', sans-serif" } as const;
const FONT_MONO = { fontFamily: "'JetBrains Mono', monospace" } as const;

type Envelope = { text: string; generated_at: number; cache_ttl_seconds: number };

function formatGeneratedAt(epochSeconds: number | null): string {
  if (!epochSeconds) return "";
  const ageSec = Math.max(0, Math.floor(Date.now() / 1000 - epochSeconds));
  if (ageSec < 60) return `${ageSec}s ago`;
  if (ageSec < 3600) return `${Math.floor(ageSec / 60)}m ago`;
  return `${Math.floor(ageSec / 3600)}h ago`;
}

function ContextCard({
  icon: Icon,
  title,
  fetcher,
  className,
}: {
  icon: LucideIcon;
  title: string;
  fetcher: () => Promise<Envelope>;
  className?: string;
}) {
  const [data, setData] = useState<Envelope | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  const load = useCallback(async (refresh = false) => {
    if (refresh) setRefreshing(true); else setLoading(true);
    setError(null);
    try {
      const res = await fetcher();
      setData(res);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [fetcher]);

  useEffect(() => { load(); }, [load]);

  return (
    <CardSpotlight className={className}>
      <div className="p-5">
        <div className="flex items-center justify-between mb-3">
          <div className="flex items-center gap-2">
            <Icon className="w-4 h-4 text-ai-blue" strokeWidth={2} />
            <span className="text-sm font-semibold text-white" style={FONT_OUTFIT}>{title}</span>
          </div>
          <div className="flex items-center gap-3">
            {data?.generated_at && (
              <span className="text-[10px] text-gray-500" style={FONT_MONO}>
                {formatGeneratedAt(data.generated_at)}
              </span>
            )}
            <button
              onClick={() => load(true)}
              disabled={refreshing || loading}
              className="flex items-center gap-1 text-[11px] text-ai-blue hover:text-white transition px-2 py-1 rounded border border-ai-blue/30 bg-ai-blue/10 disabled:opacity-50"
              style={FONT_OUTFIT}
            >
              <RefreshCw className={`w-3 h-3 ${refreshing ? "animate-spin" : ""}`} strokeWidth={2} />
              Refresh
            </button>
          </div>
        </div>
        {loading ? (
          <div className="space-y-2">
            {[1, 2, 3, 4, 5].map((i) => (
              <Skeleton key={i} className="h-3 rounded" style={{ width: `${55 + Math.random() * 40}%` }} />
            ))}
          </div>
        ) : error ? (
          <div className="text-xs text-loss">{error}</div>
        ) : data?.text ? (
          <div
            className="ai-prose max-h-[360px] overflow-y-auto pr-2"
            dangerouslySetInnerHTML={{ __html: renderMarkdown(data.text) }}
          />
        ) : (
          <div className="text-xs text-gray-500">Nothing to report.</div>
        )}
      </div>
    </CardSpotlight>
  );
}

export function NewsDigestCard() {
  return <ContextCard icon={Newspaper} title="News Digest" fetcher={api.getNewsDigest} />;
}

export function UpcomingEventsCard() {
  return <ContextCard icon={CalendarClock} title="Upcoming Events" fetcher={() => api.getUpcomingEvents(7)} />;
}

export function SectorAnalysisCard() {
  return <ContextCard icon={PieIcon} title="Sector Tape" fetcher={api.getSectorAnalysis} />;
}
