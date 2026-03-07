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
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-xl font-bold">Portfolio Leaderboard</h2>
        <Tabs value={sortBy} onValueChange={onSortChange}>
          <TabsList>
            {SORT_OPTIONS.map((opt) => (
              <TabsTrigger key={opt.value} value={opt.value}>
                {opt.label}
              </TabsTrigger>
            ))}
          </TabsList>
        </Tabs>
      </div>

      <Card className="overflow-hidden">
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
