/** The integrated dashboard: one screen that answers "where do A and B stand right now".
 *
 *  It owns no figures of its own. Every number is composed in lib/dashboard-source.ts from
 *  the Strategy B mock and the comparison mock, so the dashboard can never disagree with
 *  the detail screens it summarises. Detailed statistics stay on /strategy-compare.
 */

import type { EquityPoint, StrategyKey, StrategyRunState } from "@/types/strategy-b";

/** Whether a card's figures came from the Backend or from a mock module. */
export type StrategyDataSource = "LIVE" | "MOCK";

export interface DashboardStrategySnapshot {
  strategy: StrategyKey;
  /** The strategy's own name, e.g. GAP + OPENING RANGE. */
  name: string;
  /** Where the card's "자세히 보기" action goes. */
  href: string;
  status: StrategyRunState;
  source: StrategyDataSource;
  initial_capital_krw: number;
  current_equity_krw: number;
  total_return_pct: number;
  today_pnl_krw: number;
  open_positions: number;
  closed_trades_today: number;
}

/** One line of the recent activity list. */
export interface StrategyEvent {
  id: string;
  /** ET timestamp of the event. */
  at: string;
  strategy: StrategyKey;
  symbol: string;
  message: string;
}

export interface DashboardSnapshot {
  a: DashboardStrategySnapshot;
  b: DashboardStrategySnapshot;
  equity_curve: EquityPoint[];
  events: StrategyEvent[];
}
