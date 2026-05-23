export interface ComparisonChildSummary {
  total_return_pct: number | null;
  total_value_end: number | null;
  total_pnl: number;
  total_trades: number;
  win_rate: number | null;
  days: {
    date: string;
    daily_pnl: number;
    total_value: number;
    total_return_pct: number;
    trades: number;
  }[];
}
export interface ComparisonChildProgress {
  current_day?: number | null;
  total_days?: number | null;
  current_phase?: string | null;
  phase_progress?: number | null;
  phase_total?: number | null;
  phase_detail?: string | null;
  current_date?: string | null;
}
export interface ComparisonChild {
  model: string;
  session_id: string;
  status: string; // queued | running | completed | failed: ...
  progress?: ComparisonChildProgress;
  summary?: ComparisonChildSummary;
}
export interface ComparisonStatus {
  status: string; // not_started | running | completed | failed: ...
  base_session_id?: string;
  start_date?: string;
  end_date?: string;
  current_idx?: number;
  children?: ComparisonChild[];
  started_at?: string;
  finished_at?: string;
}

export interface CostBucket {
  usd: number;
  input_tokens: number;
  output_tokens: number;
  cycles: number;
}
export interface CostByDay {
  date: string;
  usd: number;
  input_tokens: number;
  output_tokens: number;
  cycles: number;
}
export interface CostByModel {
  model: string;
  usd: number;
  input_tokens: number;
  output_tokens: number;
  cycles: number;
}
export interface CostLedgerData {
  lifetime: CostBucket;
  today: { usd: number; cycles: number };
  daily: CostByDay[];
  by_model: CostByModel[];
}

export interface ThinkingToolCall {
  name: string;
  input: Record<string, unknown>;
}
export interface ThinkingIteration {
  iter: number;
  text: string;
  tool_calls: ThinkingToolCall[];
}
export interface ThinkingPlaced {
  action: string;
  ticker?: string;
  qty?: number;
  price?: number;
}
export interface ReplayTrade {
  id: number;
  ticker: string;
  action: string;
  direction: string;
  trade_type: string;
  quantity: number;
  entry_price: number;
  entry_time: string;
  exit_price: number | null;
  exit_time: string | null;
  pnl: number | null;
  status: string;
  reason: string;
  exit_reason: string | null;
  conviction?: number | null;
  llm_model?: string | null;
  exit_type?: string | null;
}
export interface ReplayDayData {
  session_id: string;
  date: string;
  cycles: ThinkingEntry[];
  trades: ReplayTrade[];
  day_summary: {
    date: string;
    trades: number;
    total_value: number;
    daily_pnl: number;
    total_return_pct: number;
    win_rate: number;
  } | null;
}

export interface ThinkingEntry {
  ts: string;
  phase: "executed" | "rejected" | "observed" | string;
  iterations: number;
  trail: ThinkingIteration[];
  placed: ThinkingPlaced[];
}

export interface SessionPortfolio {
  cash: number;
  total_value: number;
  total_return: number;
  total_return_pct: number;
  open_positions: number;
  realized_pnl: number;
}

export interface SessionDaily {
  date: string;
  total_value: number | null;
  daily_pnl: number | null;
}

export interface SessionBacktestProgress {
  status: string;
  current_day?: number | null;
  trading_days?: number | null;
  current_date?: string | null;
  start_date?: string | null;
  end_date?: string | null;
}

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
  backtest_mode?: boolean;
  backtest_status?: string;
  portfolio?: SessionPortfolio | null;
  daily?: SessionDaily[];
  backtest_progress?: SessionBacktestProgress | null;
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
  conviction?: number | null;
  llm_model?: string | null;
  exit_type?: string | null;
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
  breakevens?: number;
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
  source?: "scanner" | "pick";
}

export type Category = { name: string; count: number; tickers: string[] };

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
  universe?: string[] | null;
  universe_mode?: "discovery" | "fixed";
  universe_count?: number | null;
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
  backtest_mode?: boolean;
  backtest_status?: string;
  backtest_start_date?: string;
  backtest_end_date?: string;
  live_started_at?: string;
}

export interface AgentStatus {
  running: boolean;
  pid: number | null;
}

export interface Directive {
  id: string;
  text: string;
  type: "quick" | "custom";
  expiry: "this_cycle" | "today" | "until_cleared";
  created_at: string;
  expires_at: string | null;
}

export interface DailyPerformance {
  date: string;
  cash: number | null;
  portfolio_value: number | null;
  total_value: number | null;
  daily_pnl: number;
  trades_taken: number;
  wins: number;
  losses: number;
  cumulative_return: number | null;
  cumulative_return_pct: number | null;
}

// ── Backtest Types ─────────────────────────────────────────

export interface BacktestDayResult {
  date: string;
  trades: number;
  total_value: number;
  daily_pnl: number;
  total_return_pct: number;
  win_rate: number;
  total_trades: number;
  duration_sec: number;
  error?: string;
}

export type BacktestPhase =
  | "init"
  | "prefetch"
  | "scanning"
  | "selecting"
  | "trading"
  | "closing"
  | "reviewing"
  | "day_done";

export interface BacktestPick {
  ticker: string;
  direction: string;
  reason: string;
}

export interface BacktestRecentTrade {
  ticker: string;
  action: string;
  direction: string;
  quantity: number;
  entry_price: number;
  exit_price: number | null;
  pnl: number | null;
  exit_time: string | null;
}

export interface BacktestProgress {
  status: "not_started" | "running" | "completed" | "failed";
  start_date?: string;
  end_date?: string;
  trading_days?: number;
  current_day?: number;
  current_date?: string;
  daily_results?: BacktestDayResult[];
  final_stats?: Performance;
  error?: string;
  // Live transparency fields
  current_phase?: BacktestPhase;
  current_bar_time?: string | null;
  current_picks?: BacktestPick[];
  recent_trades?: BacktestRecentTrade[];
  day_trades_count?: number;
  day_pnl?: number;
  day_started_at?: number;
  started_at?: number;
  // Sub-progress within a phase (e.g. scanning 2200/2847)
  phase_progress?: number | null;
  phase_total?: number | null;
  phase_detail?: string | null;
}
