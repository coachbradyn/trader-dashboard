"use client";

/**
 * 3D Memory Map
 * =============
 * Visualizes Henry's memory embeddings in 3D space. Each memory is a node
 * colored by its gaussian cluster; cluster centroids render as translucent
 * spheres sized by member count.
 *
 * Data comes from GET /api/memory/embeddings/projection which runs PCA on
 * the L2-normalized Voyage embeddings. The projection is cached server-side
 * for 10 minutes.
 */

import { useEffect, useMemo, useRef, useState } from "react";
import { Canvas, useFrame, ThreeEvent } from "@react-three/fiber";
import { OrbitControls } from "@react-three/drei";
import * as THREE from "three";
import { api } from "@/lib/api";
import type { MemoryProjection, MemoryProjectionPoint } from "@/lib/types";

// A deterministic palette so the same cluster_id always gets the same hue.
// Keeps the viz stable across refits when cluster counts are similar.
const CLUSTER_PALETTE = [
  "#6366f1", // indigo
  "#f59e0b", // amber
  "#10b981", // emerald
  "#ef4444", // red
  "#8b5cf6", // violet
  "#06b6d4", // cyan
  "#ec4899", // pink
  "#84cc16", // lime
  "#f97316", // orange
  "#14b8a6", // teal
  "#a855f7", // purple
  "#eab308", // yellow
  "#3b82f6", // blue
  "#22c55e", // green
  "#f43f5e", // rose
];

const UNCLUSTERED_COLOR = "#4b5563"; // gray-600 for nulls

function clusterColor(clusterId: number | null | undefined): string {
  if (clusterId === null || clusterId === undefined) return UNCLUSTERED_COLOR;
  return CLUSTER_PALETTE[clusterId % CLUSTER_PALETTE.length];
}

// ─── Memory point ────────────────────────────────────────────────────────────

interface PointProps {
  point: MemoryProjectionPoint;
  onHover: (p: MemoryProjectionPoint | null) => void;
  isHovered: boolean;
}

function MemoryPoint({ point, onHover, isHovered }: PointProps) {
  const meshRef = useRef<THREE.Mesh>(null!);
  const color = useMemo(() => clusterColor(point.cluster_id), [point.cluster_id]);
  // Importance 1-10 → sphere radius 0.008-0.028. Stays readable across the
  // full range without high-importance nodes eating the scene.
  const radius = 0.008 + (Math.max(1, Math.min(10, point.importance)) / 10) * 0.02;

  // Gentle bob on hover so the cursor target is unambiguous.
  useFrame(({ clock }) => {
    if (!meshRef.current) return;
    const target = isHovered ? 1.6 : 1.0;
    const current = meshRef.current.scale.x;
    const next = current + (target - current) * 0.2;
    meshRef.current.scale.setScalar(next);
  });

  return (
    <mesh
      ref={meshRef}
      position={[point.x, point.y, point.z]}
      onPointerOver={(e: ThreeEvent<PointerEvent>) => {
        e.stopPropagation();
        onHover(point);
      }}
      onPointerOut={(e: ThreeEvent<PointerEvent>) => {
        e.stopPropagation();
        onHover(null);
      }}
    >
      <sphereGeometry args={[radius, 12, 12]} />
      <meshStandardMaterial
        color={color}
        emissive={color}
        emissiveIntensity={isHovered ? 0.9 : 0.35}
        roughness={0.4}
        metalness={0.1}
      />
    </mesh>
  );
}

// ─── Cluster centroid (translucent anchor) ──────────────────────────────────

interface CentroidProps {
  id: number;
  x: number;
  y: number;
  z: number;
  memberCount: number;
  weight: number;
}

function ClusterCentroid({ id, x, y, z, memberCount, weight }: CentroidProps) {
  const color = useMemo(() => clusterColor(id), [id]);
  // Sphere radius scales with weight (sqrt so a cluster with 4× members
  // doesn't render 4× bigger — keeps the viz readable).
  const radius = 0.05 + Math.sqrt(Math.max(0, weight)) * 0.25;
  return (
    <mesh position={[x, y, z]}>
      <sphereGeometry args={[radius, 24, 24]} />
      <meshStandardMaterial
        color={color}
        transparent
        opacity={0.12}
        emissive={color}
        emissiveIntensity={0.2}
        depthWrite={false}
      />
    </mesh>
  );
}

// ─── Scene ───────────────────────────────────────────────────────────────────

interface SceneProps {
  projection: Extract<MemoryProjection, { available: true }>;
  onHover: (p: MemoryProjectionPoint | null) => void;
  hoveredId: string | null;
}

function Scene({ projection, onHover, hoveredId }: SceneProps) {
  return (
    <>
      <ambientLight intensity={0.45} />
      <directionalLight position={[2, 3, 5]} intensity={0.8} />
      <directionalLight position={[-3, -2, -4]} intensity={0.3} />

      {/* Cluster centroids first so they sit behind points */}
      {projection.clusters.map((c) => (
        <ClusterCentroid
          key={`cluster-${c.id}`}
          id={c.id}
          x={c.x}
          y={c.y}
          z={c.z}
          memberCount={c.member_count}
          weight={c.weight}
        />
      ))}

      {/* Memory points */}
      {projection.memories.map((p) => (
        <MemoryPoint
          key={p.id}
          point={p}
          onHover={onHover}
          isHovered={p.id === hoveredId}
        />
      ))}

      <OrbitControls
        enablePan
        enableZoom
        enableRotate
        dampingFactor={0.1}
        rotateSpeed={0.5}
        zoomSpeed={0.6}
      />
    </>
  );
}

// ─── Tab component ───────────────────────────────────────────────────────────

interface HealthSummary {
  total: number;
  with_embedding: number;
  coverage_pct: number;
  clustered: number;
}

export function MemoryMap3D() {
  const [projection, setProjection] = useState<MemoryProjection | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [hovered, setHovered] = useState<MemoryProjectionPoint | null>(null);
  const [health, setHealth] = useState<HealthSummary | null>(null);

  const loadHealth = async () => {
    try {
      const h = await api.getMemoryEmbeddingsHealth();
      const clustered = Object.values(h.cluster_distribution).reduce(
        (a, b) => a + b,
        0
      );
      setHealth({
        total: h.total,
        with_embedding: h.with_embedding,
        coverage_pct: h.coverage_pct,
        clustered,
      });
    } catch {
      setHealth(null);
    }
  };

  const load = async (force = false) => {
    setLoading(true);
    setError(null);
    try {
      const data = await api.getMemoryProjection(force);
      setProjection(data);
    } catch (e) {
      setError((e as Error).message || "Failed to load projection");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load(false);
    loadHealth();
  }, []);

  // ─── Admin actions ─────────────────────────────────────────────────────

  const [adminBusy, setAdminBusy] = useState<string | null>(null);
  const [adminMsg, setAdminMsg] = useState<string | null>(null);

  const promptSecret = (): string | null => {
    // Read from sessionStorage first so the user types it once per tab.
    const cached = sessionStorage.getItem("memory_admin_secret");
    if (cached) return cached;
    const entered = window.prompt(
      "Enter ADMIN_SECRET (stored only for this browser tab):"
    );
    if (entered) sessionStorage.setItem("memory_admin_secret", entered);
    return entered;
  };

  const runBackfill = async () => {
    const secret = promptSecret();
    if (!secret) return;
    setAdminBusy("backfill");
    setAdminMsg("Starting backfill…");
    try {
      const res = await api.adminBackfillEmbeddings(secret);
      if (!res.ok) {
        setAdminMsg(`Backfill not started: ${res.reason || "unknown"}`);
        setAdminBusy(null);
        return;
      }
      // Poll status every 2s until finished.
      setAdminMsg("Backfill running…");
      const poll = async () => {
        try {
          const status = await api.adminBackfillStatus(secret);
          if (status.running) {
            setAdminMsg(
              `Backfill running — processed ${status.processed}, embedded ${status.updated}, failed ${status.failed}`
            );
            setTimeout(poll, 2000);
          } else {
            if (status.error) {
              setAdminMsg(`Backfill error: ${status.error}`);
            } else {
              setAdminMsg(
                `Backfill complete — embedded ${status.updated}, failed ${status.failed}. Now run Fit Clusters.`
              );
            }
            setAdminBusy(null);
            await loadHealth();
          }
        } catch (e) {
          setAdminMsg(`Status check failed: ${(e as Error).message}`);
          setAdminBusy(null);
        }
      };
      setTimeout(poll, 2000);
    } catch (e) {
      setAdminMsg(`Backfill request failed: ${(e as Error).message}`);
      setAdminBusy(null);
    }
  };

  const runFitClusters = async () => {
    const secret = promptSecret();
    if (!secret) return;
    setAdminBusy("fit");
    setAdminMsg("Fitting clusters…");
    try {
      const res = await api.adminFitClusters(secret);
      if (res.ok && res.summary) {
        setAdminMsg(
          `Fit complete — k=${res.summary.k}, n=${res.summary.n_memories_fit}, model=${res.summary.model}`
        );
        await loadHealth();
        await load(true);
      } else {
        setAdminMsg(`Fit failed: ${res.reason || "unknown"}`);
      }
    } catch (e) {
      setAdminMsg(`Fit request failed: ${(e as Error).message}`);
    } finally {
      setAdminBusy(null);
    }
  };

  const renderAdminPanel = () => (
    <div className="rounded-lg border border-border bg-[#1f2937]/30 p-4 space-y-2">
      <p className="text-[11px] text-gray-400 uppercase tracking-wide">
        Admin — initialize memory system
      </p>
      <p className="text-xs text-gray-500">
        First-time setup when you can&apos;t run CLI scripts. Embeds every
        memory that lacks a vector, then fits gaussian clusters. Requires
        ADMIN_SECRET.
      </p>
      <div className="flex flex-wrap gap-2 pt-1">
        <button
          onClick={runBackfill}
          disabled={adminBusy !== null}
          className="text-xs px-3 py-1.5 rounded bg-[#6366f1]/15 text-[#6366f1] hover:bg-[#6366f1]/25 disabled:opacity-40 disabled:cursor-not-allowed"
        >
          {adminBusy === "backfill" ? "Backfilling…" : "1. Backfill embeddings"}
        </button>
        <button
          onClick={runFitClusters}
          disabled={adminBusy !== null}
          className="text-xs px-3 py-1.5 rounded bg-[#10b981]/15 text-[#10b981] hover:bg-[#10b981]/25 disabled:opacity-40 disabled:cursor-not-allowed"
        >
          {adminBusy === "fit" ? "Fitting…" : "2. Fit clusters"}
        </button>
        <button
          onClick={() => {
            sessionStorage.removeItem("memory_admin_secret");
            setAdminMsg("Cleared secret from session.");
          }}
          disabled={adminBusy !== null}
          className="text-xs px-3 py-1.5 rounded bg-[#1f2937]/50 text-gray-400 hover:bg-[#1f2937] border border-border disabled:opacity-40"
        >
          Clear secret
        </button>
      </div>
      {adminMsg && (
        <p className="text-[11px] text-gray-300 font-mono pt-1 break-words">
          {adminMsg}
        </p>
      )}
    </div>
  );

  // Cluster → count for the legend.
  const clusterStats = useMemo(() => {
    if (!projection || !projection.available) return [];
    const counts = new Map<number | null, number>();
    for (const m of projection.memories) {
      counts.set(m.cluster_id, (counts.get(m.cluster_id) ?? 0) + 1);
    }
    const entries = Array.from(counts.entries()).sort((a, b) => b[1] - a[1]);
    return entries.map(([id, count]) => ({
      id,
      count,
      color: clusterColor(id),
    }));
  }, [projection]);

  if (loading) {
    return (
      <div className="flex items-center justify-center h-[60vh] text-gray-500 text-sm">
        Loading memory projection…
      </div>
    );
  }

  const renderHealthSummary = () => {
    if (!health) return null;
    return (
      <div className="text-[11px] text-gray-500 pt-3 border-t border-border/40 mt-3">
        Diagnostics:{" "}
        <span className="text-gray-400">
          {health.with_embedding}/{health.total} memories embedded (
          {health.coverage_pct}%) · {health.clustered} clustered
        </span>
      </div>
    );
  };

  if (error) {
    return (
      <div className="space-y-4">
        <div className="p-6 rounded-lg border border-red-500/20 bg-red-500/5 text-sm">
          <p className="text-red-400 font-medium mb-2">
            Failed to load 3D projection
          </p>
          <p className="text-red-300/80 text-xs font-mono whitespace-pre-wrap break-words">
            {error}
          </p>
          <p className="text-gray-400 text-xs mt-3">
            If this says &quot;API 500&quot;, the backend hit an exception.
            Check Railway logs for &quot;memory_projection failed&quot; — the
            traceback is logged there. Most common cause: an unrun migration
            or no embedded memories yet.
          </p>
          <button
            onClick={() => {
              load(true);
              loadHealth();
            }}
            className="mt-3 text-xs px-3 py-1.5 rounded bg-[#6366f1]/10 text-[#6366f1] hover:bg-[#6366f1]/20"
          >
            Retry
          </button>
          {renderHealthSummary()}
        </div>
        {renderAdminPanel()}
      </div>
    );
  }

  if (!projection || !projection.available) {
    const reason =
      projection && !projection.available
        ? projection.reason
        : "No projection data available.";
    return (
      <div className="space-y-4">
        <div className="p-6 rounded-lg border border-border bg-[#1f2937]/30 text-gray-400 text-sm">
          <p className="mb-3">{reason}</p>
          <button
            onClick={() => {
              load(true);
              loadHealth();
            }}
            className="text-xs px-3 py-1.5 rounded bg-[#6366f1]/10 text-[#6366f1] hover:bg-[#6366f1]/20"
          >
            Retry
          </button>
          {renderHealthSummary()}
        </div>
        {renderAdminPanel()}
      </div>
    );
  }

  return (
    <div className="space-y-3">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="text-xs text-gray-400">
          {projection.n_memories} memories · {projection.clusters.length}{" "}
          clusters · model{" "}
          <span className="font-mono text-gray-300">{projection.model_name}</span>
        </div>
        <button
          onClick={() => load(true)}
          className="text-xs px-3 py-1.5 rounded bg-[#1f2937]/50 text-gray-300 hover:bg-[#1f2937] border border-border"
        >
          Refresh projection
        </button>
      </div>

      {/* 3D canvas */}
      <div className="relative w-full h-[60vh] rounded-lg border border-border bg-black/40 overflow-hidden">
        <Canvas
          camera={{ position: [2.2, 1.4, 2.2], fov: 50 }}
          dpr={[1, 2]}
          gl={{ antialias: true, alpha: true }}
        >
          <color attach="background" args={["#0b0f19"]} />
          <Scene
            projection={projection}
            onHover={setHovered}
            hoveredId={hovered?.id ?? null}
          />
        </Canvas>

        {/* Hover tooltip */}
        {hovered && (
          <div className="absolute bottom-3 left-3 right-3 p-3 rounded-md bg-[#0b0f19]/95 border border-border text-xs max-w-lg pointer-events-none">
            <div className="flex items-center gap-2 mb-1">
              <span
                className="inline-block w-2 h-2 rounded-full"
                style={{ backgroundColor: clusterColor(hovered.cluster_id) }}
              />
              <span className="text-[10px] uppercase tracking-wide text-gray-500">
                {hovered.memory_type}
              </span>
              {hovered.ticker && (
                <span className="text-[10px] font-mono text-gray-400">
                  [{hovered.ticker}]
                </span>
              )}
              {hovered.strategy_id && (
                <span className="text-[10px] text-gray-500">
                  ({hovered.strategy_id})
                </span>
              )}
              <span className="text-[10px] text-gray-500 ml-auto">
                importance {hovered.importance}/10
              </span>
            </div>
            <p className="text-gray-200 leading-relaxed">
              {hovered.content_preview || "(no preview)"}
            </p>
          </div>
        )}
      </div>

      {/* Legend */}
      <div className="flex flex-wrap gap-2 pt-1">
        {clusterStats.map(({ id, count, color }) => (
          <div
            key={String(id)}
            className="flex items-center gap-1.5 px-2 py-1 rounded text-[11px] bg-[#1f2937]/40 border border-border"
          >
            <span
              className="inline-block w-2.5 h-2.5 rounded-full"
              style={{ backgroundColor: color }}
            />
            <span className="text-gray-400">
              {id === null ? "unclustered" : `cluster ${id}`}
            </span>
            <span className="text-gray-500 font-mono">{count}</span>
          </div>
        ))}
      </div>

      <p className="text-[10px] text-gray-600 pt-1">
        PCA projection of {projection.model_name} embeddings to 3D. Sphere
        size = memory importance; translucent orbs = gaussian cluster
        centroids. Drag to rotate · scroll to zoom · right-click drag to pan.
      </p>

      {/* Admin actions available from the loaded view too — lets you rerun
          backfill / refit clusters after adding new memories. */}
      <details className="pt-2">
        <summary className="text-[11px] text-gray-500 cursor-pointer hover:text-gray-400">
          Admin actions
        </summary>
        <div className="pt-2">{renderAdminPanel()}</div>
      </details>
    </div>
  );
}

export default MemoryMap3D;
