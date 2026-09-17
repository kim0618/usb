/** Strategy B (Realtime Momentum) and the A/B comparison screen.
 *
 *  These shapes are written the way the Backend will answer, so the screens can move
 *  from the mock source to real endpoints by swapping `lib/strategy-b-source.ts` alone:
 *    GET /strategy-b/summary      -> StrategySummary
 *    GET /strategy-b/candidates   -> MomentumCandidate[]
 *    GET /strategy-b/positions    -> StrategyPosition[]
 *    GET /strategy-b/trades       -> StrategyTrade[]
 *    GET /strategy-b/performance  -> StrategyPerformance
 *    GET /strategies/compare      -> StrategyComparison
 *
 *  Money is KRW (the account currency); prices, spreads and dollar volume are USD
 *  because the traded instruments are US equities. Percentages are already in percent
 *  units (7.42 means +7.42%), never fractions.
 */

export type StrategyKey = "A" | "B";
export type StrategyRunState = "RUNNING" | "PAUSED" | "STOPPED";
export type MarketSession = "PREMARKET" | "REGULAR" | "POSTMARKET" | "CLOSED";

/** The candidate lifecycle of the realtime scanner, in order. */
export type CandidateState =
  | "DETECTED" | "QUALIFIED" | "WATCHING" | "SETUP_READY"
  | "ENTRY_SIGNALLED" | "ENTERED" | "REJECTED" | "EXPIRED";

/** The entry patterns Strategy B trades. V1 allows these two only. */
export type SetupType = "HOD_BREAKOUT" | "FIRST_PULLBACK";

export type PositionState = "OPEN" | "SCALING_OUT" | "CLOSING";

export type ExitReason = "HARD_STOP" | "PARTIAL_TRAIL" | "TRAILING_STOP" | "TIME_STOP" | "EOD_EXIT";

export type TradeResult = "WIN" | "LOSS" | "BREAKEVEN";

export interface StrategySummary {
  strategy: StrategyKey;
  name: string;
  status: StrategyRunState;
  initial_capital_krw: number;
  current_equity_krw: number;
  total_return_pct: number;
  today_pnl_krw: number;
  open_positions: number;
  candidates: number;
  market_session: MarketSession;
  realtime_pool: number;
  /** ET timestamp of the last scanner tick that produced this snapshot. */
  as_of: string;
}

/** One realtime scanner row. Prices are USD, momentum legs and spread are percent. */
export interface MomentumCandidate {
  symbol: string;
  price_usd: number;
  change_1m_pct: number;
  change_3m_pct: number;
  change_5m_pct: number;
  rvol: number;
  dollar_volume_usd: number;
  spread_pct: number;
  score: number;
  state: CandidateState;
  setup: SetupType | null;
  vwap_distance_pct: number;
  hod_distance_pct: number;
  /** ET timestamps of the lifecycle steps this candidate has actually reached. */
  detected_at: string | null;
  qualified_at: string | null;
  watching_at: string | null;
  setup_ready_at: string | null;
  signal_at: string | null;
  entered_at: string | null;
  /** Why a REJECTED or EXPIRED candidate left the pool; null while it is still live. */
  drop_reason: string | null;
}

export interface StrategyPosition {
  symbol: string;
  setup: SetupType;
  entry_usd: number;
  current_usd: number;
  pnl_pct: number;
  pnl_krw: number;
  /** What the position cost and what it is worth now; market value - cost = pnl_krw. */
  cost_basis_krw: number;
  market_value_krw: number;
  r_multiple: number;
  stop_usd: number;
  holding_minutes: number;
  state: PositionState;
}

export interface StrategyTrade {
  id: string;
  symbol: string;
  setup: SetupType;
  entry_usd: number;
  exit_usd: number;
  pnl_krw: number;
  r_multiple: number;
  holding_minutes: number;
  exit_reason: ExitReason;
  result: TradeResult;
  /** ET timestamp of the exit fill. */
  exited_at: string;
}

/** The account as a trading screen reads it. Every field is derived from the positions
 *  and trades the same screen lists, never stated separately. */
export interface StrategyAccount {
  equity_krw: number;
  invested_krw: number;
  cash_krw: number;
  unrealized_pnl_krw: number;
  realized_pnl_krw: number;
}

export interface StrategyPerformance {
  total_return_pct: number;
  net_pnl_krw: number;
  trades: number;
  win_rate_pct: number;
  profit_factor: number;
  expectancy_r: number;
  max_drawdown_pct: number;
  average_r: number;
  average_win_krw: number;
  average_loss_krw: number;
}

/** One session of the comparison equity curve; both strategies share the session list. */
export interface EquityPoint {
  /** Trading date, YYYY-MM-DD. */
  date: string;
  a_equity_krw: number;
  b_equity_krw: number;
}

/** The headline figures of one strategy over the common period. */
export interface ComparisonSide {
  strategy: StrategyKey;
  label: string;
  initial_capital_krw: number;
  current_equity_krw: number;
  return_pct: number;
  net_pnl_krw: number;
  trades: number;
  win_rate_pct: number;
  profit_factor: number;
  expectancy_r: number;
  max_drawdown_pct: number;
  average_r: number;
}

/** One symbol both strategies traded inside the common period. */
export interface SymbolComparisonSide {
  entry_usd: number;
  entry_time: string;
  exit_usd: number;
  return_pct: number;
  holding_minutes: number;
}

export interface SymbolComparison {
  symbol: string;
  a: SymbolComparisonSide;
  b: SymbolComparisonSide;
}

export interface StrategyComparison {
  /** Inclusive common period both strategies were measured over. */
  period_start: string;
  period_end: string;
  a: ComparisonSide;
  b: ComparisonSide;
  equity_curve: EquityPoint[];
  same_symbols: SymbolComparison[];
}
