"use client";
import { Performance } from "@/lib/types";
import { formatCurrency, formatPercent, formatNumber, pnlColor } from "@/lib/formatters";

export default function PerformanceStats({ perf }: { perf: Performance }) {
  const stats = [
    { label: "Total Return", value: formatPercent(perf.total_return_pct), color: pnlColor(perf.total_return_pct) },
    { label: "Win Rate", value: `${perf.win_rate.toFixed(1)}%`, color: perf.win_rate >= 50 ? "text-profit" : "text-loss" },
    { label: "Profit Factor", value: formatNumber(perf.profit_factor), color: perf.profit_factor >= 1.5 ? "text-profit" : perf.profit_factor >= 1 ? "text-yellow-400" : "text-loss" },
    { label: "Sharpe Ratio", value: formatNumber(perf.sharpe_ratio), color: perf.sharpe_ratio >= 1 ? "text-profit" : "text-gray-300" },
    { label: "Max Drawdown", value: formatPercent(-Math.abs(perf.max_drawdown_pct)), color: "text-loss" },
    { label: "Total P&L", value: formatCurrency(perf.total_pnl), color: pnlColor(perf.total_pnl) },
    { label: "Avg Win", value: formatPercent(perf.avg_win_pct), color: "text-profit" },
    { label: "Avg Loss", value: formatPercent(-Math.abs(perf.avg_loss_pct)), color: "text-loss" },
    { label: "Total Trades", value: String(perf.total_trades), color: "text-white" },
    { label: "Win / Loss", value: `${perf.winning_trades} / ${perf.losing_trades}`, color: "text-gray-300" },
    { label: "Best Trade", value: formatPercent(perf.best_trade_pct), color: "text-profit" },
    { label: "Worst Trade", value: formatPercent(perf.worst_trade_pct), color: "text-loss" },
  ];

  return (
    <div className="card">
      <h3 className="font-bold text-white mb-4">Performance Metrics</h3>
      <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-4">
        {stats.map((s) => (
          <div key={s.label}>
            <div className="stat-label">{s.label}</div>
            <div className={`text-lg font-mono font-medium ${s.color}`}>{s.value}</div>
          </div>
        ))}
      </div>
    </div>
  );
}
