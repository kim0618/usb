/** The dashboard's data seam, composed from the mocks the detail screens already use.
 *
 *  Nothing here invents a figure. Strategy B's card reads the very objects /strategy-b
 *  renders, and Strategy A's equity and return read the comparison mock /strategy-compare
 *  renders, so a change to either mock moves the dashboard with it.
 *
 *  When the Backend lands, this module becomes one call each:
 *    GET /strategies/dashboard  -> DashboardSnapshot
 *  or the existing per-strategy endpoints composed in the same shape.
 */

import { mockStrategyAEvents, mockStrategyAToday } from "@/mocks/dashboard";
import { mockCandidates, mockPositions, mockSummary } from "@/mocks/strategy-b";
import { mockTrades } from "@/mocks/strategy-b-trades";
import { mockComparison } from "@/mocks/strategy-comparison";
import { setupLabel } from "@/lib/strategy-b";
import type { DashboardSnapshot, StrategyEvent } from "@/types/dashboard";
import type { MomentumCandidate } from "@/types/strategy-b";

/** True while the dashboard reads mock modules; the screen's notice renders off this. */
export const DASHBOARD_MOCK = true;

const EVENT_STEPS: ReadonlyArray<{ field: keyof Pick<MomentumCandidate, "entered_at" | "signal_at" | "setup_ready_at">; label: string }> = [
  { field: "entered_at", label: "진입 완료" },
  { field: "signal_at", label: "진입 신호" },
  { field: "setup_ready_at", label: "진입 준비" },
];

/** Strategy B's activity, read back off the scanner mock's own recorded timestamps. */
export function strategyBEvents(candidates: readonly MomentumCandidate[]): StrategyEvent[] {
  return candidates.flatMap(candidate => EVENT_STEPS.flatMap(step => {
    const at = candidate[step.field];
    return at == null ? [] : [{
      id: `B-${candidate.symbol}-${step.field}`,
      at, strategy: "B" as const, symbol: candidate.symbol,
      message: candidate.setup ? `${setupLabel(candidate.setup)} · ${step.label}` : step.label,
    }];
  }));
}

/** Newest first, but capped per strategy before merging: Strategy B produces far more
 *  events than A, and a purely chronological cut would show a timeline with only B in it. */
export function recentEvents(events: StrategyEvent[], perStrategy = 3): StrategyEvent[] {
  const newestFirst = (rows: StrategyEvent[]) => [...rows].sort((left, right) => right.at.localeCompare(left.at));
  const perSide = (["A", "B"] as const).flatMap(strategy =>
    newestFirst(events.filter(event => event.strategy === strategy)).slice(0, perStrategy));
  return newestFirst(perSide);
}

export const dashboardSource = {
  snapshot: (): Promise<DashboardSnapshot> => Promise.resolve({
    a: {
      strategy: "A",
      name: mockComparison.a.label,
      href: "/trading",
      status: mockStrategyAToday.status,
      source: "MOCK",
      initial_capital_krw: mockComparison.a.initial_capital_krw,
      current_equity_krw: mockComparison.a.current_equity_krw,
      total_return_pct: mockComparison.a.return_pct,
      today_pnl_krw: mockStrategyAToday.today_pnl_krw,
      open_positions: mockStrategyAToday.open_positions,
      closed_trades_today: mockStrategyAToday.closed_trades,
    },
    b: {
      strategy: "B",
      name: mockSummary.name,
      href: "/trading-b",
      status: mockSummary.status,
      source: "MOCK",
      initial_capital_krw: mockSummary.initial_capital_krw,
      current_equity_krw: mockSummary.current_equity_krw,
      total_return_pct: mockSummary.total_return_pct,
      today_pnl_krw: mockSummary.today_pnl_krw,
      open_positions: mockPositions.length,
      closed_trades_today: mockTrades.length,
    },
    equity_curve: mockComparison.equity_curve,
    events: recentEvents([...mockStrategyAEvents, ...strategyBEvents(mockCandidates)]),
  }),
};
