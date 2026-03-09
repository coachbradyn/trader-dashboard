"use client";

import MorningBriefing from "@/components/ai/MorningBriefing";
import TradeReview from "@/components/ai/TradeReview";
import AskHenry from "@/components/ai/AskHenry";
import ConflictLog from "@/components/ai/ConflictLog";

export default function AIAnalysisPage() {
  return (
    <div className="space-y-6">
      {/* Page header */}
      <div className="flex items-center gap-3">
        <div className="w-10 h-10 rounded-xl bg-ai-blue/10 flex items-center justify-center">
          <svg className="w-5 h-5 text-ai-blue" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M9.75 17L9 20l-1 1h8l-1-1-.75-3M3 13h18M5 17h14a2 2 0 002-2V5a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z" />
          </svg>
        </div>
        <div>
          <h1 className="text-xl font-bold text-white">AI Analysis</h1>
          <p className="text-xs text-gray-500">
            Powered by Henry — your AI trading co-pilot
          </p>
        </div>
      </div>

      {/* Hero: Morning Briefing (full width) */}
      <MorningBriefing />

      {/* Middle row: Trade Review (40%) + Ask Henry (60%) */}
      <div className="grid grid-cols-1 lg:grid-cols-5 gap-4 lg:gap-6">
        <div className="lg:col-span-2">
          <TradeReview />
        </div>
        <div className="lg:col-span-3">
          <AskHenry />
        </div>
      </div>

      {/* Bottom: Conflict Log (full width) */}
      <ConflictLog />
    </div>
  );
}
