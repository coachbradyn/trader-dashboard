"use client";
import { useState, useEffect, useCallback, useRef } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { Sheet, SheetContent } from "@/components/ui/sheet";
import { api } from "@/lib/api";
import NotificationCenter from "@/components/NotificationCenter";

const FONT_MONO = { fontFamily: "'JetBrains Mono', monospace" } as const;

const NAV_LINKS = [
  { href: "/ai", label: "Home", dot: "bg-ai-blue", key: "d" },
  { href: "/screener", label: "Watchlist", dot: "bg-amber-500", key: "w" },
  { href: "/portfolios", label: "Portfolios", key: "p" },
  { href: "/scanner", label: "Scanner", dot: "bg-ai-blue", key: "s" },
  { href: "/settings", label: "Settings" },
  { href: "/memory", label: "Memory", dot: "bg-[#6366f1]" },
];

// ── MetricTooltip ─────────────────────────────────────────────────
export function MetricTooltip({ tip, children }: { label?: string; tip: string; children: React.ReactNode }) {
  const [show, setShow] = useState(false);
  return (
    <span className="relative inline-flex" onMouseEnter={() => setShow(true)} onMouseLeave={() => setShow(false)}>
      {children}
      {show && (
        <span className="absolute bottom-full left-1/2 -translate-x-1/2 mb-2 px-3 py-1.5 rounded-lg bg-[#1f2937] border border-[#374151] text-[10px] text-gray-300 whitespace-nowrap z-50 shadow-xl">
          {tip}
        </span>
      )}
    </span>
  );
}

// ── Shortcuts Overlay ─────────────────────────────────────────────
const SHORTCUTS = [
  { key: "d", label: "Dashboard (Home)" },
  { key: "w", label: "Watchlist" },
  { key: "s", label: "Scanner" },
  { key: "p", label: "Portfolios" },
  { key: "?", label: "Toggle this overlay" },
];

function ShortcutsOverlay({ onClose }: { onClose: () => void }) {
  return (
    <div className="fixed inset-0 z-[100] flex items-center justify-center bg-black/60 backdrop-blur-sm" onClick={onClose}>
      <div
        className="bg-[#111827] border border-[#374151] rounded-xl p-6 shadow-2xl max-w-xs w-full"
        onClick={(e) => e.stopPropagation()}
      >
        <h3 className="text-sm font-semibold text-white mb-4">Keyboard Shortcuts</h3>
        <div className="space-y-2">
          {SHORTCUTS.map((s) => (
            <div key={s.key} className="flex items-center justify-between">
              <span className="text-xs text-gray-400">{s.label}</span>
              <kbd className="px-2 py-0.5 rounded bg-[#1f2937] border border-[#374151] text-[11px] font-mono text-gray-300">
                {s.key}
              </kbd>
            </div>
          ))}
        </div>
        <p className="text-[10px] text-gray-600 mt-4">Press <kbd className="px-1 rounded bg-[#1f2937] border border-[#374151] text-gray-400">?</kbd> or <kbd className="px-1 rounded bg-[#1f2937] border border-[#374151] text-gray-400">Esc</kbd> to close</p>
      </div>
    </div>
  );
}

// ── Format helpers ────────────────────────────────────────────────
function formatTimeAgoShort(dateStr: string): string {
  const now = Date.now();
  const then = new Date(dateStr).getTime();
  const diffMin = Math.floor((now - then) / 60000);
  if (diffMin < 1) return "just now";
  if (diffMin < 60) return `${diffMin}m ago`;
  const diffH = Math.floor(diffMin / 60);
  if (diffH < 24) return `${diffH}h ago`;
  return `${Math.floor(diffH / 24)}d ago`;
}

function fmtTokens(n: number): string {
  if (n >= 1e6) return (n / 1e6).toFixed(1) + "M";
  if (n >= 1e3) return (n / 1e3).toFixed(1) + "k";
  return String(n);
}

// ── AI Usage Meter ────────────────────────────────────────────────
function AIUsageMeter() {
  const [usage, setUsage] = useState<{ estimated_cost_usd: number; total_input_tokens: number; total_output_tokens: number } | null>(null);

  useEffect(() => {
    let mounted = true;
    const fetch = () => {
      api.getAIUsage(1).then((d) => { if (mounted) setUsage(d); }).catch(() => {});
    };
    fetch();
    const iv = setInterval(fetch, 60000);
    return () => { mounted = false; clearInterval(iv); };
  }, []);

  if (!usage) return null;

  const totalTok = usage.total_input_tokens + usage.total_output_tokens;
  const costStr = usage.estimated_cost_usd >= 0.01 ? `$${usage.estimated_cost_usd.toFixed(2)}` : `${fmtTokens(totalTok)} tok`;

  return (
    <MetricTooltip tip={`Today: $${usage.estimated_cost_usd.toFixed(3)} | ${fmtTokens(totalTok)} tokens`}>
      <span className="text-[11px] text-ai-blue font-mono" style={FONT_MONO}>
        ◆ {costStr}
      </span>
    </MetricTooltip>
  );
}

// ── Notification types ────────────────────────────────────────────
interface NotifItem {
  id: string;
  icon: string;
  message: string;
  time: string;
}

// ── Notification Dropdown ─────────────────────────────────────────
function NotificationBell() {
  const [open, setOpen] = useState(false);
  const [items, setItems] = useState<NotifItem[]>([]);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let mounted = true;
    const load = async () => {
      try {
        const [activity, actions] = await Promise.all([
          api.getHenryActivity(10),
          api.getActions("pending"),
        ]);
        const notifs: NotifItem[] = [];
        for (const a of activity) {
          notifs.push({
            id: a.id,
            icon: a.activity_label || "🔔",
            message: a.message,
            time: a.created_at,
          });
        }
        if (actions.length > 0) {
          notifs.unshift({
            id: "pending-actions",
            icon: "⚡",
            message: `${actions.length} pending action${actions.length !== 1 ? "s" : ""}`,
            time: actions[0]?.created_at || new Date().toISOString(),
          });
        }
        if (mounted) setItems(notifs);
      } catch {}
    };
    load();
    const iv = setInterval(load, 60000);
    return () => { mounted = false; clearInterval(iv); };
  }, []);

  // close on outside click
  useEffect(() => {
    if (!open) return;
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [open]);

  const oneHourAgo = Date.now() - 3600000;
  const recentCount = items.filter((i) => new Date(i.time).getTime() > oneHourAgo).length;

  return (
    <div className="relative" ref={ref}>
      <button
        onClick={() => setOpen(!open)}
        className="relative p-1.5 text-gray-400 hover:text-white transition"
        aria-label="Notifications"
      >
        <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
          <path strokeLinecap="round" strokeLinejoin="round" d="M14.857 17.082a23.848 23.848 0 005.454-1.31A8.967 8.967 0 0118 9.75v-.7V9A6 6 0 006 9v.75a8.967 8.967 0 01-2.312 6.022c1.733.64 3.56 1.085 5.455 1.31m5.714 0a24.255 24.255 0 01-5.714 0m5.714 0a3 3 0 11-5.714 0" />
        </svg>
        {recentCount > 0 && (
          <span className="absolute -top-0.5 -right-0.5 flex items-center justify-center w-3.5 h-3.5 rounded-full bg-ai-blue text-[8px] font-bold text-white">
            {recentCount > 9 ? "9+" : recentCount}
          </span>
        )}
      </button>
      {open && (
        <div className="absolute right-0 top-full mt-2 w-80 max-h-96 overflow-y-auto rounded-xl bg-[#111827] border border-[#374151] shadow-2xl z-[60]">
          <div className="px-4 py-2.5 border-b border-[#374151]">
            <span className="text-xs font-semibold text-white">Notifications</span>
          </div>
          {items.length === 0 ? (
            <div className="px-4 py-6 text-center text-xs text-gray-500">No recent activity</div>
          ) : (
            <div className="divide-y divide-[#1f2937]">
              {items.map((item) => (
                <div key={item.id} className="px-4 py-2.5 hover:bg-[#1f2937]/50 transition">
                  <div className="flex gap-2.5 items-start">
                    <span className="text-sm shrink-0 mt-0.5">{item.icon}</span>
                    <div className="min-w-0 flex-1">
                      <p className="text-[11px] text-gray-300 leading-relaxed line-clamp-2">{item.message}</p>
                      <p className="text-[9px] text-gray-600 mt-0.5 font-mono">{formatTimeAgoShort(item.time)}</p>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ── Navbar ─────────────────────────────────────────────────────────
function Navbar() {
  const [open, setOpen] = useState(false);
  const pathname = usePathname();

  return (
    <nav className="border-b border-border bg-surface/80 backdrop-blur sticky top-0 z-50">
      <div className="max-w-7xl mx-auto px-3 sm:px-4 h-14 flex items-center gap-4 md:gap-8">
        <Link href="/" className="font-bold text-lg tracking-tight shrink-0">
          <span className="text-accent">Henry</span> AI Trader
        </Link>

        {/* Desktop nav */}
        <div className="hidden md:flex gap-6 text-sm text-gray-400">
          {NAV_LINKS.map((link) => (
            <Link
              key={link.href}
              href={link.href}
              className={`hover:text-white transition flex items-center gap-1.5 ${
                pathname === link.href || (link.href === "/ai" && pathname === "/")
                  ? "text-white"
                  : ""
              }`}
            >
              {link.dot && (
                <span className="relative flex h-1.5 w-1.5">
                  <span className={`animate-ping absolute inline-flex h-full w-full rounded-full ${link.dot} opacity-75`} />
                  <span className={`relative inline-flex rounded-full h-1.5 w-1.5 ${link.dot}`} />
                </span>
              )}
              {link.label}
            </Link>
          ))}
        </div>

        {/* Desktop right side */}
        <div className="ml-auto hidden md:flex items-center gap-4">
          <AIUsageMeter />
          <NotificationCenter />
          <NotificationBell />
          <div className="flex items-center gap-2">
            <span className="w-2 h-2 rounded-full bg-profit animate-pulse" />
            <span className="text-xs text-gray-500">Live</span>
          </div>
        </div>

        {/* Mobile right side */}
        <div className="ml-auto flex md:hidden items-center gap-2">
          <NotificationBell />
          <button
            className="p-2 -mr-2 text-gray-400 hover:text-white transition"
            onClick={() => setOpen(true)}
            aria-label="Open menu"
          >
            <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M3.75 6.75h16.5M3.75 12h16.5m-16.5 5.25h16.5" />
            </svg>
          </button>
        </div>

        {/* Mobile drawer */}
        <Sheet open={open} onOpenChange={setOpen}>
          <SheetContent side="left" className="bg-surface border-r border-border w-72 p-0">
            <div className="px-5 pt-6 pb-4 border-b border-border">
              <Link href="/" className="font-bold text-lg tracking-tight" onClick={() => setOpen(false)}>
                <span className="text-accent">Henry</span> AI Trader
              </Link>
            </div>
            <nav className="flex flex-col py-2">
              {NAV_LINKS.map((link) => (
                <Link
                  key={link.href}
                  href={link.href}
                  onClick={() => setOpen(false)}
                  className={`flex items-center gap-3 px-5 py-3 text-sm transition ${
                    pathname === link.href || (link.href === "/ai" && pathname === "/")
                      ? "text-white bg-surface-light/40"
                      : "text-gray-400 hover:text-white hover:bg-surface-light/20"
                  }`}
                >
                  {link.dot && (
                    <span className="relative flex h-1.5 w-1.5">
                      <span className={`animate-ping absolute inline-flex h-full w-full rounded-full ${link.dot} opacity-75`} />
                      <span className={`relative inline-flex rounded-full h-1.5 w-1.5 ${link.dot}`} />
                    </span>
                  )}
                  {link.label}
                  {link.key && (
                    <kbd className="ml-auto text-[9px] px-1.5 py-0.5 rounded bg-[#1f2937] border border-[#374151] text-gray-500 font-mono">{link.key}</kbd>
                  )}
                </Link>
              ))}
            </nav>
            <div className="absolute bottom-6 left-5 flex items-center gap-2">
              <span className="w-2 h-2 rounded-full bg-profit animate-pulse" />
              <span className="text-xs text-gray-500">Live</span>
            </div>
          </SheetContent>
        </Sheet>
      </div>
    </nav>
  );
}

function KillSwitchBanner() {
  const [autoTrading, setAutoTrading] = useState(false);
  const [killed, setKilled] = useState(false);
  const [killing, setKilling] = useState(false);

  const checkStatus = useCallback(async () => {
    try {
      const portfolios = await api.getPortfolios();
      const hasAutoTrading = portfolios.some(
        (p) => p.execution_mode && p.execution_mode !== "local" && p.is_active
      );
      setAutoTrading(hasAutoTrading);
    } catch {}
  }, []);

  useEffect(() => {
    checkStatus();
    const interval = setInterval(checkStatus, 30000);
    return () => clearInterval(interval);
  }, [checkStatus]);

  const handleKill = async () => {
    if (!confirm("KILL SWITCH: This will disable ALL auto-trading across ALL portfolios. Continue?")) return;
    setKilling(true);
    try {
      await api.killSwitch();
      setKilled(true);
      setAutoTrading(false);
    } catch {}
    setKilling(false);
  };

  if (!autoTrading && !killed) return null;

  return (
    <div className={`${killed ? "bg-loss/20 border-b border-loss/30" : "bg-profit/10 border-b border-profit/20"} px-3 sm:px-4`}>
      <div className="max-w-7xl mx-auto flex items-center justify-between h-8 text-[11px] font-mono">
        {killed ? (
          <>
            <div className="flex items-center gap-2 text-loss">
              <span className="w-2 h-2 rounded-full bg-loss" />
              AUTO-TRADING STOPPED
            </div>
            <span className="text-loss/60">Kill switch activated</span>
          </>
        ) : (
          <>
            <div className="flex items-center gap-2 text-profit">
              <span className="w-2 h-2 rounded-full bg-profit animate-pulse" />
              AUTO-TRADING ACTIVE
            </div>
            <button
              onClick={handleKill}
              disabled={killing}
              className="text-[10px] font-semibold uppercase tracking-wider px-3 py-0.5 rounded bg-loss/20 text-loss border border-loss/30 hover:bg-loss/30 transition disabled:opacity-50"
            >
              {killing ? "Stopping..." : "KILL SWITCH"}
            </button>
          </>
        )}
      </div>
    </div>
  );
}

export function LayoutShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const isLogin = pathname === "/login";
  const [showShortcuts, setShowShortcuts] = useState(false);

  // ── Global keyboard shortcuts ──────────────────────────────────
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement)?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || (e.target as HTMLElement)?.isContentEditable) return;

      switch (e.key) {
        case "d": router.push("/ai"); break;
        case "w": router.push("/screener"); break;
        case "s": router.push("/scanner"); break;
        case "p": router.push("/portfolios"); break;
        case "?": setShowShortcuts((v) => !v); break;
        case "Escape": setShowShortcuts(false); break;
        default: return;
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [router]);

  if (isLogin) {
    return <>{children}</>;
  }

  return (
    <>
      <KillSwitchBanner />
      <Navbar />
      <main className="max-w-7xl mx-auto px-3 sm:px-4 py-4 sm:py-6">{children}</main>

      {/* Shortcuts overlay */}
      {showShortcuts && <ShortcutsOverlay onClose={() => setShowShortcuts(false)} />}

      {/* Shortcuts hint */}
      <button
        onClick={() => setShowShortcuts(true)}
        className="fixed bottom-4 right-4 z-40 px-2.5 py-1 rounded-lg bg-[#111827]/80 border border-[#374151]/50 text-[10px] text-gray-500 hover:text-gray-300 transition backdrop-blur-sm"
      >
        <kbd className="font-mono">?</kbd> Shortcuts
      </button>
    </>
  );
}
