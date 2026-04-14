// Shared Recharts styling constants — dark terminal aesthetic.
// Import from here in every chart across the app so the visual language stays consistent.
//
// Usage:
//   import { chartColors, chartAxis, chartGrid, chartTooltip, chartAnimation } from "@/components/ui/chart-config";
//   <CartesianGrid {...chartGrid} />
//   <XAxis {...chartAxis} dataKey="date" />
//   <Tooltip {...chartTooltip} />
//   <Area animationDuration={chartAnimation.duration} animationEasing={chartAnimation.easing} ... />

export const chartColors = {
  profit: "#22c55e",
  loss: "#ef4444",
  aiBlue: "#6366f1",
  aiPurple: "#8b5cf6",
  amber: "#fbbf24",
  grid: "#1f2937",
  axisLine: "#374151",
  axisText: "#6b7280",
  tooltipBg: "#1f2937",
  tooltipBorder: "#374151",
  textPrimary: "#f3f4f6",
  textMuted: "#9ca3af",
} as const;

export const chartGrid = {
  stroke: chartColors.grid,
  strokeDasharray: "3 3",
  vertical: false,
} as const;

export const chartAxis = {
  stroke: chartColors.axisLine,
  tick: { fill: chartColors.axisText, fontSize: 10, fontFamily: "'JetBrains Mono', monospace" },
  tickLine: false,
  axisLine: { stroke: chartColors.axisLine },
} as const;

export const chartTooltip = {
  contentStyle: {
    background: chartColors.tooltipBg,
    border: `1px solid ${chartColors.tooltipBorder}`,
    borderRadius: 8,
    padding: "8px 12px",
    fontFamily: "'JetBrains Mono', monospace",
    fontSize: 12,
    boxShadow: "0 10px 30px -10px rgba(0,0,0,0.6)",
  },
  labelStyle: {
    color: chartColors.textMuted,
    fontFamily: "'Outfit', sans-serif",
    fontSize: 11,
    marginBottom: 2,
  },
  itemStyle: {
    color: chartColors.textPrimary,
    padding: 0,
  },
  cursor: { stroke: chartColors.axisLine, strokeDasharray: "3 3" },
} as const;

export const chartAnimation = {
  duration: 800,
  easing: "ease-out" as const,
};

// Strokes
export const chartStroke = {
  primary: 2,
  secondary: 1.5,
} as const;

// Gradient id helpers — call these to get a unique gradient id for <defs>
// so multiple charts on the same page don't collide.
export function gradientId(prefix: string, id: string | number): string {
  return `${prefix}-${id}`;
}

// A convenience SVG <defs> gradient block used under line/area fills.
// Renders as a vertical gradient from color -> transparent.
//
// Pair with <Area fill={`url(#${gradientId(...)})`} />.
//
// Usage:
//   <defs>
//     <AreaGradient id="equity" color={chartColors.aiBlue} />
//   </defs>
export const chartGradients = {
  equity: { id: "chart-grad-equity", from: chartColors.aiBlue, fromOpacity: 0.35, toOpacity: 0 },
  profit: { id: "chart-grad-profit", from: chartColors.profit, fromOpacity: 0.35, toOpacity: 0 },
  loss: { id: "chart-grad-loss", from: chartColors.loss, fromOpacity: 0.35, toOpacity: 0 },
  amber: { id: "chart-grad-amber", from: chartColors.amber, fromOpacity: 0.3, toOpacity: 0 },
} as const;
