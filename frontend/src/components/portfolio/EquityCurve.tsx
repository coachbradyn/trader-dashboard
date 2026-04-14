"use client";
import { EquityPoint } from "@/lib/types";
import { formatCurrency } from "@/lib/formatters";
import { Card, CardContent } from "@/components/ui/card";
import {
  ResponsiveContainer,
  AreaChart,
  Area,
  XAxis,
  YAxis,
  Tooltip,
  CartesianGrid,
} from "recharts";
import {
  chartColors,
  chartGrid,
  chartAxis,
  chartTooltip,
  chartAnimation,
} from "@/components/ui/chart-config";

export default function EquityCurve({ data }: { data: EquityPoint[] }) {
  if (data.length === 0) {
    return (
      <Card>
        <CardContent>
          <h3 className="font-bold text-white mb-3">Equity Curve</h3>
          <p className="text-gray-500 text-sm text-center py-12">No equity data yet</p>
        </CardContent>
      </Card>
    );
  }

  const chartData = data.map((d) => ({
    ...d,
    date: new Date(d.time).toLocaleDateString("en-US", { month: "short", day: "numeric" }),
  }));

  return (
    <Card>
      <CardContent>
        <h3 className="font-bold text-white mb-4">Equity Curve</h3>
        <ResponsiveContainer width="100%" height={300}>
          <AreaChart data={chartData}>
            <defs>
              <linearGradient id="equity-curve-grad" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor={chartColors.aiBlue} stopOpacity={0.35} />
                <stop offset="100%" stopColor={chartColors.aiBlue} stopOpacity={0} />
              </linearGradient>
            </defs>
            <CartesianGrid {...chartGrid} />
            <XAxis {...chartAxis} dataKey="date" />
            <YAxis {...chartAxis} tickFormatter={(v) => `$${(v / 1000).toFixed(1)}k`} />
            <Tooltip
              {...chartTooltip}
              formatter={(value: number) => [formatCurrency(value), "Equity"]}
            />
            <Area
              type="monotone"
              dataKey="equity"
              stroke={chartColors.aiBlue}
              strokeWidth={2}
              fill="url(#equity-curve-grad)"
              dot={false}
              animationDuration={chartAnimation.duration}
              animationEasing={chartAnimation.easing}
            />
          </AreaChart>
        </ResponsiveContainer>
      </CardContent>
    </Card>
  );
}
