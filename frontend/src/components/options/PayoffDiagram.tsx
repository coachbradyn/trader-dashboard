"use client";
/**
 * PayoffDiagram
 * =============
 * Pure component: renders expiration P&L for an arbitrary multi-leg
 * options strategy. No network calls, no state, no effects — just math
 * + SVG. Safe to drop into trade builders, position cards, and action
 * cards without worrying about request waterfalls.
 *
 * Per-leg P&L at expiration:
 *   long call:  max(0, S-K) - premium
 *   short call: premium - max(0, S-K)
 *   long put:   max(0, K-S) - premium
 *   short put:  premium - max(0, K-S)
 * Multiply by quantity × 100 (contracts → shares).
 */
import { useMemo } from "react";

export interface PayoffLeg {
  type: "call" | "put";
  strike: number;
  action: "buy" | "sell";
  premium: number;
  quantity: number;
}

export interface PayoffDiagramProps {
  legs: PayoffLeg[];
  spotPrice: number;
  width?: number | string;
  height?: number;
  rangePct?: number; // default ±20%
  samples?: number;  // default 100
}

function legPnl(leg: PayoffLeg, S: number): number {
  const intrinsic =
    leg.type === "call" ? Math.max(0, S - leg.strike) : Math.max(0, leg.strike - S);
  const perShare =
    leg.action === "buy"
      ? intrinsic - leg.premium
      : leg.premium - intrinsic;
  return perShare * leg.quantity * 100;
}

export function PayoffDiagram({
  legs,
  spotPrice,
  width = "100%",
  height = 150,
  rangePct = 0.2,
  samples = 100,
}: PayoffDiagramProps) {
  const { points, maxP, minP, priceMin, priceMax, breakevens } = useMemo(() => {
    const pMin = spotPrice * (1 - rangePct);
    const pMax = spotPrice * (1 + rangePct);
    const step = (pMax - pMin) / (samples - 1);
    const pts: { S: number; pnl: number }[] = [];
    let mx = -Infinity;
    let mn = Infinity;
    for (let i = 0; i < samples; i++) {
      const S = pMin + step * i;
      const pnl = legs.reduce((acc, l) => acc + legPnl(l, S), 0);
      pts.push({ S, pnl });
      if (pnl > mx) mx = pnl;
      if (pnl < mn) mn = pnl;
    }
    // Breakevens: sign-change crossings
    const bes: number[] = [];
    for (let i = 1; i < pts.length; i++) {
      const a = pts[i - 1];
      const b = pts[i];
      if ((a.pnl <= 0 && b.pnl > 0) || (a.pnl >= 0 && b.pnl < 0)) {
        // Linear interp
        const t = Math.abs(a.pnl) / (Math.abs(a.pnl) + Math.abs(b.pnl));
        bes.push(a.S + t * (b.S - a.S));
      }
    }
    return { points: pts, maxP: mx, minP: mn, priceMin: pMin, priceMax: pMax, breakevens: bes };
  }, [legs, spotPrice, rangePct, samples]);

  // SVG coordinate system — we use percentages via viewBox so `width`
  // can be a string like "100%".
  const W = 400;
  const H = height;
  const margin = { top: 8, right: 8, bottom: 16, left: 32 };
  const innerW = W - margin.left - margin.right;
  const innerH = H - margin.top - margin.bottom;

  const xScale = (S: number) =>
    margin.left + ((S - priceMin) / (priceMax - priceMin)) * innerW;

  const pnlRange = Math.max(Math.abs(maxP), Math.abs(minP)) || 1;
  const yScale = (pnl: number) =>
    margin.top + (1 - (pnl + pnlRange) / (2 * pnlRange)) * innerH;
  const yZero = yScale(0);

  // Build separate paths for positive and negative regions, plus a full
  // line path. For simplicity we overlay two fills clipped to zero.
  const linePath = points
    .map((p, i) => `${i === 0 ? "M" : "L"}${xScale(p.S).toFixed(2)},${yScale(p.pnl).toFixed(2)}`)
    .join(" ");

  // Fill polygons: close each polygon back to the zero line.
  const polyPoints = points.map((p) => `${xScale(p.S).toFixed(2)},${yScale(p.pnl).toFixed(2)}`);
  const polyPos = `${xScale(priceMin).toFixed(2)},${yZero.toFixed(2)} ${polyPoints.join(" ")} ${xScale(priceMax).toFixed(2)},${yZero.toFixed(2)}`;

  return (
    <svg
      viewBox={`0 0 ${W} ${H}`}
      preserveAspectRatio="none"
      style={{ width, height, display: "block" }}
    >
      {/* Zero line */}
      <line
        x1={margin.left}
        x2={W - margin.right}
        y1={yZero}
        y2={yZero}
        stroke="#4b5563"
        strokeWidth={1}
        strokeDasharray="3 3"
      />
      {/* Spot vertical */}
      <line
        x1={xScale(spotPrice)}
        x2={xScale(spotPrice)}
        y1={margin.top}
        y2={H - margin.bottom}
        stroke="#6b7280"
        strokeWidth={1}
        strokeDasharray="2 3"
      />

      {/* Fill via clipped bands — positive green, negative red */}
      <defs>
        <clipPath id="pd-above-zero">
          <rect x={margin.left} y={margin.top} width={innerW} height={yZero - margin.top} />
        </clipPath>
        <clipPath id="pd-below-zero">
          <rect x={margin.left} y={yZero} width={innerW} height={H - margin.bottom - yZero} />
        </clipPath>
      </defs>
      <polygon points={polyPos} fill="#22c55e" fillOpacity={0.25} clipPath="url(#pd-above-zero)" />
      <polygon points={polyPos} fill="#ef4444" fillOpacity={0.25} clipPath="url(#pd-below-zero)" />

      {/* Line */}
      <path d={linePath} stroke="#e5e7eb" strokeWidth={1.2} fill="none" />

      {/* Breakeven markers */}
      {breakevens.map((be, i) => (
        <g key={i}>
          <circle cx={xScale(be)} cy={yZero} r={2.5} fill="#fbbf24" />
        </g>
      ))}

      {/* Y-axis labels: max / min */}
      <text x={2} y={yScale(maxP) + 3} fontSize={9} fill="#9ca3af" fontFamily="'JetBrains Mono', monospace">
        ${Math.round(maxP)}
      </text>
      <text x={2} y={yScale(minP) + 3} fontSize={9} fill="#9ca3af" fontFamily="'JetBrains Mono', monospace">
        ${Math.round(minP)}
      </text>

      {/* X-axis labels: min / spot / max */}
      <text x={margin.left} y={H - 2} fontSize={9} fill="#6b7280" fontFamily="'JetBrains Mono', monospace">
        ${priceMin.toFixed(0)}
      </text>
      <text
        x={xScale(spotPrice)}
        y={H - 2}
        fontSize={9}
        fill="#9ca3af"
        textAnchor="middle"
        fontFamily="'JetBrains Mono', monospace"
      >
        ${spotPrice.toFixed(0)}
      </text>
      <text
        x={W - margin.right}
        y={H - 2}
        fontSize={9}
        fill="#6b7280"
        textAnchor="end"
        fontFamily="'JetBrains Mono', monospace"
      >
        ${priceMax.toFixed(0)}
      </text>
    </svg>
  );
}

export default PayoffDiagram;
