"use client";
import { api } from "@/lib/api";
import { usePolling } from "./usePolling";

export function useTrades(params?: { portfolio_id?: string; trader_id?: string; status?: string; limit?: number }, intervalMs = 5000) {
  return usePolling(() => api.getTrades(params), intervalMs);
}
