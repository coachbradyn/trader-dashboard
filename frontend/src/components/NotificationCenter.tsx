"use client";

import { useState, useEffect, useRef } from "react";
import { api } from "@/lib/api";

const FONT_MONO = { fontFamily: "'JetBrains Mono', monospace" } as const;

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

interface NotifItem {
  id: string;
  icon: string;
  message: string;
  time: string;
  type: string;
}

const CRITICAL_TYPES = ["trade_execute", "error", "trade_error", "execution_error", "kill_switch"];

export default function NotificationCenter() {
  const [open, setOpen] = useState(false);
  const [items, setItems] = useState<NotifItem[]>([]);
  const [lastSeenAt, setLastSeenAt] = useState<string>(() => {
    if (typeof window !== "undefined") {
      return localStorage.getItem("notif_lastSeenAt") || new Date(0).toISOString();
    }
    return new Date(0).toISOString();
  });
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let mounted = true;
    const load = async () => {
      try {
        const activity = await api.getHenryActivity(20);
        const notifs: NotifItem[] = activity.map((a) => ({
          id: a.id,
          icon: a.activity_label || "🔔",
          message: a.message,
          time: a.created_at,
          type: a.activity_type,
        }));
        if (mounted) setItems(notifs);
      } catch {}
    };
    load();
    const iv = setInterval(load, 30000);
    return () => { mounted = false; clearInterval(iv); };
  }, []);

  useEffect(() => {
    if (!open) return;
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [open]);

  const handleOpen = () => {
    setOpen(!open);
    if (!open) {
      const now = new Date().toISOString();
      setLastSeenAt(now);
      localStorage.setItem("notif_lastSeenAt", now);
    }
  };

  const criticalItems = items.filter((i) => CRITICAL_TYPES.includes(i.type));
  const unseenCount = criticalItems.filter((i) => new Date(i.time) > new Date(lastSeenAt)).length;

  return (
    <div className="relative" ref={ref}>
      <button
        onClick={handleOpen}
        className="relative p-1.5 text-gray-400 hover:text-white transition"
        aria-label="Notifications"
      >
        <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
          <path strokeLinecap="round" strokeLinejoin="round" d="M14.857 17.082a23.848 23.848 0 005.454-1.31A8.967 8.967 0 0118 9.75v-.7V9A6 6 0 006 9v.75a8.967 8.967 0 01-2.312 6.022c1.733.64 3.56 1.085 5.455 1.31m5.714 0a24.255 24.255 0 01-5.714 0m5.714 0a3 3 0 11-5.714 0" />
        </svg>
        {unseenCount > 0 && (
          <span className="absolute -top-0.5 -right-0.5 w-2 h-2 rounded-full bg-loss" />
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
              {items.map((item) => {
                const isNew = new Date(item.time) > new Date(lastSeenAt);
                return (
                  <div key={item.id} className={`px-4 py-2.5 hover:bg-[#1f2937]/50 transition ${isNew ? "bg-[#1f2937]/30" : ""}`}>
                    <div className="flex gap-2.5 items-start">
                      <span className="text-sm shrink-0 mt-0.5">{item.icon}</span>
                      <div className="min-w-0 flex-1">
                        <p className="text-[11px] text-gray-300 leading-relaxed line-clamp-2">{item.message}</p>
                        <p className="text-[9px] text-gray-600 mt-0.5 font-mono" style={FONT_MONO}>{formatTimeAgoShort(item.time)}</p>
                      </div>
                      {isNew && <span className="w-1.5 h-1.5 rounded-full bg-ai-blue shrink-0 mt-1.5" />}
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
