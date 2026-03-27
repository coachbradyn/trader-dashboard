"use client";

import { useState, useRef, useEffect, useCallback } from "react";
import { api } from "@/lib/api";
import { renderMarkdown } from "@/lib/markdown";
import type { QueryHistoryItem } from "@/lib/types";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

const SUGGESTIONS = [
  "Which strategy has the best Sharpe?",
  "Show me my worst trades this week",
  "What are my open positions?",
];

function StrategyBadges() {
  const [strategies, setStrategies] = useState<string[]>([]);
  useEffect(() => {
    api.getTraders().then((traders) => {
      setStrategies(traders.map((t) => t.trader_id));
    }).catch(() => {});
  }, []);
  if (!strategies.length) return null;
  return (
    <div className="flex items-center gap-1.5">
      {strategies.map((s) => (
        <span
          key={s}
          className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-surface-light/60 text-gray-500"
        >
          {s}
        </span>
      ))}
    </div>
  );
}

let queryId = 0;

export default function AskHenry() {
  const [input, setInput] = useState("");
  const [history, setHistory] = useState<QueryHistoryItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [strategies, setStrategies] = useState<Array<{ trader_id: string; display_name: string }>>([]);
  const viewportRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    api.getTraders().then((traders) => {
      setStrategies(traders.map((t) => ({ trader_id: t.trader_id, display_name: t.display_name })));
    }).catch(() => {});
  }, []);

  const scrollToBottom = useCallback(() => {
    if (viewportRef.current) {
      viewportRef.current.scrollTop = viewportRef.current.scrollHeight;
    }
  }, []);

  useEffect(() => {
    scrollToBottom();
  }, [history, loading, scrollToBottom]);

  const submitQuery = async (question: string) => {
    if (!question.trim() || loading) return;

    const q = question.trim();
    setInput("");
    setLoading(true);

    // Add question placeholder
    const id = `q-${++queryId}`;
    setHistory((prev) => [
      ...prev,
      { id, question: q, answer: "", timestamp: new Date() },
    ]);

    try {
      const result = await api.postQuery(q);
      setHistory((prev) =>
        prev.map((item) =>
          item.id === id ? { ...item, answer: result.answer } : item
        )
      );
    } catch (e) {
      setHistory((prev) =>
        prev.map((item) =>
          item.id === id
            ? {
                ...item,
                answer: `Error: ${e instanceof Error ? e.message : "Failed to get response"}`,
              }
            : item
        )
      );
    } finally {
      setLoading(false);
      inputRef.current?.focus();
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      submitQuery(input);
    }
  };

  return (
    <div className="ai-card h-full flex flex-col">
      {/* Header */}
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-3">
          <div className="w-8 h-8 rounded-lg bg-ai-purple/10 flex items-center justify-center">
            <svg className="w-4 h-4 text-ai-purple" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M8 9l3 3-3 3m5 0h3M5 20h14a2 2 0 002-2V6a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z" />
            </svg>
          </div>
          <h3 className="text-base font-semibold text-white">Ask Henry</h3>
        </div>
        <StrategyBadges />
      </div>

      {/* Terminal viewport */}
      <div
        ref={viewportRef}
        className="terminal-viewport flex-1 min-h-0 rounded-lg border border-border/50 overflow-y-auto p-4"
        style={{ minHeight: "350px" }}
      >
        {/* Empty state with suggestions */}
        {history.length === 0 && !loading && (
          <div className="h-full flex flex-col items-center justify-center">
            <div className="w-14 h-14 rounded-full bg-ai-purple/10 flex items-center justify-center mb-4">
              <svg className="w-7 h-7 text-ai-purple/40" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M8 9l3 3-3 3m5 0h3M5 20h14a2 2 0 002-2V6a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z" />
              </svg>
            </div>
            <p className="text-sm text-gray-500 mb-4">
              Ask anything about your trading data
            </p>
            <div className="flex flex-wrap gap-2 justify-center max-w-md">
              {SUGGESTIONS.map((s) => (
                <button
                  key={s}
                  onClick={() => submitQuery(s)}
                  className="px-3 py-1.5 rounded-full text-xs font-mono
                             bg-ai-purple/8 text-ai-purple/70 border border-ai-purple/15
                             hover:bg-ai-purple/15 hover:text-ai-purple hover:border-ai-purple/30
                             transition-all duration-200"
                >
                  {s}
                </button>
              ))}
            </div>
          </div>
        )}

        {/* Q&A history */}
        {history.map((item, idx) => (
          <div key={item.id}>
            {idx > 0 && (
              <div className="border-t border-dashed border-gray-800 my-4" />
            )}

            {/* Query */}
            <div className="query mb-3">
              <span className="text-gray-600 select-none">&gt; </span>
              {item.question}
            </div>

            {/* Response */}
            {item.answer ? (
              <div
                className="response pl-3 animate-fade-in"
                dangerouslySetInnerHTML={{
                  __html: renderMarkdown(item.answer),
                }}
              />
            ) : (
              <div className="pl-3 flex items-center gap-2 text-gray-500">
                <span className="font-mono text-ai-purple animate-blink">
                  █
                </span>
                <span className="text-xs">Analyzing...</span>
              </div>
            )}
          </div>
        ))}

        {/* Loading cursor for new queries */}
        {loading && history.length > 0 && !history[history.length - 1].answer && null}
      </div>

      {/* Input row */}
      <div className="mt-3 flex items-center gap-2 rounded-lg border border-border/60 bg-terminal px-3 py-2">
        <span className="text-ai-purple font-mono text-sm select-none whitespace-nowrap">
          henry &gt;
        </span>
        <Input
          ref={inputRef}
          type="text"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="Ask about your trades..."
          disabled={loading}
          className="flex-1 bg-transparent font-mono text-gray-200 placeholder:text-gray-600 border-0 focus-visible:ring-0 h-auto p-0"
        />
        <Button
          variant="ai-ghost"
          size="sm"
          onClick={() => submitQuery(input)}
          disabled={loading || !input.trim()}
        >
          Submit
          <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M13 7l5 5m0 0l-5 5m5-5H6" />
          </svg>
        </Button>
      </div>
    </div>
  );
}
