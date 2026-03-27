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
  refreshBriefing: () => fetchApi<import("./types").BriefingResponse>("/ai/briefing/refresh", { method: "POST" }),
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

  // Portfolio Manager - Holdings
  getHoldings: (portfolioId?: string, activeOnly?: boolean) => {
    const sp = new URLSearchParams();
    if (portfolioId) sp.set("portfolio_id", portfolioId);
    if (activeOnly !== undefined) sp.set("active_only", String(activeOnly));
    const qs = sp.toString();
    return fetchApi<import("./types").PortfolioHolding[]>("/portfolio-manager/holdings" + (qs ? "?" + qs : ""));
  },
  createHolding: (data: { portfolio_id: string; ticker: string; direction: string; entry_price: number; qty: number; entry_date: string; strategy_name?: string; notes?: string }) =>
    fetchApi<import("./types").PortfolioHolding>("/portfolio-manager/holdings", {
      method: "POST",
      body: JSON.stringify(data),
    }),
  updateHolding: (id: string, data: Record<string, unknown>) =>
    fetchApi<import("./types").PortfolioHolding>("/portfolio-manager/holdings/" + id, {
      method: "PUT",
      body: JSON.stringify(data),
    }),
  deleteHolding: (id: string) =>
    fetchApi("/portfolio-manager/holdings/" + id, { method: "DELETE" }),

  // Portfolio Manager - Portfolio History
  getPortfolioHistory: (portfolioId: string, days: number = 90) =>
    fetchApi<{ date: string; value: number; cost_basis: number }[]>(
      "/portfolio-manager/portfolio-history?portfolio_id=" + portfolioId + "&days=" + days
    ),

  // Portfolio Manager - Actions
  getActions: (status?: string, portfolioId?: string) => {
    const sp = new URLSearchParams();
    if (status) sp.set("status", status);
    if (portfolioId) sp.set("portfolio_id", portfolioId);
    const qs = sp.toString();
    return fetchApi<import("./types").PortfolioAction[]>("/portfolio-manager/actions" + (qs ? "?" + qs : ""));
  },
  getActionStats: () =>
    fetchApi<import("./types").ActionStats>("/portfolio-manager/actions/stats"),
  approveAction: (id: string) =>
    fetchApi("/portfolio-manager/actions/" + id + "/approve", { method: "POST" }),
  rejectAction: (id: string, reason?: string) =>
    fetchApi("/portfolio-manager/actions/" + id + "/reject", {
      method: "POST",
      body: JSON.stringify({ reason: reason || null }),
    }),

  // Portfolio Manager - Backtest Imports
  getBacktestImports: () =>
    fetchApi<import("./types").BacktestImportData[]>("/portfolio-manager/imports"),
  getBacktestTrades: (importId: string) =>
    fetchApi<import("./types").BacktestTradeData[]>("/portfolio-manager/imports/" + importId + "/trades"),
  deleteBacktestImport: (id: string) =>
    fetchApi("/portfolio-manager/imports/" + id, { method: "DELETE" }),
  uploadBacktests: async (files: File[]) => {
    const formData = new FormData();
    files.forEach((f) => formData.append("files", f));
    const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api";
    const res = await fetch(`${API_URL}/portfolio-manager/import`, {
      method: "POST",
      body: formData,
    });
    if (!res.ok) throw new Error(`Upload failed: ${res.status}`);
    return res.json() as Promise<import("./types").BacktestImportData[]>;
  },

  // Watchlist
  getWatchlist: () =>
    fetchApi<import("./types").WatchlistTickerData[]>("/watchlist"),
  addWatchlistTickers: (tickers: string[], notes?: string) =>
    fetchApi<{ added: string[]; count: number }>("/watchlist", {
      method: "POST",
      body: JSON.stringify({ tickers, notes: notes || null }),
    }),
  removeWatchlistTicker: (ticker: string) =>
    fetchApi<{ removed: string }>("/watchlist/" + ticker, { method: "DELETE" }),
  getWatchlistDetail: (ticker: string) =>
    fetchApi<import("./types").WatchlistTickerDetail>("/watchlist/" + ticker + "/detail"),
  refreshWatchlistSummary: (ticker: string) =>
    fetchApi<{ status: string; ticker: string }>("/watchlist/" + ticker + "/refresh-summary", { method: "POST" }),
  getStrategies: () =>
    fetchApi<import("./types").StrategyInfo[]>("/watchlist/strategies/list"),

  // AI Portfolio
  getAIPortfolioStatus: () =>
    fetchApi<import("./types").AIPortfolioStatus>("/ai-portfolio/status"),
  createAIPortfolio: (data?: { name?: string; initial_capital?: number }) =>
    fetchApi<{ id: string; name: string; initial_capital: number; status: string }>("/ai-portfolio/create", {
      method: "POST",
      body: JSON.stringify(data || {}),
    }),
  resetAIPortfolio: () =>
    fetchApi<{ status: string; equity: number }>("/ai-portfolio/reset", { method: "POST" }),
  getAIPortfolioComparison: () =>
    fetchApi<import("./types").AIPortfolioComparison>("/ai-portfolio/compare"),
  getAIPortfolioEquityHistory: (days?: number) =>
    fetchApi<import("./types").EquityPoint[]>("/ai-portfolio/equity-history" + (days ? "?days=" + days : "")),
  getAIPortfolioDecisions: (filter?: string, limit?: number) => {
    const sp = new URLSearchParams();
    if (filter) sp.set("filter", filter);
    if (limit) sp.set("limit", String(limit));
    const qs = sp.toString();
    return fetchApi<import("./types").AIPortfolioDecision[]>("/ai-portfolio/decisions" + (qs ? "?" + qs : ""));
  },
  getAIPortfolioHoldings: () =>
    fetchApi<import("./types").AIPortfolioHolding[]>("/ai-portfolio/holdings"),

  // Analytics
  runMonteCarlo: (params: import("./types").MonteCarloRequest) =>
    fetchApi<import("./types").MonteCarloResponse>("/analytics/monte-carlo", {
      method: "POST",
      body: JSON.stringify(params),
    }),
};
