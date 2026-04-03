export interface Session {
  session_id: string;
  display_name: string;
  market: string;
  currency_symbol: string;
  starting_capital: number;
  is_running: boolean;
  pid: number | null;
  llm_provider?: string;
  llm_model?: string;
  total_trades?: number;
  win_rate?: number | null;
}

export interface MarketPreset {
  market_id: string;
  display_name: string;
  currency: string;
  currency_symbol: string;
  is_24x7: boolean;
  default_starting_capital: number;
  default_watchlist_count: number;
  timezone: string;
  ticker_suffix: string;
  trade_types: string[];
  short_allowed: boolean;
}

export interface PortfolioSummary {
  cash: number;
  cash_raw: number;
  holdings_value: number;
  total_value: number;
  starting_capital: number;
  total_return: number;
  total_return_pct: number;
  today_pnl: number;
  open_positions: number;
  holdings: Holding[];
}

export interface Holding {
  trade_id: number;
  ticker: string;
  direction: string;
  qty: number;
  entry_price: number;
  current_price: number;
  unrealized_pnl: number;
  market_value: number;
  trade_type: string;
  held_since: string;
  reason: string;
  stop_price: number | null;
  target_price: number | null;
}

export interface ClosedTrade {
  id: number;
  ticker: string;
  action: string;
  direction: string;
  trade_type: string;
  quantity: number;
  entry_price: number;
  entry_time: string;
  exit_price: number;
  exit_time: string;
  pnl: number;
  status: string;
  reason: string;
  exit_reason: string;
}

export interface RiskStatus {
  daily_pnl: number;
  daily_loss_limit: number;
  daily_limit_used_pct: number;
  open_positions: number;
  max_positions: number;
  can_trade: boolean;
  cash_available: number;
  max_trade_amount: number;
}

export interface Performance {
  total_trades: number;
  wins: number;
  losses: number;
  win_rate: number;
  total_pnl: number;
  avg_win: number;
  avg_loss: number;
  best_trade: number;
  worst_trade: number;
  message?: string;
}

export interface WatchlistItem {
  ticker: string;
  current_price: number;
  change_pct: number;
  rsi_14: number | null;
  atr_14: number | null;
  vol_ratio: number | null;
  dist_to_resistance_pct: number | null;
  dist_to_support_pct: number | null;
  sma_5: number | null;
  price_vs_sma: string;
}

export interface SessionConfig {
  starting_capital: number;
  currency: string;
  currency_symbol: string;
  max_position_pct: number;
  max_open_positions: number;
  daily_loss_limit_pct: number;
  per_trade_loss_limit_pct: number;
  max_trade_amount: number;
  watchlist_count: number;
  llm_provider: string;
  llm_model?: string;
  intraday_interval_min: number;
  market_open: string;
  market_close: string;
  market_id: string;
  market_name: string;
  is_24x7: boolean;
  ticker_suffix: string;
  locale: string;
  timezone: string;
  session_id?: string;
  session_name?: string;
}

export interface AgentStatus {
  running: boolean;
  pid: number | null;
}
