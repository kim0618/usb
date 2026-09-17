/** MOCK DATA - the dashboard's own additions only.
 *
 *  Everything the dashboard can take from an existing mock is taken from there
 *  (mocks/strategy-comparison.ts for Strategy A's period figures, mocks/strategy-b.ts and
 *  mocks/strategy-b-trades.ts for Strategy B). This file holds just the two things no
 *  other mock has: Strategy A's current session, and the Strategy A activity lines.
 */

import type { StrategyEvent } from "@/types/dashboard";
import type { StrategyRunState } from "@/types/strategy-b";

const ET = (time: string) => `2026-09-16T${time}-04:00`;

/** Strategy A's current session. Its equity, return and initial capital are NOT here:
 *  those are the comparison mock's, so the dashboard and /strategy-compare cannot drift. */
export const mockStrategyAToday: {
  status: StrategyRunState; today_pnl_krw: number; open_positions: number; closed_trades: number;
} = {
  status: "RUNNING",
  today_pnl_krw: 82_000,
  open_positions: 1,
  closed_trades: 2,
};

/** Strategy A activity. Strategy B's lines are derived from its own candidate mock rather
 *  than written again here, so a timestamp exists in exactly one place. */
export const mockStrategyAEvents: StrategyEvent[] = [
  { id: "A-1", at: ET("10:02:11"), strategy: "A", symbol: "TSLA", message: "시초 범위 돌파 · 진입 완료" },
  { id: "A-2", at: ET("09:47:35"), strategy: "A", symbol: "MARA", message: "프리마켓 게이트 통과" },
  { id: "A-3", at: ET("09:31:08"), strategy: "A", symbol: "WISA", message: "갭 상승폭 기준 미달 · 제외" },
];
