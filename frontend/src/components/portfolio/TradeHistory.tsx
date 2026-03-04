"use client";
import { Trade } from "@/lib/types";
import { formatCurrency, formatPercent, formatDateTime, pnlColor } from "@/lib/formatters";

export default function TradeHistory({ trades }: { trades: Trade[] }) {
  const closedTrades = trades.filter((t) => t.status === "closed");

  if (closedTrades.length === 0) {
    return (
      <div className="card">
        <h3 className="font-bold text-white mb-3">Trade History</h3>
        <p className="text-gray-500 text-sm text-center py-6">No closed trades yet</p>
      </div>
    );
  }

  return (
    <div className="card overflow-hidden p-0">
      <div className="px-5 pt-5 pb-3">
        <h3 className="font-bold text-white">
          Trade History <span className="text-gray-500 text-sm font-normal ml-1">({closedTrades.length})</span>
        </h3>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-border text-gray-400 text-xs uppercase tracking-wider">
              <th className="px-5 py-2 text-left">Ticker</th>
              <th className="px-3 py-2 text-left">Dir</th>
              <th className="px-3 py-2 text-right">Entry</th>
              <th className="px-3 py-2 text-right">Exit</th>
              <th className="px-3 py-2 text-right">P&L</th>
              <th className="px-3 py-2 text-right">%</th>
              <th className="px-3 py-2 text-left">Reason</th>
              <th className="px-3 py-2 text-right">Bars</th>
              <th className="px-3 py-2 text-right">Date</th>
            </tr>
          </thead>
          <tbody>
            {closedTrades.map((t) => (
              <tr key={t.id} className="border-b border-border/50 hover:bg-surface-light/50">
                <td className="px-5 py-2.5 font-medium text-white">{t.ticker}</td>
                <td className="px-3 py-2.5">
                  <span className={t.direction === "long" ? "badge-long" : "badge-short"}>
                    {t.direction.toUpperCase()}
                  </span>
                </td>
                <td className="px-3 py-2.5 text-right font-mono">{formatCurrency(t.entry_price)}</td>
                <td className="px-3 py-2.5 text-right font-mono">{t.exit_price ? formatCurrency(t.exit_price) : "—"}</td>
                <td className={`px-3 py-2.5 text-right font-mono font-medium ${pnlColor(t.pnl_dollars ?? 0)}`}>
                  {t.pnl_dollars != null ? formatCurrency(t.pnl_dollars) : "—"}
                </td>
                <td className={`px-3 py-2.5 text-right font-mono ${pnlColor(t.pnl_percent ?? 0)}`}>
                  {t.pnl_percent != null ? formatPercent(t.pnl_percent) : "—"}
                </td>
                <td className="px-3 py-2.5 text-xs text-gray-400">{t.exit_reason || "—"}</td>
                <td className="px-3 py-2.5 text-right font-mono text-gray-400">{t.bars_in_trade ?? "—"}</td>
                <td className="px-3 py-2.5 text-right text-xs text-gray-500">
                  {t.exit_time ? formatDateTime(t.exit_time) : "—"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
