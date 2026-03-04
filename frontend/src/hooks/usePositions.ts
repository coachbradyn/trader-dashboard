"use client";
import { api } from "@/lib/api";
import { usePolling } from "./usePolling";

export function usePositions(portfolioId: string, intervalMs = 15000) {
  return usePolling(() => api.getPositions(portfolioId), intervalMs);
}
