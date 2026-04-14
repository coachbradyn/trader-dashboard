"use client";
import { useState, useEffect, useRef, useCallback } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import {
  Home, Eye, Briefcase, Radar, Brain, Trophy, Activity,
  Settings as SettingsIcon, Bell, PanelLeftClose, PanelLeftOpen,
  Menu, X, ArrowUpRight, ArrowDownRight, Zap, Clock,
  FileText, Database, AlertTriangle, Search,
} from "lucide-react";
import { api } from "@/lib/api";

const FONT_OUTFIT = { fontFamily: "'Outfit', sans-serif" } as const;
const FONT_MONO = { fontFamily: "'JetBrains Mono', monospace" } as const;

// ── Navigation model ─────────────────────────────────────────────
export const NAV_ITEMS = [
  { href: "/ai", label: "Home", icon: Home, shortcut: "d" },
  { href: "/screener", label: "Watchlist", icon: Eye, shortcut: "w" },
  { href: "/portfolios", label: "Portfolios", icon: Briefcase, shortcut: "p" },
  { href: "/scanner", label: "Scanner", icon: Radar, shortcut: "s" },
  { href: "/henry", label: "Henry", icon: Brain, shortcut: "h" },
  { href: "/leaderboard", label: "Leaderboard", icon: Trophy },
  { href: "/feed", label: "Live Feed", icon: Activity },
] as const;

// ── Storage key for collapsed preference ─────────────────────────
const STORAGE_KEY = "henry_sidebar_collapsed";

export function useSidebarCollapsed(): [boolean, (v: boolean) => void] {
  const [collapsed, setCollapsed] = useState<boolean>(false);

  useEffect(() => {
    try {
      const v = window.localStorage.getItem(STORAGE_KEY);
      if (v === "1") setCollapsed(true);
    } catch {}
  }, []);

  const update = useCallback((v: boolean) => {
    setCollapsed(v);
    try {
      window.localStorage.setItem(STORAGE_KEY, v ? "1" : "0");
    } catch {}
  }, []);

  return [collapsed, update];
}

// ── Active link matcher ──────────────────────────────────────────
function isActive(pathname: string, href: string): boolean {
  if (href === "/ai") return pathname === "/ai" || pathname === "/";
  return pathname === href || pathname.startsWith(href + "/");
}

// ── Notification routing map ─────────────────────────────────────
// Map activity_type -> route. Unknown types default to /henry (activity tab).
const ACTIVITY_ROUTE: Record<string, string> = {
  trade_execute: "/feed",
  trade_exit: "/feed",
  trade_error: "/feed",
  trade_skip: "/feed",
  scan_start: "/scanner",
  scan_result: "/scanner",
  scan_profile: "/scanner",
  pattern_detect: "/screener",
  analysis: "/henry",
  error: "/henry",
  status: "/henry",
  briefing: "/ai",
  memory: "/henry",
  action_pending: "/portfolio-manager",
};

function routeForActivityType(type: string): string {
  return ACTIVITY_ROUTE[type] || "/henry";
}

// ── Icon picker for notification types ───────────────────────────
function iconForActivityType(type: string) {
  switch (type) {
    case "trade_execute":
      return { Icon: ArrowUpRight, tone: "text-profit" };
    case "trade_exit":
      return { Icon: ArrowDownRight, tone: "text-loss" };
    case "trade_error":
    case "error":
      return { Icon: AlertTriangle, tone: "text-loss" };
    case "scan_start":
    case "scan_result":
    case "scan_profile":
      return { Icon: Search, tone: "text-ai-blue" };
    case "pattern_detect":
      return { Icon: Zap, tone: "text-amber-400" };
    case "analysis":
      return { Icon: Brain, tone: "text-ai-blue" };
    case "action_pending":
      return { Icon: Clock, tone: "text-amber-400" };
    case "briefing":
      return { Icon: FileText, tone: "text-ai-blue" };
    case "memory":
      return { Icon: Database, tone: "text-ai-purple" };
    default:
      return { Icon: Bell, tone: "text-gray-400" };
  }
}

function formatTimeAgoShort(dateStr: string): string {
  const diffMin = Math.floor((Date.now() - new Date(dateStr).getTime()) / 60000);
  if (diffMin < 1) return "just now";
  if (diffMin < 60) return `${diffMin}m ago`;
  const diffH = Math.floor(diffMin / 60);
  if (diffH < 24) return `${diffH}h ago`;
  return `${Math.floor(diffH / 24)}d ago`;
}

// ── Unified notification bell ────────────────────────────────────
interface NotifItem {
  id: string;
  activityType: string;
  ticker: string | null;
  message: string;
  time: string;
}

export function NotificationBell() {
  const [open, setOpen] = useState(false);
  const [items, setItems] = useState<NotifItem[]>([]);
  const [pendingActions, setPendingActions] = useState<number>(0);
  const [lastSeen, setLastSeen] = useState<string>(() => {
    if (typeof window === "undefined") return new Date(0).toISOString();
    return window.localStorage.getItem("notif_lastSeenAt") || new Date(0).toISOString();
  });
  const ref = useRef<HTMLDivElement>(null);
  const router = useRouter();

  const load = useCallback(async () => {
    try {
      const [activity, actions] = await Promise.all([
        api.getHenryActivity(20).catch(() => []),
        api.getActions("pending").catch(() => []),
      ]);
      const notifs: NotifItem[] = activity.map((a) => ({
        id: a.id,
        activityType: a.activity_type,
        ticker: a.ticker,
        message: a.message,
        time: a.created_at,
      }));
      if (actions.length > 0) {
        notifs.unshift({
          id: "pending-actions",
          activityType: "action_pending",
          ticker: null,
          message: `${actions.length} pending action${actions.length !== 1 ? "s" : ""} awaiting review`,
          time: actions[0]?.created_at || new Date().toISOString(),
        });
      }
      setItems(notifs);
      setPendingActions(actions.length);
    } catch {}
  }, []);

  useEffect(() => {
    load();
    const iv = setInterval(load, 30000);
    return () => clearInterval(iv);
  }, [load]);

  useEffect(() => {
    if (!open) return;
    const onClick = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onClick);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onClick);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const handleOpen = () => {
    const next = !open;
    setOpen(next);
    if (next) {
      const now = new Date().toISOString();
      setLastSeen(now);
      try { window.localStorage.setItem("notif_lastSeenAt", now); } catch {}
    }
  };

  const handleClickItem = (item: NotifItem) => {
    // Route based on activity type. If a ticker is present and it's a
    // signal/pattern notification, route to the ticker page.
    let href = routeForActivityType(item.activityType);
    if (item.ticker && (item.activityType === "pattern_detect" || item.activityType === "analysis")) {
      href = `/screener/${item.ticker}`;
    }
    setOpen(false);
    router.push(href);
  };

  const unreadCount = items.filter((i) => new Date(i.time) > new Date(lastSeen)).length + pendingActions;

  return (
    <div className="relative" ref={ref}>
      <button
        onClick={handleOpen}
        className="relative flex items-center justify-center w-9 h-9 rounded-lg text-gray-400 hover:text-white hover:bg-[#1f2937]/50 transition"
        aria-label={`Notifications${unreadCount > 0 ? ` (${unreadCount} new)` : ""}`}
        aria-expanded={open}
        aria-haspopup="menu"
      >
        <Bell className="w-4 h-4" strokeWidth={1.75} />
        {unreadCount > 0 && (
          <span
            className="absolute -top-0.5 -right-0.5 min-w-[16px] h-[16px] px-1 flex items-center justify-center rounded-full bg-loss text-[9px] font-bold text-white"
            style={FONT_MONO}
          >
            {unreadCount > 9 ? "9+" : unreadCount}
          </span>
        )}
      </button>
      {open && (
        <div
          role="menu"
          aria-label="Notifications"
          className="absolute right-0 top-full mt-2 w-[340px] max-h-[480px] overflow-y-auto rounded-xl bg-[#111827] border border-[#374151] shadow-2xl z-[70]"
        >
          <div className="px-4 py-2.5 border-b border-[#374151] flex items-center justify-between">
            <span className="text-xs font-semibold text-white" style={FONT_OUTFIT}>Notifications</span>
            {unreadCount > 0 && (
              <span className="text-[10px] text-ai-blue font-mono" style={FONT_MONO}>{unreadCount} new</span>
            )}
          </div>
          {items.length === 0 ? (
            <div className="px-4 py-10 text-center text-xs text-gray-500">No recent activity</div>
          ) : (
            <ul className="divide-y divide-[#1f2937]">
              {items.map((item) => {
                const isNew = new Date(item.time) > new Date(lastSeen);
                const { Icon, tone } = iconForActivityType(item.activityType);
                return (
                  <li key={item.id}>
                    <button
                      onClick={() => handleClickItem(item)}
                      className={`w-full text-left px-4 py-2.5 hover:bg-[#1f2937]/60 transition flex gap-3 items-start ${
                        isNew ? "bg-[#1f2937]/30" : ""
                      }`}
                    >
                      <Icon className={`w-4 h-4 shrink-0 mt-0.5 ${tone}`} strokeWidth={1.75} />
                      <div className="min-w-0 flex-1">
                        <p className="text-[11px] text-gray-300 leading-relaxed line-clamp-2">
                          {item.message}
                        </p>
                        <div className="flex items-center gap-2 mt-0.5">
                          {item.ticker && (
                            <span className="text-[9px] font-mono text-white bg-[#1f2937] px-1.5 py-[1px] rounded" style={FONT_MONO}>
                              {item.ticker}
                            </span>
                          )}
                          <span className="text-[9px] text-gray-600 font-mono" style={FONT_MONO}>
                            {formatTimeAgoShort(item.time)}
                          </span>
                        </div>
                      </div>
                      {isNew && <span className="w-1.5 h-1.5 rounded-full bg-ai-blue shrink-0 mt-1.5" />}
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}

// ── Sidebar nav link ─────────────────────────────────────────────
function SidebarLink({
  item,
  collapsed,
  active,
  onClick,
}: {
  item: typeof NAV_ITEMS[number];
  collapsed: boolean;
  active: boolean;
  onClick?: () => void;
}) {
  const Icon = item.icon;
  return (
    <Link
      href={item.href}
      onClick={onClick}
      aria-current={active ? "page" : undefined}
      className={`group relative flex items-center gap-3 px-3 py-2 rounded-lg text-[13px] transition-colors ${
        active
          ? "bg-[#1f2937]/70 text-white"
          : "text-gray-400 hover:text-white hover:bg-[#1f2937]/40"
      }`}
      style={FONT_OUTFIT}
      title={collapsed ? item.label : undefined}
    >
      {active && (
        <span
          aria-hidden
          className="absolute left-0 top-1.5 bottom-1.5 w-[2px] rounded-r bg-ai-blue"
        />
      )}
      <Icon className="w-[18px] h-[18px] shrink-0" strokeWidth={1.75} />
      {!collapsed && (
        <>
          <span className="flex-1 truncate">{item.label}</span>
          {"shortcut" in item && item.shortcut && (
            <kbd
              className="hidden group-hover:inline-block text-[9px] px-1.5 py-0.5 rounded bg-[#0a0a0f]/60 border border-[#374151]/50 text-gray-500 font-mono"
              style={FONT_MONO}
            >
              {item.shortcut}
            </kbd>
          )}
        </>
      )}
      {collapsed && (
        <span
          role="tooltip"
          className="pointer-events-none absolute left-full ml-2 top-1/2 -translate-y-1/2 px-2 py-1 rounded-md bg-[#0a0a0f] border border-[#374151] text-[11px] text-white whitespace-nowrap opacity-0 group-hover:opacity-100 transition-opacity z-50"
          style={FONT_OUTFIT}
        >
          {item.label}
        </span>
      )}
    </Link>
  );
}

// ── Sidebar logo/brand ───────────────────────────────────────────
function Brand({ collapsed }: { collapsed: boolean }) {
  return (
    <Link href="/ai" className="flex items-center gap-2 px-3 py-4 text-white" style={FONT_OUTFIT}>
      <span className="flex items-center justify-center w-7 h-7 rounded-md bg-gradient-to-br from-ai-blue to-ai-purple text-white font-bold text-sm shrink-0">
        H
      </span>
      {!collapsed && (
        <span className="font-bold text-[15px] tracking-tight truncate">
          Henry <span className="text-gray-500 font-medium">AI Trader</span>
        </span>
      )}
    </Link>
  );
}

// ── Main Sidebar ─────────────────────────────────────────────────
export function Sidebar({
  collapsed,
  onToggle,
  onNavigate,
}: {
  collapsed: boolean;
  onToggle: () => void;
  onNavigate?: () => void;
}) {
  const pathname = usePathname();

  return (
    <aside
      className={`hidden md:flex flex-col shrink-0 border-r border-[#1f2937] bg-[#0a0a0f]/80 backdrop-blur-xl h-screen sticky top-0 transition-[width] duration-200 ease-out ${
        collapsed ? "w-[56px]" : "w-[220px]"
      }`}
      aria-label="Primary"
    >
      <Brand collapsed={collapsed} />

      <div className="px-2">
        <button
          onClick={onToggle}
          className="w-full flex items-center gap-2 px-2.5 py-1.5 rounded-md text-[11px] text-gray-500 hover:text-gray-300 hover:bg-[#1f2937]/40 transition"
          aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
          style={FONT_MONO}
        >
          {collapsed ? (
            <PanelLeftOpen className="w-3.5 h-3.5" strokeWidth={1.75} />
          ) : (
            <>
              <PanelLeftClose className="w-3.5 h-3.5" strokeWidth={1.75} />
              <span>Collapse</span>
              <kbd className="ml-auto px-1 rounded bg-[#1f2937] border border-[#374151] text-gray-500 text-[9px]">[</kbd>
            </>
          )}
        </button>
      </div>

      <nav className="flex-1 px-2 py-3 space-y-1 overflow-y-auto" aria-label="Main navigation">
        {NAV_ITEMS.map((item) => (
          <SidebarLink
            key={item.href}
            item={item}
            collapsed={collapsed}
            active={isActive(pathname, item.href)}
            onClick={onNavigate}
          />
        ))}
      </nav>

      {/* Footer */}
      <div className="border-t border-[#1f2937] p-2 flex items-center gap-2 justify-between">
        {!collapsed && (
          <div className="flex items-center gap-2 text-[10px] text-gray-500 font-mono px-2" style={FONT_MONO}>
            <span className="w-1.5 h-1.5 rounded-full bg-profit animate-pulse" />
            Live
          </div>
        )}
        <div className={collapsed ? "mx-auto" : ""}>
          <NotificationBell />
        </div>
      </div>
    </aside>
  );
}

// ── Mobile drawer ────────────────────────────────────────────────
export function MobileSidebar({
  open,
  onClose,
}: {
  open: boolean;
  onClose: () => void;
}) {
  const pathname = usePathname();

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div className="md:hidden fixed inset-0 z-[80]" role="dialog" aria-modal="true" aria-label="Navigation">
      <div
        className="absolute inset-0 bg-black/60 backdrop-blur-sm animate-fade-in"
        onClick={onClose}
      />
      <aside
        className="absolute left-0 top-0 bottom-0 w-[260px] bg-[#0a0a0f] border-r border-[#1f2937] flex flex-col animate-slide-up-panel"
      >
        <div className="flex items-center justify-between px-3 py-3 border-b border-[#1f2937]">
          <Brand collapsed={false} />
          <button
            onClick={onClose}
            className="p-1.5 rounded text-gray-400 hover:text-white"
            aria-label="Close navigation"
          >
            <X className="w-5 h-5" strokeWidth={1.75} />
          </button>
        </div>
        <nav className="flex-1 px-2 py-3 space-y-1 overflow-y-auto">
          {NAV_ITEMS.map((item) => (
            <SidebarLink
              key={item.href}
              item={item}
              collapsed={false}
              active={isActive(pathname, item.href)}
              onClick={onClose}
            />
          ))}
        </nav>
      </aside>
    </div>
  );
}

// ── TopBar (right-of-sidebar) ────────────────────────────────────
// Thin strip at the top of the main content. Contains page title slot,
// mobile hamburger, notification bell, and settings cog.
export function TopBar({
  onMobileMenuOpen,
}: {
  onMobileMenuOpen: () => void;
}) {
  const pathname = usePathname();
  const title = pageTitle(pathname);
  return (
    <div
      className="sticky top-0 z-40 h-12 bg-[#0a0a0f]/75 backdrop-blur-md border-b border-[#1f2937] flex items-center gap-3 px-3 sm:px-4"
    >
      {/* Mobile hamburger */}
      <button
        onClick={onMobileMenuOpen}
        className="md:hidden p-1.5 rounded text-gray-400 hover:text-white"
        aria-label="Open menu"
      >
        <Menu className="w-5 h-5" strokeWidth={1.75} />
      </button>
      <div className="flex-1 min-w-0">
        <span
          className="text-[13px] font-semibold text-white tracking-tight truncate"
          style={FONT_OUTFIT}
        >
          {title}
        </span>
      </div>

      {/* Right cluster */}
      <div className="flex items-center gap-1">
        {/* Mobile bell — on desktop it lives in sidebar footer */}
        <div className="md:hidden">
          <NotificationBell />
        </div>
        <Link
          href="/settings"
          className="flex items-center justify-center w-9 h-9 rounded-lg text-gray-400 hover:text-white hover:bg-[#1f2937]/50 transition"
          aria-label="Settings"
        >
          <SettingsIcon className="w-4 h-4" strokeWidth={1.75} />
        </Link>
      </div>
    </div>
  );
}

function pageTitle(pathname: string): string {
  for (const item of NAV_ITEMS) {
    if (isActive(pathname, item.href)) return item.label;
  }
  if (pathname.startsWith("/settings")) return "Settings";
  if (pathname.startsWith("/portfolios/")) return "Portfolio";
  if (pathname.startsWith("/screener/")) return "Watchlist";
  return "";
}
