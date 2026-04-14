"use client";
/**
 * TradeBuilder
 * ============
 * Modal panel for constructing and submitting an options order. Caller
 * passes: portfolio info (level, risk caps), the ticker, a seed strike/
 * contract from the chain viewer, and the live chain (so strike/expiry
 * pickers have real contracts to pick).
 *
 * Strategy tabs gate by `optionsLevel`:
 *   1 → covered_call only
 *   2 → long_call | long_put | covered_call
 *   3 → + bull_call_spread | bear_put_spread | iron_condor
 *
 * The live panel on the right recomputes max risk / reward / BE / Greeks
 * every time inputs change. Submission calls api.submitOptionsOrder.
 */
import { useMemo, useState, useEffect } from "react";
import type {
  OptionsChain, OptionsChainRow, OptionsOrderLeg,
} from "@/lib/types";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { PayoffDiagram, type PayoffLeg } from "./PayoffDiagram";

const FONT_MONO = { fontFamily: "'JetBrains Mono', monospace" } as const;

export type StrategyKey =
  | "long_call"
  | "long_put"
  | "covered_call"
  | "bull_call_spread"
  | "bear_put_spread"
  | "iron_condor";

const STRATEGY_LABEL: Record<StrategyKey, string> = {
  long_call: "Long call",
  long_put: "Long put",
  covered_call: "Covered call",
  bull_call_spread: "Bull call spread",
  bear_put_spread: "Bear put spread",
  iron_condor: "Iron condor",
};

const STRATEGY_MIN_LEVEL: Record<StrategyKey, number> = {
  covered_call: 1,
  long_call: 2,
  long_put: 2,
  bull_call_spread: 3,
  bear_put_spread: 3,
  iron_condor: 3,
};

export interface TradeBuilderProps {
  open: boolean;
  onClose: () => void;
  portfolioId: string;
  portfolioName?: string;
  optionsLevel: 1 | 2 | 3;
  maxOptionsRisk?: number | null;
  optionsAllocationPct?: number;
  portfolioEquity?: number;
  sharesHeld?: number;
  ticker: string;
  spotPrice: number | null;
  chain: OptionsChain | null;
  seed?: {
    strike: number;
    type: "call" | "put";
    expiration?: string;
    premium?: number;
  };
  onSubmitted?: (msg: string) => void;
}

export function TradeBuilder(props: TradeBuilderProps) {
  const {
    open, onClose, portfolioId, optionsLevel,
    maxOptionsRisk, optionsAllocationPct = 0.2, portfolioEquity,
    sharesHeld = 0, ticker, spotPrice, chain, seed,
    onSubmitted,
  } = props;

  // Allowed strategies for this level
  const allowed: StrategyKey[] = useMemo(() => {
    return (Object.keys(STRATEGY_MIN_LEVEL) as StrategyKey[]).filter(
      (k) => STRATEGY_MIN_LEVEL[k] <= optionsLevel
    );
  }, [optionsLevel]);

  const [strategy, setStrategy] = useState<StrategyKey>(() => {
    if (seed?.type === "put" && allowed.includes("long_put")) return "long_put";
    if (seed?.type === "call" && allowed.includes("long_call")) return "long_call";
    return allowed[0] ?? "long_call";
  });

  const expirations = chain?.expirations ?? [];
  const [expiration, setExpiration] = useState<string>(
    seed?.expiration || expirations[0] || ""
  );
  useEffect(() => {
    if (!expiration && expirations.length) setExpiration(expirations[0]);
  }, [expirations, expiration]);

  const bucket = expiration ? chain?.by_expiration?.[expiration] : undefined;
  const calls = bucket?.calls ?? [];
  const puts = bucket?.puts ?? [];

  // Strike state — multiple depending on strategy
  const [longStrike, setLongStrike] = useState<number | null>(seed?.strike ?? null);
  const [shortStrike, setShortStrike] = useState<number | null>(null);
  const [shortCallStrike, setShortCallStrike] = useState<number | null>(null);
  const [longCallStrike, setLongCallStrike] = useState<number | null>(null);
  const [shortPutStrike, setShortPutStrike] = useState<number | null>(null);
  const [longPutStrike, setLongPutStrike] = useState<number | null>(null);
  const [qty, setQty] = useState<number>(1);
  const [submitting, setSubmitting] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  // Reset relevant strikes when strategy or expiration changes
  useEffect(() => {
    setErr(null);
    if (strategy === "long_call" || strategy === "long_put") {
      if (longStrike == null && seed?.strike) setLongStrike(seed.strike);
    } else if (strategy === "covered_call") {
      if (shortStrike == null) {
        const otm = calls.find((c) => spotPrice != null && c.strike > spotPrice);
        setShortStrike(otm?.strike ?? null);
      }
    } else if (strategy === "bull_call_spread" || strategy === "bear_put_spread") {
      if (longStrike == null && seed?.strike) setLongStrike(seed.strike);
    } else if (strategy === "iron_condor") {
      if (shortCallStrike == null && spotPrice) {
        const sc = calls.find((c) => c.strike > spotPrice * 1.03);
        const lc = calls.find((c) => sc && c.strike > sc.strike + 2);
        const sp = [...puts].reverse().find((p) => p.strike < spotPrice * 0.97);
        const lp = [...puts].reverse().find((p) => sp && p.strike < sp.strike - 2);
        setShortCallStrike(sc?.strike ?? null);
        setLongCallStrike(lc?.strike ?? null);
        setShortPutStrike(sp?.strike ?? null);
        setLongPutStrike(lp?.strike ?? null);
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [strategy, expiration]);

  function findRow(rows: OptionsChainRow[], strike: number | null): OptionsChainRow | undefined {
    if (strike == null) return undefined;
    return rows.find((r) => r.strike === strike);
  }
  function mid(r?: OptionsChainRow): number | null {
    if (!r) return null;
    if (r.bid != null && r.ask != null && r.bid > 0 && r.ask > 0) return (r.bid + r.ask) / 2;
    return r.last ?? null;
  }

  // Derive legs + computed metrics
  const computed = useMemo(() => {
    let legs: PayoffLeg[] = [];
    let netPremium = 0;
    let maxRisk: number | null = null;
    let maxReward: number | null = null;
    const breakevens: number[] = [];

    const asLeg = (
      r: OptionsChainRow | undefined,
      type: "call" | "put",
      action: "buy" | "sell",
      q: number
    ): PayoffLeg | null => {
      if (!r) return null;
      const p = mid(r);
      if (p == null) return null;
      return { type, strike: r.strike, action, premium: p, quantity: q };
    };

    if (strategy === "long_call") {
      const leg = asLeg(findRow(calls, longStrike), "call", "buy", qty);
      if (leg) {
        legs = [leg];
        netPremium = leg.premium * qty;
        maxRisk = netPremium * 100;
        maxReward = null;
        breakevens.push(leg.strike + leg.premium);
      }
    } else if (strategy === "long_put") {
      const leg = asLeg(findRow(puts, longStrike), "put", "buy", qty);
      if (leg) {
        legs = [leg];
        netPremium = leg.premium * qty;
        maxRisk = netPremium * 100;
        maxReward = (leg.strike - leg.premium) * 100 * qty;
        breakevens.push(leg.strike - leg.premium);
      }
    } else if (strategy === "covered_call") {
      const maxContracts = Math.floor(sharesHeld / 100);
      const q = Math.min(qty, Math.max(maxContracts, 1));
      const leg = asLeg(findRow(calls, shortStrike), "call", "sell", q);
      if (leg) {
        legs = [leg];
        netPremium = -leg.premium * q;
        maxRisk = null; // covered — equity provides cover
        maxReward =
          (leg.premium + Math.max(0, leg.strike - (spotPrice ?? leg.strike))) * 100 * q;
        breakevens.push((spotPrice ?? leg.strike) - leg.premium);
      }
    } else if (strategy === "bull_call_spread" || strategy === "bear_put_spread") {
      const isBull = strategy === "bull_call_spread";
      const rows = isBull ? calls : puts;
      const longLeg = asLeg(findRow(rows, longStrike), isBull ? "call" : "put", "buy", qty);
      const shortLeg = asLeg(findRow(rows, shortStrike), isBull ? "call" : "put", "sell", qty);
      if (longLeg && shortLeg) {
        legs = [longLeg, shortLeg];
        const debit = longLeg.premium - shortLeg.premium;
        netPremium = debit * qty;
        const width = Math.abs(shortLeg.strike - longLeg.strike);
        maxRisk = debit * 100 * qty;
        maxReward = (width - debit) * 100 * qty;
        breakevens.push(isBull ? longLeg.strike + debit : longLeg.strike - debit);
      }
    } else if (strategy === "iron_condor") {
      const sc = asLeg(findRow(calls, shortCallStrike), "call", "sell", qty);
      const lc = asLeg(findRow(calls, longCallStrike), "call", "buy", qty);
      const sp = asLeg(findRow(puts, shortPutStrike), "put", "sell", qty);
      const lp = asLeg(findRow(puts, longPutStrike), "put", "buy", qty);
      if (sc && lc && sp && lp) {
        legs = [sc, lc, sp, lp];
        const credit = sc.premium - lc.premium + sp.premium - lp.premium;
        netPremium = -credit * qty;
        const callWidth = lc.strike - sc.strike;
        const putWidth = sp.strike - lp.strike;
        const w = Math.max(callWidth, putWidth);
        maxRisk = (w - credit) * 100 * qty;
        maxReward = credit * 100 * qty;
        breakevens.push(sp.strike - credit, sc.strike + credit);
      }
    }

    // Net greeks
    const g = { delta: 0, theta: 0, vega: 0 };
    const gSet = { delta: false, theta: false, vega: false };
    for (const leg of legs) {
      const row =
        leg.type === "call"
          ? findRow(calls, leg.strike)
          : findRow(puts, leg.strike);
      if (!row) continue;
      const sign = leg.action === "buy" ? 1 : -1;
      (["delta", "theta", "vega"] as const).forEach((k) => {
        const v = row[k];
        if (v != null) {
          g[k] += v * sign * leg.quantity;
          gSet[k] = true;
        }
      });
    }

    return { legs, netPremium, maxRisk, maxReward, breakevens, greeks: g, gSet };
  }, [strategy, longStrike, shortStrike, shortCallStrike, longCallStrike, shortPutStrike, longPutStrike, qty, calls, puts, sharesHeld, spotPrice]);

  // Warnings + blocks
  const dte = useMemo(() => {
    if (!expiration) return null;
    const [y, m, d] = expiration.split("-").map(Number);
    const ms = +new Date(y, m - 1, d) - +new Date();
    return Math.round(ms / 86400000);
  }, [expiration]);

  const warnings: string[] = [];
  const blocks: string[] = [];

  if (dte != null && dte > 0 && dte < 14) {
    warnings.push("Short-dated — elevated theta and gamma risk.");
  }
  // IV check: use ATM IV as a stand-in for IV rank
  const atmCall = useMemo(() => {
    if (spotPrice == null || calls.length === 0) return undefined;
    return calls.reduce((acc, r) =>
      Math.abs(r.strike - spotPrice) < Math.abs(acc.strike - spotPrice) ? r : acc,
      calls[0]
    );
  }, [calls, spotPrice]);
  if (atmCall?.iv != null && atmCall.iv > 0.5 && strategy.startsWith("long_")) {
    warnings.push("IV is elevated — long options are expensive. Consider a spread.");
  }
  if (strategy === "bull_call_spread" || strategy === "bear_put_spread") {
    if (longStrike != null && shortStrike != null) {
      const w = Math.abs(shortStrike - longStrike);
      if (w < 2) warnings.push(`Spread width is only $${w.toFixed(2)} — limited profit potential.`);
    }
  }
  if (strategy === "covered_call" && sharesHeld < qty * 100) {
    blocks.push(
      `Covered call requires ${qty * 100} shares (you hold ${sharesHeld}).`
    );
  }
  if (STRATEGY_MIN_LEVEL[strategy] > optionsLevel) {
    blocks.push(
      `Strategy ${STRATEGY_LABEL[strategy]} exceeds portfolio options level ${optionsLevel}.`
    );
  }
  if (computed.maxRisk != null && maxOptionsRisk != null && computed.maxRisk > maxOptionsRisk) {
    blocks.push(
      `Max risk $${computed.maxRisk.toFixed(0)} exceeds portfolio cap $${maxOptionsRisk.toFixed(0)}.`
    );
  }
  if (
    computed.maxRisk != null &&
    portfolioEquity != null &&
    optionsAllocationPct != null
  ) {
    const cap = portfolioEquity * optionsAllocationPct;
    if (computed.maxRisk > cap) {
      blocks.push(
        `Would exceed ${(optionsAllocationPct * 100).toFixed(0)}% options allocation ($${cap.toFixed(0)}).`
      );
    }
  }

  // Submit
  async function submit() {
    if (blocks.length || !computed.legs.length) return;
    setSubmitting(true);
    setErr(null);
    try {
      const apiLegs: OptionsOrderLeg[] = computed.legs.map((l) => {
        const row =
          l.type === "call" ? findRow(calls, l.strike) : findRow(puts, l.strike);
        return {
          option_symbol: row?.option_symbol ?? "",
          qty: l.quantity,
          side: l.action,
        };
      });
      const limit = Number(computed.netPremium.toFixed(2));
      const payload = {
        portfolio_id: portfolioId,
        strategy_type: strategy,
        legs: apiLegs,
        limit_price: limit,
        max_risk_dollars: computed.maxRisk ?? undefined,
      };
      const res = await api.submitOptionsOrder(payload);
      onSubmitted?.(
        `Order ${res.order_id || "(local)"} — ${STRATEGY_LABEL[strategy]} ${ticker}`
      );
      onClose();
    } catch (e: any) {
      setErr(e?.message || "Order submission failed");
    } finally {
      setSubmitting(false);
    }
  }

  if (!open) return null;

  const StrikePicker = ({
    label,
    value,
    onChange,
    rows,
  }: {
    label: string;
    value: number | null;
    onChange: (v: number) => void;
    rows: OptionsChainRow[];
  }) => (
    <label className="block text-[11px] text-gray-400">
      {label}
      <select
        className="mt-1 w-full bg-gray-900 border border-gray-700 rounded px-2 py-1 text-xs font-mono text-gray-200"
        value={value ?? ""}
        onChange={(e) => onChange(Number(e.target.value))}
      >
        <option value="">—</option>
        {rows.map((r) => (
          <option key={r.strike} value={r.strike}>
            ${r.strike} ({r.bid ?? "?"}/{r.ask ?? "?"})
          </option>
        ))}
      </select>
    </label>
  );

  return (
    <div className="fixed inset-0 z-50 bg-black/70 flex items-center justify-center p-4">
      <Card className="w-full max-w-3xl bg-gray-950 border-gray-800 max-h-[90vh] overflow-y-auto">
        <CardContent className="pt-5">
          <div className="flex items-baseline justify-between mb-3">
            <h2 className="text-base font-semibold text-gray-200">
              Options Trade Builder — {ticker}
            </h2>
            <span className="text-[10px] font-mono text-gray-500">
              Level {optionsLevel}
            </span>
          </div>

          {/* Strategy tabs */}
          <div className="flex flex-wrap gap-1.5 mb-4">
            {allowed.map((k) => (
              <button
                key={k}
                onClick={() => setStrategy(k)}
                className={`px-2.5 py-1 rounded text-[11px] border transition ${
                  strategy === k
                    ? "bg-ai-blue/20 text-ai-blue border-ai-blue/40"
                    : "text-gray-400 border-gray-700 hover:text-gray-200"
                }`}
              >
                {STRATEGY_LABEL[k]}
              </button>
            ))}
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {/* Inputs */}
            <div className="space-y-3">
              <label className="block text-[11px] text-gray-400">
                Expiration
                <select
                  className="mt-1 w-full bg-gray-900 border border-gray-700 rounded px-2 py-1 text-xs font-mono text-gray-200"
                  value={expiration}
                  onChange={(e) => setExpiration(e.target.value)}
                >
                  {expirations.map((e) => (
                    <option key={e} value={e}>{e}</option>
                  ))}
                </select>
              </label>

              {(strategy === "long_call" || strategy === "long_put") && (
                <StrikePicker
                  label="Strike"
                  value={longStrike}
                  onChange={setLongStrike}
                  rows={strategy === "long_call" ? calls : puts}
                />
              )}

              {strategy === "covered_call" && (
                <>
                  <StrikePicker
                    label="Strike (short call)"
                    value={shortStrike}
                    onChange={setShortStrike}
                    rows={calls}
                  />
                  <p className="text-[10px] text-gray-500" style={FONT_MONO}>
                    You hold {sharesHeld} shares → max {Math.floor(sharesHeld / 100)} contracts
                  </p>
                </>
              )}

              {(strategy === "bull_call_spread" || strategy === "bear_put_spread") && (
                <>
                  <StrikePicker
                    label={strategy === "bull_call_spread" ? "Long call strike" : "Long put strike"}
                    value={longStrike}
                    onChange={setLongStrike}
                    rows={strategy === "bull_call_spread" ? calls : puts}
                  />
                  <StrikePicker
                    label={strategy === "bull_call_spread" ? "Short call strike" : "Short put strike"}
                    value={shortStrike}
                    onChange={setShortStrike}
                    rows={strategy === "bull_call_spread" ? calls : puts}
                  />
                </>
              )}

              {strategy === "iron_condor" && (
                <>
                  <StrikePicker label="Short call" value={shortCallStrike} onChange={setShortCallStrike} rows={calls} />
                  <StrikePicker label="Long call" value={longCallStrike} onChange={setLongCallStrike} rows={calls} />
                  <StrikePicker label="Short put" value={shortPutStrike} onChange={setShortPutStrike} rows={puts} />
                  <StrikePicker label="Long put" value={longPutStrike} onChange={setLongPutStrike} rows={puts} />
                </>
              )}

              <label className="block text-[11px] text-gray-400">
                Quantity (contracts)
                <input
                  type="number"
                  min={1}
                  className="mt-1 w-full bg-gray-900 border border-gray-700 rounded px-2 py-1 text-xs font-mono text-gray-200"
                  value={qty}
                  onChange={(e) => setQty(Math.max(1, Number(e.target.value)))}
                />
              </label>
            </div>

            {/* Live panel */}
            <div className="space-y-2 text-[11px] font-mono text-gray-300">
              <div className="flex justify-between">
                <span className="text-gray-500">Net premium</span>
                <span>
                  {computed.netPremium >= 0 ? "-$" : "+$"}
                  {Math.abs(computed.netPremium * 100).toFixed(0)}
                  {computed.netPremium >= 0 ? " debit" : " credit"}
                </span>
              </div>
              <div className="flex justify-between">
                <span className="text-gray-500">Max risk</span>
                <span>{computed.maxRisk != null ? `$${computed.maxRisk.toFixed(0)}` : "—"}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-gray-500">Max reward</span>
                <span>{computed.maxReward != null ? `$${computed.maxReward.toFixed(0)}` : "∞"}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-gray-500">Breakeven</span>
                <span>{computed.breakevens.map((b) => `$${b.toFixed(2)}`).join(" / ") || "—"}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-gray-500">Δ / Θ / V</span>
                <span>
                  {computed.gSet.delta ? computed.greeks.delta.toFixed(2) : "—"} /{" "}
                  {computed.gSet.theta ? computed.greeks.theta.toFixed(2) : "—"} /{" "}
                  {computed.gSet.vega ? computed.greeks.vega.toFixed(2) : "—"}
                </span>
              </div>
              {computed.legs.length > 0 && spotPrice != null && (
                <div className="pt-2 border-t border-gray-800">
                  <PayoffDiagram
                    legs={computed.legs}
                    spotPrice={spotPrice}
                    height={180}
                  />
                </div>
              )}
            </div>
          </div>

          {/* Warnings + blocks */}
          {warnings.length > 0 && (
            <div className="mt-3 space-y-1">
              {warnings.map((w, i) => (
                <div key={i} className="text-[10px] text-yellow-400">⚠ {w}</div>
              ))}
            </div>
          )}
          {blocks.length > 0 && (
            <div className="mt-3 space-y-1">
              {blocks.map((b, i) => (
                <div key={i} className="text-[10px] text-loss">✖ {b}</div>
              ))}
            </div>
          )}
          {err && <div className="mt-3 text-[11px] text-loss">{err}</div>}

          {/* Actions */}
          <div className="flex gap-2 mt-5 justify-end">
            <Button variant="outline" onClick={onClose} disabled={submitting}>
              Cancel
            </Button>
            <Button
              onClick={submit}
              disabled={submitting || blocks.length > 0 || computed.legs.length === 0}
            >
              {submitting ? "Submitting…" : "Submit order"}
            </Button>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}

export default TradeBuilder;
