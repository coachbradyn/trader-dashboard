"use client";
import { useRouter } from "next/navigation";
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
  const router = useRouter();

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
    <>
      {/* ── Mobile Card View ── */}
      <div className="md:hidden space-y-3">
        <h3 className="font-bold text-white px-1">
          Open Positions <span className="text-gray-500 text-sm font-normal ml-1">({positions.length})</span>
        </h3>
        {positions.map((p) => (
          <Card
            key={p.trade_id}
            onClick={() => router.push(`/screener/${p.ticker}`)}
            className="overflow-hidden cursor-pointer hover:bg-[#1f2937]/50 transition-colors"
          >
            <CardContent className="p-4">
              <div className="flex items-center justify-between mb-3">
                <div className="flex items-center gap-2">
                  <span className="font-bold text-white text-base">{p.ticker}</span>
                  <Badge variant={p.direction === "long" ? "long" : "short"}>
                    {p.direction.toUpperCase()}
                  </Badge>
                </div>
                {p.unrealized_pnl != null ? (
                  <div className="text-right">
                    <span className={`font-mono font-bold text-base ${pnlColor(p.unrealized_pnl)}`}>
                      {formatCurrency(p.unrealized_pnl)}
                    </span>
                    <span className={`block text-xs font-mono ${pnlColor(p.unrealized_pnl_pct ?? 0)}`}>
                      {formatPercent(p.unrealized_pnl_pct ?? 0)}
                    </span>
                  </div>
                ) : (
                  <span className="text-gray-500 font-mono">—</span>
                )}
              </div>
              <div className="grid grid-cols-3 gap-x-4 gap-y-2 text-xs">
                <div>
                  <span className="text-gray-500 block">Entry</span>
                  <span className="font-mono">{formatCurrency(p.entry_price)}</span>
                </div>
                <div>
                  <span className="text-gray-500 block">Current</span>
                  <span className="font-mono text-white">{p.current_price != null ? formatCurrency(p.current_price) : "—"}</span>
                </div>
                <div>
                  <span className="text-gray-500 block">Stop</span>
                  <span className="font-mono text-loss">{p.stop_price != null ? formatCurrency(p.stop_price) : "—"}</span>
                </div>
                <div>
                  <span className="text-gray-500 block">Qty</span>
                  <span className="font-mono">{p.qty}</span>
                </div>
                <div className="col-span-2">
                  <span className="text-gray-500 block">Opened</span>
                  <span className="text-gray-400">{formatDateTime(p.entry_time)}</span>
                </div>
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
                <TableRow
                  key={p.trade_id}
                  onClick={() => router.push(`/screener/${p.ticker}`)}
                  className="border-b border-border/50 cursor-pointer hover:bg-[#1f2937]/50 transition-colors"
                >
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
    </>
  );
}
