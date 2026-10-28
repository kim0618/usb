/** Strategy I (MOM12-2) order-less paper: types and fetcher for GET /api/v1/strategy-i/paper.
 *
 *  Strategy I is deliberately not in the strategy registry: it never enters the A/E cards, the paper
 *  gate or the combined board. Every figure here comes from its own paper store; before launch, or
 *  with no completed run, the API returns a status and no figure, and the screen invents none. */

import { apiFetch } from "@/lib/api";

export const STRATEGY_I = "STRATEGY_I_MOM12_2";
export const STRATEGY_I_TAB = { strategy_id: STRATEGY_I, href: "/trading-i", label: "Strategy I · MOM12-2" };

export type IStatus = "NOT_LAUNCHED" | "LAUNCHED_NO_RUN" | "RUNNING" | "DATA_DELAYED" | "INPUT_BLOCKED" | "ERROR";

export type IPosition = { ticker: string; shares: number; avg_cost: number | null; last_close: number; value: number;
  weight: number | null; stale_price: boolean };
export type ITrade = { session: string; k: number; side: string; ticker: string; shares: number; notional: number; cost: number;
  stale_last_close: boolean };
export type IRebalance = { k: number; session: string; signal: string; holdings: number; trade_count: number; cash_pct: number;
  nav_post: number; costs: number; turnover_one_way: number | null };
export type IContinuityEvent = { type: string; session: string; ticker: string; last_close_session?: string | null; reason?: string;
  new?: string };
export type IOverlayObs = { status: string; reason?: string; session: string; signal: string; weights_pct?: Record<string, number>;
  over_cap?: string[]; virtual_forced_exits?: string[]; virtual_blocked_entries?: string[]; unknown_weight_pct?: number };

export type IPaperView = {
  strategy_id: string; display_name: string; status: IStatus; reason?: string | null; launched: boolean | null;
  orders: "NONE"; initial_capital?: string; target_holdings?: number; launch_signal?: string; launched_at?: string | null;
  continuity_contract?: string;
  last_run?: { as_of: string; completed_at: string | null; events: number };
  next_signal?: string | null;
  account?: { as_of: string; nav: number; cash: number | null; positions_value: number; total_pnl: number; total_return: number;
    day_pnl: number; day_return: number | null; day_session: string; holdings: number; operating_sessions: number } | null;
  positions?: IPosition[]; rebalances?: IRebalance[]; trades?: ITrade[];
  overlay?: { virtual: true; affects_book: false; latest: IOverlayObs | null; evaluated_count: number; observations: number };
  flags?: { continuity_unresolved: IContinuityEvent[]; price_stop_held: IContinuityEvent[]; continuity_linked: IContinuityEvent[];
    stale_positions: string[]; data_delay: { as_of: string; expected: string; delayed: boolean };
    pending_signals: string[]; refused_signals: string[]; error: { kind: string; error: string; at: string } | null };
  /** Timer guard decisions read from guard.jsonl. A SKIP is a normal hold under host pressure, not an error. */
  guard?: { latest: IGuardDecision; last_skip: IGuardDecision | null } | null;
};

export type IGuardDecision = { decision: string; at?: string | null; guard?: string | null; exit_code?: number | null;
  reasons?: string[] | null };

export const strategyIApi = { paper: () => apiFetch<IPaperView>("/api/v1/strategy-i/paper") };

export const STATUS_LABEL: Readonly<Record<IStatus, string>> = {
  NOT_LAUNCHED: "미시작", LAUNCHED_NO_RUN: "시작 · 실행 대기", RUNNING: "운영 중", DATA_DELAYED: "데이터 지연",
  INPUT_BLOCKED: "입력 차단", ERROR: "실행 오류",
};
export const STATUS_TONE: Readonly<Record<IStatus, "success" | "warning" | "danger" | "neutral">> = {
  NOT_LAUNCHED: "neutral", LAUNCHED_NO_RUN: "neutral", RUNNING: "success", DATA_DELAYED: "warning", INPUT_BLOCKED: "danger", ERROR: "danger",
};
