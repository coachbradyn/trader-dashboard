"use client";
import { Trade } from "@/lib/types";
import { formatCurrency, formatPercent, formatDateTime, pnlColor } from "@/lib/formatters";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import {
  Table,
  TableHeader,
  TableBody,
  TableRow,
  TableHead,
  TableCell,
} from "@/components/ui/table";

export default function TradeHistory({ trades }: { trades: Trade[] }) {
  const closedTrades = trades.filter((t) => t.status === "closed");

  if (closedTrades.length === 0) {
    return (
      <Card>
        <CardContent>
          <h3 className="font-bold text-white mb-3">Trade History</h3>
          <p className="text-gray-500 text-sm text-center py-6">No closed trades yet</p>
        </CardContent>
      </Card>
    );
  }

  return (
    <>
      {/* ── Mobile Card View ── */}
      <div className="md:hidden space-y-3">
        <h3 className="font-bold text-white px-1">
          Trade History <span className="text-gray-500 text-sm font-normal ml-1">({closedTrades.length})</span>
        </h3>
        {closedTrades.map((t) => (
          <Card key={t.id} className="overflow-hidden">
            <CardContent className="p-4">
              <div className="flex items-center justify-between mb-3">
                <div className="flex items-center gap-2">
                  <span className="font-medium text-white text-base">{t.ticker}</span>
                  <Badge variant={t.direction === "long" ? "long" : "short"}>
                    {t.direction.toUpperCase()}
                  </Badge>
                </div>
                <div className="text-right">
                  <span className={`font-mono font-bold text-base ${pnlColor(t.pnl_percent ?? 0)}`}>
                    {t.pnl_percent != null ? formatPercent(t.pnl_percent) : "—"}
                  </span>
                  {t.pnl_dollars != null && (
                    <span className={`block text-xs font-mono ${pnlColor(t.pnl_dollars)}`}>
                      {formatCurrency(t.pnl_dollars)}
                    </span>
                  )}
                </div>
              </div>
              <div className="grid grid-cols-3 gap-x-4 gap-y-2 text-xs">
                <div>
                  <span className="text-gray-500 block">Entry</span>
                  <span className="font-mono">{formatCurrency(t.entry_price)}</span>
                </div>
                <div>
                  <span className="text-gray-500 block">Exit</span>
                  <span className="font-mono">{t.exit_price ? formatCurrency(t.exit_price) : "—"}</span>
                </div>
                <div>
                  <span className="text-gray-500 block">Bars</span>
                  <span className="font-mono text-gray-400">{t.bars_in_trade ?? "—"}</span>
                </div>
              </div>
              <div className="flex items-center justify-between mt-2 pt-2 border-t border-border/30">
                <span className="text-[10px] text-gray-500">{t.exit_time ? formatDateTime(t.exit_time) : "—"}</span>
                {t.exit_reason && (
                  <span className="text-[10px] text-gray-400 bg-surface-light/30 px-2 py-0.5 rounded">{t.exit_reason}</span>
                )}
              </div>
            </CardContent>
          </Card>
        ))}
      </div>

      {/* ── Desktop Table View ── */}
      <Card className="overflow-hidden hidden md:block">
        <CardContent className="p-0">
          <div className="px-5 pt-5 pb-3">
            <h3 className="font-bold text-white">
              Trade History <span className="text-gray-500 text-sm font-normal ml-1">({closedTrades.length})</span>
            </h3>
          </div>
          <Table>
            <TableHeader>
              <TableRow className="border-b border-border">
                <TableHead className="px-5">Ticker</TableHead>
                <TableHead className="px-3">Dir</TableHead>
                <TableHead className="px-3 text-right">Entry</TableHead>
                <TableHead className="px-3 text-right">Exit</TableHead>
                <TableHead className="px-3 text-right">P&L</TableHead>
                <TableHead className="px-3 text-right">%</TableHead>
                <TableHead className="px-3">Reason</TableHead>
                <TableHead className="px-3 text-right">Bars</TableHead>
                <TableHead className="px-3 text-right">Date</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {closedTrades.map((t) => (
                <TableRow key={t.id} className="border-b border-border/50">
                  <TableCell className="px-5 py-2.5 font-medium text-white">{t.ticker}</TableCell>
                  <TableCell className="px-3 py-2.5">
                    <Badge variant={t.direction === "long" ? "long" : "short"}>
                      {t.direction.toUpperCase()}
                    </Badge>
                  </TableCell>
                  <TableCell className="px-3 py-2.5 text-right font-mono">{formatCurrency(t.entry_price)}</TableCell>
                  <TableCell className="px-3 py-2.5 text-right font-mono">{t.exit_price ? formatCurrency(t.exit_price) : "—"}</TableCell>
                  <TableCell className={`px-3 py-2.5 text-right font-mono font-medium ${pnlColor(t.pnl_dollars ?? 0)}`}>
                    {t.pnl_dollars != null ? formatCurrency(t.pnl_dollars) : "—"}
                  </TableCell>
                  <TableCell className={`px-3 py-2.5 text-right font-mono ${pnlColor(t.pnl_percent ?? 0)}`}>
                    {t.pnl_percent != null ? formatPercent(t.pnl_percent) : "—"}
                  </TableCell>
                  <TableCell className="px-3 py-2.5 text-xs text-gray-400">{t.exit_reason || "—"}</TableCell>
                  <TableCell className="px-3 py-2.5 text-right font-mono text-gray-400">{t.bars_in_trade ?? "—"}</TableCell>
                  <TableCell className="px-3 py-2.5 text-right text-xs text-gray-500">
                    {t.exit_time ? formatDateTime(t.exit_time) : "—"}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </CardContent>
      </Card>
    </>
  );
}
