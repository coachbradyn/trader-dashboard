"use client";
import { useState, useEffect, useCallback } from "react";
import { usePathname, useRouter } from "next/navigation";
import { api } from "@/lib/api";
import { Sidebar, MobileSidebar, TopBar, useSidebarCollapsed } from "@/components/ui/sidebar";

const FONT_MONO = { fontFamily: "'JetBrains Mono', monospace" } as const;

// ── Shortcuts Overlay ─────────────────────────────────────────────
const SHORTCUTS = [
  { key: "d", label: "Home" },
  { key: "w", label: "Watchlist" },
  { key: "s", label: "Scanner" },
  { key: "p", label: "Portfolios" },
  { key: "h", label: "Henry" },
  { key: "[", label: "Toggle sidebar" },
  { key: "?", label: "Toggle this overlay" },
];

function ShortcutsOverlay({ onClose }: { onClose: () => void }) {
  return (
    <div
      className="fixed inset-0 z-[100] flex items-center justify-center bg-black/60 backdrop-blur-sm"
      onClick={onClose}
      role="dialog"
      aria-modal="true"
      aria-label="Keyboard shortcuts"
    >
      <div
        className="bg-[#111827] border border-[#374151] rounded-xl p-6 shadow-2xl max-w-xs w-full"
        onClick={(e) => e.stopPropagation()}
      >
        <h3 className="text-sm font-semibold text-white mb-4">Keyboard Shortcuts</h3>
        <div className="space-y-2">
          {SHORTCUTS.map((s) => (
            <div key={s.key} className="flex items-center justify-between">
              <span className="text-xs text-gray-400">{s.label}</span>
              <kbd
                className="px-2 py-0.5 rounded bg-[#1f2937] border border-[#374151] text-[11px] font-mono text-gray-300"
                style={FONT_MONO}
              >
                {s.key}
              </kbd>
            </div>
          ))}
        </div>
        <p className="text-[10px] text-gray-600 mt-4">
          Press <kbd className="px-1 rounded bg-[#1f2937] border border-[#374151] text-gray-400">?</kbd> or{" "}
          <kbd className="px-1 rounded bg-[#1f2937] border border-[#374151] text-gray-400">Esc</kbd> to close
        </p>
      </div>
    </div>
  );
}

// ── KillSwitch Banner ─────────────────────────────────────────────
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
      <div className="flex items-center justify-between h-8 text-[11px] font-mono" style={FONT_MONO}>
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

// ── MetricTooltip (preserved — used elsewhere) ────────────────────
export function MetricTooltip({ tip, children }: { label?: string; tip: string; children: React.ReactNode }) {
  const [show, setShow] = useState(false);
  return (
    <span
      className="relative inline-flex"
      onMouseEnter={() => setShow(true)}
      onMouseLeave={() => setShow(false)}
    >
      {children}
      {show && (
        <span className="absolute bottom-full left-1/2 -translate-x-1/2 mb-2 px-3 py-1.5 rounded-lg bg-[#1f2937] border border-[#374151] text-[10px] text-gray-300 whitespace-nowrap z-50 shadow-xl">
          {tip}
        </span>
      )}
    </span>
  );
}

// ── LayoutShell ───────────────────────────────────────────────────
export function LayoutShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const isLogin = pathname === "/login";
  const [showShortcuts, setShowShortcuts] = useState(false);
  const [collapsed, setCollapsed] = useSidebarCollapsed();
  const [mobileOpen, setMobileOpen] = useState(false);

  // Global keyboard shortcuts
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement)?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || (e.target as HTMLElement)?.isContentEditable) return;

      switch (e.key) {
        case "d": router.push("/ai"); break;
        case "w": router.push("/screener"); break;
        case "s": router.push("/scanner"); break;
        case "p": router.push("/portfolios"); break;
        case "h": router.push("/henry"); break;
        case "[": setCollapsed(!collapsed); break;
        case "?": setShowShortcuts((v) => !v); break;
        case "Escape": setShowShortcuts(false); break;
        default: return;
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [router, collapsed, setCollapsed]);

  if (isLogin) {
    return <>{children}</>;
  }

  return (
    <div className="min-h-screen flex">
      <Sidebar
        collapsed={collapsed}
        onToggle={() => setCollapsed(!collapsed)}
      />
      <MobileSidebar open={mobileOpen} onClose={() => setMobileOpen(false)} />

      <div className="flex-1 min-w-0 flex flex-col">
        <KillSwitchBanner />
        <TopBar onMobileMenuOpen={() => setMobileOpen(true)} />

        <main className="flex-1 px-3 sm:px-5 py-4 sm:py-6 relative">
          {children}
        </main>

        {/* Shortcuts hint */}
        <button
          onClick={() => setShowShortcuts(true)}
          className="fixed bottom-4 right-4 z-40 px-2.5 py-1 rounded-lg bg-[#111827]/80 border border-[#374151]/50 text-[10px] text-gray-500 hover:text-gray-300 transition backdrop-blur-sm"
          style={FONT_MONO}
        >
          <kbd className="font-mono">?</kbd> Shortcuts
        </button>

        {showShortcuts && <ShortcutsOverlay onClose={() => setShowShortcuts(false)} />}
      </div>
    </div>
  );
}
