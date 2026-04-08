const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api";
const API_KEY = process.env.NEXT_PUBLIC_API_KEY || "";

async function fetchApi<T>(path: string, init?: RequestInit): Promise<T> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...init?.headers as Record<string, string>,
  };
  if (API_KEY) {
    headers["x-api-key"] = API_KEY;
  }
  const res = await fetch(`${API_URL}${path}`, {
    ...init,
    headers,
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
  getBriefingHistory: (limit = 14) =>
    fetchApi<{ id: string; briefing: string; generated_at: string; tickers: string[] | null }[]>(`/ai/briefing/history?limit=${limit}`),
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
  toggleTraderActive: (slug: string) =>
    fetchApi<{ is_active: boolean }>("/traders/" + slug + "/toggle-active", { method: "PATCH" }),
  testWebhook: (payload: Record<string, unknown>) =>
    fetchApi<Record<string, unknown>>("/webhook", { method: "POST", body: JSON.stringify(payload) }),

  // Settings - Keys
  getKeys: () => fetchApi<import("./types").AllowlistedKey[]>("/settings/keys"),
  generateKey: (label?: string) =>
    fetchApi<{ id: string; api_key: string; label: string | null; message: string }>("/settings/keys/generate", {
      method: "POST",
      body: JSON.stringify({ label: label || null }),
    }),
  revokeKey: (id: string) =>
    fetchApi("/settings/keys/" + id, { method: "DELETE" }),

  // Execution
  testAlpacaConnection: (portfolioId: string) =>
    fetchApi<import("./types").AlpacaConnectionTest>("/execution/test-connection", {
      method: "POST",
      body: JSON.stringify({ portfolio_id: portfolioId }),
    }),
  submitOrder: (data: { portfolio_id: string; ticker: string; qty: number; side: "buy" | "sell" }) =>
    fetchApi<import("./types").OrderResult>("/execution/order", {
      method: "POST",
      body: JSON.stringify(data),
    }),
  killSwitch: () =>
    fetchApi<{ status: string; portfolios_affected: number }>("/execution/kill-switch", {
      method: "POST",
      body: JSON.stringify({ confirm: true }),
    }),
  syncAlpacaPositions: (portfolioId: string) =>
    fetchApi<{ status: string; synced: number; created: number }>("/execution/sync", {
      method: "POST",
      body: JSON.stringify({ portfolio_id: portfolioId }),
    }),
  getAlpacaPositions: (portfolioId: string) =>
    fetchApi<Array<{ symbol: string; qty: number; avg_entry_price: number; current_price: number; unrealized_pl: number }>>("/execution/positions?portfolio_id=" + portfolioId),

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
  deleteHolding: (id: string, recordTrade: boolean = false) =>
    fetchApi("/portfolio-manager/holdings/" + id + (recordTrade ? "?record_trade=true" : ""), { method: "DELETE" }),

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
  getWatchlistFundamentals: () =>
    fetchApi<Record<string, import("./types").TickerFundamentals>>("/watchlist/fundamentals"),
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

  // Henry Context / Memory
  getHenryPriceTargets: (ticker: string, force = false) =>
    fetchApi<Record<string, unknown>>(`/ai/price-targets/${ticker}${force ? "?force=true" : ""}`),
  getHenryActivity: (limit?: number, ticker?: string) => {
    const sp = new URLSearchParams();
    if (limit) sp.set("limit", String(limit));
    if (ticker) sp.set("ticker", ticker);
    const qs = sp.toString();
    return fetchApi<Array<{ id: string; message: string; activity_type: string; activity_label: string; ticker: string | null; created_at: string }>>("/ai/activity" + (qs ? "?" + qs : ""));
  },
  chatWithHenry: (question: string) =>
    fetchApi<{ answer: string; trades_in_context: number }>("/ai/chat", {
      method: "POST",
      body: JSON.stringify({ question }),
    }),
  getHenryContext: (ticker?: string) =>
    fetchApi<import("./types").HenryContextEntry[]>("/ai/context" + (ticker ? "?ticker=" + ticker : "")),
  getHenryStats: () =>
    fetchApi<import("./types").HenryStatsEntry[]>("/ai/stats"),

  // Scanner
  getScannerResults: async () => {
    const data = await fetchApi<{ results: import("./types").ScannerOpportunity[] }>("/scanner/results");
    return data.results || [];
  },
  getScannerHistory: async () => {
    const data = await fetchApi<{ history: import("./types").ScannerOpportunity[] }>("/scanner/history");
    return data.history || [];
  },
  runScanner: () =>
    fetchApi<{ status: string; message?: string }>("/scanner/run", { method: "POST" }),
  getScannerRunStatus: () =>
    fetchApi<{ running: boolean; last_result: { status: string; message: string; count?: number } | null }>("/scanner/run-status"),
  flushFmpCache: () =>
    fetchApi<{ status: string; entries_deleted?: number }>("/scanner/flush-cache", { method: "POST" }),
  testFmpConnection: () =>
    fetchApi<Record<string, unknown>>("/scanner/test-fmp"),
  getScannerCriteria: async () => {
    const data = await fetchApi<{ criteria: Record<string, unknown> } | Record<string, unknown>>("/scanner/criteria");
    return (data as { criteria: Record<string, unknown> }).criteria || data;
  },
  updateScannerCriteria: (criteria: Record<string, unknown>) =>
    fetchApi<Record<string, unknown>>("/scanner/criteria", { method: "PUT", body: JSON.stringify(criteria) }),
  getScannerProfiles: () =>
    fetchApi<{ profiles: import("./types").ScanProfile[] }>("/scanner/profiles"),
  saveScannerProfile: (profileId: string, data: Record<string, unknown>) =>
    fetchApi<{ profiles: import("./types").ScanProfile[] }>("/scanner/profiles/" + profileId, {
      method: "PUT",
      body: JSON.stringify(data),
    }),
  deleteScannerProfile: (profileId: string) =>
    fetchApi<{ profiles: import("./types").ScanProfile[] }>("/scanner/profiles/" + profileId, { method: "DELETE" }),
  runScannerWithProfile: (profileId: string) =>
    fetchApi<{ status: string }>("/scanner/run/" + profileId, { method: "POST" }),
  getScannerStats: () =>
    fetchApi<import("./types").ScannerStats>("/scanner/stats"),
  getFmpUsage: () =>
    fetchApi<import("./types").FmpUsage>("/scanner/fmp-usage"),
  getTickerFundamentals: (ticker: string) =>
    fetchApi<import("./types").TickerFundamentals>("/ai/fundamentals/" + ticker),

  // Memory
  getMemories: (params?: { type?: string; source?: string }) => {
    const sp = new URLSearchParams();
    if (params?.type) sp.set("type", params.type);
    if (params?.source) sp.set("source", params.source);
    const qs = sp.toString();
    return fetchApi<Array<{ id: string; type: string; ticker: string | null; strategy: string | null; content: string; importance: number; validated: boolean; source: string; created_at: string }>>("/memory" + (qs ? "?" + qs : ""));
  },
  updateMemory: (id: string, data: Record<string, unknown>) =>
    fetchApi("/memory/" + id, { method: "PUT", body: JSON.stringify(data) }),
  deleteMemory: (id: string) =>
    fetchApi("/memory/" + id, { method: "DELETE" }),

  // AI Usage
  getAIUsage: (days?: number) =>
    fetchApi<{ total_calls: number; estimated_cost_usd: number; total_input_tokens: number; total_output_tokens: number }>("/ai/usage" + (days ? "?days=" + days : "")),

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
