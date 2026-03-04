"use client";
import Link from "next/link";
import { LeaderboardEntry } from "@/lib/types";
import { formatCurrency, formatPercent, formatNumber, pnlColor } from "@/lib/formatters";

const SORT_OPTIONS = [
  { value: "total_return_pct", label: "Return %" },
  { value: "win_rate", label: "Win Rate" },
  { value: "profit_factor", label: "Profit Factor" },
  { value: "sharpe_ratio", label: "Sharpe" },
  { value: "total_trades", label: "Trades" },
] as const;

interface Props {
  entries: LeaderboardEntry[];
  sortBy: string;
  onSortChange: (value: string) => void;
}

export default function LeaderboardTable({ entries, sortBy, onSortChange }: Props) {
  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-xl font-bold">Portfolio Leaderboard</h2>
        <div className="flex gap-1">
          {SORT_OPTIONS.map((opt) => (
            <button
              key={opt.value}
              onClick={() => onSortChange(opt.value)}
              className={`px-3 py-1.5 text-xs rounded-lg transition ${
                sortBy === opt.value
                  ? "bg-accent text-white"
                  : "bg-surface-light text-gray-400 hover:text-white"
              }`}
            >
              {opt.label}
            </button>
          ))}
        </div>
      </div>

      <div className="card overflow-hidden p-0">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-border text-gray-400 text-xs uppercase tracking-wider">
              <th className="px-4 py-3 text-left">#</th>
              <th className="px-4 py-3 text-left">Portfolio</th>
              <th className="px-4 py-3 text-right">Return</th>
              <th className="px-4 py-3 text-right">Win Rate</th>
              <th className="px-4 py-3 text-right">PF</th>
              <th className="px-4 py-3 text-right">Sharpe</th>
              <th className="px-4 py-3 text-right">Drawdown</th>
              <th className="px-4 py-3 text-right">P&L</th>
              <th className="px-4 py-3 text-right">Trades</th>
              <th className="px-4 py-3 text-right">Streak</th>
            </tr>
          </thead>
          <tbody>
            {entries.map((entry) => (
              <tr
                key={entry.portfolio_id}
                className="border-b border-border/50 hover:bg-surface-light/50 transition"
              >
                <td className="px-4 py-3 font-mono text-gray-500">{entry.rank}</td>
                <td className="px-4 py-3">
                  <Link
                    href={`/portfolios/${entry.portfolio_id}`}
                    className="font-medium text-white hover:text-accent transition"
                  >
                    {entry.portfolio_name}
                  </Link>
                  <p className="text-xs text-gray-500 mt-0.5 line-clamp-1">{entry.description}</p>
                </td>
                <td className={`px-4 py-3 text-right font-mono font-medium ${pnlColor(entry.total_return_pct)}`}>
                  {formatPercent(entry.total_return_pct)}
                </td>
                <td className="px-4 py-3 text-right font-mono">{formatPercent(entry.win_rate, 1).replace("+", "")}</td>
                <td className="px-4 py-3 text-right font-mono">{formatNumber(entry.profit_factor)}</td>
                <td className="px-4 py-3 text-right font-mono">{formatNumber(entry.sharpe_ratio)}</td>
                <td className="px-4 py-3 text-right font-mono text-loss">
                  {formatPercent(-Math.abs(entry.max_drawdown_pct), 1)}
                </td>
                <td className={`px-4 py-3 text-right font-mono ${pnlColor(entry.total_pnl)}`}>
                  {formatCurrency(entry.total_pnl)}
                </td>
                <td className="px-4 py-3 text-right font-mono text-gray-300">{entry.total_trades}</td>
                <td className="px-4 py-3 text-right">
                  <span className={`font-mono ${entry.current_streak > 0 ? "text-profit" : entry.current_streak < 0 ? "text-loss" : "text-gray-400"}`}>
                    {entry.current_streak > 0 ? `${entry.current_streak}W` : entry.current_streak < 0 ? `${Math.abs(entry.current_streak)}L` : "—"}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {entries.length === 0 && (
          <div className="px-4 py-12 text-center text-gray-500">No portfolios yet</div>
        )}
      </div>
    </div>
  );
}
