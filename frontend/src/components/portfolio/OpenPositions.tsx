"use client";
import { Position } from "@/lib/types";
import { formatCurrency, formatPercent, formatDateTime, pnlColor } from "@/lib/formatters";

export default function OpenPositions({ positions }: { positions: Position[] }) {
  if (positions.length === 0) {
    return (
      <div className="card">
        <h3 className="font-bold text-white mb-3">Open Positions</h3>
        <p className="text-gray-500 text-sm text-center py-6">No open positions</p>
      </div>
    );
  }

  return (
    <div className="card overflow-hidden p-0">
      <div className="px-5 pt-5 pb-3">
        <h3 className="font-bold text-white">
          Open Positions <span className="text-gray-500 text-sm font-normal ml-1">({positions.length})</span>
        </h3>
      </div>
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-border text-gray-400 text-xs uppercase tracking-wider">
            <th className="px-5 py-2 text-left">Ticker</th>
            <th className="px-3 py-2 text-left">Dir</th>
            <th className="px-3 py-2 text-right">Entry</th>
            <th className="px-3 py-2 text-right">Current</th>
            <th className="px-3 py-2 text-right">Qty</th>
            <th className="px-3 py-2 text-right">Unrealized P&L</th>
            <th className="px-3 py-2 text-right">Stop</th>
            <th className="px-3 py-2 text-right">Opened</th>
          </tr>
        </thead>
        <tbody>
          {positions.map((p) => (
            <tr key={p.trade_id} className="border-b border-border/50 hover:bg-surface-light/50">
              <td className="px-5 py-3 font-bold text-white">{p.ticker}</td>
              <td className="px-3 py-3">
                <span className={p.direction === "long" ? "badge-long" : "badge-short"}>
                  {p.direction.toUpperCase()}
                </span>
              </td>
              <td className="px-3 py-3 text-right font-mono">{formatCurrency(p.entry_price)}</td>
              <td className="px-3 py-3 text-right font-mono text-white">
                {p.current_price != null ? formatCurrency(p.current_price) : "—"}
              </td>
              <td className="px-3 py-3 text-right font-mono">{p.qty}</td>
              <td className="px-3 py-3 text-right">
                {p.unrealized_pnl != null ? (
                  <div>
                    <span className={`font-mono font-medium ${pnlColor(p.unrealized_pnl)}`}>
                      {formatCurrency(p.unrealized_pnl)}
                    </span>
                    <span className={`text-xs ml-1 ${pnlColor(p.unrealized_pnl_pct ?? 0)}`}>
                      ({formatPercent(p.unrealized_pnl_pct ?? 0)})
                    </span>
                  </div>
                ) : (
                  <span className="text-gray-500">—</span>
                )}
              </td>
              <td className="px-3 py-3 text-right font-mono text-loss">
                {p.stop_price != null ? formatCurrency(p.stop_price) : "—"}
              </td>
              <td className="px-3 py-3 text-right text-xs text-gray-500">{formatDateTime(p.entry_time)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
