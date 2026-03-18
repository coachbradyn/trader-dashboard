"use client";
import { Performance } from "@/lib/types";
import { formatCurrency, formatPercent, formatNumber, pnlColor } from "@/lib/formatters";
import { Card, CardContent } from "@/components/ui/card";

export default function PerformanceStats({ perf }: { perf: Performance }) {
  const stats = [
    { label: "Total Return", value: formatPercent(perf.total_return_pct), color: pnlColor(perf.total_return_pct) },
    { label: "Win Rate", value: `${perf.win_rate.toFixed(1)}%`, color: perf.win_rate >= 50 ? "text-profit" : "text-loss" },
    { label: "Profit Factor", value: formatNumber(perf.profit_factor), color: perf.profit_factor >= 1.5 ? "text-profit" : perf.profit_factor >= 1 ? "text-yellow-400" : "text-loss" },
    { label: "Sharpe Ratio", value: formatNumber(perf.sharpe_ratio), color: perf.sharpe_ratio >= 1 ? "text-profit" : "text-gray-300" },
    { label: "Max Drawdown", value: formatPercent(-Math.abs(perf.max_drawdown_pct)), color: "text-loss" },
    { label: "Total P&L", value: formatCurrency(perf.total_pnl), color: pnlColor(perf.total_pnl) },
    { label: "Avg Win", value: formatCurrency(perf.avg_win), color: "text-profit" },
    { label: "Avg Loss", value: formatCurrency(perf.avg_loss), color: "text-loss" },
    { label: "Total Trades", value: String(perf.total_trades), color: "text-white" },
    { label: "Win / Loss", value: `${perf.wins} / ${perf.losses}`, color: "text-gray-300" },
  ];

  return (
    <Card>
      <CardContent>
        <h3 className="font-bold text-white mb-4">Performance Metrics</h3>
        <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-5 gap-4">
          {stats.map((s) => (
            <div key={s.label}>
              <div className="stat-label">{s.label}</div>
              <div className={`text-lg font-mono font-medium ${s.color}`}>{s.value}</div>
            </div>
          ))}
        </div>
      </CardContent>
    </Card>
  );
}
