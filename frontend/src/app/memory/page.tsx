"use client";

import { useState, useEffect, useCallback } from "react";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";

const FONT_OUTFIT = { fontFamily: "'Outfit', sans-serif" } as const;
const FONT_MONO = { fontFamily: "'JetBrains Mono', monospace" } as const;

interface Memory {
  id: string;
  type: string;
  ticker: string | null;
  strategy: string | null;
  content: string;
  importance: number;
  validated: boolean;
  source: string;
  created_at: string;
}

export default function MemoryPage() {
  const [memories, setMemories] = useState<Memory[]>([]);
  const [loading, setLoading] = useState(true);
  const [typeFilter, setTypeFilter] = useState("");
  const [sourceFilter, setSourceFilter] = useState("");
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editValue, setEditValue] = useState(0);

  const fetchMemories = useCallback(async () => {
    try {
      const params: { type?: string; source?: string } = {};
      if (typeFilter) params.type = typeFilter;
      if (sourceFilter) params.source = sourceFilter;
      const data = await api.getMemories(params);
      setMemories(data);
    } catch {}
    setLoading(false);
  }, [typeFilter, sourceFilter]);

  useEffect(() => { fetchMemories(); }, [fetchMemories]);

  const types = Array.from(new Set(memories.map((m) => m.type))).sort();
  const sources = Array.from(new Set(memories.map((m) => m.source))).sort();

  const handleUpdateImportance = async (id: string, importance: number) => {
    try {
      await api.updateMemory(id, { importance });
      setEditingId(null);
      await fetchMemories();
    } catch {}
  };

  const handleDelete = async (id: string) => {
    if (!confirm("Delete this memory?")) return;
    try {
      await api.deleteMemory(id);
      await fetchMemories();
    } catch {}
  };

  return (
    <div className="space-y-6 pb-12">
      <div>
        <h1 className="text-2xl md:text-3xl font-bold text-white" style={FONT_OUTFIT}>
          Henry&apos;s Memory
        </h1>
        <p className="text-sm text-gray-500 mt-1" style={FONT_OUTFIT}>
          View and manage Henry&apos;s stored memories and learnings
        </p>
      </div>

      <div className="flex items-center gap-3 flex-wrap">
        <div className="space-y-1">
          <label className="text-[10px] text-gray-500 uppercase tracking-wider block" style={FONT_OUTFIT}>Type</label>
          <select
            value={typeFilter}
            onChange={(e) => setTypeFilter(e.target.value)}
            className="h-8 px-2 rounded-lg bg-[#1f2937] border border-[#374151] text-xs text-white"
          >
            <option value="">All Types</option>
            {types.map((t) => <option key={t} value={t}>{t}</option>)}
          </select>
        </div>
        <div className="space-y-1">
          <label className="text-[10px] text-gray-500 uppercase tracking-wider block" style={FONT_OUTFIT}>Source</label>
          <select
            value={sourceFilter}
            onChange={(e) => setSourceFilter(e.target.value)}
            className="h-8 px-2 rounded-lg bg-[#1f2937] border border-[#374151] text-xs text-white"
          >
            <option value="">All Sources</option>
            {sources.map((s) => <option key={s} value={s}>{s}</option>)}
          </select>
        </div>
        <div className="ml-auto self-end">
          <Badge variant="outline" className="text-[10px]">{memories.length} memories</Badge>
        </div>
      </div>

      {loading ? (
        <div className="space-y-2">
          {[0, 1, 2, 3].map((i) => <Skeleton key={i} className="h-16 rounded-xl" />)}
        </div>
      ) : memories.length === 0 ? (
        <div className="rounded-xl border border-[#374151]/50 bg-[#1f2937]/20 p-12 flex flex-col items-center justify-center text-center">
          <p className="text-gray-500 text-sm">No memories found</p>
        </div>
      ) : (
        <div className="rounded-xl border border-[#374151]/50 bg-[#1f2937]/20 overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full text-[11px]">
              <thead>
                <tr className="text-gray-500 border-b border-[#374151]">
                  <th className="text-left py-3 px-4 font-medium" style={FONT_OUTFIT}>Type</th>
                  <th className="text-left py-3 px-3 font-medium" style={FONT_OUTFIT}>Ticker</th>
                  <th className="text-left py-3 px-3 font-medium" style={FONT_OUTFIT}>Strategy</th>
                  <th className="text-left py-3 px-3 font-medium max-w-xs" style={FONT_OUTFIT}>Content</th>
                  <th className="text-center py-3 px-3 font-medium" style={FONT_OUTFIT}>Importance</th>
                  <th className="text-center py-3 px-3 font-medium" style={FONT_OUTFIT}>Validated</th>
                  <th className="text-left py-3 px-3 font-medium" style={FONT_OUTFIT}>Source</th>
                  <th className="text-left py-3 px-3 font-medium" style={FONT_OUTFIT}>Created</th>
                  <th className="text-right py-3 px-4 font-medium" style={FONT_OUTFIT}>Actions</th>
                </tr>
              </thead>
              <tbody>
                {memories.map((m) => (
                  <tr key={m.id} className="border-b border-[#374151]/50 hover:bg-[#1f2937]/50 transition">
                    <td className="py-2.5 px-4">
                      <Badge variant="outline" className="text-[9px]">{m.type}</Badge>
                    </td>
                    <td className="py-2.5 px-3 text-white font-semibold" style={FONT_MONO}>{m.ticker || "—"}</td>
                    <td className="py-2.5 px-3 text-gray-400" style={FONT_MONO}>{m.strategy || "—"}</td>
                    <td className="py-2.5 px-3 text-gray-300 max-w-xs truncate" title={m.content}>{m.content.slice(0, 80)}{m.content.length > 80 ? "..." : ""}</td>
                    <td className="py-2.5 px-3 text-center">
                      {editingId === m.id ? (
                        <div className="flex items-center gap-1 justify-center">
                          <input
                            type="number"
                            value={editValue}
                            onChange={(e) => setEditValue(parseInt(e.target.value) || 0)}
                            className="w-12 h-6 text-center text-xs bg-[#1f2937] border border-[#374151] rounded text-white"
                            min={0}
                            max={10}
                          />
                          <button
                            onClick={() => handleUpdateImportance(m.id, editValue)}
                            className="text-[9px] px-1.5 py-0.5 rounded bg-profit/20 text-profit border border-profit/30 hover:bg-profit/30"
                          >
                            Save
                          </button>
                          <button
                            onClick={() => setEditingId(null)}
                            className="text-[9px] px-1.5 py-0.5 rounded text-gray-400 hover:text-white"
                          >
                            Cancel
                          </button>
                        </div>
                      ) : (
                        <button
                          onClick={() => { setEditingId(m.id); setEditValue(m.importance); }}
                          className="text-white font-mono hover:text-ai-blue transition"
                          style={FONT_MONO}
                        >
                          {m.importance}
                        </button>
                      )}
                    </td>
                    <td className="py-2.5 px-3 text-center">
                      <span className={`w-2 h-2 rounded-full inline-block ${m.validated ? "bg-profit" : "bg-gray-600"}`} />
                    </td>
                    <td className="py-2.5 px-3 text-gray-400">{m.source}</td>
                    <td className="py-2.5 px-3 text-gray-500" style={FONT_MONO}>
                      {new Date(m.created_at).toLocaleDateString("en-US", { month: "short", day: "numeric" })}
                    </td>
                    <td className="py-2.5 px-4 text-right">
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => handleDelete(m.id)}
                        className="text-loss/50 hover:text-loss h-6 w-6 p-0"
                      >
                        <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                          <path strokeLinecap="round" strokeLinejoin="round" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                        </svg>
                      </Button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}
