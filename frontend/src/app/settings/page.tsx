"use client";

import { useState, useEffect, useCallback } from "react";
import { api } from "@/lib/api";
import { formatCurrency, formatTimeAgo, formatDate } from "@/lib/formatters";
import type { PortfolioSettings, TraderSettings, AllowlistedKey, AlpacaConnectionTest } from "@/lib/types";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import { Separator } from "@/components/ui/separator";
import { Skeleton } from "@/components/ui/skeleton";

const FONT_OUTFIT = { fontFamily: "'Outfit', sans-serif" } as const;
const FONT_MONO = { fontFamily: "'JetBrains Mono', monospace" } as const;

function FontLoader() {
  return (
    // eslint-disable-next-line @next/next/no-head-element
    <head>
      <link rel="preconnect" href="https://fonts.googleapis.com" />
      <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="" />
      <link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;600&family=Outfit:wght@400;500;600;700&display=swap" rel="stylesheet" />
    </head>
  );
}

function Toast({ message, type }: { message: string; type: "success" | "error" }) {
  const colors = type === "success" ? "bg-profit/15 text-profit border-profit/30" : "bg-loss/15 text-loss border-loss/30";
  return (
    <div className="fixed bottom-6 right-6 z-50 animate-fade-in" style={FONT_OUTFIT}>
      <div className={`px-5 py-3 rounded-lg text-sm font-medium shadow-2xl backdrop-blur-md border ${colors}`}>{message}</div>
    </div>
  );
}

function DirectionControl({ value, onChange, disabled }: { value: string | null; onChange: (v: string | null) => void; disabled?: boolean }) {
  const opts: [string, string | null][] = [["All", null], ["Long", "long"], ["Short", "short"]];
  return (
    <div className={`inline-flex rounded-md border border-border overflow-hidden ${disabled ? "opacity-30 pointer-events-none" : ""}`}>
      {opts.map(([label, val]) => (
        <button key={label} type="button" onClick={() => onChange(val)}
          className={`px-3 py-1 text-[11px] font-mono font-medium transition-all ${value === val ? "bg-primary text-white" : "bg-surface-light/40 text-gray-500 hover:text-gray-300"}`}
        >{label}</button>
      ))}
    </div>
  );
}

function RangeField({ label, value, onChange, min, max, step, suffix }: {
  label: string; value: number; onChange: (n: number) => void; min: number; max: number; step: number; suffix: string;
}) {
  return (
    <div>
      <div className="flex items-center justify-between mb-2">
        <span className="text-xs text-gray-400 font-medium" style={FONT_OUTFIT}>{label}</span>
        <span className="text-sm font-mono text-white tabular-nums">{value}{suffix}</span>
      </div>
      <input type="range" min={min} max={max} step={step} value={value}
        onChange={(e) => onChange(parseFloat(e.target.value))}
        className="w-full h-1.5 rounded-full appearance-none cursor-pointer bg-surface-light accent-primary" />
    </div>
  );
}

function SectionTitle({ children }: { children: React.ReactNode }) {
  return <h3 className="text-sm font-semibold text-white mb-2" style={FONT_OUTFIT}>{children}</h3>;
}

function EmptyPanel({ icon, text }: { icon: React.ReactNode; text: string }) {
  return (
    <div className="rounded-xl border border-[#374151]/50 bg-[#1f2937]/20 p-12 flex flex-col items-center justify-center text-center min-h-[400px]">
      <div className="w-16 h-16 rounded-2xl bg-[#1f2937]/50 flex items-center justify-center mb-4">{icon}</div>
      <p className="text-gray-500 text-sm">{text}</p>
    </div>
  );
}

function RevealedKeyBox({ apiKey, onCopy }: { apiKey: string; onCopy: (s: string) => void }) {
  return (
    <div className="p-4 rounded-lg border border-screener-amber/40 bg-screener-amber/5">
      <div className="flex items-center gap-2 mb-2">
        <svg className="w-4 h-4 text-screener-amber" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
          <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v3.75m-9.303 3.376c-.866 1.5.217 3.374 1.948 3.374h14.71c1.73 0 2.813-1.874 1.948-3.374L13.949 3.378c-.866-1.5-3.032-1.5-3.898 0L2.697 16.126zM12 15.75h.007v.008H12v-.008z" />
        </svg>
        <span className="text-xs text-screener-amber font-semibold uppercase tracking-wider">Copy now -- will not be shown again</span>
      </div>
      <div className="flex items-center gap-2">
        <code className="flex-1 text-sm bg-black/40 px-3 py-2 rounded border border-screener-amber/20 text-white select-all break-all" style={FONT_MONO}>{apiKey}</code>
        <Button size="sm" variant="secondary" onClick={() => onCopy(apiKey)}>Copy</Button>
      </div>
    </div>
  );
}

/* ================================================================= */
export default function SettingsPage() {
  const [tab, setTab] = useState("portfolios");
  const [portfolios, setPortfolios] = useState<PortfolioSettings[]>([]);
  const [portfoliosLoading, setPortfoliosLoading] = useState(true);
  const [selectedPortfolio, setSelectedPortfolio] = useState<string | null>(null);
  const [isCreatingPf, setIsCreatingPf] = useState(false);
  const [pfName, setPfName] = useState(""); const [pfDesc, setPfDesc] = useState("");
  const [pfCapital, setPfCapital] = useState(10000);
  const [pfMaxPct, setPfMaxPct] = useState(25); const [pfMaxPos, setPfMaxPos] = useState(10); const [pfMaxDD, setPfMaxDD] = useState(20);
  const [pfStrats, setPfStrats] = useState<Record<string, { assigned: boolean; direction: string | null }>>({});

  // Execution state
  const [execMode, setExecMode] = useState<"local" | "paper" | "live">("local");
  const [alpacaApiKey, setAlpacaApiKey] = useState("");
  const [alpacaSecretKey, setAlpacaSecretKey] = useState("");
  const [maxOrderAmount, setMaxOrderAmount] = useState(1000);
  const [liveConfirmText, setLiveConfirmText] = useState("");
  const [liveConfirmed, setLiveConfirmed] = useState(false);
  const [connectionTest, setConnectionTest] = useState<AlpacaConnectionTest | null>(null);
  const [testingConnection, setTestingConnection] = useState(false);
  const [savingCreds, setSavingCreds] = useState(false);
  const [aiEvalEnabled, setAiEvalEnabled] = useState(false);

  const [traders, setTraders] = useState<TraderSettings[]>([]);
  const [tradersLoading, setTradersLoading] = useState(true);
  const [keys, setKeys] = useState<AllowlistedKey[]>([]);
  const [selectedTrader, setSelectedTrader] = useState<string | null>(null);
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const [trName, setTrName] = useState(""); const [trDesc, setTrDesc] = useState("");
  const [revealedKey, setRevealedKey] = useState<string | null>(null);
  const [toast, setToast] = useState<{ message: string; type: "success" | "error" } | null>(null);
  const [saving, setSaving] = useState(false);

  const flash = useCallback((msg: string, type: "success" | "error" = "success") => {
    setToast({ message: msg, type }); setTimeout(() => setToast(null), 3000);
  }, []);

  const fetchPortfolios = useCallback(async () => {
    try { setPortfolios(await api.getSettingsPortfolios()); }
    catch { flash("Failed to load portfolios", "error"); }
    finally { setPortfoliosLoading(false); }
  }, [flash]);

  const fetchTraders = useCallback(async () => {
    try { const [t, k] = await Promise.all([api.getSettingsTraders(), api.getKeys()]); setTraders(t); setKeys(k); }
    catch { flash("Failed to load strategies", "error"); }
    finally { setTradersLoading(false); }
  }, [flash]);

  useEffect(() => { fetchPortfolios(); fetchTraders(); }, [fetchPortfolios, fetchTraders]);

  // Populate portfolio form on selection
  useEffect(() => {
    if (isCreatingPf) {
      setPfName(""); setPfDesc(""); setPfCapital(10000); setPfMaxPct(25); setPfMaxPos(10); setPfMaxDD(20);
      setExecMode("local"); setAlpacaApiKey(""); setAlpacaSecretKey(""); setMaxOrderAmount(1000);
      setLiveConfirmText(""); setLiveConfirmed(false); setConnectionTest(null);
      const init: Record<string, { assigned: boolean; direction: string | null }> = {};
      traders.forEach((t) => { init[t.id] = { assigned: false, direction: null }; });
      setPfStrats(init); return;
    }
    const pf = portfolios.find((p) => p.id === selectedPortfolio);
    if (!pf) return;
    setPfName(pf.name); setPfDesc(pf.description || ""); setPfCapital(pf.initial_capital);
    setPfMaxPct(pf.max_pct_per_trade ?? 25); setPfMaxPos(pf.max_open_positions ?? 10); setPfMaxDD(pf.max_drawdown_pct ?? 20);
    setExecMode((pf.execution_mode as "local" | "paper" | "live") || "local");
    setAiEvalEnabled((pf as unknown as Record<string, unknown>).ai_evaluation_enabled as boolean || false);
    setMaxOrderAmount(pf.max_order_amount ?? 1000);
    setAlpacaApiKey(""); setAlpacaSecretKey(""); setLiveConfirmText(""); setLiveConfirmed(false); setConnectionTest(null);
    const s: Record<string, { assigned: boolean; direction: string | null }> = {};
    traders.forEach((t) => {
      const m = pf.strategies.find((st) => st.trader_id === t.id);
      s[t.id] = m ? { assigned: true, direction: m.direction_filter } : { assigned: false, direction: null };
    });
    setPfStrats(s);
  }, [selectedPortfolio, isCreatingPf, portfolios, traders]);

  // Populate trader form on selection
  useEffect(() => {
    const tr = traders.find((t) => t.trader_id === selectedTrader);
    if (!tr) { setTrName(""); setTrDesc(""); return; }
    setTrName(tr.display_name || ""); setTrDesc(tr.description || ""); setRevealedKey(null);
  }, [selectedTrader, traders]);

  // ── Handlers
  const handleSavePf = async () => {
    if (!pfName.trim()) { flash("Portfolio name is required", "error"); return; }
    setSaving(true);
    try {
      const stratList = Object.entries(pfStrats).filter(([, v]) => v.assigned).map(([tid, v]) => ({ trader_id: tid, direction_filter: v.direction }));
      if (isCreatingPf) {
        const c = await api.createPortfolio({ name: pfName, description: pfDesc || undefined, initial_capital: pfCapital, max_pct_per_trade: pfMaxPct, max_open_positions: pfMaxPos, max_drawdown_pct: pfMaxDD });
        if (stratList.length) await api.updatePortfolio(c.id, { strategies: stratList });
        setIsCreatingPf(false); setSelectedPortfolio(c.id); flash("Portfolio created");
      } else if (selectedPortfolio) {
        await api.updatePortfolio(selectedPortfolio, { portfolio: { name: pfName, description: pfDesc, max_pct_per_trade: pfMaxPct, max_open_positions: pfMaxPos, max_drawdown_pct: pfMaxDD }, strategies: stratList });
        flash("Portfolio updated");
      }
      await fetchPortfolios();
    } catch { flash("Save failed", "error"); }
    finally { setSaving(false); }
  };

  const handleArchivePf = async () => {
    if (!selectedPortfolio) return; setSaving(true);
    try { await api.archivePortfolio(selectedPortfolio); setSelectedPortfolio(null); flash("Portfolio archived"); await fetchPortfolios(); }
    catch { flash("Archive failed", "error"); } finally { setSaving(false); }
  };

  const handleDeletePf = async () => {
    if (!selectedPortfolio) return;
    if (!confirm("Permanently delete this portfolio and ALL its data (trades, snapshots, holdings)? This cannot be undone.")) return;
    setSaving(true);
    try { await api.deletePortfolio(selectedPortfolio); setSelectedPortfolio(null); flash("Portfolio deleted"); await fetchPortfolios(); }
    catch { flash("Delete failed", "error"); } finally { setSaving(false); }
  };

  const handleDeleteTrader = async () => {
    if (!selectedTrader) return;
    if (!confirm("Permanently delete this strategy and ALL its trades? This cannot be undone.")) return;
    setSaving(true);
    try { await api.deleteTrader(selectedTrader); setSelectedTrader(null); flash("Strategy deleted"); await fetchTraders(); }
    catch { flash("Delete failed", "error"); } finally { setSaving(false); }
  };

  const handleSaveTrader = async () => {
    if (!selectedTrader) return; setSaving(true);
    try { await api.updateTrader(selectedTrader, { display_name: trName, description: trDesc }); flash("Strategy updated"); await fetchTraders(); }
    catch { flash("Save failed", "error"); } finally { setSaving(false); }
  };

  const handleRotateKey = async () => {
    if (!selectedTrader) return; setSaving(true);
    try { const r = await api.rotateTraderKey(selectedTrader); setRevealedKey(r.api_key); flash("Key rotated - copy it now"); }
    catch { flash("Rotate failed", "error"); } finally { setSaving(false); }
  };

  const handleGenKey = async () => {
    setSaving(true);
    try { const r = await api.generateKey(); setRevealedKey(r.api_key); setSelectedKey(r.id); setSelectedTrader(null); flash("Key generated - copy it now"); await fetchTraders(); }
    catch { flash("Generate failed", "error"); } finally { setSaving(false); }
  };

  const handleRevokeKey = async (id: string) => {
    setSaving(true);
    try { await api.revokeKey(id); setSelectedKey(null); flash("Key revoked"); await fetchTraders(); }
    catch { flash("Revoke failed", "error"); } finally { setSaving(false); }
  };

  const handleTestConnection = async () => {
    if (!selectedPortfolio) return;
    setTestingConnection(true); setConnectionTest(null);
    try {
      const result = await api.testAlpacaConnection(selectedPortfolio);
      setConnectionTest(result);
      if (result.status === "connected") flash("Connected to Alpaca");
      else flash(result.message || "Connection failed", "error");
    } catch { flash("Connection test failed", "error"); }
    finally { setTestingConnection(false); }
  };

  const handleSaveCreds = async () => {
    if (!selectedPortfolio) return;
    setSavingCreds(true);
    try {
      const data: Record<string, unknown> = { execution_mode: execMode, max_order_amount: maxOrderAmount, ai_evaluation_enabled: aiEvalEnabled };
      if (alpacaApiKey) data.alpaca_api_key = alpacaApiKey;
      if (alpacaSecretKey) data.alpaca_secret_key = alpacaSecretKey;
      await api.updatePortfolio(selectedPortfolio, { portfolio: data });
      flash("Execution settings saved");
      await fetchPortfolios();
    } catch { flash("Save failed", "error"); }
    finally { setSavingCreds(false); }
  };

  const handleKillSwitch = async () => {
    setSaving(true);
    try {
      const r = await api.killSwitch();
      flash(`Kill switch activated - ${r.portfolios_affected} portfolio(s) set to Local`);
      await fetchPortfolios();
    } catch { flash("Kill switch failed", "error"); }
    finally { setSaving(false); }
  };

  const copyClip = (t: string) => { navigator.clipboard.writeText(t); flash("Copied to clipboard"); };

  const activePfs = portfolios.filter((p) => p.status === "active");
  const archivedPfs = portfolios.filter((p) => p.status === "archived");
  const unclaimedKeys = keys.filter((k) => !k.claimed_by_id);
  const editing = isCreatingPf || selectedPortfolio !== null;
  const curPf = portfolios.find((p) => p.id === selectedPortfolio);
  const curTrader = traders.find((t) => t.trader_id === selectedTrader);
  const curKey = keys.find((k) => k.id === selectedKey);

  const folderIcon = <svg className="w-8 h-8 text-gray-600" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}><path strokeLinecap="round" strokeLinejoin="round" d="M2.25 12.75V12A2.25 2.25 0 014.5 9.75h15A2.25 2.25 0 0121.75 12v.75m-8.69-6.44l-2.12-2.12a1.5 1.5 0 00-1.061-.44H4.5A2.25 2.25 0 002.25 6v12a2.25 2.25 0 002.25 2.25h15A2.25 2.25 0 0021.75 18V9a2.25 2.25 0 00-2.25-2.25h-5.379a1.5 1.5 0 01-1.06-.44z" /></svg>;
  const userIcon = <svg className="w-8 h-8 text-gray-600" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}><path strokeLinecap="round" strokeLinejoin="round" d="M17.982 18.725A7.488 7.488 0 0012 15.75a7.488 7.488 0 00-5.982 2.975m11.963 0a9 9 0 10-11.963 0m11.963 0A8.966 8.966 0 0112 21a8.966 8.966 0 01-5.982-2.275M15 9.75a3 3 0 11-6 0 3 3 0 016 0z" /></svg>;

  return (
    <>
      <FontLoader />
      <div className="max-w-5xl mx-auto" style={FONT_OUTFIT}>
        {/* Page header */}
        {/* Page header */}
        <div className="mb-8">
          <h1 className="text-3xl font-bold text-white" style={FONT_OUTFIT}>Settings</h1>
          <p className="text-sm text-gray-500 mt-1">Manage portfolios, strategies, scanner, and API configuration</p>
        </div>

        <Tabs value={tab} onValueChange={setTab}>
          {/* Pill tab bar */}
          <div className="flex gap-2 mb-6 overflow-x-auto pb-1">
            {[
              { value: "portfolios", label: "Portfolios" },
              { value: "strategies", label: "Strategies" },
              { value: "backtests", label: "Backtests" },
              { value: "henry", label: "Henry AI" },
              { value: "scanner", label: "Scanner" },
            ].map((t) => (
              <button
                key={t.value}
                onClick={() => setTab(t.value)}
                className={`px-4 py-2 rounded-lg text-sm font-medium transition whitespace-nowrap ${
                  tab === t.value
                    ? "bg-[#6366f1]/15 text-[#6366f1] border border-[#6366f1]/30 shadow-sm shadow-[#6366f1]/10"
                    : "text-gray-400 hover:text-white hover:bg-[#1f2937]/40"
                }`}
                style={FONT_OUTFIT}
              >
                {t.label}
              </button>
            ))}
          </div>

          {/* ═══ PORTFOLIOS TAB ═══ */}
          <TabsContent value="portfolios">
            <div className="grid grid-cols-1 lg:grid-cols-3 gap-6 mt-4">
              {/* Left: list */}
              <div className="lg:col-span-1 space-y-3 opacity-0 animate-fade-in" style={{ animationDelay: "160ms" }}>
                <Button variant="outline" size="sm" className="w-full border-dashed border-primary/40 text-primary hover:bg-primary/10"
                  onClick={() => { setIsCreatingPf(true); setSelectedPortfolio(null); }}>
                  + New Portfolio
                </Button>
                {portfoliosLoading ? [0,1,2].map(i => <Skeleton key={i} className="h-20 rounded-xl" style={{ animationDelay: `${i*80}ms` }} />) : (
                  <>
                    {activePfs.map((pf, i) => (
                      <button key={pf.id} onClick={() => { setSelectedPortfolio(pf.id); setIsCreatingPf(false); }}
                        className={`w-full text-left rounded-xl border border-[#374151]/50 bg-[#1f2937]/20 p-4 transition-all opacity-0 animate-fade-in hover:border-primary/40 ${selectedPortfolio === pf.id ? "border-primary/60 bg-primary/5" : ""}`}
                        style={{ animationDelay: `${200 + i * 60}ms` }}>
                        <div className="flex items-start justify-between mb-2">
                          <span className="font-semibold text-sm text-white">{pf.name}</span>
                          <div className="flex items-center gap-1.5">
                            {pf.execution_mode === "paper" && <Badge className="bg-screener-amber/15 text-screener-amber text-[10px]">PAPER</Badge>}
                            {pf.execution_mode === "live" && <Badge className="bg-loss/15 text-loss text-[10px]">LIVE</Badge>}
                            <Badge className="bg-profit/15 text-profit text-[10px]">active</Badge>
                          </div>
                        </div>
                        <div className="flex items-center gap-4 text-xs text-gray-500">
                          <span className="font-mono">{formatCurrency(pf.cash)}</span>
                          <span>{pf.strategies.length} strat{pf.strategies.length !== 1 ? "s" : ""}</span>
                        </div>
                      </button>
                    ))}
                    {archivedPfs.length > 0 && (
                      <div className="pt-2">
                        <span className="text-[10px] uppercase tracking-widest text-gray-600 font-mono">Archived</span>
                        {archivedPfs.map((pf) => (
                          <button key={pf.id} onClick={() => { setSelectedPortfolio(pf.id); setIsCreatingPf(false); }}
                            className={`w-full text-left rounded-xl border border-[#374151]/50 bg-[#1f2937]/20 p-4 mt-2 opacity-50 hover:opacity-70 transition-all ${selectedPortfolio === pf.id ? "border-gray-500/40" : ""}`}>
                            <div className="flex items-start justify-between">
                              <span className="text-sm text-gray-400">{pf.name}</span>
                              <Badge variant="closed" className="text-[10px]">archived</Badge>
                            </div>
                          </button>
                        ))}
                      </div>
                    )}
                  </>
                )}
              </div>

              {/* Right: editor */}
              <div className="lg:col-span-2 opacity-0 animate-fade-in" style={{ animationDelay: "240ms" }}>
                {!editing ? <EmptyPanel icon={folderIcon} text="Select a portfolio to edit or create a new one" /> : (
                  <div className="space-y-5">
                    {/* General */}
                    <Card><CardContent className="space-y-4">
                      <SectionTitle>General</SectionTitle>
                      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                        <div><label className="text-xs text-gray-400 mb-1.5 block">Name</label>
                          <Input value={pfName} onChange={(e) => setPfName(e.target.value)} placeholder="Portfolio name" /></div>
                        <div><label className="text-xs text-gray-400 mb-1.5 block">Initial Capital</label>
                          <Input type="number" value={pfCapital} onChange={(e) => setPfCapital(parseFloat(e.target.value) || 0)} disabled={!isCreatingPf} className="font-mono" /></div>
                      </div>
                      <div><label className="text-xs text-gray-400 mb-1.5 block">Description</label>
                        <Input value={pfDesc} onChange={(e) => setPfDesc(e.target.value)} placeholder="Optional description..." /></div>
                    </CardContent></Card>

                    {/* Risk & Sizing */}
                    <Card><CardContent className="space-y-5">
                      <SectionTitle>Risk &amp; Sizing</SectionTitle>
                      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                        <RangeField label="Max % Per Ticker" value={pfMaxPct} onChange={setPfMaxPct} min={5} max={100} step={5} suffix="%" />
                        <RangeField label="Max Drawdown %" value={pfMaxDD} onChange={setPfMaxDD} min={1} max={50} step={1} suffix="%" />
                      </div>
                      <div className="max-w-[200px]"><label className="text-xs text-gray-400 mb-1.5 block">Max Open Positions</label>
                        <Input type="number" value={pfMaxPos} onChange={(e) => setPfMaxPos(parseInt(e.target.value) || 1)} min={1} max={100} className="font-mono" /></div>
                    </CardContent></Card>

                    {/* Henry AI Evaluation */}
                    {!isCreatingPf && (
                      <Card><CardContent className="space-y-3">
                        <SectionTitle>Henry AI Evaluation</SectionTitle>
                        <p className="text-[10px] text-gray-500">
                          When enabled, Henry evaluates every webhook signal before it&apos;s added to this portfolio.
                          He decides BUY or SKIP based on signal quality, portfolio state, and his analysis.
                        </p>
                        <div className="flex items-center gap-3">
                          <button
                            onClick={() => setAiEvalEnabled(!aiEvalEnabled)}
                            className={`w-10 h-5 rounded-full transition ${aiEvalEnabled ? "bg-[#6366f1]" : "bg-gray-600"}`}
                          >
                            <div className={`rounded-full bg-white transition-transform ${aiEvalEnabled ? "translate-x-5" : "translate-x-0.5"}`} style={{ width: 18, height: 18 }} />
                          </button>
                          <span className={`text-xs font-medium ${aiEvalEnabled ? "text-[#6366f1]" : "text-gray-500"}`}>
                            {aiEvalEnabled ? "Henry evaluates signals" : "Signals added directly"}
                          </span>
                        </div>
                        {aiEvalEnabled && (
                          <div className="p-3 rounded-lg bg-[#6366f1]/5 border border-[#6366f1]/20 text-[10px] text-gray-400">
                            <strong className="text-[#6366f1]">How it works:</strong> Webhook signals go through Henry first.
                            BUY (conf ≥5) → sized to cash and added. SKIP → logged, not executed.
                            See the Activity tab for decisions.
                          </div>
                        )}
                        <Button size="sm" onClick={handleSaveCreds} disabled={savingCreds}>
                          {savingCreds ? "Saving..." : "Save Settings"}
                        </Button>
                      </CardContent></Card>
                    )}

                    {/* Strategy Assignments */}
                    <Card><CardContent className="space-y-4">
                      <SectionTitle>Strategy Assignments</SectionTitle>
                      {traders.length === 0 ? <p className="text-xs text-gray-500">No strategies available. Generate an API key first.</p> : (
                        <div className="space-y-2">
                          {traders.map((tr) => {
                            const s = pfStrats[tr.id]; const on = s?.assigned ?? false;
                            return (
                              <div key={tr.id} className={`flex items-center justify-between p-3 rounded-lg border transition-all ${on ? "border-primary/30 bg-primary/5" : "border-border/50 opacity-40 hover:opacity-60"}`}>
                                <div className="flex items-center gap-3">
                                  <input type="checkbox" checked={on} onChange={(e) => setPfStrats((p) => ({ ...p, [tr.id]: { ...p[tr.id], assigned: e.target.checked } }))}
                                    className="w-4 h-4 rounded border-gray-600 accent-primary bg-surface" />
                                  <div>
                                    <span className="text-sm text-white font-medium">{tr.display_name || "Unnamed Strategy"}</span>
                                    <span className="text-[11px] text-gray-500 font-mono ml-2">{tr.trader_id}</span>
                                  </div>
                                </div>
                                <DirectionControl value={s?.direction ?? null} onChange={(d) => setPfStrats((p) => ({ ...p, [tr.id]: { ...p[tr.id], direction: d } }))} disabled={!on} />
                              </div>
                            );
                          })}
                        </div>
                      )}
                    </CardContent></Card>

                    {/* Trading Execution */}
                    {!isCreatingPf && (
                      <Card><CardContent className="space-y-4">
                        <SectionTitle>Trading Execution</SectionTitle>
                        <div className="flex items-center gap-2">
                          <span className="text-xs text-gray-400 font-medium min-w-[40px]">Mode</span>
                          <div className="inline-flex rounded-md border border-border overflow-hidden">
                            {(["local", "paper", "live"] as const).map((m) => (
                              <button key={m} type="button"
                                onClick={() => { setExecMode(m); if (m !== "live") { setLiveConfirmText(""); setLiveConfirmed(false); } }}
                                className={`px-4 py-1.5 text-[11px] font-mono font-medium transition-all uppercase ${
                                  execMode === m
                                    ? m === "live" ? "bg-loss/20 text-loss" : m === "paper" ? "bg-screener-amber/20 text-screener-amber" : "bg-primary/20 text-primary"
                                    : "bg-surface-light/40 text-gray-500 hover:text-gray-300"
                                }`}
                              >{m}</button>
                            ))}
                          </div>
                          {curPf?.has_alpaca_credentials && execMode !== "local" && (
                            <Badge className="bg-profit/15 text-profit text-[10px] ml-2">Keys configured</Badge>
                          )}
                        </div>

                        {execMode !== "local" && (
                          <div className="space-y-3 pt-1">
                            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                              <div>
                                <label className="text-xs text-gray-400 mb-1.5 block">Alpaca API Key</label>
                                <Input type="password" value={alpacaApiKey} onChange={(e) => setAlpacaApiKey(e.target.value)}
                                  placeholder={curPf?.alpaca_key_preview || "Enter API key..."} className="font-mono text-sm" />
                              </div>
                              <div>
                                <label className="text-xs text-gray-400 mb-1.5 block">Alpaca Secret Key</label>
                                <Input type="password" value={alpacaSecretKey} onChange={(e) => setAlpacaSecretKey(e.target.value)}
                                  placeholder="Enter secret key..." className="font-mono text-sm" />
                              </div>
                            </div>
                            <div className="max-w-[200px]">
                              <label className="text-xs text-gray-400 mb-1.5 block">Max Order $ (per trade)</label>
                              <Input type="number" value={maxOrderAmount} onChange={(e) => setMaxOrderAmount(parseFloat(e.target.value) || 0)}
                                min={0} step={100} className="font-mono" />
                            </div>
                            <div className="flex items-center gap-3">
                              <Button variant="outline" size="sm" onClick={handleTestConnection} disabled={testingConnection}>
                                {testingConnection ? "Testing..." : "Test Connection"}
                              </Button>
                              <Button size="sm" onClick={handleSaveCreds} disabled={savingCreds}>
                                {savingCreds ? "Saving..." : "Save Credentials"}
                              </Button>
                            </div>
                            {connectionTest && connectionTest.status === "connected" && (
                              <div className="p-3 rounded-lg border border-profit/30 bg-profit/5 text-sm space-y-1">
                                <div className="text-profit font-medium text-xs">Connected to Alpaca {connectionTest.paper ? "(Paper)" : "(Live)"}</div>
                                <div className="grid grid-cols-2 gap-2 text-xs text-gray-300" style={FONT_MONO}>
                                  <span>Equity: ${connectionTest.equity?.toLocaleString()}</span>
                                  <span>Buying Power: ${connectionTest.buying_power?.toLocaleString()}</span>
                                  <span>Cash: ${connectionTest.cash?.toLocaleString()}</span>
                                  <span>Portfolio: ${connectionTest.portfolio_value?.toLocaleString()}</span>
                                </div>
                              </div>
                            )}
                            {connectionTest && connectionTest.status === "error" && (
                              <div className="p-3 rounded-lg border border-loss/30 bg-loss/5 text-xs text-loss">{connectionTest.message}</div>
                            )}
                          </div>
                        )}

                        {execMode === "live" && !liveConfirmed && (
                          <div className="border border-loss/50 bg-loss/5 p-4 rounded-lg space-y-3">
                            <div className="flex items-center gap-2 text-loss text-sm font-semibold">
                              <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                                <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v3.75m-9.303 3.376c-.866 1.5.217 3.374 1.948 3.374h14.71c1.73 0 2.813-1.874 1.948-3.374L13.949 3.378c-.866-1.5-3.032-1.5-3.898 0L2.697 16.126zM12 15.75h.007v.008H12v-.008z" />
                              </svg>
                              LIVE TRADING -- Real money at risk
                            </div>
                            <div className="text-xs text-gray-400">Type &quot;CONFIRM&quot; to enable live trading for this portfolio.</div>
                            <div className="flex items-center gap-3">
                              <Input value={liveConfirmText} onChange={(e) => { setLiveConfirmText(e.target.value); setLiveConfirmed(e.target.value === "CONFIRM"); }}
                                placeholder='Type "CONFIRM"' className="max-w-[200px] text-sm" />
                              <Button size="sm" disabled={!liveConfirmed} onClick={handleSaveCreds}
                                className="bg-loss/20 text-loss border-loss/30 hover:bg-loss/30">
                                Enable Live Trading
                              </Button>
                            </div>
                          </div>
                        )}
                      </CardContent></Card>
                    )}

                    <div className="flex items-center gap-3 pt-1">
                      <Button onClick={handleSavePf} disabled={saving} className="min-w-[120px]">{saving ? "Saving..." : isCreatingPf ? "Create Portfolio" : "Save Changes"}</Button>
                      {!isCreatingPf && curPf?.status === "active" && <Button variant="destructive" size="sm" onClick={handleArchivePf} disabled={saving}>Archive</Button>}
                      {!isCreatingPf && <Button variant="destructive" size="sm" onClick={handleDeletePf} disabled={saving} className="bg-loss/20 text-loss border-loss/30 hover:bg-loss/30">Delete</Button>}
                      <Button variant="ghost" size="sm" onClick={() => { setSelectedPortfolio(null); setIsCreatingPf(false); }}>Cancel</Button>
                    </div>
                  </div>
                )}
              </div>
            </div>
            {/* Kill Switch */}
            <Card className="mt-6 border-loss/20">
              <CardContent className="flex items-center justify-between gap-4">
                <div>
                  <div className="flex items-center gap-2 mb-1">
                    <svg className="w-5 h-5 text-loss" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                      <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v3.75m-9.303 3.376c-.866 1.5.217 3.374 1.948 3.374h14.71c1.73 0 2.813-1.874 1.948-3.374L13.949 3.378c-.866-1.5-3.032-1.5-3.898 0L2.697 16.126zM12 15.75h.007v.008H12v-.008z" />
                    </svg>
                    <span className="text-sm font-semibold text-loss">Emergency Kill Switch</span>
                  </div>
                  <p className="text-xs text-gray-500">Immediately disables all Alpaca execution across ALL portfolios. Sets every portfolio to &quot;Local&quot; mode.</p>
                </div>
                <Button variant="destructive" size="sm" onClick={handleKillSwitch} disabled={saving}
                  className="bg-loss text-white hover:bg-loss/80 whitespace-nowrap min-w-[180px]">
                  DISABLE ALL TRADING
                </Button>
              </CardContent>
            </Card>
          </TabsContent>

          {/* ═══ STRATEGIES TAB ═══ */}
          <TabsContent value="strategies">
            <div className="grid grid-cols-1 lg:grid-cols-3 gap-6 mt-4">
              {/* Left: list */}
              <div className="lg:col-span-1 space-y-3 opacity-0 animate-fade-in" style={{ animationDelay: "160ms" }}>
                <Button variant="outline" size="sm" className="w-full border-dashed border-primary/40 text-primary hover:bg-primary/10"
                  onClick={handleGenKey} disabled={saving}>
                  + Generate Key
                </Button>
                {tradersLoading ? [0,1,2].map(i => <Skeleton key={i} className="h-20 rounded-xl" style={{ animationDelay: `${i*80}ms` }} />) : (
                  <>
                    {traders.map((tr, i) => (
                      <button key={tr.trader_id} onClick={() => { setSelectedTrader(tr.trader_id); setSelectedKey(null); }}
                        className={`w-full text-left rounded-xl border border-[#374151]/50 bg-[#1f2937]/20 p-4 transition-all opacity-0 animate-fade-in hover:border-primary/40 ${selectedTrader === tr.trader_id ? "border-primary/60 bg-primary/5" : ""}`}
                        style={{ animationDelay: `${200 + i * 60}ms` }}>
                        <div className="flex items-start justify-between mb-1.5">
                          <span className="font-semibold text-sm text-white">{tr.display_name || "Unnamed Strategy"}</span>
                          {tr.portfolio_count > 0 && <Badge variant="default" className="text-[10px]">{tr.portfolio_count} pf</Badge>}
                        </div>
                        <div className="text-[11px] text-gray-500 mb-1.5 truncate" style={FONT_MONO}>ID: {tr.trader_id}</div>
                        <span className="text-[11px] text-gray-600">{tr.trade_count} trades</span>
                      </button>
                    ))}
                    {unclaimedKeys.length > 0 && (
                      <div className="pt-2">
                        <span className="text-[10px] uppercase tracking-widest text-gray-600 font-mono">Unclaimed Keys</span>
                        {unclaimedKeys.map((k) => (
                          <button key={k.id} onClick={() => { setSelectedKey(k.id); setSelectedTrader(null); }}
                            className={`w-full text-left p-4 mt-2 rounded-xl border border-dashed transition-all hover:border-primary/40 ${selectedKey === k.id ? "border-primary/50 bg-primary/5" : "border-gray-700 bg-surface/50"}`}>
                            <div className="flex items-center justify-between">
                              <span className="text-sm text-gray-400">{k.label || "Unlabeled Key"}</span>
                              <Badge variant="outline" className="text-[10px] text-gray-500">pending</Badge>
                            </div>
                            <div className="text-[11px] text-gray-600 mt-1" style={FONT_MONO}>{k.id.slice(0, 12)}...</div>
                          </button>
                        ))}
                      </div>
                    )}
                  </>
                )}
              </div>

              {/* Right: detail */}
              <div className="lg:col-span-2 opacity-0 animate-fade-in" style={{ animationDelay: "240ms" }}>
                {!selectedTrader && !selectedKey ? <EmptyPanel icon={userIcon} text="Select a strategy or generate a new API key" /> : selectedTrader && curTrader ? (
                  <div className="space-y-5">
                    {/* Identity */}
                    <Card><CardContent className="space-y-4">
                      <SectionTitle>Identity</SectionTitle>
                      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                        <div><label className="text-xs text-gray-400 mb-1.5 block">Display Name</label>
                          <Input value={trName} onChange={(e) => setTrName(e.target.value)} placeholder="Strategy display name" /></div>
                        <div><label className="text-xs text-gray-400 mb-1.5 block">Trader ID</label>
                          <div className="h-9 flex items-center px-3 rounded-lg border border-border bg-surface-light/30 text-sm text-gray-400 select-all cursor-default" style={FONT_MONO}>{curTrader.trader_id}</div></div>
                      </div>
                      <div><label className="text-xs text-gray-400 mb-1.5 block">Description</label>
                        <Input value={trDesc} onChange={(e) => setTrDesc(e.target.value)} placeholder="Strategy description..." /></div>
                    </CardContent></Card>

                    {/* API Key Status */}
                    <Card><CardContent className="space-y-4">
                      <SectionTitle>API Key Status</SectionTitle>
                      <div className="flex items-center gap-4 flex-wrap">
                        <Badge className="bg-profit/15 text-profit text-[11px]">{curTrader.is_active ? "Active" : "Inactive"}</Badge>
                        {curTrader.last_webhook_at && <span className="text-xs text-gray-500">Last webhook: {formatTimeAgo(curTrader.last_webhook_at)}</span>}
                        <Button variant="outline" size="sm" onClick={handleRotateKey} disabled={saving} className="ml-auto">Rotate Key</Button>
                      </div>
                      {revealedKey && <RevealedKeyBox apiKey={revealedKey} onCopy={copyClip} />}
                    </CardContent></Card>

                    {/* Portfolio Links */}
                    {curTrader.portfolios.length > 0 && (
                      <Card><CardContent className="space-y-3">
                        <SectionTitle>Portfolio Links</SectionTitle>
                        {curTrader.portfolios.map((pl) => (
                          <div key={pl.portfolio_id} className="flex items-center justify-between p-3 rounded-lg border border-border/50 bg-surface-light/20">
                            <span className="text-sm text-gray-300">{pl.portfolio_name}</span>
                            <Badge variant={pl.direction_filter === "long" ? "long" : pl.direction_filter === "short" ? "short" : "outline"} className="text-[10px]">{pl.direction_filter || "All"}</Badge>
                          </div>
                        ))}
                      </CardContent></Card>
                    )}

                    <div className="flex items-center gap-3 pt-1">
                      <Button onClick={handleSaveTrader} disabled={saving} className="min-w-[120px]">{saving ? "Saving..." : "Save Changes"}</Button>
                      <Button variant="ghost" size="sm" onClick={() => setSelectedTrader(null)}>Cancel</Button>
                      <Button variant="destructive" size="sm" onClick={handleDeleteTrader} disabled={saving} className="ml-auto bg-loss/20 text-loss border-loss/30 hover:bg-loss/30">Delete Strategy</Button>
                    </div>
                  </div>
                ) : selectedKey && curKey ? (
                  <div className="space-y-5">
                    <Card><CardContent className="space-y-4">
                      <SectionTitle>Unclaimed API Key</SectionTitle>
                      <Separator />
                      <div className="grid grid-cols-2 gap-4">
                        <div><span className="stat-label">Label</span><div className="text-sm text-white mt-1">{curKey.label || "None"}</div></div>
                        <div><span className="stat-label">Created</span><div className="text-sm text-white mt-1">{formatDate(curKey.created_at)}</div></div>
                      </div>
                      <div className="flex items-center gap-3 p-3 rounded-lg bg-surface-light/30 border border-border/50">
                        <div className="w-2 h-2 rounded-full bg-screener-amber animate-pulse" />
                        <span className="text-xs text-gray-400">Waiting for first webhook to claim this key</span>
                      </div>
                      {revealedKey && <RevealedKeyBox apiKey={revealedKey} onCopy={copyClip} />}
                    </CardContent></Card>
                    <div className="flex items-center gap-3 pt-1">
                      <Button variant="destructive" size="sm" onClick={() => handleRevokeKey(curKey.id)} disabled={saving}>{saving ? "Revoking..." : "Revoke Key"}</Button>
                      <Button variant="ghost" size="sm" onClick={() => setSelectedKey(null)}>Cancel</Button>
                    </div>
                  </div>
                ) : null}
              </div>
            </div>
          </TabsContent>

          {/* ── BACKTESTS TAB ─────────────────────────────── */}
          <TabsContent value="backtests">
            <BacktestsTab flash={flash} />
          </TabsContent>

          {/* ── HENRY AI CONFIG TAB ───────────────────────── */}
          <TabsContent value="henry">
            <HenryConfigTab flash={flash} />
          </TabsContent>

          {/* ── SCANNER CONFIG TAB ──────────────────────────── */}
          <TabsContent value="scanner">
            <ScannerConfigTab flash={flash} />
          </TabsContent>
        </Tabs>
      </div>
      {toast && <Toast message={toast.message} type={toast.type} />}
    </>
  );
}

/* ── Backtests Tab Component ───────────────────────────────────── */
function BacktestsTab({ flash }: { flash: (msg: string, type?: "success" | "error") => void }) {
  const [imports, setImports] = useState<import("@/lib/types").BacktestImportData[]>([]);
  const [loading, setLoading] = useState(true);
  const [uploading, setUploading] = useState(false);

  const fetchImports = useCallback(async () => {
    try { setImports(await api.getBacktestImports()); }
    catch {} finally { setLoading(false); }
  }, []);

  useEffect(() => { fetchImports(); }, [fetchImports]);

  const handleUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = e.target.files;
    if (!files?.length) return;
    setUploading(true);
    try {
      await api.uploadBacktests(Array.from(files));
      flash("Backtests imported");
      await fetchImports();
    } catch { flash("Upload failed", "error"); }
    setUploading(false);
    e.target.value = "";
  };

  const handleDelete = async (id: string) => {
    if (!confirm("Delete this backtest import?")) return;
    try {
      await api.deleteBacktestImport(id);
      flash("Backtest deleted");
      await fetchImports();
    } catch { flash("Delete failed", "error"); }
  };

  if (loading) return <div className="p-8"><Skeleton className="h-40 rounded-xl" /></div>;

  return (
    <div className="rounded-xl border border-[#374151]/50 bg-[#1f2937]/20 p-6 space-y-6">
      <div className="flex items-center justify-between">
        <SectionTitle>Backtest Imports</SectionTitle>
        <label className="cursor-pointer bg-indigo-500/15 text-indigo-300 border border-indigo-500/30 hover:bg-indigo-500/25 px-4 py-2 rounded-lg text-xs font-medium transition">
          {uploading ? "Uploading..." : "Upload CSV"}
          <input type="file" accept=".csv" multiple onChange={handleUpload} className="hidden" disabled={uploading} />
        </label>
      </div>
      <p className="text-xs text-gray-500">Upload TradingView backtest CSV exports. Filename format: STRATEGY_VERSION_EXCHANGE_TICKER_DATE.csv</p>
      {imports.length === 0 ? (
        <div className="text-center py-12 text-gray-500 text-sm">No backtest data imported yet</div>
      ) : (
        <div className="space-y-2">
          {imports.map((bt) => (
            <div key={bt.id} className="flex items-center gap-3 p-3 rounded-lg border border-border/40 bg-surface-light/20">
              <div className="flex-1 min-w-0">
                <div className="text-sm text-white font-medium">{bt.strategy_name} — {bt.ticker}</div>
                <div className="text-[10px] text-gray-500 font-mono mt-0.5">
                  {bt.trade_count} trades | WR {bt.win_rate?.toFixed(1) ?? "?"}% | PF {bt.profit_factor?.toFixed(2) ?? "?"} | PnL {bt.total_pnl_pct?.toFixed(2) ?? "?"}%
                </div>
              </div>
              <Badge variant="outline" className="text-[10px]">{bt.filename}</Badge>
              <Button variant="ghost" size="sm" onClick={() => handleDelete(bt.id)} className="text-loss/50 hover:text-loss">
                <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                </svg>
              </Button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

/* ── FMP Status Panel ─────────────────────────────────────────── */
function FmpStatusPanel({ fmpUsage, flash }: { fmpUsage: { calls_today: number; limit: number; remaining: number; throttled: boolean } | null; flash: (msg: string, type?: "success" | "error") => void }) {
  const [testResult, setTestResult] = useState<Record<string, unknown> | null>(null);
  const [testing, setTesting] = useState(false);
  const [flushing, setFlushing] = useState(false);

  const testConnection = async () => {
    setTesting(true);
    setTestResult(null);
    try {
      const result = await api.testFmpConnection();
      setTestResult(result);
      flash(result.status === "ok" ? "FMP connection OK" : `FMP error: ${result.message || result.http_status}`, result.status === "ok" ? "success" : "error");
    } catch (e) { flash("Test failed", "error"); }
    setTesting(false);
  };

  const flushCache = async () => {
    setFlushing(true);
    try {
      const result = await api.flushFmpCache();
      flash(`Cache flushed: ${result.entries_deleted || 0} entries deleted`);
    } catch { flash("Flush failed", "error"); }
    setFlushing(false);
  };

  return (
    <Card><CardContent className="pt-4">
      {fmpUsage && (
        <>
          <div className="grid grid-cols-3 gap-4 mb-3">
            <div><div className="stat-label">Calls Today</div><div className="text-sm font-mono text-white">{fmpUsage.calls_today}</div></div>
            <div><div className="stat-label">Limit</div><div className="text-sm font-mono text-white">{fmpUsage.limit}</div></div>
            <div><div className="stat-label">Status</div><div className={`text-sm font-mono ${fmpUsage.throttled ? "text-loss" : "text-profit"}`}>{fmpUsage.throttled ? "Throttled" : "Active"}</div></div>
          </div>
          <div className="w-full bg-gray-700 rounded-full h-2 mb-4">
            <div className={`h-2 rounded-full transition-all ${fmpUsage.calls_today / fmpUsage.limit > 0.8 ? "bg-loss" : fmpUsage.calls_today / fmpUsage.limit > 0.5 ? "bg-amber-400" : "bg-profit"}`}
              style={{ width: `${Math.min(100, (fmpUsage.calls_today / fmpUsage.limit) * 100)}%` }} />
          </div>
        </>
      )}
      <div className="flex items-center gap-3">
        <Button variant="outline" size="sm" onClick={testConnection} disabled={testing} className="text-[10px]">
          {testing ? "Testing..." : "Test FMP Connection"}
        </Button>
        <Button variant="outline" size="sm" onClick={flushCache} disabled={flushing} className="text-[10px] text-amber-400 border-amber-500/30">
          {flushing ? "Flushing..." : "Flush Cache"}
        </Button>
      </div>
      {testResult && (
        <div className={`mt-3 p-3 rounded-lg text-[10px] font-mono ${testResult.status === "ok" ? "bg-profit/10 border border-profit/20" : "bg-loss/10 border border-loss/20"}`}>
          <div className="text-white mb-1">Status: {String(testResult.status)} | HTTP: {String(testResult.http_status || "N/A")}</div>
          <div className="text-gray-400">Key: {String(testResult.key_preview || "N/A")}</div>
          {testResult.response_preview != null && (
            <div className="text-gray-500 mt-1 break-all max-h-20 overflow-y-auto">{String(testResult.response_preview).slice(0, 300)}</div>
          )}
        </div>
      )}
    </CardContent></Card>
  );
}

/* ── Scanner Config Tab (Full FMP) ─────────────────────────────── */

interface TechRule {
  enabled: boolean; indicator: string; period: number; timeframe: string;
  condition: string; value: number; compare_indicator: { indicator: string; period: number } | null; label: string;
}

const INDICATOR_OPTIONS = ["rsi","adx","sma","ema","wma","dema","tema","williams","standardDeviation","macd","bollinger_lower","bollinger_upper","price"];
const CONDITION_OPTIONS = ["above","below","crosses_above","crosses_below","increasing","decreasing"];
const TIMEFRAME_OPTIONS = ["daily","1hour","15min","5min"];
const MARKET_CAP_PRESETS: Record<string, [number | null, number | null]> = {
  "Any": [null, null], "Micro (<$300M)": [null, 3e8], "Small ($300M-$2B)": [3e8, 2e9],
  "Mid ($2B-$10B)": [2e9, 1e10], "Large ($10B-$200B)": [1e10, 2e11], "Mega (>$200B)": [2e11, null],
};

const SCAN_PRESETS: Record<string, { label: string; desc: string; color: string }> = {
  momentum: { label: "Momentum", desc: "Large cap stocks in established uptrends", color: "border-amber-500/40 text-amber-400" },
  oversold_bounce: { label: "Oversold Bounce", desc: "Pullback buying in uptrends", color: "border-profit/40 text-profit" },
  breakout: { label: "Breakout", desc: "New trends with volume confirmation", color: "border-ai-blue/40 text-ai-blue" },
  value_catalyst: { label: "Value + Catalyst", desc: "Fundamentally solid with upcoming events", color: "border-ai-purple/40 text-ai-purple" },
};

function ScannerConfigTab({ flash }: { flash: (msg: string, type?: "success" | "error") => void }) {
  // ── Profiles state ──
  const [profiles, setProfiles] = useState<import("@/lib/types").ScanProfile[]>([]);
  const [selectedProfileId, setSelectedProfileId] = useState<string | null>(null);
  const [runningProfile, setRunningProfile] = useState<string | null>(null);

  // ── Active profile's criteria (loaded when a profile is selected) ──
  const [screener, setScreener] = useState<Record<string, unknown>>({
    priceMoreThan: 5, priceLessThan: null, marketCapMoreThan: 5e8, marketCapLessThan: null,
    volumeMoreThan: 500000, volumeLessThan: null, betaMoreThan: null, betaLessThan: null,
    dividendMoreThan: null, dividendLessThan: null, sector: "", industry: "", country: "US",
    exchange: "NYSE,NASDAQ", isEtf: false, isFund: false, isActivelyTrading: true, limit: 50,
  });
  const [rules, setRules] = useState<TechRule[]>([]);
  const [volumeFilter, setVolumeFilter] = useState({ enabled: false, surge_multiplier: 1.5, avg_period: 20 });
  const [marketConditions, setMarketConditions] = useState<Record<string, unknown>>({ trend: "any", time_slots: ["morning", "midday", "afternoon"] });
  const [activePreset, setActivePreset] = useState<string | null>(null);
  const [fmpUsage, setFmpUsage] = useState<{ calls_today: number; limit: number; remaining: number; throttled: boolean } | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);

  const loadProfileCriteria = useCallback((profile: import("@/lib/types").ScanProfile) => {
    const c = profile.criteria || {};
    if (c.screener) setScreener(prev => ({ ...prev, ...(c.screener as Record<string, unknown>) }));
    if (c.technical_rules && Array.isArray(c.technical_rules)) setRules(c.technical_rules as TechRule[]);
    if (c.volume_filter) setVolumeFilter(prev => ({ ...prev, ...(c.volume_filter as Record<string, unknown>) }));
    setMarketConditions(profile.market_conditions || { trend: "any", time_slots: ["morning", "midday", "afternoon"] });
  }, []);

  useEffect(() => {
    Promise.all([
      api.getScannerProfiles().catch(() => ({ profiles: [] })),
      api.getScannerCriteria().catch(() => null),
      api.getFmpUsage().catch(() => null),
    ]).then(([profilesRes, c, u]) => {
      const profs = profilesRes.profiles || [];
      setProfiles(profs);
      // Select first enabled profile
      const first = profs.find(p => p.enabled) || profs[0];
      if (first) {
        setSelectedProfileId(first.id);
        loadProfileCriteria(first);
      } else if (c && typeof c === "object") {
        const cr = c as Record<string, unknown>;
        if (cr.screener) setScreener(prev => ({ ...prev, ...(cr.screener as Record<string, unknown>) }));
        if (cr.technical_rules && Array.isArray(cr.technical_rules)) setRules(cr.technical_rules as TechRule[]);
        if (cr.volume_filter) setVolumeFilter(prev => ({ ...prev, ...(cr.volume_filter as Record<string, unknown>) }));
      }
      if (u) setFmpUsage(u as typeof fmpUsage);
    }).finally(() => setLoading(false));
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const selectProfile = (profile: import("@/lib/types").ScanProfile) => {
    setSelectedProfileId(profile.id);
    loadProfileCriteria(profile);
  };

  const handleSave = async () => {
    setSaving(true);
    try {
      const criteria = { screener, technical_rules: rules, volume_filter: volumeFilter, active_preset: activePreset };

      if (selectedProfileId) {
        // Save to the selected profile
        const profile = profiles.find(p => p.id === selectedProfileId);
        await api.saveScannerProfile(selectedProfileId, {
          id: selectedProfileId,
          name: profile?.name || selectedProfileId,
          description: profile?.description || "",
          enabled: profile?.enabled ?? true,
          market_conditions: marketConditions,
          criteria,
        });
        // Refresh profiles
        const res = await api.getScannerProfiles().catch(() => ({ profiles: [] }));
        setProfiles(res.profiles || []);
        flash(`Profile "${profile?.name || selectedProfileId}" saved`);
      } else {
        await api.updateScannerCriteria(criteria);
        flash("Scanner criteria saved");
      }
    } catch { flash("Save failed", "error"); }
    setSaving(false);
  };

  const handleToggleProfile = async (profileId: string) => {
    const profile = profiles.find(p => p.id === profileId);
    if (!profile) return;
    try {
      await api.saveScannerProfile(profileId, { ...profile, enabled: !profile.enabled });
      const res = await api.getScannerProfiles().catch(() => ({ profiles: [] }));
      setProfiles(res.profiles || []);
      flash(`${profile.name} ${profile.enabled ? "disabled" : "enabled"}`);
    } catch { flash("Toggle failed", "error"); }
  };

  const handleRunProfile = async (profileId: string) => {
    setRunningProfile(profileId);
    try {
      await api.runScannerWithProfile(profileId);
      flash("Scan started");
    } catch { flash("Scan failed", "error"); }
    setTimeout(() => setRunningProfile(null), 5000);
  };

  const applyPreset = async (name: string) => {
    setActivePreset(name);
    const profile = profiles.find(p => p.id === name);
    if (profile) {
      selectProfile(profile);
      flash(`Loaded "${profile.name}" profile`);
    }
  };

  const updateRule = (idx: number, field: string, val: unknown) => {
    setRules(prev => prev.map((r, i) => i === idx ? { ...r, [field]: val } : r));
  };
  const removeRule = (idx: number) => setRules(prev => prev.filter((_, i) => i !== idx));
  const addRule = () => setRules(prev => [...prev, {
    enabled: true, indicator: "rsi", period: 14, timeframe: "daily",
    condition: "above", value: 50, compare_indicator: null, label: "New rule",
  }]);

  const sf = (key: string, val: unknown) => setScreener(prev => ({ ...prev, [key]: val }));
  const numOrNull = (v: string) => { const n = parseFloat(v); return isNaN(n) ? null : n; };
  // Safe getter for Input value — converts null/undefined/object to "" for the DOM
  const sv = (key: string): string | number => { const v = screener[key]; return v == null || typeof v === "object" ? "" : v as string | number; };

  if (loading) return <div className="p-8"><Skeleton className="h-60 rounded-xl" /></div>;

  return (
    <div className="rounded-xl border border-[#374151]/50 bg-[#1f2937]/20 p-6 space-y-6">
      {/* ── Scan Profiles ── */}
      <SectionTitle>Scan Profiles</SectionTitle>
      <p className="text-[10px] text-gray-500">Henry rotates through enabled profiles based on market conditions. Select a profile to edit its criteria.</p>
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
        {profiles.map((p) => {
          const presetStyle = SCAN_PRESETS[p.id];
          const isSelected = selectedProfileId === p.id;
          return (
            <div key={p.id}
              className={`relative text-left p-3 rounded-lg border transition text-xs ${
                isSelected ? (presetStyle?.color || "border-ai-blue/40 text-ai-blue") + " bg-surface-light/40" :
                p.enabled ? "border-border/50 text-gray-300 hover:border-border" :
                "border-border/20 text-gray-600 opacity-50"
              }`}>
              <button onClick={() => applyPreset(p.id)} className="w-full text-left">
                <div className="flex items-center gap-2 mb-0.5">
                  <span className="font-semibold" style={FONT_OUTFIT}>{p.name}</span>
                  {!p.enabled && <span className="text-[8px] text-gray-600 uppercase">disabled</span>}
                </div>
                <div className="text-[10px] opacity-70 mb-2">{p.description}</div>
              </button>
              <div className="flex items-center gap-1.5">
                <button onClick={() => handleToggleProfile(p.id)}
                  className={`w-6 h-3 rounded-full transition ${p.enabled ? "bg-profit" : "bg-gray-600"}`}
                  title={p.enabled ? "Disable" : "Enable"}>
                  <div className={`w-2.5 h-2.5 rounded-full bg-white transition-transform ${p.enabled ? "translate-x-3" : "translate-x-0.5"}`} />
                </button>
                <button onClick={() => handleRunProfile(p.id)} disabled={runningProfile === p.id || !p.enabled}
                  className="text-[9px] text-ai-blue/60 hover:text-ai-blue disabled:opacity-30 ml-auto" title="Run now">
                  {runningProfile === p.id ? "Running..." : "Run"}
                </button>
              </div>
              {/* Market condition badges */}
              <div className="flex flex-wrap gap-1 mt-2">
                {(p.market_conditions?.time_slots || []).map(s => (
                  <span key={s} className="text-[8px] px-1 py-0.5 rounded bg-surface-light/30 text-gray-500 font-mono">{s}</span>
                ))}
                {p.market_conditions?.vix_max && <span className="text-[8px] px-1 py-0.5 rounded bg-surface-light/30 text-gray-500 font-mono">VIX&lt;{p.market_conditions.vix_max}</span>}
                {p.market_conditions?.vix_min && <span className="text-[8px] px-1 py-0.5 rounded bg-surface-light/30 text-gray-500 font-mono">VIX&gt;{p.market_conditions.vix_min}</span>}
                {p.market_conditions?.trend && p.market_conditions.trend !== "any" && (
                  <span className="text-[8px] px-1 py-0.5 rounded bg-surface-light/30 text-gray-500 font-mono">{p.market_conditions.trend}</span>
                )}
              </div>
            </div>
          );
        })}
      </div>

      {/* Market conditions for selected profile */}
      {selectedProfileId && (
        <div className="flex flex-wrap items-center gap-3 text-[10px] text-gray-400 p-3 rounded-lg bg-surface-light/10 border border-border/20">
          <span className="font-medium text-white" style={FONT_OUTFIT}>When to use:</span>
          <div className="flex items-center gap-1">
            Trend:
            <select value={String(marketConditions.trend || "any")} onChange={e => setMarketConditions(prev => ({ ...prev, trend: e.target.value }))}
              className="bg-transparent border border-border/40 rounded px-1 py-0.5 text-white text-[10px]">
              <option value="any">Any</option><option value="bullish">Bullish</option><option value="bearish">Bearish</option>
            </select>
          </div>
          <div className="flex items-center gap-1">
            VIX:
            <Input type="number" placeholder="Min" value={typeof marketConditions.vix_min === "number" ? marketConditions.vix_min : ""} onChange={e => setMarketConditions(prev => ({ ...prev, vix_min: parseFloat(e.target.value) || undefined }))} className="h-5 w-12 text-[10px]" />
            -
            <Input type="number" placeholder="Max" value={typeof marketConditions.vix_max === "number" ? marketConditions.vix_max : ""} onChange={e => setMarketConditions(prev => ({ ...prev, vix_max: parseFloat(e.target.value) || undefined }))} className="h-5 w-12 text-[10px]" />
          </div>
          <div className="flex items-center gap-1">
            Time:
            {["morning", "midday", "afternoon"].map(slot => {
              const slots = (marketConditions.time_slots as string[] | undefined) || [];
              const active = slots.includes(slot);
              return (
                <button key={slot} onClick={() => {
                  const newSlots = active ? slots.filter(s => s !== slot) : [...slots, slot];
                  setMarketConditions(prev => ({ ...prev, time_slots: newSlots }));
                }} className={`px-1.5 py-0.5 rounded text-[9px] font-mono border transition ${active ? "bg-ai-blue/20 border-ai-blue/40 text-ai-blue" : "border-border/30 text-gray-500"}`}>
                  {slot}
                </button>
              );
            })}
          </div>
        </div>
      )}

      <Separator />

      {/* ── Market Filters ── */}
      <SectionTitle>Market Filters (FMP Screener)</SectionTitle>
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
        <div>
          <label className="text-[10px] text-gray-400 mb-1 block" style={FONT_OUTFIT}>Price Range ($)</label>
          <div className="flex gap-2">
            <Input type="number" placeholder="Min" value={sv("priceMoreThan")} onChange={e => sf("priceMoreThan", numOrNull(e.target.value))} className="h-8 text-xs" />
            <Input type="number" placeholder="Max" value={sv("priceLessThan")} onChange={e => sf("priceLessThan", numOrNull(e.target.value))} className="h-8 text-xs" />
          </div>
        </div>
        <div>
          <label className="text-[10px] text-gray-400 mb-1 block" style={FONT_OUTFIT}>Market Cap</label>
          <select value={Object.entries(MARKET_CAP_PRESETS).find(([, [lo, hi]]) => lo === screener.marketCapMoreThan && hi === screener.marketCapLessThan)?.[0] || "Custom"}
            onChange={e => { const p = MARKET_CAP_PRESETS[e.target.value]; if (p) { sf("marketCapMoreThan", p[0]); sf("marketCapLessThan", p[1]); } }}
            className="w-full h-8 text-xs rounded-lg border border-input bg-transparent px-2 text-white">
            {Object.keys(MARKET_CAP_PRESETS).map(k => <option key={k} value={k}>{k}</option>)}
            <option value="Custom">Custom</option>
          </select>
        </div>
        <div>
          <label className="text-[10px] text-gray-400 mb-1 block" style={FONT_OUTFIT}>Min Volume</label>
          <Input type="number" value={sv("volumeMoreThan")} onChange={e => sf("volumeMoreThan", numOrNull(e.target.value))} className="h-8 text-xs" placeholder="500000" />
        </div>
        <div>
          <label className="text-[10px] text-gray-400 mb-1 block" style={FONT_OUTFIT}>Beta Range</label>
          <div className="flex gap-2">
            <Input type="number" step="0.1" placeholder="Min" value={sv("betaMoreThan")} onChange={e => sf("betaMoreThan", numOrNull(e.target.value))} className="h-8 text-xs" />
            <Input type="number" step="0.1" placeholder="Max" value={sv("betaLessThan")} onChange={e => sf("betaLessThan", numOrNull(e.target.value))} className="h-8 text-xs" />
          </div>
        </div>
        <div>
          <label className="text-[10px] text-gray-400 mb-1 block" style={FONT_OUTFIT}>Dividend Yield (%)</label>
          <div className="flex gap-2">
            <Input type="number" step="0.1" placeholder="Min" value={sv("dividendMoreThan")} onChange={e => sf("dividendMoreThan", numOrNull(e.target.value))} className="h-8 text-xs" />
            <Input type="number" step="0.1" placeholder="Max" value={sv("dividendLessThan")} onChange={e => sf("dividendLessThan", numOrNull(e.target.value))} className="h-8 text-xs" />
          </div>
        </div>
        <div>
          <label className="text-[10px] text-gray-400 mb-1 block" style={FONT_OUTFIT}>Sector</label>
          <Input value={String(screener.sector || "")} onChange={e => sf("sector", e.target.value)} className="h-8 text-xs" placeholder="Technology,Healthcare" />
        </div>
        <div>
          <label className="text-[10px] text-gray-400 mb-1 block" style={FONT_OUTFIT}>Industry</label>
          <Input value={String(screener.industry || "")} onChange={e => sf("industry", e.target.value)} className="h-8 text-xs" placeholder="e.g. Semiconductors" />
        </div>
        <div>
          <label className="text-[10px] text-gray-400 mb-1 block" style={FONT_OUTFIT}>Country</label>
          <Input value={String(screener.country || "")} onChange={e => sf("country", e.target.value)} className="h-8 text-xs" placeholder="US" />
        </div>
        <div>
          <label className="text-[10px] text-gray-400 mb-1 block" style={FONT_OUTFIT}>Exchange</label>
          <Input value={String(screener.exchange || "")} onChange={e => sf("exchange", e.target.value)} className="h-8 text-xs" placeholder="NYSE,NASDAQ" />
        </div>
      </div>
      <div className="flex flex-wrap gap-4 text-xs">
        {([["isEtf", "Exclude ETFs", true], ["isFund", "Exclude Funds", true], ["isActivelyTrading", "Active Only", false]] as const).map(([key, lbl, invert]) => (
          <label key={key} className="flex items-center gap-2 cursor-pointer text-gray-400">
            <button onClick={() => sf(key, !screener[key])}
              className={`w-8 h-4 rounded-full transition ${(invert ? !screener[key] : screener[key]) ? "bg-profit" : "bg-gray-600"}`}>
              <div className={`w-3.5 h-3.5 rounded-full bg-white transition-transform ${(invert ? !screener[key] : screener[key]) ? "translate-x-4" : "translate-x-0.5"}`} />
            </button>
            {lbl}
          </label>
        ))}
        <div className="flex items-center gap-2 text-gray-400">
          <span>Max results:</span>
          <Input type="number" value={sv("limit") || 50} onChange={e => sf("limit", parseInt(e.target.value) || 50)} className="h-6 w-16 text-xs" />
        </div>
      </div>

      <Separator />

      {/* ── Technical Rules ── */}
      <div className="flex items-center justify-between">
        <SectionTitle>Technical Rules (AND logic)</SectionTitle>
        <Button variant="outline" size="sm" onClick={addRule} className="text-[10px] h-7">+ Add Rule</Button>
      </div>
      <p className="text-[10px] text-gray-500">All enabled rules must pass for a stock to be selected. Rules are evaluated in order — put fast filters first.</p>
      <div className="space-y-2">
        {rules.map((r, i) => (
          <div key={i} className={`flex flex-wrap items-center gap-2 p-2.5 rounded-lg border text-[10px] font-mono ${r.enabled ? "border-border/60 bg-surface-light/20" : "border-border/20 bg-surface-light/5 opacity-50"}`}>
            <button onClick={() => updateRule(i, "enabled", !r.enabled)}
              className={`w-3 h-3 rounded-full border ${r.enabled ? "bg-profit border-profit" : "border-gray-500"}`} />
            <select value={r.indicator} onChange={e => updateRule(i, "indicator", e.target.value)}
              className="bg-transparent border border-border/40 rounded px-1.5 py-0.5 text-white">
              {INDICATOR_OPTIONS.map(o => <option key={o} value={o}>{o.toUpperCase()}</option>)}
            </select>
            {!["price","macd","bollinger_lower","bollinger_upper"].includes(r.indicator) && (
              <Input type="number" value={r.period} onChange={e => updateRule(i, "period", parseInt(e.target.value) || 14)}
                className="h-6 w-12 text-[10px] text-center" title="Period" />
            )}
            <select value={r.condition} onChange={e => updateRule(i, "condition", e.target.value)}
              className="bg-transparent border border-border/40 rounded px-1.5 py-0.5 text-white">
              {CONDITION_OPTIONS.map(o => <option key={o} value={o}>{o.replace("_", " ")}</option>)}
            </select>
            {r.compare_indicator ? (
              <div className="flex items-center gap-1">
                <select value={r.compare_indicator.indicator} onChange={e => updateRule(i, "compare_indicator", { ...r.compare_indicator!, indicator: e.target.value })}
                  className="bg-transparent border border-border/40 rounded px-1.5 py-0.5 text-white">
                  {INDICATOR_OPTIONS.map(o => <option key={o} value={o}>{o.toUpperCase()}</option>)}
                </select>
                <Input type="number" value={r.compare_indicator.period} onChange={e => updateRule(i, "compare_indicator", { ...r.compare_indicator!, period: parseInt(e.target.value) || 0 })}
                  className="h-6 w-12 text-[10px] text-center" />
                <button onClick={() => updateRule(i, "compare_indicator", null)} className="text-gray-500 hover:text-white" title="Switch to value">&#x21c4;</button>
              </div>
            ) : (
              <div className="flex items-center gap-1">
                <Input type="number" step="any" value={r.value} onChange={e => updateRule(i, "value", parseFloat(e.target.value) || 0)}
                  className="h-6 w-16 text-[10px] text-center" />
                <button onClick={() => updateRule(i, "compare_indicator", { indicator: "sma", period: 200 })} className="text-gray-500 hover:text-white" title="Compare to indicator">&#x21c4;</button>
              </div>
            )}
            <select value={r.timeframe} onChange={e => updateRule(i, "timeframe", e.target.value)}
              className="bg-transparent border border-border/40 rounded px-1.5 py-0.5 text-gray-400">
              {TIMEFRAME_OPTIONS.map(o => <option key={o} value={o}>{o}</option>)}
            </select>
            <Input value={r.label} onChange={e => updateRule(i, "label", e.target.value)}
              className="h-6 flex-1 min-w-[100px] text-[10px] text-gray-400 border-transparent hover:border-border/40" placeholder="Label" />
            <button onClick={() => removeRule(i)} className="text-loss/40 hover:text-loss ml-auto">
              <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}><path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" /></svg>
            </button>
          </div>
        ))}
      </div>

      <Separator />

      {/* ── Volume Filter ── */}
      <SectionTitle>Volume Surge Filter</SectionTitle>
      <div className="flex flex-wrap items-center gap-4 text-xs text-gray-400">
        <label className="flex items-center gap-2 cursor-pointer">
          <button onClick={() => setVolumeFilter(prev => ({ ...prev, enabled: !prev.enabled }))}
            className={`w-8 h-4 rounded-full transition ${volumeFilter.enabled ? "bg-profit" : "bg-gray-600"}`}>
            <div className={`w-3.5 h-3.5 rounded-full bg-white transition-transform ${volumeFilter.enabled ? "translate-x-4" : "translate-x-0.5"}`} />
          </button>
          Enable
        </label>
        <div className="flex items-center gap-1">
          Volume &ge;
          <Input type="number" step="0.1" value={volumeFilter.surge_multiplier} onChange={e => setVolumeFilter(prev => ({ ...prev, surge_multiplier: parseFloat(e.target.value) || 1 }))}
            className="h-6 w-14 text-xs text-center" />
          &times; {volumeFilter.avg_period}-day avg
        </div>
        <div className="flex items-center gap-1">
          Avg period:
          <Input type="number" value={volumeFilter.avg_period} onChange={e => setVolumeFilter(prev => ({ ...prev, avg_period: parseInt(e.target.value) || 20 }))}
            className="h-6 w-14 text-xs text-center" />
        </div>
      </div>

      <Separator />

      {/* ── Save ── */}
      <div className="flex items-center gap-4">
        <Button onClick={handleSave} disabled={saving} className="min-w-[140px]">
          {saving ? "Saving..." : selectedProfileId ? `Save "${profiles.find(p => p.id === selectedProfileId)?.name || selectedProfileId}"` : "Save Criteria"}
        </Button>
        {selectedProfileId && <span className="text-[10px] text-gray-500">Editing: {profiles.find(p => p.id === selectedProfileId)?.name}</span>}
      </div>

      {/* ── FMP Status ── */}
      <Separator />
      <SectionTitle>FMP API Status</SectionTitle>
      <FmpStatusPanel fmpUsage={fmpUsage} flash={flash} />
    </div>
  );
}

function HenryConfigTab({ flash }: { flash: (msg: string, type?: "success" | "error") => void }) {
  const [config, setConfig] = useState({
    min_confidence: 5,
    high_alloc_pct: 5.0,
    mid_alloc_pct: 3.0,
    min_adx: 20,
    require_stop: true,
    reward_risk_ratio: 2.0,
  });
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    api.getAIConfig().then((c) => {
      setConfig({
        min_confidence: c.min_confidence as number ?? 5,
        high_alloc_pct: c.high_alloc_pct as number ?? 5.0,
        mid_alloc_pct: c.mid_alloc_pct as number ?? 3.0,
        min_adx: c.min_adx as number ?? 20,
        require_stop: c.require_stop as boolean ?? true,
        reward_risk_ratio: c.reward_risk_ratio as number ?? 2.0,
      });
    }).catch(() => {}).finally(() => setLoading(false));
  }, []);

  const handleSave = async () => {
    setSaving(true);
    try {
      await api.updateAIConfig(config);
      flash("Henry AI config updated");
    } catch { flash("Save failed", "error"); }
    setSaving(false);
  };

  if (loading) return <div className="p-8"><Skeleton className="h-40 rounded-xl" /></div>;

  return (
    <div className="rounded-xl border border-[#374151]/50 bg-[#1f2937]/20 p-6 space-y-6">
      <SectionTitle>Henry&apos;s Trading Decision Framework</SectionTitle>
      <p className="text-xs text-gray-500">These rules govern how Henry evaluates signals for the AI paper portfolio.</p>

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-6">
        <RangeField label="Min Confidence to Trade" value={config.min_confidence} onChange={(v) => setConfig({ ...config, min_confidence: v })}
          min={1} max={10} step={1} suffix="/10" />
        <RangeField label="High Confidence Allocation" value={config.high_alloc_pct} onChange={(v) => setConfig({ ...config, high_alloc_pct: v })}
          min={1} max={20} step={0.5} suffix="%" />
        <RangeField label="Mid Confidence Allocation" value={config.mid_alloc_pct} onChange={(v) => setConfig({ ...config, mid_alloc_pct: v })}
          min={1} max={15} step={0.5} suffix="%" />
        <RangeField label="Min ADX for Trend" value={config.min_adx} onChange={(v) => setConfig({ ...config, min_adx: v })}
          min={10} max={40} step={1} suffix="" />
        <RangeField label="Reward/Risk Ratio" value={config.reward_risk_ratio} onChange={(v) => setConfig({ ...config, reward_risk_ratio: v })}
          min={1} max={5} step={0.5} suffix=":1" />
        <div>
          <div className="flex items-center justify-between mb-2">
            <span className="text-xs text-gray-400 font-medium" style={FONT_OUTFIT}>Require Stop Loss</span>
          </div>
          <button onClick={() => setConfig({ ...config, require_stop: !config.require_stop })}
            className={`w-12 h-6 rounded-full transition ${config.require_stop ? "bg-profit" : "bg-gray-600"}`}>
            <div className={`w-5 h-5 rounded-full bg-white transition-transform ${config.require_stop ? "translate-x-6" : "translate-x-0.5"}`} />
          </button>
        </div>
      </div>

      <div className="pt-2">
        <Button onClick={handleSave} disabled={saving} className="min-w-[140px]">
          {saving ? "Saving..." : "Save Config"}
        </Button>
      </div>

      <Card><CardContent className="pt-4">
        <div className="text-xs text-gray-400 space-y-1.5">
          <p><strong className="text-white">Min Confidence:</strong> Signals below this confidence score are auto-skipped. Higher = more selective.</p>
          <p><strong className="text-white">High Confidence Alloc:</strong> % of equity allocated per trade when confidence is 8-10.</p>
          <p><strong className="text-white">Mid Confidence Alloc:</strong> % of equity allocated per trade when confidence is {config.min_confidence}-7.</p>
          <p><strong className="text-white">Min ADX:</strong> Trend strength threshold. Signals below this ADX are skipped (no trend).</p>
          <p><strong className="text-white">Reward/Risk:</strong> Minimum expected reward-to-risk ratio for Henry to take a trade.</p>
          <p><strong className="text-white">Require Stop:</strong> When on, Henry skips any signal that doesn&apos;t include a stop loss price.</p>
        </div>
      </CardContent></Card>
    </div>
  );
}
