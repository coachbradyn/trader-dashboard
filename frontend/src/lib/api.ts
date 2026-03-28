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
  postReview: (daysBack: number) =>
    fetchApi<import("./types").ReviewResponse>("/ai/review", {
      method: "POST",
      body: JSON.stringify({ days_back: daysBack }),
    }),
  postQuery: (question: string, portfolioId?: string) =>
    fetchApi<import("./types").QueryResponse>("/ai/query", {
      method: "POST",
      body: JSON.stringify({ question, portfolio_id: portfolioId || null }),
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
  deletePortfolio: (id: string) =>
    fetchApi("/settings/portfolios/" + id, { method: "DELETE" }),
  depositToPortfolio: (id: string, amount: number) =>
    fetchApi<{ status: string; new_cash: number; new_initial_capital: number }>("/portfolios/" + id + "/deposit", {
      method: "POST",
      body: JSON.stringify({ amount }),
    }),
  withdrawFromPortfolio: (id: string, amount: number) =>
    fetchApi<{ status: string; new_cash: number; new_initial_capital: number }>("/portfolios/" + id + "/withdraw", {
      method: "POST",
      body: JSON.stringify({ amount }),
    }),

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
  deleteTrader: (slug: string) =>
    fetchApi("/settings/traders/" + slug, { method: "DELETE" }),

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
  createHolding: (data: {
    portfolio_id: string; ticker: string; direction: string; entry_price: number; qty: number; entry_date: string;
    strategy_name?: string; notes?: string;
    position_type?: string; thesis?: string; catalyst_date?: string; catalyst_description?: string;
    max_allocation_pct?: number; dca_enabled?: boolean; dca_threshold_pct?: number;
  }) =>
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

  // Portfolio Manager - Brokerage CSV Import
  previewImportTrades: async (file: File) => {
    const formData = new FormData();
    formData.append("file", file);
    const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api";
    const res = await fetch(`${API_URL}/portfolio-manager/import-trades/preview`, {
      method: "POST",
      body: formData,
    });
    if (!res.ok) throw new Error(`Preview failed: ${res.status}`);
    return res.json() as Promise<import("./types").ImportPreview>;
  },
  confirmImportTrades: (data: { portfolio_id: string; trades: import("./types").ImportedTrade[] }) =>
    fetchApi<import("./types").ImportResult>("/portfolio-manager/import-trades/confirm", {
      method: "POST",
      body: JSON.stringify(data),
    }),
  parseWithMapping: async (file: File, mapping: Record<string, string>) => {
    const formData = new FormData();
    formData.append("file", file);
    formData.append("mapping", JSON.stringify(mapping));
    const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api";
    const res = await fetch(`${API_URL}/portfolio-manager/import-trades/parse-with-mapping`, {
      method: "POST",
      body: formData,
    });
    if (!res.ok) throw new Error(`Parse failed: ${res.status}`);
    return res.json() as Promise<import("./types").ImportPreview>;
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
  getAIConfig: () =>
    fetchApi<Record<string, number | boolean>>("/ai-portfolio/config"),
  updateAIConfig: (config: Record<string, number | boolean>) =>
    fetchApi<Record<string, number | boolean>>("/ai-portfolio/config", {
      method: "PUT",
      body: JSON.stringify(config),
    }),
  chatAIPortfolio: (question: string) =>
    fetchApi<{ answer: string }>("/ai-portfolio/chat", {
      method: "POST",
      body: JSON.stringify({ question }),
    }),

  // Analytics
  runMonteCarlo: (params: import("./types").MonteCarloRequest) =>
    fetchApi<import("./types").MonteCarloResponse>("/analytics/monte-carlo", {
      method: "POST",
      body: JSON.stringify(params),
    }),

  // News
  getTickerNews: (ticker: string) =>
    fetchApi<import("./types").TickerNewsResponse>("/news/ticker/" + ticker),
  getTickerThesis: (ticker: string) =>
    fetchApi<{ ticker: string; thesis: { bull_case: string; bear_case: string; key_catalysts: string[]; risk_factors: string[]; sentiment_summary: string } | null; cached: boolean; generated_at?: string }>("/news/ticker/" + ticker + "/thesis"),
  generateTickerThesis: (ticker: string) =>
    fetchApi<{ ticker: string; thesis: { bull_case: string; bear_case: string; key_catalysts: string[]; risk_factors: string[]; sentiment_summary: string } | null; cached: boolean }>("/news/ticker/" + ticker + "/thesis", { method: "POST" }),
  getNews: (params?: { ticker?: string; limit?: number; hours?: number }) => {
    const sp = new URLSearchParams();
    if (params?.ticker) sp.set("ticker", params.ticker);
    if (params?.limit) sp.set("limit", String(params.limit));
    if (params?.hours) sp.set("hours", String(params.hours));
    const qs = sp.toString();
    return fetchApi<import("./types").NewsArticle[]>("/news" + (qs ? "?" + qs : ""));
  },
};
