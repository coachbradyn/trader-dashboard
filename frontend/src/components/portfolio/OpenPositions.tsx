"use client";
import { Position } from "@/lib/types";
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

export default function OpenPositions({ positions }: { positions: Position[] }) {
  if (positions.length === 0) {
    return (
      <Card>
        <CardContent>
          <h3 className="font-bold text-white mb-3">Open Positions</h3>
          <p className="text-gray-500 text-sm text-center py-6">No open positions</p>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card className="overflow-hidden">
      <CardContent className="p-0">
        <div className="px-5 pt-5 pb-3">
          <h3 className="font-bold text-white">
            Open Positions <span className="text-gray-500 text-sm font-normal ml-1">({positions.length})</span>
          </h3>
        </div>
        <Table>
          <TableHeader>
            <TableRow className="border-b border-border">
              <TableHead className="px-5">Ticker</TableHead>
              <TableHead className="px-3">Dir</TableHead>
              <TableHead className="px-3 text-right">Entry</TableHead>
              <TableHead className="px-3 text-right">Current</TableHead>
              <TableHead className="px-3 text-right">Qty</TableHead>
              <TableHead className="px-3 text-right">Unrealized P&L</TableHead>
              <TableHead className="px-3 text-right">Stop</TableHead>
              <TableHead className="px-3 text-right">Opened</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {positions.map((p) => (
              <TableRow key={p.trade_id} className="border-b border-border/50">
                <TableCell className="px-5 font-bold text-white">{p.ticker}</TableCell>
                <TableCell className="px-3">
                  <Badge variant={p.direction === "long" ? "long" : "short"}>
                    {p.direction.toUpperCase()}
                  </Badge>
                </TableCell>
                <TableCell className="px-3 text-right font-mono">{formatCurrency(p.entry_price)}</TableCell>
                <TableCell className="px-3 text-right font-mono text-white">
                  {p.current_price != null ? formatCurrency(p.current_price) : "—"}
                </TableCell>
                <TableCell className="px-3 text-right font-mono">{p.qty}</TableCell>
                <TableCell className="px-3 text-right">
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
                </TableCell>
                <TableCell className="px-3 text-right font-mono text-loss">
                  {p.stop_price != null ? formatCurrency(p.stop_price) : "—"}
                </TableCell>
                <TableCell className="px-3 text-right text-xs text-gray-500">{formatDateTime(p.entry_time)}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </CardContent>
    </Card>
  );
}
