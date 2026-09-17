/** The one seam between the Strategy B screens and their data.
 *
 *  Every function here already has the shape and the async contract of the Backend call
 *  that will replace it, so switching to the real API is a change to this file only:
 *
 *    summary:     () => apiFetch<StrategySummary>("/api/v1/strategy-b/summary")
 *    candidates:  () => apiFetch<MomentumCandidate[]>("/api/v1/strategy-b/candidates")
 *    positions:   () => apiFetch<StrategyPosition[]>("/api/v1/strategy-b/positions")
 *    trades:      () => apiFetch<StrategyTrade[]>("/api/v1/strategy-b/trades")
 *    performance: () => apiFetch<StrategyPerformance>("/api/v1/strategy-b/performance")
 *    comparison:  () => apiFetch<StrategyComparison>("/api/v1/strategies/compare")
 *
 *  No endpoint is added in this stage and no component imports a mock directly.
 */

import { mockCandidates, mockPositions, mockSummary } from "@/mocks/strategy-b";
import { mockPerformance, mockTrades } from "@/mocks/strategy-b-trades";
import { mockComparison } from "@/mocks/strategy-comparison";
import { strategyAccount } from "@/lib/strategy-b";
import type {
  EquityPoint, MomentumCandidate, StrategyAccount, StrategyComparison, StrategyPerformance,
  StrategyPosition, StrategySummary, StrategyTrade,
} from "@/types/strategy-b";

/** True while the screens read the mock module. The MOCK banner renders off this flag,
 *  so pointing the source at the Backend also removes the banner. */
export const STRATEGY_B_MOCK = true;

export const strategyBSource = {
  summary: (): Promise<StrategySummary> => Promise.resolve(mockSummary),
  /** The trading screen's account row, derived here so no view recomputes it. */
  account: (): Promise<StrategyAccount> => Promise.resolve(strategyAccount(mockSummary.current_equity_krw, mockPositions, mockTrades)),
  candidates: (): Promise<MomentumCandidate[]> => Promise.resolve(mockCandidates),
  positions: (): Promise<StrategyPosition[]> => Promise.resolve(mockPositions),
  trades: (): Promise<StrategyTrade[]> => Promise.resolve(mockTrades),
  performance: (): Promise<StrategyPerformance> => Promise.resolve(mockPerformance),
  /** Strategy B's own equity line. It is the comparison mock's B series, not a second
   *  copy of it, so the performance screen and the A/B screen draw the same numbers. */
  equityCurve: (): Promise<EquityPoint[]> => Promise.resolve(mockComparison.equity_curve),
};

export const strategyCompareSource = {
  comparison: (): Promise<StrategyComparison> => Promise.resolve(mockComparison),
};
