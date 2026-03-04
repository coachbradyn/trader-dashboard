"use client";
import { api } from "@/lib/api";
import { usePolling } from "./usePolling";

export function useLeaderboard(sortBy?: string, intervalMs = 30000) {
  return usePolling(() => api.getLeaderboard(sortBy), intervalMs);
}
