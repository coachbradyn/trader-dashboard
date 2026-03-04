"use client";
import { api } from "@/lib/api";
import { usePolling } from "./usePolling";

export function usePortfolios(intervalMs = 30000) {
  return usePolling(() => api.getPortfolios(), intervalMs);
}

export function usePortfolio(id: string, intervalMs = 15000) {
  return usePolling(() => api.getPortfolio(id), intervalMs);
}

export function usePerformance(id: string, intervalMs = 60000) {
  return usePolling(() => api.getPerformance(id), intervalMs);
}

export function useEquityHistory(id: string, intervalMs = 60000) {
  return usePolling(() => api.getEquityHistory(id), intervalMs);
}

export function useDailyStats(id: string, intervalMs = 60000) {
  return usePolling(() => api.getDailyStats(id), intervalMs);
}
