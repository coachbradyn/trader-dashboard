"use client";
import { EquityPoint } from "@/lib/types";
import { formatPercent } from "@/lib/formatters";
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

export default function DrawdownChart({ data }: { data: EquityPoint[] }) {
  if (data.length === 0) return null;

  const chartData = data.map((d) => ({
    date: new Date(d.timestamp).toLocaleDateString("en-US", { month: "short", day: "numeric" }),
    drawdown: -Math.abs(d.drawdown_pct),
  }));

  return (
    <Card>
      <CardContent>
        <h3 className="font-bold text-white mb-4">Drawdown</h3>
        <ResponsiveContainer width="100%" height={200}>
          <AreaChart data={chartData}>
            <CartesianGrid strokeDasharray="3 3" stroke="#374151" />
            <XAxis dataKey="date" stroke="#6b7280" tick={{ fontSize: 11 }} />
            <YAxis stroke="#6b7280" tick={{ fontSize: 11 }} tickFormatter={(v) => `${v.toFixed(1)}%`} />
            <Tooltip
              contentStyle={{ background: "#1f2937", border: "1px solid #374151", borderRadius: 8 }}
              formatter={(value: number) => [formatPercent(value), "Drawdown"]}
            />
            <Area type="monotone" dataKey="drawdown" stroke="#ef4444" fill="#ef4444" fillOpacity={0.15} />
          </AreaChart>
        </ResponsiveContainer>
      </CardContent>
    </Card>
  );
}
