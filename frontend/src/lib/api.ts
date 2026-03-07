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

  // Settings - Portfolios
  getSettingsPortfolios: () => fetchApi<import("./types").PortfolioSettings[]>("/settings/portfolios"),
  createPortfolio: (data: { name: string; description?: string; initial_capital: number; max_pct_per_trade?: number; max_open_positions?: number; max_drawdown_pct?: number }) =>
    fetchApi<import("./types").PortfolioSettings>("/settings/portfolios", {
      method: "POST",
      body: JSON.stringify(data),
    }),
  updatePortfolio: (id: string, data: { portfolio?: Record<string, unknown>; strategies?: Array<{ trader_id: string; direction_filter: string | null }> }) =>
    fetchApi("/settings/portfolios/" + id, {
      method: "PUT",
      body: JSON.stringify(data),
    }),
  archivePortfolio: (id: string) =>
    fetchApi("/settings/portfolios/" + id + "/archive", { method: "PATCH" }),

  // Settings - Traders
  getSettingsTraders: () => fetchApi<import("./types").TraderSettings[]>("/settings/traders"),
  updateTrader: (slug: string, data: { display_name?: string; strategy_name?: string; description?: string }) =>
    fetchApi("/settings/traders/" + slug, {
      method: "PUT",
      body: JSON.stringify(data),
    }),
  rotateTraderKey: (slug: string) =>
    fetchApi<{ api_key: string; message: string }>("/settings/traders/" + slug + "/rotate-key", {
      method: "POST",
    }),

  // Settings - Keys
  getKeys: () => fetchApi<import("./types").AllowlistedKey[]>("/settings/keys"),
  generateKey: (label?: string) =>
    fetchApi<{ id: string; api_key: string; label: string | null; message: string }>("/settings/keys/generate", {
      method: "POST",
      body: JSON.stringify({ label: label || null }),
    }),
  revokeKey: (id: string) =>
    fetchApi("/settings/keys/" + id, { method: "DELETE" }),

  // Screener
  getScreenerAlerts: (params?: { ticker?: string; indicator?: string; signal?: string; hours?: number }) => {
    const sp = new URLSearchParams();
    if (params) {
      Object.entries(params).forEach(([k, v]) => {
        if (v !== undefined) sp.set(k, String(v));
      });
    }
    const qs = sp.toString();
    return fetchApi<import("./types").IndicatorAlert[]>("/screener/alerts" + (qs ? "?" + qs : ""));
  },
  getScreenerTickers: (hours?: number) =>
    fetchApi<import("./types").TickerAggregation[]>("/screener/tickers" + (hours ? "?hours=" + hours : "")),
  getScreenerChart: (ticker: string, days?: number) =>
    fetchApi<import("./types").ChartDataPoint[]>("/screener/chart/" + ticker + (days ? "?days=" + days : "")),
  getScreenerAnalysis: () =>
    fetchApi<import("./types").ScreenerAnalysis | null>("/screener/analysis/latest"),
  analyzeScreenerTicker: (ticker: string, hours?: number) =>
    fetchApi<import("./types").TickerAnalysis>(
      "/screener/analyze/" + ticker,
      {
        method: "POST",
        body: JSON.stringify({ hours: hours || 24 }),
      }
    ),

  // Market Summaries
  getSummaries: () => fetchApi<import("./types").MarketSummary[]>("/ai/summaries"),
  generateSummary: () =>
    fetchApi("/ai/summaries/generate", { method: "POST" }),
};
