"use client";
import { EquityPoint } from "@/lib/types";
import { formatCurrency } from "@/lib/formatters";
import {
  ResponsiveContainer,
  LineChart,
  Line,
  XAxis,
  YAxis,
  Tooltip,
  CartesianGrid,
} from "recharts";

export default function EquityCurve({ data }: { data: EquityPoint[] }) {
  if (data.length === 0) {
    return (
      <div className="card">
        <h3 className="font-bold text-white mb-3">Equity Curve</h3>
        <p className="text-gray-500 text-sm text-center py-12">No equity data yet</p>
      </div>
    );
  }

  const chartData = data.map((d) => ({
    ...d,
    date: new Date(d.timestamp).toLocaleDateString("en-US", { month: "short", day: "numeric" }),
  }));

  return (
    <div className="card">
      <h3 className="font-bold text-white mb-4">Equity Curve</h3>
      <ResponsiveContainer width="100%" height={300}>
        <LineChart data={chartData}>
          <CartesianGrid strokeDasharray="3 3" stroke="#374151" />
          <XAxis dataKey="date" stroke="#6b7280" tick={{ fontSize: 11 }} />
          <YAxis stroke="#6b7280" tick={{ fontSize: 11 }} tickFormatter={(v) => `$${(v / 1000).toFixed(1)}k`} />
          <Tooltip
            contentStyle={{ background: "#1f2937", border: "1px solid #374151", borderRadius: 8 }}
            labelStyle={{ color: "#9ca3af" }}
            formatter={(value: number) => [formatCurrency(value), "Equity"]}
          />
          <Line type="monotone" dataKey="equity" stroke="#3b82f6" strokeWidth={2} dot={false} />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
