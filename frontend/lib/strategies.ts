/** The multi-strategy read layer: one shape per question, one row per strategy in the registry.
 *
 *  Screens are built from this registry rather than from hardcoded A/B branches, so a strategy that
 *  reaches paper later shows up by appearing in `GET /strategies`. Nothing here composes a figure
 *  across strategies: A and E keep separate accounts, positions, trades and equity, and Strategy E
 *  keeps its provisional and official books apart on top of that. */

import { apiFetch } from "@/lib/api";

/** Internal ids, exactly as the backend and every ledger key them. E's id is not renamed: its books
 *  and runtime files are written under STRATEGY_E_MAX_V1. What an operator reads ("Strategy E")
 *  is the registry's display_name, never a string typed into a screen. */
export const STRATEGY_A = "STRATEGY_A";
export const STRATEGY_E = "STRATEGY_E_MAX_V1";
export const PROVISIONAL = "PROVISIONAL_RVOL_BOOTSTRAP";
export const OFFICIAL = "OFFICIAL_KIWOOM_PAPER";

export type StrategyRow = {
  strategy_id: string; display_name: string; version: string; enabled: boolean; mode: string;
  market_data_source: string; note?: string;
  short_name?: string; variant_label?: string;
  /** What research concluded (PASSED_TO_PAPER / CLOSED) and what operations does with it (PAPER / RETIRED). */
  research_lifecycle?: string; lifecycle?: string; closed_on?: string | null; closeout?: string | null;
  runtime_status: string | null; paper_status: string | null; evidence_status: string | null;
  session: string | null;
};

/** One operating strategy's card, as GET /strategies/cards answers it. */
export type StrategyCardData = {
  strategy_id: string; display_name: string; short_name?: string; variant_label?: string; version: string;
  research_lifecycle: string; lifecycle: string; operational_status: string; runtime_status: string | null;
  evidence_status: string | null; currency: string;
  equity: string | null; initial_equity: string | null; open_positions: number;
  today_realized_pnl: string | null; today_unrealized_pnl: string | null;
  /** Official = ACCOUNTING_V1 from the official start; legacy = V0 rows, reference only. */
  net_pnl: string | null; equity_change?: string | null; trades: number;
  official?: BookSummary; legacy?: BookSummary; paper_clock?: PaperClock;
  last_signal_at: string | null; last_trade_at: string | null; na: Record<string, string>;
};

export type BookSummary = { trades: number; net_pnl: string | null; accounting_versions: string[] };
export type PaperClock = {
  paper_evaluation_version: string; accounting_version: string; official_paper_start: string | null;
  status: "STARTED" | "NOT_STARTED"; gate_contract: string;
};

export type StrategyMetrics = {
  strategy_id: string; trades: number; unreconciled_trades: number; operating_sessions: number;
  gross_pnl: string | null; costs: string | null; net_pnl: string | null; return: string | null;
  win_rate: string | null; pf: string | null; expectancy: string | null; mdd: string | null; mdd_amount: string | null;
  avg_mfe: string | null; avg_mae: string | null; avg_holding_seconds: number | null;
  initial_equity: string | null; equity_change: string | null; ledger_vs_equity_gap: string | null;
  accounting_mix: string[]; na: Record<string, string>; definition?: string; accounting_version?: string;
};

export type GateCondition = { condition: string; value: string | null; threshold: string; status: string; note: string | null };
export type GateResult = {
  strategy_id: string; contract_id: string; verdict: "PASS" | "INCONCLUSIVE" | "FAIL"; reasons: string[];
  failed: string[]; pending: string[]; conditions: GateCondition[]; accounting: string[];
  error_rate: string | null; final: boolean;
};

export type PerformanceBoard = {
  currency: string; paper_clock: PaperClock;
  /** Per strategy: the official (V1) book the gate reads, and the legacy (V0) book kept for reference. */
  strategies: Record<string, { strategy_id: string; official: StrategyMetrics; legacy: StrategyMetrics }>;
  combined: StrategyMetrics; legacy_combined: StrategyMetrics;
  gate: Record<string, GateResult>; books: Record<string, string>;
  excluded: Record<string, { trades: number; sessions: number; reason: string }>;
  e_sessions: Array<{ session: string; phase: string | null; error: boolean }>;
};

export type Overlap = { both: number; either: number; a_only: number; e_only: number; share_of_either: string | null; symbols?: string[] };
export type PortfolioView = {
  currency: string; book: "official" | "legacy"; accounting_version: string; paper_clock: PaperClock;
  baseline: {
    mode: string; risk_budget: Record<string, string>; allocation_rule: string; definition: string;
    window: { start: string; end: string } | null; days: number;
    combined_return?: string | null; combined_mdd?: string | null; correlation?: string | null;
    losing_day_overlap?: Overlap; winning_day_overlap?: Overlap; symbol_overlap: Overlap;
    same_session_symbol_collisions: string[]; sector_overlap: null;
    equity_curve: Array<{ date: string; r_a: string; r_e: string; combined: string; index: string }>;
    na: Record<string, string>;
  };
  combined: { net_pnl: string | null; mdd: string | null; mdd_amount?: string | null; initial_equity?: string | null; equity_change?: string | null; trades?: number };
  exposure: {
    per_strategy: Record<string, { open_positions: number; cost_basis: string | null }>;
    per_symbol: Array<{ symbol: string; quantity: string; cost_basis: string | null;
                        by_strategy: Record<string, { quantity: string; cost_basis: string | null }> }>;
    total_cost_basis: string | null;
  };
  cash_usage: Record<string, { cash: string | null; equity: string | null; cash_share: string | null; note?: string }>;
};

export type BootstrapState = {
  available: boolean; status: string; symbols?: number; ready?: number; partial?: number; zero?: number;
  history_exhausted?: number; target_sessions?: number; minimum_sessions?: number; percent?: number | null;
  last_update?: string | null; estimated_completion?: string | null; estimate_note?: string; reason?: string;
};

export type UniverseState = {
  available: boolean; status?: string; file?: string; target_session?: string | null;
  asof_session?: string | null; symbol_count?: number | null; source?: string; digest?: string | null;
  generated_at?: string | null; identity?: string; reason?: string;
};

export type StrategyStatus = StrategyRow & {
  available: boolean;
  detail: {
    market_data_source?: string; rvol_threshold?: number | null; rvol_window?: number | null;
    canonical_universe?: number | null; rvol_ready?: number | null; rvol_missing?: number | null;
    market_data_unavailable?: number | null; sparse_no_premarket?: number | null;
    h5_true?: number | null; h5_false?: number | null; h5_unknown?: number | null;
    selected?: string[] | null; no_decision_reason?: string | null;
    bootstrap?: BootstrapState; universe?: UniverseState; account_id?: number | null;
  };
  last_decision?: string | null; last_update?: string | null;
};

export type StrategyAccount = {
  strategy_id: string; display_name?: string; available: boolean; currency: string | null;
  initial_equity: string | null; current_equity: string | null; today_pnl: string | null;
  total_pnl: string | null; open_positions: number; closed_trades_today: number | null;
  source: string; evidence_status?: string | null; empty_reason?: string | null;
  books?: Record<string, { present: boolean; initial_equity: string | null; equity: string | null;
                           realized_pnl: string | null; sessions: number }>;
};

export type StrategyPosition = {
  strategy_id: string; symbol: string; quantity: string | number | null;
  average_price: string | null; cost_basis: string | null; opened_at: string | null;
  evidence_status?: string | null; source: string;
};

export type StrategyTrade = {
  strategy_id: string; session?: string | null; symbol: string; status: string | null;
  entry_price: string | null; exit_price: string | null; net_pnl: string | null;
  exit_reason: string | null; evidence_status?: string | null; source: string;
};

export type StrategyEquity = {
  strategy_id: string; currency: string; source: string; points: Array<{ date: string; equity: string | null }>;
  baseline: string | null; current_book?: string;
  books?: Record<string, { points: Array<{ date: string; equity: string | null }>; baseline: string | null }>;
  note?: string;
};

/** Where each operating strategy's screen lives. Only the route is a frontend concern; the label
 *  comes from the registry (strategyLabel) and which strategies operate is the registry's answer. */
export const OPERATING_ROUTES: Readonly<Record<string, { href: string }>> = {
  [STRATEGY_A]: { href: "/trading" },
  [STRATEGY_E]: { href: "/strategy-e" },
};

/** "Strategy E · E-MAX V1": the registry's own display name and variant, nothing typed here. */
export function strategyLabel(row: Pick<StrategyRow, "display_name" | "variant_label">): string {
  return row.variant_label ? `${row.display_name} · ${row.variant_label}` : row.display_name;
}

/** The operating tabs: registry rows that are enabled, run as paper, and have a screen.
 *  A closed research strategy is listed on the research history screen instead. */
export function operatingTabs(rows: readonly StrategyRow[]): Array<{ href: string; label: string; strategy_id: string }> {
  return rows
    .filter(row => row.enabled && row.mode === "SIMULATION_PAPER" && OPERATING_ROUTES[row.strategy_id])
    .map(row => ({ strategy_id: row.strategy_id, href: OPERATING_ROUTES[row.strategy_id].href, label: strategyLabel(row) }));
}

/** Research lifecycle and operations are separate questions; screens label them with these words. */
export const RESEARCH_LABELS: Readonly<Record<string, string>> = { PASSED_TO_PAPER: "PASSED TO PAPER", CLOSED: "CLOSED" };
export const OPERATION_LABELS: Readonly<Record<string, string>> = {
  RUNNING: "RUNNING", WAITING_SIGNAL: "WAITING SIGNAL", POSITION_OPEN: "POSITION OPEN",
  PAUSED: "PAUSED", DATA_ERROR: "DATA ERROR",
};

export const strategiesApi = {
  list: () => apiFetch<StrategyRow[]>("/api/v1/strategies"),
  status: (id: string) => apiFetch<StrategyStatus>(`/api/v1/strategies/${encodeURIComponent(id)}/status`),
  account: (id: string) => apiFetch<StrategyAccount>(`/api/v1/strategies/${encodeURIComponent(id)}/account`),
  positions: (id: string) => apiFetch<StrategyPosition[]>(`/api/v1/strategies/${encodeURIComponent(id)}/positions`),
  trades: (id: string, limit = 50) =>
    apiFetch<StrategyTrade[]>(`/api/v1/strategies/${encodeURIComponent(id)}/trades?limit=${limit}`),
  equity: (id: string) => apiFetch<StrategyEquity>(`/api/v1/strategies/${encodeURIComponent(id)}/equity`),
  cards: () => apiFetch<StrategyCardData[]>("/api/v1/strategies/cards"),
  performance: () => apiFetch<PerformanceBoard>("/api/v1/strategies/performance"),
  portfolio: () => apiFetch<PortfolioView>("/api/v1/strategies/portfolio"),
};

/** Everything the dashboard needs, fetched per strategy and never summed across them. */
export async function dashboardStrategies(): Promise<Array<{
  row: StrategyRow; status: StrategyStatus; account: StrategyAccount; equity: StrategyEquity; card?: StrategyCardData;
}>> {
  const [rows, cards] = await Promise.all([strategiesApi.list(), strategiesApi.cards()]);
  return Promise.all(rows.filter(row => row.enabled).map(async row => ({
    row,
    status: await strategiesApi.status(row.strategy_id),
    account: await strategiesApi.account(row.strategy_id),
    equity: await strategiesApi.equity(row.strategy_id),
    card: cards.find(card => card.strategy_id === row.strategy_id),
  })));
}

/** The label a card shows instead of a PnL figure when the strategy has produced no trade yet. */
export function emptyLabel(account: StrategyAccount): string | null {
  if (account.current_equity != null || (account.books && Object.values(account.books).some(b => b.sessions > 0))) {
    return null;
  }
  if (account.evidence_status === OFFICIAL) return "아직 official paper 거래 없음";
  if (account.evidence_status === PROVISIONAL) return "PROVISIONAL · RVOL 부트스트랩 중";
  return account.empty_reason || "아직 기록된 거래 없음";
}

export function evidenceLabel(status: string | null | undefined): string {
  if (status === PROVISIONAL) return "PROVISIONAL PAPER";
  if (status === OFFICIAL) return "OFFICIAL PAPER";
  return "-";
}

export function bootstrapLabel(state: BootstrapState | undefined): string {
  if (!state || !state.available) return "-";
  const ready = state.ready ?? 0;
  const total = state.symbols ?? 0;
  const percent = state.percent == null ? "" : ` · ${state.percent}%`;
  return `${ready.toLocaleString()} / ${total.toLocaleString()}${percent}`;
}
