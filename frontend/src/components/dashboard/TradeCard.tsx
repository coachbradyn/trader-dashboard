"use client";
import { Trade } from "@/lib/types";
import { formatCurrency, formatPercent, formatDateTime, formatTimeAgo, pnlColor } from "@/lib/formatters";

export default function TradeCard({ trade }: { trade: Trade }) {
  const isEntry = trade.status === "open";

  return (
    <div className="card flex items-start gap-4">
      {/* Direction badge */}
      <div className="flex-shrink-0 mt-1">
        <span className={trade.direction === "long" ? "badge-long" : "badge-short"}>
          {trade.direction === "long" ? "LONG" : "SHORT"}
        </span>
      </div>

      {/* Main content */}
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <span className="font-bold text-white">{trade.ticker}</span>
          <span className={isEntry ? "badge-open" : "badge-closed"}>
            {isEntry ? "OPEN" : "CLOSED"}
          </span>
          <span className="text-xs text-gray-500 ml-auto">
            {formatTimeAgo(isEntry ? trade.entry_time : trade.exit_time!)}
          </span>
        </div>

        <div className="flex gap-4 mt-2 text-xs text-gray-400">
          <span>Entry: {formatCurrency(trade.entry_price)}</span>
          {trade.exit_price && <span>Exit: {formatCurrency(trade.exit_price)}</span>}
          <span>Qty: {trade.qty}</span>
          {trade.timeframe && <span>{trade.timeframe}</span>}
          <span className="text-gray-600">{trade.trader_name}</span>
        </div>

        {trade.exit_reason && (
          <div className="mt-1 text-xs text-gray-500">
            Exit: {trade.exit_reason}
            {trade.bars_in_trade != null && ` · ${trade.bars_in_trade} bars`}
          </div>
        )}
      </div>

      {/* P&L */}
      {trade.pnl_dollars != null && (
        <div className="flex-shrink-0 text-right">
          <div className={`font-mono font-bold ${pnlColor(trade.pnl_dollars)}`}>
            {formatCurrency(trade.pnl_dollars)}
          </div>
          <div className={`text-xs font-mono ${pnlColor(trade.pnl_percent ?? 0)}`}>
            {formatPercent(trade.pnl_percent ?? 0)}
          </div>
        </div>
      )}
    </div>
  );
}
