/** MOCK DATA - Strategy A / Strategy B comparison over one common period.
 *
 *  The equity curve is the source of the daily P&L table: the daily returns the compare
 *  screen shows are derived from these closes, never stated separately, so the two views
 *  cannot drift apart. The curve was built to end on the stated returns (+3.80% / +7.42%)
 *  and to bottom out at exactly the stated max drawdowns (-3.8% / -6.2%).
 */

import type { StrategyComparison } from "@/types/strategy-b";

export const mockComparison: StrategyComparison = {
  period_start: "2026-09-01",
  period_end: "2026-09-30",
  a: {
    strategy: "A", label: "GAP + OPENING RANGE",
    initial_capital_krw: 10_000_000, current_equity_krw: 10_380_000,
    return_pct: 3.8, net_pnl_krw: 380_000, trades: 14, win_rate_pct: 57.1,
    profit_factor: 1.52, expectancy_r: 0.18, max_drawdown_pct: -3.8, average_r: 0.21,
  },
  b: {
    strategy: "B", label: "REALTIME MOMENTUM",
    initial_capital_krw: 10_000_000, current_equity_krw: 10_742_000,
    return_pct: 7.42, net_pnl_krw: 742_000, trades: 37, win_rate_pct: 48.6,
    profit_factor: 1.79, expectancy_r: 0.31, max_drawdown_pct: -6.2, average_r: 0.34,
  },
  equity_curve: [
    { date: "2026-09-01", a_equity_krw: 10_080_000, b_equity_krw: 10_140_000 },
    { date: "2026-09-02", a_equity_krw: 10_049_760, b_equity_krw: 10_190_700 },
    { date: "2026-09-03", a_equity_krw: 10_160_307, b_equity_krw: 10_109_174 },
    { date: "2026-09-04", a_equity_krw: 10_200_948, b_equity_krw: 10_321_467 },
    { date: "2026-09-08", a_equity_krw: 10_109_139, b_equity_krw: 10_197_609 },
    { date: "2026-09-09", a_equity_krw: 10_169_794, b_equity_krw: 10_360_771 },
    { date: "2026-09-10", a_equity_krw: 10_291_832, b_equity_krw: 10_267_524 },
    { date: "2026-09-11", a_equity_krw: 10_240_373, b_equity_krw: 10_513_945 },
    { date: "2026-09-14", a_equity_krw: 10_271_094, b_equity_krw: 10_324_694 },
    { date: "2026-09-15", a_equity_krw: 10_112_096, b_equity_krw: 10_520_863 },
    { date: "2026-09-16", a_equity_krw: 9_989_103, b_equity_krw: 10_127_036 },
    { date: "2026-09-17", a_equity_krw: 9_900_742, b_equity_krw: 9_868_569 },
    { date: "2026-09-18", a_equity_krw: 9_989_849, b_equity_krw: 10_125_152 },
    { date: "2026-09-21", a_equity_krw: 10_119_717, b_equity_krw: 10_418_781 },
    { date: "2026-09-22", a_equity_krw: 10_079_238, b_equity_krw: 10_304_174 },
    { date: "2026-09-23", a_equity_krw: 10_149_793, b_equity_krw: 10_458_737 },
    { date: "2026-09-24", a_equity_krw: 10_261_441, b_equity_krw: 10_646_994 },
    { date: "2026-09-25", a_equity_krw: 10_199_872, b_equity_krw: 10_465_995 },
    { date: "2026-09-28", a_equity_krw: 10_250_871, b_equity_krw: 10_633_451 },
    { date: "2026-09-29", a_equity_krw: 10_380_000, b_equity_krw: 10_742_000 },
  ],
  same_symbols: [
    {
      symbol: "NVTS",
      a: { entry_usd: 5.1, entry_time: "09:54", exit_usd: 5.71, return_pct: 11.96, holding_minutes: 68 },
      b: { entry_usd: 5.42, entry_time: "10:08", exit_usd: 6.18, return_pct: 14.02, holding_minutes: 34 },
    },
    {
      symbol: "MARA",
      a: { entry_usd: 17.9, entry_time: "09:47", exit_usd: 18.36, return_pct: 2.57, holding_minutes: 91 },
      b: { entry_usd: 18.42, entry_time: "10:21", exit_usd: 19.05, return_pct: 3.42, holding_minutes: 31 },
    },
    {
      symbol: "RGTI",
      a: { entry_usd: 2.44, entry_time: "09:51", exit_usd: 2.29, return_pct: -6.15, holding_minutes: 74 },
      b: { entry_usd: 2.31, entry_time: "10:02", exit_usd: 2.18, return_pct: -5.63, holding_minutes: 14 },
    },
  ],
};
