"use client";

import { useState } from "react";
import MorningBriefing from "@/components/ai/MorningBriefing";
import TradeReview from "@/components/ai/TradeReview";
import AskHenry from "@/components/ai/AskHenry";
import ConflictLog from "@/components/ai/ConflictLog";
import LiveTradeFeed from "@/components/dashboard/LiveTradeFeed";

const TABS = [
  {
    id: "briefing",
    label: "Morning Briefing",
    icon: (
      <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
        <path strokeLinecap="round" strokeLinejoin="round" d="M12 3v2.25m6.364.386l-1.591 1.591M21 12h-2.25m-.386 6.364l-1.591-1.591M12 18.75V21m-4.773-4.227l-1.591 1.591M5.25 12H3m4.227-4.773L5.636 5.636M15.75 12a3.75 3.75 0 11-7.5 0 3.75 3.75 0 017.5 0z" />
      </svg>
    ),
  },
  {
    id: "review",
    label: "Trade Review",
    icon: (
      <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
        <path strokeLinecap="round" strokeLinejoin="round" d="M3 13.125C3 12.504 3.504 12 4.125 12h2.25c.621 0 1.125.504 1.125 1.125v6.75C7.5 20.496 6.996 21 6.375 21h-2.25A1.125 1.125 0 013 19.875v-6.75zM9.75 8.625c0-.621.504-1.125 1.125-1.125h2.25c.621 0 1.125.504 1.125 1.125v11.25c0 .621-.504 1.125-1.125 1.125h-2.25a1.125 1.125 0 01-1.125-1.125V8.625zM16.5 4.125c0-.621.504-1.125 1.125-1.125h2.25C20.496 3 21 3.504 21 4.125v15.75c0 .621-.504 1.125-1.125 1.125h-2.25a1.125 1.125 0 01-1.125-1.125V4.125z" />
      </svg>
    ),
  },
  {
    id: "ask",
    label: "Ask Henry",
    icon: (
      <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
        <path strokeLinecap="round" strokeLinejoin="round" d="M7.5 8.25h9m-9 3H12m-9.75 1.51c0 1.6 1.123 2.994 2.707 3.227 1.129.166 2.27.293 3.423.379.35.026.67.21.865.501L12 21l2.755-4.133a1.14 1.14 0 01.865-.501 48.172 48.172 0 003.423-.379c1.584-.233 2.707-1.626 2.707-3.228V6.741c0-1.602-1.123-2.995-2.707-3.228A48.394 48.394 0 0012 3c-2.392 0-4.744.175-7.043.513C3.373 3.746 2.25 5.14 2.25 6.741v6.018z" />
      </svg>
    ),
  },
  {
    id: "conflicts",
    label: "Conflicts",
    icon: (
      <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
        <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v3.75m-9.303 3.376c-.866 1.5.217 3.374 1.948 3.374h14.71c1.73 0 2.813-1.874 1.948-3.374L13.949 3.378c-.866-1.5-3.032-1.5-3.898 0L2.697 16.126zM12 15.75h.007v.008H12v-.008z" />
      </svg>
    ),
  },
  {
    id: "feed",
    label: "Live Feed",
    icon: (
      <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
        <path strokeLinecap="round" strokeLinejoin="round" d="M3.75 12h16.5m-16.5 3.75h16.5M3.75 19.5h16.5M5.625 4.5h12.75a1.875 1.875 0 010 3.75H5.625a1.875 1.875 0 010-3.75z" />
      </svg>
    ),
    dot: "bg-profit",
  },
];

export default function HomePage() {
  const [activeTab, setActiveTab] = useState("briefing");

  return (
    <div className="flex flex-col lg:flex-row gap-0 lg:gap-6 -mx-3 sm:-mx-4 lg:mx-0">
      {/* ── Mobile: Horizontal scrollable tab bar ── */}
      <div className="lg:hidden overflow-x-auto border-b border-border bg-surface/60 backdrop-blur sticky top-14 z-40">
        <div className="flex min-w-max px-3 sm:px-4">
          {TABS.map((tab) => (
            <button
              key={tab.id}
              onClick={() => setActiveTab(tab.id)}
              className={`flex items-center gap-2 px-4 py-3 text-xs font-medium whitespace-nowrap border-b-2 transition ${
                activeTab === tab.id
                  ? "text-white border-ai-blue"
                  : "text-gray-500 border-transparent hover:text-gray-300"
              }`}
            >
              {tab.dot && (
                <span className="relative flex h-1.5 w-1.5">
                  <span className={`animate-ping absolute inline-flex h-full w-full rounded-full ${tab.dot} opacity-75`} />
                  <span className={`relative inline-flex rounded-full h-1.5 w-1.5 ${tab.dot}`} />
                </span>
              )}
              <span className={activeTab === tab.id ? "text-ai-blue" : "text-gray-600"}>{tab.icon}</span>
              {tab.label}
            </button>
          ))}
        </div>
      </div>

      {/* ── Desktop: Left sidebar ── */}
      <aside className="hidden lg:flex flex-col w-52 shrink-0 sticky top-20 self-start">
        <div className="mb-4">
          <div className="flex items-center gap-2.5">
            <div className="w-8 h-8 rounded-lg bg-ai-blue/10 flex items-center justify-center">
              <svg className="w-4 h-4 text-ai-blue" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M9.75 17L9 20l-1 1h8l-1-1-.75-3M3 13h18M5 17h14a2 2 0 002-2V5a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z" />
              </svg>
            </div>
            <div>
              <h1 className="text-sm font-bold text-white leading-tight">Henry&apos;s Desk</h1>
              <p className="text-[10px] text-gray-500">AI Trading Co-Pilot</p>
            </div>
          </div>
        </div>

        <nav className="flex flex-col gap-0.5">
          {TABS.map((tab) => (
            <button
              key={tab.id}
              onClick={() => setActiveTab(tab.id)}
              className={`flex items-center gap-2.5 px-3 py-2.5 rounded-lg text-sm text-left transition ${
                activeTab === tab.id
                  ? "text-white bg-surface-light/50 border border-border"
                  : "text-gray-500 hover:text-gray-300 hover:bg-surface-light/20"
              }`}
            >
              <span className={activeTab === tab.id ? "text-ai-blue" : "text-gray-600"}>
                {tab.icon}
              </span>
              {tab.label}
              {tab.dot && (
                <span className="relative flex h-1.5 w-1.5 ml-auto">
                  <span className={`animate-ping absolute inline-flex h-full w-full rounded-full ${tab.dot} opacity-75`} />
                  <span className={`relative inline-flex rounded-full h-1.5 w-1.5 ${tab.dot}`} />
                </span>
              )}
            </button>
          ))}
        </nav>
      </aside>

      {/* ── Content area ── */}
      <div className="flex-1 min-w-0 px-3 sm:px-4 lg:px-0 pt-4 lg:pt-0">
        {activeTab === "briefing" && <MorningBriefing />}
        {activeTab === "review" && <TradeReview />}
        {activeTab === "ask" && <AskHenry />}
        {activeTab === "conflicts" && <ConflictLog />}
        {activeTab === "feed" && (
          <div>
            <div className="mb-4">
              <h2 className="text-lg font-bold text-white">Live Trade Feed</h2>
              <p className="text-xs text-gray-500 mt-1">
                Real-time entries and exits — updates every 5 seconds
              </p>
            </div>
            <LiveTradeFeed limit={100} />
          </div>
        )}
      </div>
    </div>
  );
}
