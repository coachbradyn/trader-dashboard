const TZ = "America/New_York";

export function formatCurrency(value: number): string {
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(value);
}

export function formatPercent(value: number, decimals = 2): string {
  const sign = value > 0 ? "+" : "";
  return `${sign}${value.toFixed(decimals)}%`;
}

export function formatNumber(value: number, decimals = 2): string {
  return value.toFixed(decimals);
}

export function formatDate(dateStr: string): string {
  return new Date(dateStr).toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
    timeZone: TZ,
  });
}

export function formatDateTime(dateStr: string): string {
  return new Date(dateStr).toLocaleString("en-US", {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
    timeZone: TZ,
  });
}

export function formatTimeAgo(dateStr: string): string {
  const seconds = Math.floor((Date.now() - new Date(dateStr).getTime()) / 1000);
  if (seconds < 60) return `${seconds}s ago`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  return `${days}d ago`;
}

export function formatTime(dateStr: string): string {
  return new Date(dateStr).toLocaleTimeString("en-US", {
    hour: "numeric",
    minute: "2-digit",
    timeZone: TZ,
  });
}

export function pnlColor(value: number): string {
  if (value > 0) return "text-profit";
  if (value < 0) return "text-loss";
  return "text-gray-400";
}

export function pnlBg(value: number): string {
  if (value > 0) return "bg-profit/10";
  if (value < 0) return "bg-loss/10";
  return "bg-gray-800";
}

/** Convert snake_case or UPPER_CASE to human-readable labels */
export function formatLabel(raw: string | null | undefined): string {
  if (!raw) return "";
  return raw
    .replace(/_/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase())
    .replace(/\bPnl\b/g, "P&L")
    .replace(/\bPct\b/g, "%")
    .replace(/\bQty\b/g, "Qty")
    .replace(/\bAdx\b/g, "ADX")
    .replace(/\bAtr\b/g, "ATR");
}

/** Human-readable exit reason labels */
export function formatExitReason(reason: string | null | undefined): string {
  if (!reason) return "Unknown";
  const map: Record<string, string> = {
    "stop_loss": "Stop Loss",
    "take_profit": "Take Profit",
    "trailing_stop": "Trailing Stop",
    "signal_exit": "Signal Exit",
    "time_exit": "Time Exit",
    "manual": "Manual",
    "ai_review_close": "AI Review",
    "reverse_signal": "Reverse Signal",
    "strategy_exit": "Strategy Exit",
    "unknown": "Unknown",
  };
  return map[reason.toLowerCase()] || formatLabel(reason);
}

/** Human-readable source labels */
export function formatSource(source: string | null | undefined): string {
  if (!source) return "Unknown";
  const map: Record<string, string> = {
    "manual": "Manual",
    "webhook": "Webhook",
    "ai_portfolio": "AI Portfolio",
  };
  return map[source.toLowerCase()] || formatLabel(source);
}

/** Format strategy slug into readable name (fallback if no display_name) */
export function formatStrategyId(id: string): string {
  return id
    .replace(/^henry-/, "Henry ")
    .replace(/-/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase());
}
