"use client";
import { DailyStats } from "@/lib/types";
import { formatPercent } from "@/lib/formatters";
import { Card, CardContent } from "@/components/ui/card";

export default function MonthlyHeatmap({ data }: { data: DailyStats[] }) {
  if (data.length === 0) return null;

  // Group by month
  const months: Record<string, { total_pnl_pct: number; days: number }> = {};
  for (const d of data) {
    const key = d.date.slice(0, 7); // "YYYY-MM"
    if (!months[key]) months[key] = { total_pnl_pct: 0, days: 0 };
    months[key].total_pnl_pct += d.daily_pnl_pct;
    months[key].days++;
  }

  const entries = Object.entries(months).sort(([a], [b]) => a.localeCompare(b));

  function cellColor(pct: number): string {
    if (pct > 5) return "bg-green-500/70";
    if (pct > 2) return "bg-green-500/50";
    if (pct > 0) return "bg-green-500/25";
    if (pct === 0) return "bg-gray-700";
    if (pct > -2) return "bg-red-500/25";
    if (pct > -5) return "bg-red-500/50";
    return "bg-red-500/70";
  }

  return (
    <Card>
      <CardContent>
        <h3 className="font-bold text-white mb-4">Monthly Returns</h3>
        <div className="grid grid-cols-4 md:grid-cols-6 gap-2">
          {entries.map(([month, val]) => {
            const label = new Date(month + "-01").toLocaleDateString("en-US", { month: "short", year: "2-digit" });
            return (
              <div
                key={month}
                className={`rounded-lg p-3 text-center ${cellColor(val.total_pnl_pct)}`}
                title={`${label}: ${formatPercent(val.total_pnl_pct)} (${val.days} trading days)`}
              >
                <div className="text-xs text-gray-300">{label}</div>
                <div className={`text-sm font-mono font-bold ${val.total_pnl_pct >= 0 ? "text-white" : "text-white"}`}>
                  {formatPercent(val.total_pnl_pct, 1)}
                </div>
              </div>
            );
          })}
        </div>
      </CardContent>
    </Card>
  );
}
