"use client";

/**
 * Memory Curation Panel
 * =====================
 * Three operational tools for keeping Henry's memory store healthy:
 *
 *   1. Duplicates — find pairs with cosine ≥ 0.92, merge or drop one
 *   2. Orphans   — list memories with low silhouette (don't fit any
 *                  cluster well) — candidates for re-categorization or
 *                  deletion
 *   3. Forget    — bulk delete by importance + reference count + age
 *                  filter; preview the candidates before confirming
 *
 * All destructive actions are gated by ADMIN_SECRET (cached per
 * sessionStorage tab) and require explicit confirm.
 *
 * Not coupled to the 3D viz — invoking any action triggers an
 * onChanged() callback so the parent can refresh its projection.
 */

import { useState } from "react";
import { api } from "@/lib/api";
import type {
  DuplicatePair,
  OrphanMemory,
  ForgetCandidate,
  ConsolidateGroup,
  MemoryDiffResponse,
  MemoryDiffEntry,
  GapAnalysisResponse,
} from "@/lib/types";

interface Props {
  // Called after any successful destructive action so the parent can
  // refresh the 3D projection / health.
  onChanged?: () => void;
}

type Tab = "duplicates" | "orphans" | "forget" | "consolidate" | "diff" | "gaps";

function promptSecret(): string | null {
  const cached = sessionStorage.getItem("memory_admin_secret");
  if (cached) return cached;
  const entered = window.prompt(
    "Enter ADMIN_SECRET (stored only for this browser tab):"
  );
  if (entered) sessionStorage.setItem("memory_admin_secret", entered);
  return entered;
}

export function MemoryCurationPanel({ onChanged }: Props) {
  const [tab, setTab] = useState<Tab>("duplicates");

  return (
    <div className="rounded-lg border border-border bg-[#1f2937]/30">
      {/* Tabs */}
      <div className="flex gap-1 p-1.5 border-b border-border">
        {(["duplicates", "orphans", "forget", "consolidate", "diff", "gaps"] as Tab[]).map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={
              "text-xs px-3 py-1.5 rounded transition-colors " +
              (tab === t
                ? "bg-[#6366f1]/20 text-[#6366f1]"
                : "text-gray-400 hover:bg-[#1f2937] hover:text-gray-200")
            }
          >
            {t === "duplicates" && "Duplicates"}
            {t === "orphans" && "Orphans"}
            {t === "forget" && "Forget"}
            {t === "consolidate" && "Auto-consolidate"}
            {t === "diff" && "Diff"}
            {t === "gaps" && "Gaps"}
          </button>
        ))}
      </div>

      <div className="p-3">
        {tab === "duplicates" && <DuplicatesTab onChanged={onChanged} />}
        {tab === "orphans" && <OrphansTab onChanged={onChanged} />}
        {tab === "forget" && <ForgetTab onChanged={onChanged} />}
        {tab === "consolidate" && <ConsolidateTab onChanged={onChanged} />}
        {tab === "diff" && <DiffTab />}
        {tab === "gaps" && <GapsTab />}
      </div>
    </div>
  );
}

// ─── Duplicates ──────────────────────────────────────────────────────────────

function DuplicatesTab({ onChanged }: { onChanged?: () => void }) {
  const [pairs, setPairs] = useState<DuplicatePair[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [threshold, setThreshold] = useState(0.92);
  const [sameClusterOnly, setSameClusterOnly] = useState(true);
  const [busyPair, setBusyPair] = useState<string | null>(null);

  const load = async () => {
    setLoading(true);
    setMsg(null);
    try {
      const res = await api.curationDuplicates(threshold, 50, sameClusterOnly);
      setPairs(res.pairs);
      setMsg(
        `Compared ${res.n_compared} pairs · found ${res.pairs.length} ≥ ${threshold}`
      );
    } catch (e) {
      setMsg(`Failed: ${(e as Error).message}`);
    } finally {
      setLoading(false);
    }
  };

  const merge = async (pair: DuplicatePair) => {
    if (
      !confirm(
        `Merge: keep "${pair.keep.content_preview.slice(0, 60)}…" and delete "${pair.drop.content_preview.slice(0, 60)}…"?\n\nReference counts merge; importance bumps +1.`
      )
    ) {
      return;
    }
    const secret = promptSecret();
    if (!secret) return;
    setBusyPair(pair.drop.id);
    try {
      const res = await api.adminMergeMemory(secret, {
        keep_id: pair.keep.id,
        drop_id: pair.drop.id,
        bump_importance: true,
      });
      if (!res.ok) {
        setMsg(`Merge failed: ${res.reason || "unknown"}`);
      } else {
        setMsg(
          `Merged → kept ${res.kept?.id?.slice(0, 8)} (importance ${res.kept?.importance}, refs ${res.kept?.reference_count})`
        );
        // Drop the merged pair from the list so the user sees progress.
        setPairs((cur) =>
          (cur || []).filter(
            (p) => p.keep.id !== pair.keep.id || p.drop.id !== pair.drop.id
          )
        );
        onChanged?.();
      }
    } catch (e) {
      setMsg(`Merge request failed: ${(e as Error).message}`);
    } finally {
      setBusyPair(null);
    }
  };

  const dropOnly = async (pair: DuplicatePair) => {
    if (!confirm(`Delete the lower-importance duplicate?`)) return;
    const secret = promptSecret();
    if (!secret) return;
    setBusyPair(pair.drop.id);
    try {
      const res = await api.adminBulkDelete(secret, [pair.drop.id]);
      if (!res.ok) {
        setMsg(`Delete failed: ${res.reason || "unknown"}`);
      } else {
        setMsg(`Deleted 1 memory.`);
        setPairs((cur) =>
          (cur || []).filter((p) => p.drop.id !== pair.drop.id)
        );
        onChanged?.();
      }
    } catch (e) {
      setMsg(`Delete request failed: ${(e as Error).message}`);
    } finally {
      setBusyPair(null);
    }
  };

  return (
    <div className="space-y-3">
      <p className="text-xs text-gray-400">
        Find memory pairs with cosine similarity above the threshold. Merge
        keeps the higher-importance one and combines reference counts.
      </p>

      <div className="flex items-center gap-3 flex-wrap text-xs">
        <label className="flex items-center gap-1.5 text-gray-400">
          Threshold
          <input
            type="number"
            min={0.5}
            max={1}
            step={0.01}
            value={threshold}
            onChange={(e) => setThreshold(parseFloat(e.target.value) || 0.92)}
            className="w-16 bg-[#0b0f19] border border-border rounded px-1.5 py-1 text-gray-200"
          />
        </label>
        <label className="flex items-center gap-1.5 text-gray-400">
          <input
            type="checkbox"
            checked={sameClusterOnly}
            onChange={(e) => setSameClusterOnly(e.target.checked)}
            className="accent-[#6366f1]"
          />
          same cluster only (faster)
        </label>
        <button
          onClick={load}
          disabled={loading}
          className="text-xs px-3 py-1.5 rounded bg-[#6366f1]/15 text-[#6366f1] hover:bg-[#6366f1]/25 disabled:opacity-40"
        >
          {loading ? "Scanning…" : "Scan"}
        </button>
      </div>

      {msg && (
        <p className="text-[11px] text-gray-400 font-mono">{msg}</p>
      )}

      {pairs && pairs.length > 0 && (
        <div className="space-y-2 max-h-[40vh] overflow-y-auto pr-1">
          {pairs.map((p) => (
            <div
              key={`${p.keep.id}-${p.drop.id}`}
              className="rounded border border-border bg-[#0b0f19] p-2.5 text-xs"
            >
              <div className="flex items-center justify-between mb-1.5">
                <span className="text-[10px] uppercase tracking-wide text-amber-400">
                  similarity {(p.similarity * 100).toFixed(1)}%
                </span>
                <div className="flex gap-1.5">
                  <button
                    onClick={() => merge(p)}
                    disabled={busyPair !== null}
                    className="text-[10px] px-2 py-0.5 rounded bg-[#10b981]/15 text-[#10b981] hover:bg-[#10b981]/25 disabled:opacity-40"
                  >
                    {busyPair === p.drop.id ? "…" : "Merge"}
                  </button>
                  <button
                    onClick={() => dropOnly(p)}
                    disabled={busyPair !== null}
                    className="text-[10px] px-2 py-0.5 rounded bg-red-500/15 text-red-400 hover:bg-red-500/25 disabled:opacity-40"
                  >
                    Delete dup
                  </button>
                </div>
              </div>
              <div className="grid grid-cols-2 gap-2">
                <CurationCard label="KEEP" snap={p.keep} accent="#10b981" />
                <CurationCard label="DROP" snap={p.drop} accent="#ef4444" />
              </div>
            </div>
          ))}
        </div>
      )}

      {pairs && pairs.length === 0 && !loading && (
        <p className="text-xs text-gray-500 italic">
          No duplicate pairs above {threshold}.
        </p>
      )}
    </div>
  );
}

function CurationCard({
  label,
  snap,
  accent,
}: {
  label: string;
  snap: { id: string; content_preview: string; importance: number; reference_count: number; ticker: string | null; memory_type: string };
  accent: string;
}) {
  return (
    <div className="rounded p-1.5 border border-border bg-[#1f2937]/40">
      <div className="flex items-center gap-1.5 mb-1">
        <span
          className="text-[9px] font-bold tracking-wide"
          style={{ color: accent }}
        >
          {label}
        </span>
        <span className="text-[9px] uppercase text-gray-500">
          {snap.memory_type}
        </span>
        {snap.ticker && (
          <span className="text-[9px] font-mono text-gray-400">
            [{snap.ticker}]
          </span>
        )}
        <span className="ml-auto text-[9px] text-gray-500">
          imp {snap.importance} · refs {snap.reference_count}
        </span>
      </div>
      <p className="text-[11px] text-gray-200 leading-snug line-clamp-3">
        {snap.content_preview || "(no preview)"}
      </p>
    </div>
  );
}

// ─── Orphans ────────────────────────────────────────────────────────────────

function OrphansTab({ onChanged }: { onChanged?: () => void }) {
  const [orphans, setOrphans] = useState<OrphanMemory[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [threshold, setThreshold] = useState(-0.05);

  const load = async () => {
    setLoading(true);
    setMsg(null);
    try {
      const res = await api.curationOrphans(threshold, 100);
      setOrphans(res.orphans);
      setMsg(`Found ${res.count} memories with silhouette < ${threshold}`);
    } catch (e) {
      setMsg(`Failed: ${(e as Error).message}`);
    } finally {
      setLoading(false);
    }
  };

  const deleteOne = async (id: string) => {
    if (!confirm("Delete this orphan memory?")) return;
    const secret = promptSecret();
    if (!secret) return;
    try {
      const res = await api.adminBulkDelete(secret, [id]);
      if (!res.ok) {
        setMsg(`Delete failed: ${res.reason}`);
      } else {
        setOrphans((cur) => (cur || []).filter((o) => o.id !== id));
        onChanged?.();
      }
    } catch (e) {
      setMsg(`Delete request failed: ${(e as Error).message}`);
    }
  };

  const deleteAll = async () => {
    if (!orphans || orphans.length === 0) return;
    if (
      !confirm(
        `Delete all ${orphans.length} orphan memories? This cannot be undone.`
      )
    ) {
      return;
    }
    const secret = promptSecret();
    if (!secret) return;
    try {
      const res = await api.adminBulkDelete(
        secret,
        orphans.map((o) => o.id)
      );
      if (!res.ok) {
        setMsg(`Bulk delete failed: ${res.reason}`);
      } else {
        setMsg(`Deleted ${res.deleted} orphans.`);
        setOrphans([]);
        onChanged?.();
      }
    } catch (e) {
      setMsg(`Bulk delete request failed: ${(e as Error).message}`);
    }
  };

  return (
    <div className="space-y-3">
      <p className="text-xs text-gray-400">
        Memories with low silhouette score don&apos;t fit their assigned
        cluster well — likely outliers, mis-categorized, or one-off
        observations that don&apos;t belong to a recurring theme.
      </p>

      <div className="flex items-center gap-3 flex-wrap text-xs">
        <label className="flex items-center gap-1.5 text-gray-400">
          Threshold
          <input
            type="number"
            min={-1}
            max={1}
            step={0.05}
            value={threshold}
            onChange={(e) => setThreshold(parseFloat(e.target.value) || -0.05)}
            className="w-16 bg-[#0b0f19] border border-border rounded px-1.5 py-1 text-gray-200"
          />
        </label>
        <button
          onClick={load}
          disabled={loading}
          className="text-xs px-3 py-1.5 rounded bg-[#6366f1]/15 text-[#6366f1] hover:bg-[#6366f1]/25 disabled:opacity-40"
        >
          {loading ? "Scanning…" : "Find orphans"}
        </button>
        {orphans && orphans.length > 0 && (
          <button
            onClick={deleteAll}
            className="text-xs px-3 py-1.5 rounded bg-red-500/15 text-red-400 hover:bg-red-500/25"
          >
            Delete all {orphans.length}
          </button>
        )}
      </div>

      {msg && (
        <p className="text-[11px] text-gray-400 font-mono">{msg}</p>
      )}

      {orphans && orphans.length > 0 && (
        <div className="space-y-1.5 max-h-[40vh] overflow-y-auto pr-1">
          {orphans.map((o) => (
            <div
              key={o.id}
              className="rounded border border-border bg-[#0b0f19] p-2 text-xs flex items-start gap-2"
            >
              <span className="text-amber-400 text-[10px] mt-0.5">⚠</span>
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-1.5 mb-0.5">
                  <span className="text-[9px] uppercase text-gray-500">
                    {o.memory_type}
                  </span>
                  {o.ticker && (
                    <span className="text-[9px] font-mono text-gray-400">
                      [{o.ticker}]
                    </span>
                  )}
                  <span className="text-[9px] text-gray-500">
                    cluster {o.cluster_id} · sil{" "}
                    {o.silhouette !== null ? o.silhouette.toFixed(2) : "—"}{" "}
                    · imp {o.importance}
                  </span>
                </div>
                <p className="text-[11px] text-gray-200 leading-snug line-clamp-2">
                  {o.content_preview}
                </p>
              </div>
              <button
                onClick={() => deleteOne(o.id)}
                className="text-[10px] px-2 py-0.5 rounded bg-red-500/15 text-red-400 hover:bg-red-500/25"
              >
                Delete
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ─── Forget selector ────────────────────────────────────────────────────────

function ForgetTab({ onChanged }: { onChanged?: () => void }) {
  const [maxImportance, setMaxImportance] = useState(4);
  const [maxRefs, setMaxRefs] = useState(0);
  const [minAgeDays, setMinAgeDays] = useState(30);
  const [requireUnvalidated, setRequireUnvalidated] = useState(true);

  const [candidates, setCandidates] = useState<ForgetCandidate[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  const preview = async () => {
    setLoading(true);
    setMsg(null);
    try {
      const res = await api.curationForgetCandidates({
        max_importance: maxImportance,
        max_reference_count: maxRefs,
        min_age_days: minAgeDays,
        require_unvalidated: requireUnvalidated,
        limit: 200,
      });
      setCandidates(res.candidates);
      setMsg(`Found ${res.count} candidate memories matching the filters`);
    } catch (e) {
      setMsg(`Failed: ${(e as Error).message}`);
    } finally {
      setLoading(false);
    }
  };

  const forgetAll = async () => {
    if (!candidates || candidates.length === 0) return;
    if (
      !confirm(
        `Permanently delete all ${candidates.length} memories matching these filters? This cannot be undone.`
      )
    ) {
      return;
    }
    const secret = promptSecret();
    if (!secret) return;
    try {
      const res = await api.adminBulkDelete(
        secret,
        candidates.map((c) => c.id)
      );
      if (!res.ok) {
        setMsg(`Forget failed: ${res.reason}`);
      } else {
        setMsg(`Forgot ${res.deleted} memories.`);
        setCandidates([]);
        onChanged?.();
      }
    } catch (e) {
      setMsg(`Forget request failed: ${(e as Error).message}`);
    }
  };

  return (
    <div className="space-y-3">
      <p className="text-xs text-gray-400">
        Bulk delete low-value memories. Recommended defaults: importance ≤
        4, never referenced, &gt; 30 days old. Preview shows the matches
        before any delete happens.
      </p>

      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-xs">
        <label className="flex flex-col gap-1 text-gray-400">
          Max importance
          <input
            type="number"
            min={1}
            max={10}
            value={maxImportance}
            onChange={(e) => setMaxImportance(parseInt(e.target.value) || 4)}
            className="bg-[#0b0f19] border border-border rounded px-1.5 py-1 text-gray-200"
          />
        </label>
        <label className="flex flex-col gap-1 text-gray-400">
          Max references
          <input
            type="number"
            min={0}
            value={maxRefs}
            onChange={(e) => setMaxRefs(parseInt(e.target.value) || 0)}
            className="bg-[#0b0f19] border border-border rounded px-1.5 py-1 text-gray-200"
          />
        </label>
        <label className="flex flex-col gap-1 text-gray-400">
          Min age (days)
          <input
            type="number"
            min={0}
            value={minAgeDays}
            onChange={(e) => setMinAgeDays(parseInt(e.target.value) || 30)}
            className="bg-[#0b0f19] border border-border rounded px-1.5 py-1 text-gray-200"
          />
        </label>
        <label className="flex items-center gap-1.5 text-gray-400 sm:mt-5">
          <input
            type="checkbox"
            checked={requireUnvalidated}
            onChange={(e) => setRequireUnvalidated(e.target.checked)}
            className="accent-[#6366f1]"
          />
          Skip validated
        </label>
      </div>

      <div className="flex items-center gap-2 flex-wrap">
        <button
          onClick={preview}
          disabled={loading}
          className="text-xs px-3 py-1.5 rounded bg-[#6366f1]/15 text-[#6366f1] hover:bg-[#6366f1]/25 disabled:opacity-40"
        >
          {loading ? "Scanning…" : "Preview candidates"}
        </button>
        {candidates && candidates.length > 0 && (
          <button
            onClick={forgetAll}
            className="text-xs px-3 py-1.5 rounded bg-red-500/20 text-red-400 hover:bg-red-500/30"
          >
            Forget all {candidates.length}
          </button>
        )}
        {msg && (
          <span className="text-[11px] text-gray-400 font-mono">{msg}</span>
        )}
      </div>

      {candidates && candidates.length > 0 && (
        <div className="space-y-1 max-h-[40vh] overflow-y-auto pr-1">
          {candidates.map((c) => (
            <div
              key={c.id}
              className="rounded border border-border bg-[#0b0f19] p-1.5 text-xs"
            >
              <div className="flex items-center gap-1.5 mb-0.5">
                <span className="text-[9px] uppercase text-gray-500">
                  {c.memory_type}
                </span>
                {c.ticker && (
                  <span className="text-[9px] font-mono text-gray-400">
                    [{c.ticker}]
                  </span>
                )}
                <span className="text-[9px] text-gray-500">
                  imp {c.importance} · refs {c.reference_count}
                  {c.created_at && " · " + c.created_at.slice(0, 10)}
                </span>
              </div>
              <p className="text-[11px] text-gray-300 leading-snug line-clamp-1">
                {c.content_preview}
              </p>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ─── Auto-consolidate ───────────────────────────────────────────────────────

function ConsolidateTab({ onChanged }: { onChanged?: () => void }) {
  const [groups, setGroups] = useState<ConsolidateGroup[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [threshold, setThreshold] = useState(0.93);
  const [sameClusterOnly, setSameClusterOnly] = useState(true);
  // Per-group editable proposed content. Keyed by anchor_id (stable
  // within one preview pass).
  const [edits, setEdits] = useState<Record<string, string>>({});
  const [committingId, setCommittingId] = useState<string | null>(null);

  const load = async () => {
    setLoading(true);
    setMsg(null);
    try {
      const res = await api.curationConsolidatePreview({
        threshold,
        same_cluster_only: sameClusterOnly,
        max_groups: 10,
      });
      setGroups(res.groups);
      setEdits(
        Object.fromEntries(
          res.groups.map((g) => [g.anchor_id, g.proposed_content])
        )
      );
      setMsg(
        `Compared ${res.n_compared} pairs · ${res.n_groups_found ?? res.groups.length} ` +
          `group${(res.n_groups_found ?? res.groups.length) === 1 ? "" : "s"} found · ` +
          `${res.groups.length} draft${res.groups.length === 1 ? "" : "s"} ready`
      );
    } catch (e) {
      setMsg(`Failed: ${(e as Error).message}`);
    } finally {
      setLoading(false);
    }
  };

  const commit = async (g: ConsolidateGroup) => {
    const content = (edits[g.anchor_id] || g.proposed_content || "").trim();
    if (!content) {
      alert("Consolidated content is empty.");
      return;
    }
    if (
      !confirm(
        `Replace ${g.n} memories with this single consolidated lesson?\n\n${content.slice(0, 200)}…\n\nThis cannot be undone.`
      )
    ) {
      return;
    }
    const secret = promptSecret();
    if (!secret) return;
    setCommittingId(g.anchor_id);
    try {
      const res = await api.adminConsolidateCommit(secret, {
        member_ids: g.member_ids,
        content,
        importance: g.proposed_importance,
        memory_type: g.memory_type,
        ticker: g.ticker,
        strategy_id: g.strategy_id,
      });
      if (!res.ok) {
        setMsg(`Commit failed: ${res.reason || "unknown"}`);
      } else {
        setMsg(
          `Consolidated ${res.deleted} memories → 1 (carried ${res.consolidated_reference_count} refs).`
        );
        setGroups((cur) =>
          (cur || []).filter((x) => x.anchor_id !== g.anchor_id)
        );
        onChanged?.();
      }
    } catch (e) {
      setMsg(`Commit request failed: ${(e as Error).message}`);
    } finally {
      setCommittingId(null);
    }
  };

  return (
    <div className="space-y-3">
      <p className="text-xs text-gray-400">
        Find groups of near-duplicate memories and let Gemini draft a
        single consolidated lesson per group. Review &amp; edit each
        proposal before committing — this replaces the originals
        permanently.
      </p>

      <div className="flex items-center gap-3 flex-wrap text-xs">
        <label className="flex items-center gap-1.5 text-gray-400">
          Threshold
          <input
            type="number"
            min={0.5}
            max={1}
            step={0.01}
            value={threshold}
            onChange={(e) => setThreshold(parseFloat(e.target.value) || 0.93)}
            className="w-16 bg-[#0b0f19] border border-border rounded px-1.5 py-1 text-gray-200"
          />
        </label>
        <label className="flex items-center gap-1.5 text-gray-400">
          <input
            type="checkbox"
            checked={sameClusterOnly}
            onChange={(e) => setSameClusterOnly(e.target.checked)}
            className="accent-[#6366f1]"
          />
          same cluster only (faster)
        </label>
        <button
          onClick={load}
          disabled={loading}
          className="text-xs px-3 py-1.5 rounded bg-[#6366f1]/15 text-[#6366f1] hover:bg-[#6366f1]/25 disabled:opacity-40"
        >
          {loading ? "Drafting…" : "Find groups + draft"}
        </button>
      </div>

      {msg && <p className="text-[11px] text-gray-400 font-mono">{msg}</p>}

      {groups && groups.length > 0 && (
        <div className="space-y-3 max-h-[60vh] overflow-y-auto pr-1">
          {groups.map((g) => (
            <div
              key={g.anchor_id}
              className="rounded border border-border bg-[#0b0f19] p-3 text-xs space-y-2"
            >
              <div className="flex items-center gap-2 flex-wrap">
                <span className="text-[10px] uppercase tracking-wide text-amber-400">
                  {g.n} memories · avg sim {(g.avg_similarity * 100).toFixed(1)}%
                </span>
                {g.ticker && (
                  <span className="text-[10px] font-mono text-gray-400">
                    [{g.ticker}]
                  </span>
                )}
                {g.strategy_id && (
                  <span className="text-[10px] text-gray-500">
                    ({g.strategy_id})
                  </span>
                )}
                <span className="text-[10px] text-gray-500 ml-auto">
                  type: {g.memory_type} · target imp {g.proposed_importance}
                </span>
              </div>

              {/* Editable proposal */}
              <div>
                <p className="text-[10px] uppercase tracking-wide text-gray-500 mb-1">
                  Proposed consolidated content (editable)
                </p>
                <textarea
                  value={edits[g.anchor_id] ?? g.proposed_content}
                  onChange={(e) =>
                    setEdits((prev) => ({
                      ...prev,
                      [g.anchor_id]: e.target.value,
                    }))
                  }
                  rows={4}
                  className="w-full text-[11px] bg-[#1f2937]/60 border border-border rounded p-2 text-gray-200 font-mono leading-relaxed focus:border-[#6366f1] focus:outline-none"
                />
              </div>

              {/* Originals — collapsed by default */}
              <details>
                <summary className="text-[10px] text-gray-500 cursor-pointer hover:text-gray-300">
                  Show {g.members.length} original memories
                </summary>
                <div className="mt-1.5 space-y-1.5">
                  {g.members.map((m) => (
                    <div
                      key={m.id}
                      className="rounded p-1.5 border border-border bg-[#1f2937]/40"
                    >
                      <div className="flex items-center gap-1.5 mb-0.5">
                        <span className="text-[9px] uppercase text-gray-500">
                          {m.memory_type}
                        </span>
                        <span className="text-[9px] text-gray-500">
                          imp {m.importance} · refs {m.reference_count}
                        </span>
                        <span className="text-[9px] font-mono text-gray-600 ml-auto">
                          {m.id.slice(0, 8)}
                        </span>
                      </div>
                      <p className="text-[11px] text-gray-300 leading-snug">
                        {m.content_preview}
                      </p>
                    </div>
                  ))}
                </div>
              </details>

              <div className="flex justify-end gap-1.5">
                <button
                  onClick={() => commit(g)}
                  disabled={committingId !== null}
                  className="text-[11px] px-3 py-1 rounded bg-[#10b981]/15 text-[#10b981] hover:bg-[#10b981]/25 disabled:opacity-40"
                >
                  {committingId === g.anchor_id
                    ? "Committing…"
                    : `Replace ${g.n} → 1`}
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      {groups && groups.length === 0 && !loading && (
        <p className="text-xs text-gray-500 italic">
          No consolidation candidates above {threshold}.
        </p>
      )}
    </div>
  );
}

// ─── Diff tab (carryover #42) ───────────────────────────────────────────────

type DiffPreset = "24h" | "7d" | "30d" | "custom";

function DiffTab() {
  const [preset, setPreset] = useState<DiffPreset>("24h");
  const [customIso, setCustomIso] = useState("");
  const [diff, setDiff] = useState<MemoryDiffResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  // Section default-open flags. Created is the most interesting so
  // it opens by default; others start collapsed to save scroll.
  const [openSections, setOpenSections] = useState<Record<string, boolean>>({
    created: true,
    retrieved: false,
    updated: false,
  });

  const resolveSince = (): string | null => {
    if (preset === "custom") {
      const trimmed = customIso.trim();
      if (!trimmed) return null;
      // Accept plain date (2026-04-13) → assume start of day UTC
      const hasTime = /\d{2}:\d{2}/.test(trimmed);
      return hasTime ? trimmed : trimmed + "T00:00:00Z";
    }
    const now = Date.now();
    const hours = preset === "24h" ? 24 : preset === "7d" ? 7 * 24 : 30 * 24;
    return new Date(now - hours * 3_600_000).toISOString();
  };

  const load = async () => {
    const since = resolveSince();
    if (!since) {
      setMsg("Enter a valid ISO timestamp for custom mode.");
      return;
    }
    setLoading(true);
    setMsg(null);
    try {
      const res = await api.memoryDiff(since, 100);
      setDiff(res);
      setMsg(
        `Since ${new Date(res.since).toLocaleString()} — ${res.summary.created} created · ` +
          `${res.summary.retrieved} retrieved · ${res.summary.updated} updated ` +
          `(${res.total_memories} total in store)`
      );
    } catch (e) {
      setMsg(`Failed: ${(e as Error).message}`);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="space-y-3">
      <p className="text-xs text-gray-400">
        See what&apos;s changed in the memory store since a reference point.
        Three buckets: newly created, retrieved (existed before, pulled by
        Henry since), and updated (importance nudges from outcome resolution
        or user edits). Useful for morning or post-session review.
      </p>

      <div className="flex items-center gap-3 flex-wrap text-xs">
        <label className="flex items-center gap-1.5 text-gray-400">
          Since
          <select
            value={preset}
            onChange={(e) => setPreset(e.target.value as DiffPreset)}
            className="bg-[#0b0f19] border border-border rounded px-1.5 py-1 text-gray-200"
          >
            <option value="24h">last 24h</option>
            <option value="7d">last 7 days</option>
            <option value="30d">last 30 days</option>
            <option value="custom">custom…</option>
          </select>
        </label>
        {preset === "custom" && (
          <input
            type="text"
            value={customIso}
            onChange={(e) => setCustomIso(e.target.value)}
            placeholder="2026-04-13 or 2026-04-13T14:30:00Z"
            className="flex-1 min-w-[240px] bg-[#0b0f19] border border-border rounded px-2 py-1 text-gray-200 focus:border-[#6366f1] focus:outline-none"
          />
        )}
        <button
          onClick={load}
          disabled={loading}
          className="text-xs px-3 py-1.5 rounded bg-[#6366f1]/15 text-[#6366f1] hover:bg-[#6366f1]/25 disabled:opacity-40"
        >
          {loading ? "Loading…" : "Show diff"}
        </button>
      </div>

      {msg && (
        <p className="text-[11px] text-gray-400 font-mono break-words">{msg}</p>
      )}

      {diff && (
        <div className="space-y-2">
          {/* Summary pill row */}
          <div className="flex flex-wrap gap-2">
            <SummaryPill label="Created" count={diff.summary.created} color="#10b981" />
            <SummaryPill label="Retrieved" count={diff.summary.retrieved} color="#6366f1" />
            <SummaryPill label="Updated" count={diff.summary.updated} color="#f59e0b" />
          </div>

          <DiffSection
            title="Newly created"
            color="#10b981"
            entries={diff.created}
            open={openSections.created}
            onToggle={() =>
              setOpenSections((s) => ({ ...s, created: !s.created }))
            }
            hint="Memories saved after the reference point. Shows what Henry learned."
          />
          <DiffSection
            title="Retrieved (pre-existing)"
            color="#6366f1"
            entries={diff.retrieved}
            open={openSections.retrieved}
            onToggle={() =>
              setOpenSections((s) => ({ ...s, retrieved: !s.retrieved }))
            }
            hint="Older memories that Henry pulled into at least one AI call since then."
            showRetrievalInfo
          />
          <DiffSection
            title="Importance / outcome updated"
            color="#f59e0b"
            entries={diff.updated}
            open={openSections.updated}
            onToggle={() =>
              setOpenSections((s) => ({ ...s, updated: !s.updated }))
            }
            hint="Memories nudged by outcome resolution (System 7) or edited by the user."
          />
        </div>
      )}
    </div>
  );
}

function SummaryPill({
  label,
  count,
  color,
}: {
  label: string;
  count: number;
  color: string;
}) {
  return (
    <div
      className="flex items-center gap-1.5 px-2.5 py-1 rounded text-[11px] bg-[#1f2937]/40 border border-border"
    >
      <span
        className="inline-block w-2 h-2 rounded-full"
        style={{ backgroundColor: color }}
      />
      <span className="text-gray-400">{label}</span>
      <span className="font-mono text-gray-200">{count}</span>
    </div>
  );
}

function DiffSection({
  title,
  color,
  entries,
  open,
  onToggle,
  hint,
  showRetrievalInfo,
}: {
  title: string;
  color: string;
  entries: MemoryDiffEntry[];
  open: boolean;
  onToggle: () => void;
  hint: string;
  showRetrievalInfo?: boolean;
}) {
  return (
    <div className="rounded border border-border bg-[#0b0f19]">
      <button
        onClick={onToggle}
        className="w-full flex items-center gap-2 px-3 py-2 text-left hover:bg-[#1f2937]/40"
      >
        <span
          className="inline-block w-2 h-2 rounded-full"
          style={{ backgroundColor: color }}
        />
        <span className="text-xs text-gray-200">{title}</span>
        <span className="text-[10px] text-gray-500 font-mono">
          {entries.length}
        </span>
        <span className="ml-auto text-[10px] text-gray-500">
          {open ? "▾" : "▸"}
        </span>
      </button>
      {open && (
        <div className="border-t border-border max-h-[40vh] overflow-y-auto">
          {entries.length === 0 ? (
            <p className="text-[11px] text-gray-500 italic p-3">
              None. {hint}
            </p>
          ) : (
            <>
              <p className="text-[10px] text-gray-500 px-3 pt-2 pb-1 italic">
                {hint}
              </p>
              <div className="space-y-1 p-2">
                {entries.map((m) => (
                  <DiffEntryCard
                    key={m.id}
                    entry={m}
                    showRetrievalInfo={showRetrievalInfo}
                  />
                ))}
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
}

function DiffEntryCard({
  entry,
  showRetrievalInfo,
}: {
  entry: MemoryDiffEntry;
  showRetrievalInfo?: boolean;
}) {
  const ts = showRetrievalInfo
    ? entry.last_retrieved_at
    : entry.updated_at || entry.created_at;
  const tsRel = ts
    ? relativeTime(new Date(ts).getTime())
    : null;
  return (
    <div className="rounded border border-border bg-[#1f2937]/40 p-1.5 text-xs">
      <div className="flex items-center gap-1.5 mb-0.5 flex-wrap">
        <span className="text-[9px] uppercase text-gray-500">
          {entry.memory_type}
        </span>
        {entry.ticker && (
          <span className="text-[9px] font-mono text-gray-400">
            [{entry.ticker}]
          </span>
        )}
        {entry.strategy_id && (
          <span className="text-[9px] text-gray-500">
            ({entry.strategy_id})
          </span>
        )}
        {entry.importance !== null && (
          <span className="text-[9px] text-gray-500">
            imp {entry.importance.toFixed(1)}
          </span>
        )}
        {showRetrievalInfo && (
          <span className="text-[9px] text-gray-500">
            · refs {entry.reference_count} · pulls {entry.retrieval_count}
          </span>
        )}
        {tsRel && (
          <span className="text-[9px] text-gray-500 ml-auto">{tsRel}</span>
        )}
      </div>
      <p className="text-[11px] text-gray-300 leading-snug line-clamp-2">
        {entry.content_preview}
      </p>
    </div>
  );
}

function relativeTime(thenMs: number): string {
  const d = (Date.now() - thenMs) / 86_400_000;
  if (d < 0) return "future";
  if (d < 1 / 24) return `${Math.round(d * 24 * 60)}m ago`;
  if (d < 1) return `${Math.round(d * 24)}h ago`;
  if (d < 30) return `${d.toFixed(1)}d ago`;
  return `${(d / 30).toFixed(1)}mo ago`;
}

// ─── Gaps tab (carryover #40) ───────────────────────────────────────────────

function GapsTab() {
  const [result, setResult] = useState<GapAnalysisResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [windowDays, setWindowDays] = useState(30);
  const [thinRatio, setThinRatio] = useState(0.5);

  const run = async () => {
    setLoading(true);
    setMsg(null);
    try {
      const res = await api.curationGapAnalysis({
        thin_cluster_ratio: thinRatio,
        recent_trade_window_days: windowDays,
      });
      setResult(res);
      setMsg(
        `${res.thin_clusters.length} thin cluster${
          res.thin_clusters.length === 1 ? "" : "s"
        } · ${res.under_covered_tickers.length} under-covered ticker${
          res.under_covered_tickers.length === 1 ? "" : "s"
        }`
      );
    } catch (e) {
      setMsg(`Failed: ${(e as Error).message}`);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="space-y-3">
      <p className="text-xs text-gray-400">
        Find underrepresented regions of Henry&apos;s memory. Gemini proposes
        3-5 specific observations to collect per thin cluster; recently-traded
        tickers with fewer than the minimum memory count are flagged as blind
        spots. Runs at most 6 Gemini calls per invocation (~$0.001 total).
      </p>

      <div className="flex items-center gap-3 flex-wrap text-xs">
        <label className="flex items-center gap-1.5 text-gray-400">
          Thin ratio
          <input
            type="number"
            min={0.1}
            max={1}
            step={0.05}
            value={thinRatio}
            onChange={(e) => setThinRatio(parseFloat(e.target.value) || 0.5)}
            className="w-16 bg-[#0b0f19] border border-border rounded px-1.5 py-1 text-gray-200"
          />
          <span className="text-[10px] text-gray-500">× median</span>
        </label>
        <label className="flex items-center gap-1.5 text-gray-400">
          Trade window
          <input
            type="number"
            min={1}
            max={365}
            value={windowDays}
            onChange={(e) => setWindowDays(parseInt(e.target.value) || 30)}
            className="w-16 bg-[#0b0f19] border border-border rounded px-1.5 py-1 text-gray-200"
          />
          <span className="text-[10px] text-gray-500">days</span>
        </label>
        <button
          onClick={run}
          disabled={loading}
          className="text-xs px-3 py-1.5 rounded bg-[#6366f1]/15 text-[#6366f1] hover:bg-[#6366f1]/25 disabled:opacity-40"
        >
          {loading ? "Analyzing…" : "Run gap analysis"}
        </button>
      </div>

      {msg && (
        <p className="text-[11px] text-gray-400 font-mono">{msg}</p>
      )}

      {result && result.under_covered_tickers.length > 0 && (
        <div className="rounded border border-border bg-[#0b0f19] p-3 space-y-2">
          <div className="flex items-center gap-2">
            <span
              className="inline-block w-2 h-2 rounded-full"
              style={{ backgroundColor: "#f59e0b" }}
            />
            <p className="text-xs text-gray-200 font-medium">
              Under-covered tickers ({result.under_covered_tickers.length})
            </p>
          </div>
          <p className="text-[10px] text-gray-500">
            Traded in the last {result.window_days}d but with fewer than{" "}
            {result.min_ticker_memories} memories. You&apos;re trading these
            without banking observations.
          </p>
          <div className="flex flex-wrap gap-1.5">
            {result.under_covered_tickers.map((t) => (
              <div
                key={t.ticker}
                className="flex items-center gap-1.5 px-2 py-1 rounded text-[11px] bg-[#1f2937]/40 border border-border"
                title={`${t.trade_count} trades · ${t.memory_count} memories · gap ${t.gap}`}
              >
                <span className="font-mono text-amber-400">{t.ticker}</span>
                <span className="text-gray-500">
                  {t.memory_count}/{t.trade_count + t.memory_count}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      {result && result.thin_clusters.length > 0 && (
        <div className="space-y-2">
          <div className="flex items-center gap-2">
            <span
              className="inline-block w-2 h-2 rounded-full"
              style={{ backgroundColor: "#6366f1" }}
            />
            <p className="text-xs text-gray-200 font-medium">
              Thin clusters ({result.thin_clusters.length})
            </p>
          </div>
          <div className="space-y-2 max-h-[50vh] overflow-y-auto pr-1">
            {result.thin_clusters.map((c) => (
              <div
                key={c.cluster_id}
                className="rounded border border-border bg-[#0b0f19] p-3 space-y-2"
              >
                <div className="flex items-center gap-2 flex-wrap">
                  <span className="text-[11px] font-mono text-gray-300">
                    cluster {c.cluster_id}
                  </span>
                  {c.cluster_label && (
                    <span className="text-[11px] text-gray-400 italic">
                      — {c.cluster_label}
                    </span>
                  )}
                  <span className="ml-auto text-[10px] text-gray-500 font-mono">
                    {c.member_count} members (median {c.median_cluster_size})
                  </span>
                </div>
                <p className="text-[10px] text-gray-500">{c.reason}</p>
                {c.suggested_topics.length > 0 ? (
                  <ul className="space-y-1 pl-1">
                    {c.suggested_topics.map((s, i) => (
                      <li
                        key={i}
                        className="text-[11px] text-gray-200 leading-snug flex items-start gap-1.5"
                      >
                        <span className="text-[#6366f1] mt-0.5">→</span>
                        <span>{s}</span>
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="text-[11px] text-gray-500 italic">
                    No suggestions returned by LLM.
                  </p>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {result &&
        result.thin_clusters.length === 0 &&
        result.under_covered_tickers.length === 0 && (
          <p className="text-xs text-gray-500 italic">
            No gaps detected — memory store is well-distributed and every
            recently-traded ticker has ≥{result.min_ticker_memories} memories.
          </p>
        )}
    </div>
  );
}

export default MemoryCurationPanel;
