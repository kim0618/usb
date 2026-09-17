/** MOCK DATA - Strategy B closed trades and performance.
 *
 *  The six closed trades are the mock session's realized side: they sum to +KRW 60,200,
 *  which together with the two open positions (+KRW 91,200 and +KRW 32,600) makes the
 *  +KRW 184,000 the overview reports as today's P&L. Wins and losses are both present
 *  on purpose; this screen must never look like a strategy that only wins.
 */

import type { StrategyPerformance, StrategyTrade } from "@/types/strategy-b";

const ET = (time: string) => `2026-09-16T${time}-04:00`;

export const mockTrades: StrategyTrade[] = [
  {
    id: "B-2026-09-16-001", symbol: "PRSO", setup: "HOD_BREAKOUT",
    entry_usd: 4.12, exit_usd: 4.38, pnl_krw: 58_400, r_multiple: 1.2,
    holding_minutes: 22, exit_reason: "PARTIAL_TRAIL", result: "WIN", exited_at: ET("09:58:12"),
  },
  {
    id: "B-2026-09-16-002", symbol: "RGTI", setup: "HOD_BREAKOUT",
    entry_usd: 2.31, exit_usd: 2.18, pnl_krw: -41_300, r_multiple: -1.0,
    holding_minutes: 14, exit_reason: "HARD_STOP", result: "LOSS", exited_at: ET("10:11:47"),
  },
  {
    id: "B-2026-09-16-003", symbol: "MARA", setup: "FIRST_PULLBACK",
    entry_usd: 18.42, exit_usd: 19.05, pnl_krw: 74_600, r_multiple: 1.6,
    holding_minutes: 31, exit_reason: "TRAILING_STOP", result: "WIN", exited_at: ET("10:24:03"),
  },
  {
    id: "B-2026-09-16-004", symbol: "WISA", setup: "HOD_BREAKOUT",
    entry_usd: 1.95, exit_usd: 1.88, pnl_krw: -23_100, r_multiple: -0.7,
    holding_minutes: 9, exit_reason: "HARD_STOP", result: "LOSS", exited_at: ET("10:39:55"),
  },
  {
    id: "B-2026-09-16-005", symbol: "BTAI", setup: "FIRST_PULLBACK",
    entry_usd: 6.11, exit_usd: 6.02, pnl_krw: -18_900, r_multiple: -0.5,
    holding_minutes: 26, exit_reason: "TIME_STOP", result: "LOSS", exited_at: ET("10:47:20"),
  },
  {
    id: "B-2026-09-16-006", symbol: "CRKN", setup: "HOD_BREAKOUT",
    entry_usd: 3.18, exit_usd: 3.29, pnl_krw: 10_500, r_multiple: 0.3,
    holding_minutes: 41, exit_reason: "EOD_EXIT", result: "WIN", exited_at: ET("11:02:36"),
  },
];

/** Period performance of Strategy B over the same common period the compare screen uses. */
export const mockPerformance: StrategyPerformance = {
  total_return_pct: 7.42,
  net_pnl_krw: 742_000,
  trades: 37,
  win_rate_pct: 48.6,
  profit_factor: 1.79,
  expectancy_r: 0.31,
  max_drawdown_pct: -6.2,
  average_r: 0.34,
  average_win_krw: 93_400,
  average_loss_krw: -49_400,
};
