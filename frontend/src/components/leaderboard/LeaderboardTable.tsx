"use client";
import Link from "next/link";
import { LeaderboardEntry } from "@/lib/types";
import { formatCurrency, formatPercent, formatNumber, pnlColor } from "@/lib/formatters";
import { Card, CardContent } from "@/components/ui/card";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  Table,
  TableHeader,
  TableBody,
  TableRow,
  TableHead,
  TableCell,
} from "@/components/ui/table";

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
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 mb-4">
        <h2 className="text-xl font-bold">Portfolio Leaderboard</h2>
        <Tabs value={sortBy} onValueChange={onSortChange}>
          <TabsList className="overflow-x-auto flex-nowrap">
            {SORT_OPTIONS.map((opt) => (
              <TabsTrigger key={opt.value} value={opt.value} className="whitespace-nowrap text-xs sm:text-sm">
                {opt.label}
              </TabsTrigger>
            ))}
          </TabsList>
        </Tabs>
      </div>

      {/* ── Mobile Card View ── */}
      <div className="md:hidden space-y-3">
        {entries.map((entry) => (
          <Card key={entry.portfolio_id} className="overflow-hidden">
            <CardContent className="p-4">
              <div className="flex items-start justify-between mb-3">
                <div className="flex items-center gap-2.5 min-w-0">
                  <span className="text-xs font-mono text-gray-500 shrink-0">#{entry.rank}</span>
                  <div className="min-w-0">
                    <Link
                      href={`/portfolios/${entry.portfolio_id}`}
                      className="font-medium text-white hover:text-accent transition block truncate"
                    >
                      {entry.portfolio_name}
                    </Link>
                    {entry.description && (
                      <p className="text-[10px] text-gray-500 mt-0.5 truncate">{entry.description}</p>
                    )}
                  </div>
                </div>
                <span className={`font-mono font-bold text-lg shrink-0 ml-2 ${pnlColor(entry.total_return_pct)}`}>
                  {formatPercent(entry.total_return_pct)}
                </span>
              </div>
              <div className="grid grid-cols-3 gap-x-4 gap-y-2 text-xs">
                <div>
                  <span className="text-gray-500 block">P&L</span>
                  <span className={`font-mono font-medium ${pnlColor(entry.total_pnl)}`}>
                    {formatCurrency(entry.total_pnl)}
                  </span>
                </div>
                <div>
                  <span className="text-gray-500 block">Win Rate</span>
                  <span className="font-mono">{formatPercent(entry.win_rate, 1).replace("+", "")}</span>
                </div>
                <div>
                  <span className="text-gray-500 block">Sharpe</span>
                  <span className="font-mono">{formatNumber(entry.sharpe_ratio)}</span>
                </div>
                <div>
                  <span className="text-gray-500 block">Drawdown</span>
                  <span className="font-mono text-loss">{formatPercent(-Math.abs(entry.max_drawdown_pct), 1)}</span>
                </div>
                <div>
                  <span className="text-gray-500 block">Trades</span>
                  <span className="font-mono text-gray-300">{entry.total_trades}</span>
                </div>
                <div>
                  <span className="text-gray-500 block">Streak</span>
                  <span className={`font-mono ${entry.current_streak > 0 ? "text-profit" : entry.current_streak < 0 ? "text-loss" : "text-gray-400"}`}>
                    {entry.current_streak > 0 ? `${entry.current_streak}W` : entry.current_streak < 0 ? `${Math.abs(entry.current_streak)}L` : "—"}
                  </span>
                </div>
              </div>
            </CardContent>
          </Card>
        ))}
        {entries.length === 0 && (
          <div className="px-4 py-12 text-center text-gray-500">No portfolios yet</div>
        )}
      </div>

      {/* ── Desktop Table View ── */}
      <Card className="overflow-hidden hidden md:block">
        <CardContent className="p-0">
          <Table>
            <TableHeader>
              <TableRow className="border-b border-border">
                <TableHead>#</TableHead>
                <TableHead>Portfolio</TableHead>
                <TableHead className="text-right">Return</TableHead>
                <TableHead className="text-right">Win Rate</TableHead>
                <TableHead className="text-right">PF</TableHead>
                <TableHead className="text-right">Sharpe</TableHead>
                <TableHead className="text-right">Drawdown</TableHead>
                <TableHead className="text-right">P&L</TableHead>
                <TableHead className="text-right">Trades</TableHead>
                <TableHead className="text-right">Streak</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {entries.map((entry) => (
                <TableRow
                  key={entry.portfolio_id}
                  className="border-b border-border/50"
                >
                  <TableCell className="font-mono text-gray-500">{entry.rank}</TableCell>
                  <TableCell>
                    <Link
                      href={`/portfolios/${entry.portfolio_id}`}
                      className="font-medium text-white hover:text-accent transition"
                    >
                      {entry.portfolio_name}
                    </Link>
                    <p className="text-xs text-gray-500 mt-0.5 line-clamp-1">{entry.description}</p>
                  </TableCell>
                  <TableCell className={`text-right font-mono font-medium ${pnlColor(entry.total_return_pct)}`}>
                    {formatPercent(entry.total_return_pct)}
                  </TableCell>
                  <TableCell className="text-right font-mono">{formatPercent(entry.win_rate, 1).replace("+", "")}</TableCell>
                  <TableCell className="text-right font-mono">{formatNumber(entry.profit_factor)}</TableCell>
                  <TableCell className="text-right font-mono">{formatNumber(entry.sharpe_ratio)}</TableCell>
                  <TableCell className="text-right font-mono text-loss">
                    {formatPercent(-Math.abs(entry.max_drawdown_pct), 1)}
                  </TableCell>
                  <TableCell className={`text-right font-mono ${pnlColor(entry.total_pnl)}`}>
                    {formatCurrency(entry.total_pnl)}
                  </TableCell>
                  <TableCell className="text-right font-mono text-gray-300">{entry.total_trades}</TableCell>
                  <TableCell className="text-right">
                    <span className={`font-mono ${entry.current_streak > 0 ? "text-profit" : entry.current_streak < 0 ? "text-loss" : "text-gray-400"}`}>
                      {entry.current_streak > 0 ? `${entry.current_streak}W` : entry.current_streak < 0 ? `${Math.abs(entry.current_streak)}L` : "—"}
                    </span>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
          {entries.length === 0 && (
            <div className="px-4 py-12 text-center text-gray-500">No portfolios yet</div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
