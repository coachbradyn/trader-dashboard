const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api";

async function fetchApi<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_URL}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
  if (!res.ok) {
    throw new Error(`API ${res.status}: ${res.statusText}`);
  }
  return res.json();
}

export const api = {
  // Portfolios
  getPortfolios: () => fetchApi<import("./types").Portfolio[]>("/portfolios"),
  getPortfolio: (id: string) => fetchApi<import("./types").Portfolio>(`/portfolios/${id}`),
  getPositions: (id: string) => fetchApi<import("./types").Position[]>(`/portfolios/${id}/positions`),
  getPerformance: (id: string) => fetchApi<import("./types").Performance>(`/portfolios/${id}/performance`),
  getEquityHistory: (id: string) => fetchApi<import("./types").EquityPoint[]>(`/portfolios/${id}/equity-history`),
  getDailyStats: (id: string) => fetchApi<import("./types").DailyStats[]>(`/portfolios/${id}/daily-stats`),

  // Trades
  getTrades: (params?: { trader_id?: string; portfolio_id?: string; status?: string; limit?: number; offset?: number }) => {
    const searchParams = new URLSearchParams();
    if (params) {
      Object.entries(params).forEach(([k, v]) => {
        if (v !== undefined) searchParams.set(k, String(v));
      });
    }
    const qs = searchParams.toString();
    return fetchApi<import("./types").Trade[]>(`/trades${qs ? `?${qs}` : ""}`);
  },

  // Traders
  getTraders: () => fetchApi<import("./types").Trader[]>("/traders"),
  getTrader: (slug: string) => fetchApi<import("./types").Trader>(`/traders/${slug}`),

  // Leaderboard
  getLeaderboard: (sortBy?: string) => {
    const qs = sortBy ? `?sort_by=${sortBy}` : "";
    return fetchApi<import("./types").LeaderboardEntry[]>(`/leaderboard${qs}`);
  },

  // Prices
  getPrices: () => fetchApi<import("./types").PriceCache>("/prices"),

  // AI Analysis
  getBriefing: () => fetchApi<import("./types").BriefingResponse>("/ai/briefing"),
  postReview: (daysBack: number) =>
    fetchApi<import("./types").ReviewResponse>("/ai/review", {
      method: "POST",
      body: JSON.stringify({ days_back: daysBack }),
    }),
  postQuery: (question: string) =>
    fetchApi<import("./types").QueryResponse>("/ai/query", {
      method: "POST",
      body: JSON.stringify({ question }),
    }),
  getConflicts: (daysBack: number = 7) =>
    fetchApi<import("./types").ConflictResolution[]>(`/ai/conflicts?days_back=${daysBack}`),
};
