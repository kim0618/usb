/** MOCK DATA - Strategy B (Realtime Momentum).
 *
 *  Nothing here comes from the Backend, Kiwoom, or a live quote. The screens read it
 *  through `lib/strategy-b-source.ts`; replacing that module with real endpoints
 *  removes every value in this file from the UI.
 *
 *  The set is internally consistent on purpose: the two ENTERED candidates are the two
 *  open positions at the same price, `candidates` counts the live rows, and the closed
 *  trades use symbols that are no longer in the pool.
 */

import type { MomentumCandidate, StrategyPosition, StrategySummary } from "@/types/strategy-b";

/** The mock session everything below is timestamped inside (ET). */
const ET = (time: string) => `2026-09-16T${time}-04:00`;

/** Candidates that have left the pool no longer count toward the live candidate total. */
const LIVE_STATES = ["DETECTED", "QUALIFIED", "WATCHING", "SETUP_READY", "ENTRY_SIGNALLED", "ENTERED"] as const;

export const mockCandidates: MomentumCandidate[] = [
  {
    symbol: "NVTS", price_usd: 5.72, change_1m_pct: 2.8, change_3m_pct: 6.4, change_5m_pct: 11.2,
    rvol: 7.3, dollar_volume_usd: 8_400_000, spread_pct: 0.42, score: 91,
    state: "ENTERED", setup: "HOD_BREAKOUT", vwap_distance_pct: 3.8, hod_distance_pct: -1.1,
    detected_at: ET("10:14:21"), qualified_at: ET("10:14:36"), watching_at: ET("10:15:02"),
    setup_ready_at: ET("10:17:48"), signal_at: ET("10:18:30"), entered_at: ET("10:18:44"),
    drop_reason: null,
  },
  {
    symbol: "SOUN", price_usd: 9.21, change_1m_pct: 1.9, change_3m_pct: 5.1, change_5m_pct: 8.7,
    rvol: 5.8, dollar_volume_usd: 12_100_000, spread_pct: 0.31, score: 86,
    state: "ENTERED", setup: "FIRST_PULLBACK", vwap_distance_pct: 2.4, hod_distance_pct: -2.3,
    detected_at: ET("10:26:05"), qualified_at: ET("10:26:19"), watching_at: ET("10:27:41"),
    setup_ready_at: ET("10:29:52"), signal_at: ET("10:30:18"), entered_at: ET("10:30:33"),
    drop_reason: null,
  },
  {
    symbol: "ABCD", price_usd: 3.77, change_1m_pct: 3.3, change_3m_pct: 6.7, change_5m_pct: 9.8,
    rvol: 4.9, dollar_volume_usd: 3_700_000, spread_pct: 0.76, score: 82,
    state: "SETUP_READY", setup: "HOD_BREAKOUT", vwap_distance_pct: 2.9, hod_distance_pct: -0.4,
    detected_at: ET("10:41:12"), qualified_at: ET("10:41:27"), watching_at: ET("10:42:55"),
    setup_ready_at: ET("10:46:09"), signal_at: null, entered_at: null,
    drop_reason: null,
  },
  {
    symbol: "VRAX", price_usd: 2.86, change_1m_pct: 1.2, change_3m_pct: 4.4, change_5m_pct: 7.1,
    rvol: 3.6, dollar_volume_usd: 2_900_000, spread_pct: 0.88, score: 74,
    state: "WATCHING", setup: "FIRST_PULLBACK", vwap_distance_pct: 0.6, hod_distance_pct: -3.8,
    detected_at: ET("10:52:30"), qualified_at: ET("10:52:44"), watching_at: ET("10:54:02"),
    setup_ready_at: null, signal_at: null, entered_at: null,
    drop_reason: null,
  },
  {
    symbol: "GNLN", price_usd: 1.74, change_1m_pct: 2.1, change_3m_pct: 3.9, change_5m_pct: 6.2,
    rvol: 3.1, dollar_volume_usd: 1_800_000, spread_pct: 1.04, score: 68,
    state: "QUALIFIED", setup: "HOD_BREAKOUT", vwap_distance_pct: 1.7, hod_distance_pct: -2.6,
    detected_at: ET("11:03:16"), qualified_at: ET("11:03:31"), watching_at: null,
    setup_ready_at: null, signal_at: null, entered_at: null,
    drop_reason: null,
  },
  {
    symbol: "TNXP", price_usd: 4.05, change_1m_pct: 2.6, change_3m_pct: 5.8, change_5m_pct: 8.1,
    rvol: 4.2, dollar_volume_usd: 5_200_000, spread_pct: 0.53, score: 79,
    state: "ENTRY_SIGNALLED", setup: "FIRST_PULLBACK", vwap_distance_pct: 2.2, hod_distance_pct: -1.5,
    detected_at: ET("11:08:44"), qualified_at: ET("11:08:59"), watching_at: ET("11:10:12"),
    setup_ready_at: ET("11:12:38"), signal_at: ET("11:13:05"), entered_at: null,
    drop_reason: null,
  },
  {
    symbol: "SNTI", price_usd: 2.47, change_1m_pct: 1.8, change_3m_pct: 3.2, change_5m_pct: 4.6,
    rvol: 2.9, dollar_volume_usd: 1_050_000, spread_pct: 1.18, score: 58,
    state: "DETECTED", setup: null, vwap_distance_pct: 0.9, hod_distance_pct: -4.1,
    detected_at: ET("11:12:51"), qualified_at: null, watching_at: null,
    setup_ready_at: null, signal_at: null, entered_at: null,
    drop_reason: null,
  },
  {
    symbol: "ATNF", price_usd: 1.92, change_1m_pct: 0.7, change_3m_pct: 2.8, change_5m_pct: 5.4,
    rvol: 2.4, dollar_volume_usd: 640_000, spread_pct: 2.31, score: 51,
    state: "REJECTED", setup: null, vwap_distance_pct: -0.4, hod_distance_pct: -5.2,
    detected_at: ET("10:36:08"), qualified_at: null, watching_at: null,
    setup_ready_at: null, signal_at: null, entered_at: null,
    drop_reason: "SPREAD_TOO_WIDE",
  },
  {
    symbol: "LGVN", price_usd: 3.11, change_1m_pct: -0.3, change_3m_pct: 1.1, change_5m_pct: 4.9,
    rvol: 2.8, dollar_volume_usd: 1_300_000, spread_pct: 0.94, score: 63,
    state: "EXPIRED", setup: "HOD_BREAKOUT", vwap_distance_pct: -1.2, hod_distance_pct: -6.7,
    detected_at: ET("09:58:22"), qualified_at: ET("09:58:37"), watching_at: ET("10:00:14"),
    setup_ready_at: null, signal_at: null, entered_at: null,
    drop_reason: "SETUP_WINDOW_EXPIRED",
  },
];

export const mockPositions: StrategyPosition[] = [
  {
    symbol: "NVTS", setup: "HOD_BREAKOUT", entry_usd: 5.31, current_usd: 5.72,
    pnl_pct: 7.72, pnl_krw: 91_200, cost_basis_krw: 1_181_300, market_value_krw: 1_272_500,
    r_multiple: 1.41, stop_usd: 5.02, holding_minutes: 18, state: "OPEN",
  },
  {
    symbol: "SOUN", setup: "FIRST_PULLBACK", entry_usd: 9.04, current_usd: 9.21,
    pnl_pct: 1.88, pnl_krw: 32_600, cost_basis_krw: 1_734_000, market_value_krw: 1_766_600,
    r_multiple: 0.61, stop_usd: 8.76, holding_minutes: 6, state: "OPEN",
  },
];

export const mockSummary: StrategySummary = {
  strategy: "B",
  name: "REALTIME MOMENTUM",
  status: "RUNNING",
  initial_capital_krw: 10_000_000,
  current_equity_krw: 10_742_000,
  total_return_pct: 7.42,
  today_pnl_krw: 184_000,
  open_positions: mockPositions.length,
  candidates: mockCandidates.filter(candidate => (LIVE_STATES as readonly string[]).includes(candidate.state)).length,
  market_session: "REGULAR",
  realtime_pool: 128,
  as_of: ET("11:14:02"),
};
