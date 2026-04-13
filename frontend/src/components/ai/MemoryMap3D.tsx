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
import { useRouter, useSearchParams } from "next/navigation";
import { Canvas, useFrame, useThree, ThreeEvent } from "@react-three/fiber";
import { OrbitControls, Text, Billboard } from "@react-three/drei";
import * as THREE from "three";
import { api } from "@/lib/api";
import { MemoryCurationPanel } from "./MemoryCurationPanel";
import { BayesianOptimizationPanel } from "./BayesianOptimizationPanel";
import type {
  MemoryProjection,
  MemoryProjectionPoint,
  MemoryProjectionCluster,
} from "@/lib/types";

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

// Per-memory-type palette — stable, distinct, readable on dark bg.
const TYPE_COLORS: Record<string, string> = {
  observation: "#6366f1", // indigo
  lesson: "#10b981",      // emerald
  preference: "#f59e0b",  // amber
  strategy_note: "#8b5cf6", // violet
  decision: "#06b6d4",    // cyan
};
const UNKNOWN_TYPE_COLOR = "#6b7280"; // gray-500

function typeColor(memType: string | null | undefined): string {
  if (!memType) return UNKNOWN_TYPE_COLOR;
  return TYPE_COLORS[memType] ?? UNKNOWN_TYPE_COLOR;
}

// Lerp between two hex colors. Returns hex string.
function lerpHex(a: string, b: string, t: number): string {
  const clamp = (x: number) => Math.max(0, Math.min(1, x));
  t = clamp(t);
  const ai = parseInt(a.slice(1), 16);
  const bi = parseInt(b.slice(1), 16);
  const ar = (ai >> 16) & 0xff, ag = (ai >> 8) & 0xff, ab = ai & 0xff;
  const br = (bi >> 16) & 0xff, bg = (bi >> 8) & 0xff, bb = bi & 0xff;
  const r = Math.round(ar + (br - ar) * t);
  const g = Math.round(ag + (bg - ag) * t);
  const bl = Math.round(ab + (bb - ab) * t);
  return "#" + ((r << 16) | (g << 8) | bl).toString(16).padStart(6, "0");
}

// Importance 1-10 → cool blue → warm red gradient. Matches the "heatmap"
// aesthetic people intuitively read as low-to-high.
function importanceColor(importance: number): string {
  const t = (Math.max(1, Math.min(10, importance)) - 1) / 9;
  // #1e40af (blue-800) → #7c3aed (violet-600) → #dc2626 (red-600)
  if (t < 0.5) return lerpHex("#1e40af", "#7c3aed", t * 2);
  return lerpHex("#7c3aed", "#dc2626", (t - 0.5) * 2);
}

// Age (hours since created) → fresh cyan → faded gray. Clamp at 30 days.
function ageColor(createdAtIso: string | null | undefined): string {
  if (!createdAtIso) return "#6b7280";
  const age = (Date.now() - new Date(createdAtIso).getTime()) / 3_600_000;
  const t = Math.max(0, Math.min(1, age / (24 * 30))); // 0-30 days → 0-1
  return lerpHex("#22d3ee", "#4b5563", t); // cyan → gray-600
}

// reference_count on log scale → dim → bright white.
function referenceColor(refCount: number): string {
  const t = Math.max(0, Math.min(1, Math.log2(Math.max(1, refCount + 1)) / 6));
  return lerpHex("#374151", "#f3f4f6", t); // gray-700 → gray-100
}

// silhouette ∈ [-1, 1] → red (outlier) → gray → green (prototypical).
function silhouetteColor(sil: number | null | undefined): string {
  if (sil === null || sil === undefined) return "#4b5563";
  // Map [-1, 1] → [0, 1]
  const t = Math.max(0, Math.min(1, (sil + 1) / 2));
  if (t < 0.5) return lerpHex("#ef4444", "#6b7280", t * 2); // red → gray
  return lerpHex("#6b7280", "#10b981", (t - 0.5) * 2);       // gray → emerald
}

// Retrieval recency (carryover #41) → red (stale / never retrieved) →
// gray → green (fresh). Time scale: 0d → 30d+. Distinct from "age"
// (which is created_at) — recency tracks last_retrieved_at so an old
// memory that's still actively pulled stays green.
function recencyColor(lastRetrievedAt: string | null | undefined): string {
  if (!lastRetrievedAt) return "#ef4444"; // never retrieved — stale
  const days =
    (Date.now() - new Date(lastRetrievedAt).getTime()) / 86_400_000;
  const t = Math.max(0, Math.min(1, days / 30)); // 0-30d → 0-1
  if (t < 0.5) return lerpHex("#10b981", "#6b7280", t * 2); // emerald → gray
  return lerpHex("#6b7280", "#ef4444", (t - 0.5) * 2);       // gray → red
}

export type ColorMode =
  | "cluster"
  | "type"
  | "importance"
  | "age"
  | "recency"
  | "reference"
  | "silhouette";

export type SizeMode = "importance" | "reference";

function colorForPoint(
  p: MemoryProjectionPoint,
  mode: ColorMode
): string {
  switch (mode) {
    case "cluster":
      return clusterColor(p.cluster_id);
    case "type":
      return typeColor(p.memory_type);
    case "importance":
      return importanceColor(p.importance);
    case "age":
      return ageColor(p.created_at);
    case "recency":
      return recencyColor(p.last_retrieved_at);
    case "reference":
      return referenceColor(p.reference_count);
    case "silhouette":
      return silhouetteColor(p.silhouette);
  }
}

function sizeForPoint(
  p: MemoryProjectionPoint,
  mode: SizeMode,
  maxRef: number
): number {
  if (mode === "reference") {
    // Log-scale so a 100-ref memory doesn't render 100× bigger.
    const t = Math.log2(Math.max(1, p.reference_count + 1)) / Math.max(1, Math.log2(Math.max(2, maxRef + 1)));
    return 0.008 + t * 0.02;
  }
  // default: importance
  return 0.008 + (Math.max(1, Math.min(10, p.importance)) / 10) * 0.02;
}

// Created within the last 24h? Drives the recency glow pulse.
function isRecent(createdAtIso: string | null | undefined): boolean {
  if (!createdAtIso) return false;
  return Date.now() - new Date(createdAtIso).getTime() < 86_400_000;
}

// ─── Memory point ────────────────────────────────────────────────────────────

interface PointProps {
  point: MemoryProjectionPoint;
  onHover: (p: MemoryProjectionPoint | null) => void;
  isHovered: boolean;
  onRightClick: (p: MemoryProjectionPoint, screenX: number, screenY: number) => void;
  onClick: (p: MemoryProjectionPoint) => void;
  colorMode: ColorMode;
  sizeMode: SizeMode;
  maxRef: number;
  is2D: boolean;
  // When search is active, non-matching points dim out.
  searchDim: boolean;
  // Created in the last 24h — drives the recency glow pulse.
  recent: boolean;
  // Just retrieved by Henry (or a live preview query). Stronger amplitude
  // pulse at ~1Hz that decays — pulseStrength in [0, 1] from the parent.
  pulseStrength: number;
}

function MemoryPoint({
  point,
  onHover,
  isHovered,
  onRightClick,
  onClick,
  colorMode,
  sizeMode,
  maxRef,
  is2D,
  searchDim,
  recent,
  pulseStrength,
}: PointProps) {
  const meshRef = useRef<THREE.Mesh>(null!);
  const matRef = useRef<THREE.MeshStandardMaterial>(null!);
  const color = useMemo(
    () => colorForPoint(point, colorMode),
    [point, colorMode]
  );
  const radius = useMemo(
    () => sizeForPoint(point, sizeMode, maxRef),
    [point, sizeMode, maxRef]
  );

  // 2D mode: collapse z to 0 so all points lie on a plane.
  const position: [number, number, number] = is2D
    ? [point.x, point.y, 0]
    : [point.x, point.y, point.z];

  // Animated scale + emissive pulse. Three layered effects:
  //   - hover: scale up + bright
  //   - recent (≤24h old): gentle 0.5 Hz breathing
  //   - pulseStrength > 0: just retrieved by Henry / live query — a faster,
  //     stronger pulse that decays as the parent ramps the strength down
  //     to 0 over PULSE_TTL_MS. Scale also kicks up slightly so the user's
  //     eye is drawn to it.
  useFrame(({ clock }) => {
    if (!meshRef.current) return;
    const pulseScale = pulseStrength * 0.4;
    const targetScale = isHovered
      ? 1.6
      : searchDim
      ? 0.85
      : 1.0 + pulseScale;
    const cur = meshRef.current.scale.x;
    meshRef.current.scale.setScalar(cur + (targetScale - cur) * 0.2);

    if (matRef.current) {
      let baseEm = isHovered ? 0.9 : 0.35;
      if (recent) {
        baseEm += 0.25 + 0.2 * Math.sin(clock.elapsedTime * Math.PI);
      }
      if (pulseStrength > 0) {
        // Stronger 1 Hz pulse, scaled by remaining strength.
        baseEm += pulseStrength * (0.8 + 0.4 * Math.sin(clock.elapsedTime * 2 * Math.PI));
      }
      // Dim if search is active and this point doesn't match — but never
      // dim a pulsed point (current retrieval beats search filter).
      const dim = searchDim && pulseStrength <= 0.05;
      const targetEm = dim ? baseEm * 0.25 : baseEm;
      matRef.current.emissiveIntensity =
        matRef.current.emissiveIntensity +
        (targetEm - matRef.current.emissiveIntensity) * 0.2;

      const targetOpacity = dim ? 0.2 : 1.0;
      matRef.current.opacity =
        matRef.current.opacity + (targetOpacity - matRef.current.opacity) * 0.2;
    }
  });

  return (
    <mesh
      ref={meshRef}
      position={position}
      onPointerOver={(e: ThreeEvent<PointerEvent>) => {
        e.stopPropagation();
        onHover(point);
      }}
      onPointerOut={(e: ThreeEvent<PointerEvent>) => {
        e.stopPropagation();
        onHover(null);
      }}
      onClick={(e: ThreeEvent<MouseEvent>) => {
        e.stopPropagation();
        onClick(point);
      }}
      onContextMenu={(e: ThreeEvent<MouseEvent>) => {
        e.stopPropagation();
        const ne = e.nativeEvent as MouseEvent | undefined;
        onRightClick(point, ne?.clientX ?? 0, ne?.clientY ?? 0);
      }}
    >
      <sphereGeometry args={[radius, 12, 12]} />
      <meshStandardMaterial
        ref={matRef}
        color={color}
        emissive={color}
        emissiveIntensity={0.35}
        roughness={0.4}
        metalness={0.1}
        transparent
        opacity={1.0}
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
  is2D: boolean;
}

function ClusterCentroid({ id, x, y, z, weight, is2D }: CentroidProps) {
  const color = useMemo(() => clusterColor(id), [id]);
  const radius = 0.05 + Math.sqrt(Math.max(0, weight)) * 0.25;
  return (
    <mesh position={is2D ? [x, y, 0] : [x, y, z]}>
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

// ─── Cluster label (Gemini-generated, billboarded toward camera) ────────────

interface LabelProps {
  cluster: MemoryProjectionCluster;
  is2D: boolean;
}

function ClusterLabel({ cluster, is2D }: LabelProps) {
  const color = useMemo(() => clusterColor(cluster.id), [cluster.id]);
  // Y-offset scales with cluster size so labels float just above the centroid
  // sphere. Matches the ClusterCentroid radius formula.
  const yOffset = 0.05 + Math.sqrt(Math.max(0, cluster.weight)) * 0.25 + 0.06;
  const text = cluster.label || `cluster ${cluster.id}`;
  return (
    <Billboard
      position={[cluster.x, cluster.y + yOffset, is2D ? 0 : cluster.z]}
    >
      <Text
        fontSize={0.06}
        color={color}
        anchorX="center"
        anchorY="middle"
        outlineWidth={0.006}
        outlineColor="#000"
        outlineOpacity={0.85}
        maxWidth={0.8}
      >
        {text}
      </Text>
    </Billboard>
  );
}

// ─── Screenshot capture helper ──────────────────────────────────────────────
// Exposes a function that grabs the canvas contents as a data URL. Parent
// component stores the function via setCapture and calls it when the user
// clicks the screenshot button. Has to live inside <Canvas> so it can call
// useThree() to reach the renderer.

function CaptureHelper({ onReady }: { onReady: (fn: () => string) => void }) {
  const { gl, scene, camera } = useThree();
  const readyRef = useRef(false);
  useEffect(() => {
    if (readyRef.current) return;
    readyRef.current = true;
    onReady(() => {
      // Force a render right before capture so we don't grab a stale frame.
      gl.render(scene, camera);
      return gl.domElement.toDataURL("image/png");
    });
  }, [gl, scene, camera, onReady]);
  return null;
}

// ─── Scene ───────────────────────────────────────────────────────────────────

interface SceneProps {
  projection: Extract<MemoryProjection, { available: true }>;
  onHover: (p: MemoryProjectionPoint | null) => void;
  onRightClick: (p: MemoryProjectionPoint, screenX: number, screenY: number) => void;
  onClick: (p: MemoryProjectionPoint) => void;
  hoveredId: string | null;
  onCaptureReady: (fn: () => string) => void;
  colorMode: ColorMode;
  sizeMode: SizeMode;
  is2D: boolean;
  showFog: boolean;
  showRecentGlow: boolean;
  searchMatches: Set<string>; // empty = no filter; populated = only these match
  // memory_id → pulse strength in [0, 1]. Driven by retrieval-event polling
  // and the live query playback. Decays in the parent.
  pulses: Map<string, number>;
}

function Scene({
  projection,
  onHover,
  onRightClick,
  onClick,
  hoveredId,
  onCaptureReady,
  colorMode,
  sizeMode,
  is2D,
  showFog,
  showRecentGlow,
  searchMatches,
  pulses,
}: SceneProps) {
  const maxRef = useMemo(() => {
    let m = 0;
    for (const p of projection.memories) {
      if (p.reference_count > m) m = p.reference_count;
    }
    return m;
  }, [projection.memories]);

  return (
    <>
      {/* Fog depth cue — far points fade into the scene background. Matches
          the Canvas background color so there's no visible boundary. */}
      {showFog && <fog attach="fog" args={["#0b0f19", 2.5, 7]} />}

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
          is2D={is2D}
        />
      ))}

      {/* Cluster labels — Gemini-generated when available. */}
      {projection.clusters.map((c) => (
        <ClusterLabel key={`label-${c.id}`} cluster={c} is2D={is2D} />
      ))}

      {/* Memory points */}
      {projection.memories.map((p) => (
        <MemoryPoint
          key={p.id}
          point={p}
          onHover={onHover}
          isHovered={p.id === hoveredId}
          onRightClick={onRightClick}
          onClick={onClick}
          colorMode={colorMode}
          sizeMode={sizeMode}
          maxRef={maxRef}
          is2D={is2D}
          searchDim={searchMatches.size > 0 && !searchMatches.has(p.id)}
          recent={showRecentGlow && isRecent(p.created_at)}
          pulseStrength={pulses.get(p.id) ?? 0}
        />
      ))}

      <OrbitControls
        enablePan
        enableZoom
        enableRotate={!is2D}
        dampingFactor={0.1}
        rotateSpeed={0.5}
        zoomSpeed={0.6}
      />

      <CaptureHelper onReady={onCaptureReady} />
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

interface ContextMenu {
  point: MemoryProjectionPoint;
  x: number;
  y: number;
}

export function MemoryMap3D() {
  const router = useRouter();
  const searchParams = useSearchParams();
  // ?focus=<full-id-or-12-char-prefix> → pulse the matching memory.
  // Used by the chat citation parser to deep-link from a [mem:abc12345]
  // tag in Henry's response. Prefix-matched against the loaded
  // projection (citation tags ship as the first 12 hex chars of the
  // memory UUID; collision probability is negligible at our scale).
  const focusId = searchParams?.get("focus") || null;
  const [projection, setProjection] = useState<MemoryProjection | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [hovered, setHovered] = useState<MemoryProjectionPoint | null>(null);
  const [health, setHealth] = useState<HealthSummary | null>(null);
  const [contextMenu, setContextMenu] = useState<ContextMenu | null>(null);
  const [captureMsg, setCaptureMsg] = useState<string | null>(null);
  const captureFnRef = useRef<(() => string) | null>(null);

  // View controls
  const [colorMode, setColorMode] = useState<ColorMode>("cluster");
  const [sizeMode, setSizeMode] = useState<SizeMode>("importance");
  const [is2D, setIs2D] = useState(false);
  const [showFog, setShowFog] = useState(true);
  const [showRecentGlow, setShowRecentGlow] = useState(true);
  const [search, setSearch] = useState("");

  // ─── Live retrieval pulse ──────────────────────────────────────────
  // Map of memory_id → strength in [0, 1]. The decay loop ramps each
  // entry down to 0 over PULSE_TTL_MS, then deletes it. New retrievals
  // (polled from backend or fired via the live query input) reset the
  // strength to 1.0.
  const PULSE_TTL_MS = 8000;
  const PULSE_POLL_MS = 3000;
  const [pulses, setPulses] = useState<Map<string, number>>(new Map());
  // Cursor used by the polling loop — backend returns `cursor` which we
  // pass back as `since` next request.
  const pulseCursorRef = useRef<number>(0);
  // Seed cursor on first successful poll so we don't pulse the entire
  // ring buffer on first mount (would flood the viz with stale events).
  const pulseSeededRef = useRef<boolean>(false);

  // Decay loop — runs every 200ms, smoothly ramps down to 0.
  useEffect(() => {
    const id = setInterval(() => {
      setPulses((prev) => {
        if (prev.size === 0) return prev;
        const next = new Map(prev);
        // Use forEach to avoid relying on downlevelIteration of Map
        // (project's tsconfig targets es5, so for-of on Map is rejected).
        next.forEach((v, k) => {
          const decayed = v - 200 / PULSE_TTL_MS;
          if (decayed <= 0) next.delete(k);
          else next.set(k, decayed);
        });
        return next;
      });
    }, 200);
    return () => clearInterval(id);
  }, []);

  // Live retrieval feed — Phase 5b. Prefer WebSocket; fall back to the
  // 3s polling Sprint B used if the socket can't connect or drops.
  // The polling fallback is also what runs on backends that haven't
  // shipped /ws/retrieval-events yet.
  const [wsConnected, setWsConnected] = useState(false);
  useEffect(() => {
    let cancelled = false;
    let ws: WebSocket | null = null;
    let pollTimer: ReturnType<typeof setTimeout> | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    let reconnectAttempt = 0;

    const applyEvents = (events: { memory_ids: string[] }[]) => {
      if (!events || events.length === 0) return;
      setPulses((prev) => {
        const next = new Map(prev);
        for (const e of events) {
          for (const id of e.memory_ids) {
            next.set(id, 1.0);
          }
        }
        return next;
      });
    };

    const startPolling = () => {
      if (pollTimer || cancelled) return;
      const tick = async () => {
        try {
          const res = await api.getRetrievalEvents(pulseCursorRef.current);
          pulseCursorRef.current = res.cursor;
          if (!pulseSeededRef.current) {
            pulseSeededRef.current = true;
          } else if (res.events.length) {
            applyEvents(res.events);
          }
        } catch {
          /* swallow */
        }
        if (!cancelled) {
          pollTimer = setTimeout(tick, PULSE_POLL_MS);
        }
      };
      tick();
    };

    const stopPolling = () => {
      if (pollTimer) {
        clearTimeout(pollTimer);
        pollTimer = null;
      }
    };

    const wsUrl = (): string | null => {
      // Derive ws(s):// URL from the API base. NEXT_PUBLIC_API_URL looks
      // like https://...railway.app/api — swap protocol to wss.
      const apiBase =
        process.env.NEXT_PUBLIC_API_URL ||
        (typeof window !== "undefined"
          ? `${window.location.protocol}//${window.location.host}/api`
          : null);
      if (!apiBase) return null;
      try {
        const u = new URL(apiBase, typeof window !== "undefined" ? window.location.href : undefined);
        u.protocol = u.protocol === "https:" ? "wss:" : "ws:";
        // Strip trailing slash, append the ws route
        const path = u.pathname.replace(/\/$/, "");
        return `${u.protocol}//${u.host}${path}/memory/ws/retrieval-events`;
      } catch {
        return null;
      }
    };

    const connect = () => {
      if (cancelled) return;
      const url = wsUrl();
      if (!url) {
        startPolling();
        return;
      }
      try {
        ws = new WebSocket(url);
      } catch {
        startPolling();
        return;
      }
      ws.onopen = () => {
        if (cancelled) return;
        setWsConnected(true);
        reconnectAttempt = 0;
        stopPolling(); // WS is the source of truth while connected
        pulseSeededRef.current = true; // ignore historical events
      };
      ws.onmessage = (msg) => {
        try {
          const data = JSON.parse(msg.data);
          if (data && Array.isArray(data.events)) {
            applyEvents(data.events);
          }
          // {hello: <ts>} arrives once on connect — used to advance the
          // polling cursor so a future fallback doesn't replay history.
          if (typeof data?.hello === "number") {
            pulseCursorRef.current = data.hello;
          }
        } catch {
          /* ignore malformed messages */
        }
      };
      ws.onerror = () => {
        // Don't act here — onclose runs next and triggers reconnect.
      };
      ws.onclose = () => {
        if (cancelled) return;
        setWsConnected(false);
        ws = null;
        // Resume polling immediately so we don't miss events while
        // backing off. Reconnect WS in the background.
        startPolling();
        // Exponential backoff: 1s, 2s, 4s, 8s, max 30s.
        const delay = Math.min(30_000, 1000 * Math.pow(2, reconnectAttempt));
        reconnectAttempt += 1;
        reconnectTimer = setTimeout(connect, delay);
      };
    };

    // Pause when the tab is hidden — close the socket and stop polling.
    const onVisibility = () => {
      if (cancelled) return;
      if (document.visibilityState === "hidden") {
        if (ws) {
          try {
            ws.close();
          } catch {
            /* ignore */
          }
        }
        stopPolling();
      } else {
        // Tab visible again — reconnect immediately.
        if (!ws) connect();
      }
    };
    document.addEventListener("visibilitychange", onVisibility);

    connect();

    return () => {
      cancelled = true;
      document.removeEventListener("visibilitychange", onVisibility);
      stopPolling();
      if (reconnectTimer) clearTimeout(reconnectTimer);
      if (ws) {
        try {
          ws.close();
        } catch {
          /* ignore */
        }
      }
    };
  }, []);

  // Live query playback — type a query, fire a preview retrieval, pulse
  // the top-K returned IDs. Same pulse infrastructure as the live feed.
  const [liveQuery, setLiveQuery] = useState("");
  const [liveBusy, setLiveBusy] = useState(false);
  const [liveMsg, setLiveMsg] = useState<string | null>(null);

  const runLiveQuery = async () => {
    const q = liveQuery.trim();
    if (!q || liveBusy) return;
    setLiveBusy(true);
    setLiveMsg(null);
    try {
      const res = await api.previewRetrieval({ query: q, top_k: 8 });
      if (!res.ok) {
        setLiveMsg(res.reason);
      } else if (res.results.length === 0) {
        setLiveMsg("No matching memories found.");
      } else {
        setPulses((prev) => {
          const next = new Map(prev);
          for (const r of res.results) next.set(r.id, 1.0);
          return next;
        });
        setLiveMsg(
          `Pulsed top ${res.results.length} of ${res.n_candidates} candidates`
        );
      }
    } catch (e) {
      setLiveMsg(`Failed: ${(e as Error).message}`);
    } finally {
      setLiveBusy(false);
    }
  };

  // Compute the set of matching memory IDs for search dimming. Empty set
  // means no search active (everything renders at full opacity).
  const searchMatches = useMemo(() => {
    const matches = new Set<string>();
    const q = search.trim().toLowerCase();
    if (!q || !projection || !projection.available) return matches;
    for (const p of projection.memories) {
      const hay =
        (p.content_preview || "") +
        " " +
        (p.ticker || "") +
        " " +
        (p.memory_type || "") +
        " " +
        (p.strategy_id || "");
      if (hay.toLowerCase().includes(q)) {
        matches.add(p.id);
      }
    }
    return matches;
  }, [search, projection]);

  const handleCaptureReady = (fn: () => string) => {
    captureFnRef.current = fn;
  };

  const takeScreenshot = () => {
    if (!captureFnRef.current) return;
    try {
      const dataUrl = captureFnRef.current();
      const a = document.createElement("a");
      a.href = dataUrl;
      a.download = `henry-memory-3d-${new Date().toISOString().replace(/[:.]/g, "-")}.png`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      setCaptureMsg("Screenshot saved");
      setTimeout(() => setCaptureMsg(null), 2000);
    } catch (e) {
      setCaptureMsg(`Screenshot failed: ${(e as Error).message}`);
      setTimeout(() => setCaptureMsg(null), 4000);
    }
  };

  const handleRightClick = (
    p: MemoryProjectionPoint,
    screenX: number,
    screenY: number
  ) => {
    setContextMenu({ point: p, x: screenX, y: screenY });
  };

  const handlePointClick = (p: MemoryProjectionPoint) => {
    // Navigate to the Memory tab with the focused id in the URL. The Henry
    // page reads `?tab=memory&focus=<id>` and switches tabs accordingly.
    router.push(`/henry?tab=memory&focus=${encodeURIComponent(p.id)}`);
  };

  const handleExportJson = () => {
    if (!projection || !projection.available) return;
    const blob = new Blob(
      [JSON.stringify(projection, null, 2)],
      { type: "application/json" }
    );
    triggerDownload(blob, "json");
  };

  const handleExportCsv = () => {
    if (!projection || !projection.available) return;
    const headers = [
      "id",
      "x",
      "y",
      "z",
      "cluster_id",
      "silhouette",
      "importance",
      "reference_count",
      "memory_type",
      "ticker",
      "strategy_id",
      "validated",
      "created_at",
      "updated_at",
      "content_preview",
    ];
    const esc = (v: unknown): string => {
      if (v === null || v === undefined) return "";
      const s = String(v);
      if (/[",\n]/.test(s)) return `"${s.replace(/"/g, '""')}"`;
      return s;
    };
    const lines = [headers.join(",")];
    for (const m of projection.memories) {
      lines.push(
        [
          m.id,
          m.x.toFixed(4),
          m.y.toFixed(4),
          m.z.toFixed(4),
          m.cluster_id,
          m.silhouette ?? "",
          m.importance,
          m.reference_count,
          m.memory_type,
          m.ticker,
          m.strategy_id,
          m.validated,
          m.created_at,
          m.updated_at,
          m.content_preview,
        ]
          .map(esc)
          .join(",")
      );
    }
    const blob = new Blob([lines.join("\n")], { type: "text/csv" });
    triggerDownload(blob, "csv");
  };

  const triggerDownload = (blob: Blob, ext: "json" | "csv") => {
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `henry-memory-${new Date().toISOString().replace(/[:.]/g, "-")}.${ext}`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  };

  const handleDelete = async (id: string) => {
    if (!confirm("Delete this memory permanently?")) return;
    try {
      await api.deleteMemory(id);
      setContextMenu(null);
      // Reload projection so the deleted node disappears. Force refresh to
      // bypass the 10-min cache.
      await load(true);
    } catch (e) {
      alert(`Delete failed: ${(e as Error).message}`);
    }
  };

  // Carryover #32 — manually pin a memory to a specific cluster (or
  // clear an existing override). Routes through the admin-gated
  // /admin/reassign-cluster endpoint; secret cached per sessionStorage tab.
  const handleReassignCluster = async (
    memoryId: string,
    clusterId: number | null
  ) => {
    const cached = sessionStorage.getItem("memory_admin_secret");
    const secret =
      cached ||
      window.prompt(
        "Enter ADMIN_SECRET (stored only for this browser tab):"
      );
    if (!secret) return;
    if (!cached) sessionStorage.setItem("memory_admin_secret", secret);
    try {
      const res = await api.adminReassignCluster(secret, {
        memory_id: memoryId,
        cluster_id: clusterId,
      });
      setContextMenu(null);
      if (!res.ok) {
        alert(`Reassign failed: ${res.reason || "unknown"}`);
        return;
      }
      // Force-refresh the projection so the node moves visually.
      await load(true);
    } catch (e) {
      alert(`Reassign request failed: ${(e as Error).message}`);
    }
  };

  // Close context menu on outside click / Escape
  useEffect(() => {
    if (!contextMenu) return;
    const onClick = () => setContextMenu(null);
    const onEsc = (e: KeyboardEvent) => {
      if (e.key === "Escape") setContextMenu(null);
    };
    document.addEventListener("click", onClick);
    document.addEventListener("keydown", onEsc);
    return () => {
      document.removeEventListener("click", onClick);
      document.removeEventListener("keydown", onEsc);
    };
  }, [contextMenu]);

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

  // ?focus=<id> handler — pulses the matching memory whenever the URL
  // changes OR a fresh projection lands. Prefix-matches against the
  // loaded projection so the 12-char chat-citation IDs resolve to full
  // UUIDs cleanly.
  useEffect(() => {
    if (!focusId) return;
    if (!projection || !projection.available) return;
    const id = focusId.toLowerCase();
    const target = projection.memories.find(
      (m) => m.id.toLowerCase().startsWith(id) || m.id.toLowerCase() === id
    );
    if (!target) return;
    setPulses((prev) => {
      const next = new Map(prev);
      next.set(target.id, 1.0);
      return next;
    });
  }, [focusId, projection]);

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

  const runEnsureSchema = async () => {
    const secret = promptSecret();
    if (!secret) return;
    setAdminBusy("schema");
    setAdminMsg("Ensuring schema…");
    try {
      const res = await api.adminEnsureSchema(secret);
      const missingBefore = res.missing_before ?? [];
      const changesLine =
        res.changes && res.changes.length
          ? ` — ${res.changes.join(" · ")}`
          : "";
      if (res.ok) {
        if (missingBefore.length === 0) {
          setAdminMsg(`Schema was already complete${changesLine}`);
        } else {
          setAdminMsg(
            `Schema updated — added ${missingBefore.join(", ")}${changesLine}`
          );
        }
        await loadHealth();
      } else {
        setAdminMsg(`Schema ensure failed: ${res.reason || "unknown"}${changesLine}`);
      }
    } catch (e) {
      setAdminMsg(`Schema request failed: ${(e as Error).message}`);
    } finally {
      setAdminBusy(null);
    }
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

  // Relabel-only — runs the LLM labeling pass on the existing fit
  // without refitting. Use when clusters show as "cluster 1/2/3" because
  // they were created before the labeling code shipped, or after a
  // GEMINI_API_KEY change.
  const runRelabelClusters = async () => {
    const secret = promptSecret();
    if (!secret) return;
    setAdminBusy("relabel");
    setAdminMsg("Relabeling…");
    try {
      const res = await api.adminRelabelClusters(secret);
      if (res.ok && res.summary) {
        setAdminMsg(
          `Relabeled ${res.summary.labeled}/${res.summary.attempted} clusters. Refresh the projection to see the new labels.`
        );
        await load(true);
      } else {
        setAdminMsg(`Relabel failed: ${res.reason || "unknown"}`);
      }
    } catch (e) {
      setAdminMsg(`Relabel request failed: ${(e as Error).message}`);
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
        First-time setup when you can&apos;t run CLI scripts. Run in order:
        (0) create missing columns if the migration didn&apos;t land,
        (1) embed every memory that lacks a vector, (2) fit gaussian
        clusters. Requires ADMIN_SECRET. All steps are idempotent.
      </p>
      <div className="flex flex-wrap gap-2 pt-1">
        <button
          onClick={runEnsureSchema}
          disabled={adminBusy !== null}
          className="text-xs px-3 py-1.5 rounded bg-[#f59e0b]/15 text-[#f59e0b] hover:bg-[#f59e0b]/25 disabled:opacity-40 disabled:cursor-not-allowed"
        >
          {adminBusy === "schema" ? "Ensuring…" : "0. Ensure schema"}
        </button>
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
          onClick={runRelabelClusters}
          disabled={adminBusy !== null}
          className="text-xs px-3 py-1.5 rounded bg-[#a855f7]/15 text-[#a855f7] hover:bg-[#a855f7]/25 disabled:opacity-40 disabled:cursor-not-allowed"
          title="Re-run only the LLM labeling pass on the existing GMM fit (no refit). Use when clusters show as 'cluster 1/2/3'."
        >
          {adminBusy === "relabel" ? "Relabeling…" : "Relabel clusters"}
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
      <div className="flex items-center justify-between gap-2 flex-wrap">
        <div className="text-xs text-gray-400">
          {projection.n_memories} memories · {projection.clusters.length}{" "}
          clusters · model{" "}
          <span className="font-mono text-gray-300">{projection.model_name}</span>
          <span
            className={
              "ml-2 inline-flex items-center gap-1 text-[10px] " +
              (wsConnected ? "text-emerald-400" : "text-gray-500")
            }
            title={
              wsConnected
                ? "Live retrieval feed via WebSocket"
                : "Polling fallback (3s interval) — WebSocket disconnected"
            }
          >
            <span
              className={
                "inline-block w-1.5 h-1.5 rounded-full " +
                (wsConnected ? "bg-emerald-400 animate-pulse" : "bg-gray-500")
              }
            />
            {wsConnected ? "live" : "polling"}
          </span>
          {searchMatches.size > 0 && (
            <span className="ml-2 text-amber-400">
              · {searchMatches.size} match{searchMatches.size === 1 ? "" : "es"}
            </span>
          )}
        </div>
        <div className="flex items-center gap-2">
          {captureMsg && (
            <span className="text-[11px] text-gray-400 font-mono">
              {captureMsg}
            </span>
          )}
          <button
            onClick={handleExportJson}
            className="text-xs px-3 py-1.5 rounded bg-[#1f2937]/50 text-gray-300 hover:bg-[#1f2937] border border-border"
            title="Download the projection as JSON"
          >
            Export JSON
          </button>
          <button
            onClick={handleExportCsv}
            className="text-xs px-3 py-1.5 rounded bg-[#1f2937]/50 text-gray-300 hover:bg-[#1f2937] border border-border"
            title="Download a CSV of memory coords + metadata"
          >
            Export CSV
          </button>
          <button
            onClick={takeScreenshot}
            className="text-xs px-3 py-1.5 rounded bg-[#1f2937]/50 text-gray-300 hover:bg-[#1f2937] border border-border"
            title="Download a PNG of the current 3D view"
          >
            Screenshot
          </button>
          <button
            onClick={() => load(true)}
            className="text-xs px-3 py-1.5 rounded bg-[#1f2937]/50 text-gray-300 hover:bg-[#1f2937] border border-border"
          >
            Refresh projection
          </button>
        </div>
      </div>

      {/* Controls panel — color/size modes, search, 2D toggle, fog/glow */}
      <div className="flex items-center gap-3 flex-wrap p-2 rounded-md bg-[#1f2937]/20 border border-border">
        {/* Search */}
        <div className="flex items-center gap-1.5">
          <label className="text-[10px] uppercase tracking-wide text-gray-500">
            Search
          </label>
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="content, ticker, type…"
            className="text-xs bg-[#0b0f19] border border-border rounded px-2 py-1 text-gray-200 w-52 focus:border-[#6366f1] focus:outline-none"
          />
          {search && (
            <button
              onClick={() => setSearch("")}
              className="text-[10px] text-gray-500 hover:text-gray-300"
              aria-label="Clear search"
            >
              clear
            </button>
          )}
        </div>

        <div className="h-5 w-px bg-border" />

        {/* Color-by */}
        <div className="flex items-center gap-1.5">
          <label className="text-[10px] uppercase tracking-wide text-gray-500">
            Color
          </label>
          <select
            value={colorMode}
            onChange={(e) => setColorMode(e.target.value as ColorMode)}
            className="text-xs bg-[#0b0f19] border border-border rounded px-1.5 py-1 text-gray-200 focus:border-[#6366f1] focus:outline-none"
          >
            <option value="cluster">cluster</option>
            <option value="type">memory type</option>
            <option value="importance">importance</option>
            <option value="age">age (created)</option>
            <option value="recency">recency (last retrieved)</option>
            <option value="reference">reference count</option>
            <option value="silhouette">silhouette</option>
          </select>
        </div>

        {/* Size-by */}
        <div className="flex items-center gap-1.5">
          <label className="text-[10px] uppercase tracking-wide text-gray-500">
            Size
          </label>
          <select
            value={sizeMode}
            onChange={(e) => setSizeMode(e.target.value as SizeMode)}
            className="text-xs bg-[#0b0f19] border border-border rounded px-1.5 py-1 text-gray-200 focus:border-[#6366f1] focus:outline-none"
          >
            <option value="importance">importance</option>
            <option value="reference">reference count</option>
          </select>
        </div>

        <div className="h-5 w-px bg-border" />

        {/* Toggles */}
        <label className="flex items-center gap-1.5 text-[11px] text-gray-400 cursor-pointer">
          <input
            type="checkbox"
            checked={is2D}
            onChange={(e) => setIs2D(e.target.checked)}
            className="accent-[#6366f1]"
          />
          2D mode
        </label>
        <label className="flex items-center gap-1.5 text-[11px] text-gray-400 cursor-pointer">
          <input
            type="checkbox"
            checked={showFog}
            onChange={(e) => setShowFog(e.target.checked)}
            className="accent-[#6366f1]"
          />
          Fog
        </label>
        <label
          className="flex items-center gap-1.5 text-[11px] text-gray-400 cursor-pointer"
          title="Pulse memories created in the last 24h"
        >
          <input
            type="checkbox"
            checked={showRecentGlow}
            onChange={(e) => setShowRecentGlow(e.target.checked)}
            className="accent-[#6366f1]"
          />
          Recent glow
        </label>
      </div>

      {/* Live query playback — runs the same semantic+cluster ranking as
          the system-prompt builder and pulses the top-K hits. Pure
          retrieval, no LLM cost. Pairs with the live retrieval-event
          polling: every Henry call already pulses memories it pulled. */}
      <div className="flex items-center gap-2 flex-wrap">
        <span className="text-[10px] uppercase tracking-wide text-gray-500">
          Query
        </span>
        <input
          type="text"
          value={liveQuery}
          onChange={(e) => setLiveQuery(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") runLiveQuery();
          }}
          placeholder="What memories would Henry retrieve for…"
          className="flex-1 min-w-[260px] text-xs bg-[#0b0f19] border border-border rounded px-2 py-1.5 text-gray-200 focus:border-[#6366f1] focus:outline-none"
        />
        <button
          onClick={runLiveQuery}
          disabled={liveBusy || !liveQuery.trim()}
          className="text-xs px-3 py-1.5 rounded bg-[#6366f1]/20 text-[#6366f1] hover:bg-[#6366f1]/30 disabled:opacity-40 disabled:cursor-not-allowed"
        >
          {liveBusy ? "Searching…" : "Pulse top-K"}
        </button>
        {pulses.size > 0 && (
          <button
            onClick={() => setPulses(new Map())}
            className="text-[11px] text-gray-500 hover:text-gray-300"
          >
            clear pulses ({pulses.size})
          </button>
        )}
        {liveMsg && (
          <span className="text-[11px] text-gray-400 font-mono">{liveMsg}</span>
        )}
      </div>

      {/* 3D canvas */}
      <div className="relative w-full h-[60vh] rounded-lg border border-border bg-black/40 overflow-hidden">
        <Canvas
          // Key on is2D so the camera position reset takes effect when
          // toggling modes (otherwise OrbitControls retains its own state
          // and you stay in whatever 3D-orbit view you had).
          key={is2D ? "2d" : "3d"}
          camera={{
            position: is2D ? [0, 0, 2.2] : [2.2, 1.4, 2.2],
            fov: 50,
          }}
          dpr={[1, 2]}
          // preserveDrawingBuffer: required so toDataURL returns pixels
          // instead of a blank canvas on Chromium (spec-default clears
          // after composition).
          gl={{ antialias: true, alpha: true, preserveDrawingBuffer: true }}
          onContextMenu={(e) => e.preventDefault()}
        >
          <color attach="background" args={["#0b0f19"]} />
          <Scene
            projection={projection}
            onHover={setHovered}
            onRightClick={handleRightClick}
            onClick={handlePointClick}
            hoveredId={hovered?.id ?? null}
            onCaptureReady={handleCaptureReady}
            colorMode={colorMode}
            sizeMode={sizeMode}
            is2D={is2D}
            showFog={showFog}
            showRecentGlow={showRecentGlow}
            searchMatches={searchMatches}
            pulses={pulses}
          />
        </Canvas>

        {/* Hover tooltip */}
        {hovered && !contextMenu && (
          <div className="absolute bottom-3 left-3 right-3 p-3 rounded-md bg-[#0b0f19]/95 border border-border text-xs max-w-lg pointer-events-none">
            <div className="flex items-center gap-2 mb-1 flex-wrap">
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
              {/* Prototype indicator — ★ if this memory is closest to its
                  cluster centroid. Small but meaningful "this is the
                  archetypal memory for its cluster" signal. */}
              {projection.clusters.some(
                (c) => c.prototype_memory_id === hovered.id
              ) && (
                <span
                  className="text-[10px] text-amber-400"
                  title="Prototype for its cluster (closest to centroid)"
                >
                  ★ prototype
                </span>
              )}
              {/* Orphan indicator — silhouette below 0 means this memory
                  doesn't fit its assigned cluster well. Surfaces dupes,
                  mis-categorizations, and one-offs that don't belong to
                  any recurring theme. */}
              {hovered.silhouette !== null &&
                hovered.silhouette < -0.05 && (
                  <span
                    className="text-[10px] text-amber-300"
                    title={`Orphan — silhouette ${hovered.silhouette.toFixed(2)}. Doesn't fit its cluster well.`}
                  >
                    ⚠ orphan
                  </span>
                )}
              {/* Cluster label if available */}
              {hovered.cluster_id !== null &&
                (() => {
                  const c = projection.clusters.find(
                    (x) => x.id === hovered.cluster_id
                  );
                  return c?.label ? (
                    <span
                      className="text-[10px] text-gray-400 italic"
                      title="Cluster label (Gemini-generated)"
                    >
                      {c.label}
                    </span>
                  ) : null;
                })()}
              <span className="text-[10px] text-gray-500 ml-auto">
                importance {hovered.importance}/10
              </span>
            </div>
            <p className="text-gray-200 leading-relaxed">
              {hovered.content_preview || "(no preview)"}
            </p>
            {/* Per-memory recency line — carryover #41. Shows when this
                memory was last pulled by a retrieval; 'never' for brand-new
                memories. Paired with the cluster aging block below for
                context about the neighborhood. */}
            {hovered.last_retrieved_at !== undefined && (
              <p className="text-[10px] text-gray-500 mt-1.5 font-mono">
                Last retrieved:{" "}
                {hovered.last_retrieved_at
                  ? (() => {
                      const d =
                        (Date.now() -
                          new Date(hovered.last_retrieved_at).getTime()) /
                        86_400_000;
                      if (d < 1) return `${Math.round(d * 24)}h ago`;
                      return `${d.toFixed(1)}d ago`;
                    })()
                  : "never"}
                {hovered.retrieval_count !== undefined &&
                  ` · ${hovered.retrieval_count} total pulls`}
              </p>
            )}
            {/* Cluster aging block — carryover #41. Surfaces "hot" vs
                "stale" status of the hovered memory's cluster so the user
                can read neighborhood health at a glance. */}
            {hovered.cluster_id !== null &&
              (() => {
                const c = projection.clusters.find(
                  (x) => x.id === hovered.cluster_id
                );
                if (!c) return null;
                const avgDays = c.avg_days_since_retrieval;
                const stale = c.never_retrieved_count ?? 0;
                const decayed = c.decayed_count ?? 0;
                const avgImp = c.avg_importance;
                const bits: string[] = [];
                if (avgDays !== null && avgDays !== undefined) {
                  bits.push(`avg ${avgDays.toFixed(1)}d since retrieval`);
                }
                if (avgImp !== null && avgImp !== undefined) {
                  bits.push(`avg importance ${avgImp.toFixed(1)}/10`);
                }
                if (stale > 0) bits.push(`${stale} never retrieved`);
                if (decayed > 0) bits.push(`${decayed} decayed`);
                if (!bits.length) return null;
                // Loose heuristic for status color
                const hot = avgDays !== null && avgDays !== undefined && avgDays < 7;
                const cold = avgDays !== null && avgDays !== undefined && avgDays > 30;
                const hue = hot ? "text-emerald-400" : cold ? "text-red-400" : "text-amber-400";
                return (
                  <p className={"text-[10px] mt-0.5 font-mono " + hue}>
                    Cluster aging: {bits.join(" · ")}
                  </p>
                );
              })()}
            <p className="text-[10px] text-gray-600 mt-1.5 italic">
              Click to open in Memory tab · Right-click to delete
            </p>
          </div>
        )}

        {/* Right-click context menu */}
        {contextMenu && (
          <div
            className="fixed z-50 min-w-[220px] max-h-[400px] overflow-y-auto rounded-md bg-[#0b0f19] border border-border shadow-lg text-xs"
            style={{
              left: Math.min(contextMenu.x, window.innerWidth - 240),
              top: Math.min(contextMenu.y, window.innerHeight - 200),
            }}
            onClick={(e) => e.stopPropagation()}
          >
            <div className="px-3 py-2 border-b border-border text-[10px] text-gray-500 uppercase tracking-wide">
              {contextMenu.point.memory_type}
              {contextMenu.point.ticker && ` · ${contextMenu.point.ticker}`}
              {contextMenu.point.cluster_id_override !== null &&
                contextMenu.point.cluster_id_override !== undefined && (
                  <span className="ml-1 text-amber-400">· override</span>
                )}
            </div>
            <button
              onClick={() => handleDelete(contextMenu.point.id)}
              className="w-full text-left px-3 py-2 text-red-400 hover:bg-red-500/10"
            >
              Delete memory
            </button>

            {/* Carryover #32 — manually pin to a cluster. Lists every
                cluster from the current projection so the user can pick.
                Selecting "Auto (clear override)" returns the memory to
                GMM-assigned cluster_id. */}
            <details className="border-t border-border">
              <summary className="px-3 py-2 cursor-pointer text-gray-300 hover:bg-[#1f2937]/40">
                Reassign cluster…
                {contextMenu.point.cluster_id !== null && (
                  <span className="ml-1 text-[10px] text-gray-500">
                    (currently {contextMenu.point.cluster_id})
                  </span>
                )}
              </summary>
              <div className="max-h-[200px] overflow-y-auto">
                {contextMenu.point.cluster_id_override !== null &&
                  contextMenu.point.cluster_id_override !== undefined && (
                    <button
                      onClick={() =>
                        handleReassignCluster(contextMenu.point.id, null)
                      }
                      className="w-full text-left px-3 py-1.5 text-[11px] text-gray-400 hover:bg-[#1f2937]/40"
                    >
                      Clear override (auto)
                    </button>
                  )}
                {projection &&
                  projection.available &&
                  projection.clusters.map((c) => {
                    const isCurrent = c.id === contextMenu.point.cluster_id;
                    return (
                      <button
                        key={`reassign-${c.id}`}
                        onClick={() =>
                          handleReassignCluster(contextMenu.point.id, c.id)
                        }
                        disabled={isCurrent}
                        className={
                          "w-full text-left px-3 py-1.5 text-[11px] flex items-center gap-2 " +
                          (isCurrent
                            ? "text-gray-600 cursor-not-allowed"
                            : "text-gray-300 hover:bg-[#1f2937]/40")
                        }
                      >
                        <span
                          className="inline-block w-2 h-2 rounded-full"
                          style={{
                            backgroundColor: clusterColor(c.id),
                          }}
                        />
                        <span>cluster {c.id}</span>
                        {c.label && (
                          <span className="text-[10px] text-gray-500 truncate">
                            — {c.label}
                          </span>
                        )}
                        <span className="ml-auto text-[10px] text-gray-600">
                          {c.member_count}
                        </span>
                      </button>
                    );
                  })}
              </div>
            </details>

            <button
              onClick={() => setContextMenu(null)}
              className="w-full text-left px-3 py-2 text-gray-400 hover:bg-[#1f2937]/40 border-t border-border"
            >
              Cancel
            </button>
          </div>
        )}
      </div>

      {/* Fit-quality diagnostics card */}
      {projection.cluster_quality && projection.cluster_quality.k !== null && (
        <div className="rounded-lg border border-border bg-[#1f2937]/20 p-3 text-[11px] text-gray-400 grid grid-cols-2 md:grid-cols-5 gap-3">
          <div>
            <div className="text-[10px] text-gray-600 uppercase tracking-wide">
              k
            </div>
            <div className="text-gray-300 font-mono">
              {projection.cluster_quality.k}
            </div>
          </div>
          <div>
            <div className="text-[10px] text-gray-600 uppercase tracking-wide">
              fit log-likelihood
            </div>
            <div className="text-gray-300 font-mono">
              {projection.cluster_quality.log_likelihood?.toFixed(1) ?? "—"}
            </div>
          </div>
          <div>
            <div className="text-[10px] text-gray-600 uppercase tracking-wide">
              BIC
            </div>
            <div className="text-gray-300 font-mono">
              {projection.cluster_quality.bic?.toFixed(1) ?? "—"}
            </div>
          </div>
          <div>
            <div
              className="text-[10px] text-gray-600 uppercase tracking-wide"
              title="Mean silhouette ∈ [-1, 1]. >0 = clusters are meaningfully separated; ≈0 = overlap; <0 = bad fit."
            >
              avg silhouette
            </div>
            <div
              className={
                "font-mono " +
                ((projection.cluster_quality.avg_silhouette ?? 0) > 0.15
                  ? "text-emerald-400"
                  : (projection.cluster_quality.avg_silhouette ?? 0) > 0
                  ? "text-gray-300"
                  : "text-amber-400")
              }
            >
              {projection.cluster_quality.avg_silhouette?.toFixed(3) ?? "—"}
            </div>
          </div>
          <div>
            <div className="text-[10px] text-gray-600 uppercase tracking-wide">
              fit at
            </div>
            <div className="text-gray-400 font-mono text-[10px]">
              {projection.cluster_quality.fit_at
                ? new Date(projection.cluster_quality.fit_at).toLocaleString()
                : "—"}
            </div>
          </div>
        </div>
      )}

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

      {/* Curation panel — duplicate detection, orphan flagging, forget
          selector. Refresh projection + health on any change so the viz
          stays in sync. */}
      <details className="pt-2" open>
        <summary className="text-[11px] text-gray-500 cursor-pointer hover:text-gray-400">
          Curation
        </summary>
        <div className="pt-2">
          <MemoryCurationPanel
            onChanged={() => {
              load(true);
              loadHealth();
            }}
          />
        </div>
      </details>

      {/* Phase 7 — Bayesian hyperparameter optimization. Closed by
          default; the panel polls /optimization/status on open. */}
      <details className="pt-2">
        <summary className="text-[11px] text-gray-500 cursor-pointer hover:text-gray-400">
          Hyperparameter Optimization (System 10)
        </summary>
        <div className="pt-2">
          <BayesianOptimizationPanel
            onChanged={() => {
              load(true);
              loadHealth();
            }}
          />
        </div>
      </details>

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
