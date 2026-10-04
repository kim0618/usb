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
export const STRATEGY_H = "STRATEGY_H_V2";
export const PROVISIONAL = "PROVISIONAL_RVOL_BOOTSTRAP";
export const OFFICIAL = "OFFICIAL_KIWOOM_PAPER";

/** What a strategy does with capital. A and E place simulated orders (SIMULATION_PAPER); H observes
 *  its own decisions on future data and holds no capital book (FORWARD_SHADOW). Both are operating
 *  modes, so every screen that asks "is this strategy operating" asks about this set. */
export const PAPER_MODES: readonly string[] = ["SIMULATION_PAPER", "FORWARD_SHADOW"];

/** The three states Strategy H's decision engine can produce. Only APPROVE could ever hold a
 *  position, and only once a sizing contract is frozen; WATCH and REJECT never do. */
export const H_APPROVE = "APPROVE";
export const H_WATCH = "WATCH";
export const H_REJECT = "REJECT";

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
  official?: BookSummary; legacy?: BookSummary; paper_clock?: PaperClock | null;
  /** Present only for Strategy H: the counts and states its card shows instead of money. */
  forward?: {
    contract_id: string; launch: HLaunchState; decision_counts: Record<string, number>;
    watchlist: number; rejected: number; approved: number; issuers: number;
    maturity: HMaturity; evaluation: HEvaluation; sizing_contract: string;
  };
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
  /** Strategy H's forward shadow. H is not in `combined` and not in `gate`: it has no capital book
   *  to sum and the A/E paper gate contract does not list it. */
  h_forward?: HForwardView;
  combined_definition?: string;
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
  /** Strategy H only: how many issuers sit in each decision state. */
  decision_counts?: Record<string, number>;
  na?: Record<string, string>;
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
  [STRATEGY_H]: { href: "/strategy-h" },
};

/** "Strategy E · E-MAX V1": the registry's own display name and variant, nothing typed here. */
export function strategyLabel(row: Pick<StrategyRow, "display_name" | "variant_label">): string {
  return row.variant_label ? `${row.display_name} · ${row.variant_label}` : row.display_name;
}

/** The operating tabs: registry rows that are enabled, run as paper, and have a screen.
 *  A closed research strategy is listed on the research history screen instead. */
export function operatingTabs(rows: readonly StrategyRow[]): Array<{ href: string; label: string; strategy_id: string }> {
  return rows
    .filter(row => row.enabled && PAPER_MODES.includes(row.mode) && OPERATING_ROUTES[row.strategy_id])
    .map(row => ({ strategy_id: row.strategy_id, href: OPERATING_ROUTES[row.strategy_id].href, label: strategyLabel(row) }));
}

/** Research lifecycle and operations are separate questions; screens label them with these words. */
export const RESEARCH_LABELS: Readonly<Record<string, string>> = { PASSED_TO_PAPER: "PASSED TO PAPER", CLOSED: "CLOSED" };
export const OPERATION_LABELS: Readonly<Record<string, string>> = {
  RUNNING: "RUNNING", WAITING_SIGNAL: "WAITING SIGNAL", POSITION_OPEN: "POSITION OPEN",
  PAUSED: "PAUSED", DATA_ERROR: "DATA ERROR",
};
export const LIFECYCLE_LABELS: Readonly<Record<string, string>> = {
  PAPER: "ACTIVE · PAPER", FORWARD_SHADOW: "ACTIVE · FORWARD SHADOW", RETIRED: "RETIRED",
};

/** The strategy selector on the comparison screen: ALL, or exactly one strategy. */
export const ALL_STRATEGIES = "ALL";
export function selectorOptions(rows: readonly StrategyRow[]): Array<{ id: string; label: string }> {
  return [{ id: ALL_STRATEGIES, label: "ALL" },
    ...rows.filter(row => row.enabled && PAPER_MODES.includes(row.mode))
      .map(row => ({ id: row.strategy_id, label: row.short_name || row.display_name }))];
}

/** Strategy H's forward shadow. None of these shapes exist for A or E: a decision cohort is not a
 *  trade ledger, and forcing it into one would mean printing empty trade columns beside a WATCH. */
export type HOutcome = {
  ticker: string; horizon_sessions: number; baseline_session: string; benchmark: string;
  maturity_session: string | null; state: "MATURED" | "PENDING" | "INCOMPLETE" | "NO_BASELINE";
  reason?: string; missing_sessions?: string[];
  sessions_observed?: number; sessions_required?: number;
  baseline_price?: number; maturity_price?: number;
  security_return?: number | null; benchmark_return?: number | null; excess_return?: number | null;
  mfe?: number | null; mae?: number | null;
  tp1_hit?: boolean | null; tp2_hit?: boolean | null; bear_breach?: boolean | null;
  bear_na_reason?: string | null;
};

export type HCohortRow = {
  strategy_id: string; ticker: string; cik: string | null; security_id: string | null;
  cohort_tag: string | null;
  decision: string | null; previous_decision: string | null; decision_at_launch: string | null;
  decision_time: string | null; effective_session: string | null; thesis_version: string | null;
  decision_changes: number;
  position: null; open_positions: number; position_reason: string;
  d4_expectation_gap: string | null; d4_gap_confidence: string | null;
  valuation_method: string | null; valuation_window: string | null;
  valuation_confidence: string | null; valuation_status: string | null;
  /** `null` with `bear_na_reason` when the Bear leg was refused. Never rendered as 0. */
  bear: number | null; bear_na_reason: string | null;
  tp1: number | null; tp2: number | null;
  tp1_upside_at_decision: number | null; tp2_upside_at_decision: number | null;
  range_complete: boolean | null;
  key_binding_clause: string | null;
  approve_blockers: string[]; reject_fired: string[]; watch_matched: string[];
  decision_close: number | null; current_price: number | null; current_price_session: string | null;
  tp1_distance: number | null; tp2_distance: number | null; bear_distance: number | null;
  pre_launch_drift: { decision_session: string; decision_close: number | null;
                      baseline_session: string; baseline_close: number | null;
                      return_since_decision: number | null; is_forward_evidence: false; note: string };
  forward: Record<string, HOutcome>;
  checksums: Record<string, string | null>;
};

export type HLaunchState = {
  status: "LAUNCHED" | "NOT_LAUNCHED"; launched_at: string | null; baseline_session: string | null;
  decision_session: string; d5_contract?: string; issuers: number; cohort_tags?: string[];
  reason?: string;
};

export type HMaturity = Record<string, { matured: number; pending: number; incomplete: number;
                                         no_baseline: number; maturity_session: string | null }>;

export type HEvaluation = {
  strategy_id: string; contract_id: string; state: string; verdict: string; reasons: string[];
  sample_needed: Record<string, number>; sample_reached: Record<string, boolean>;
  maturity: HMaturity; under_ae_paper_gate: false; note: string;
};

export type HForwardView = {
  available?: boolean; strategy_id: string;
  contract: { contract_id: string; contract_sha256: string; step: string; research_build: string;
              forward_observation: string; d5_contract: string; d6_contract: string;
              decision_session: string; horizons: number[]; benchmark: string;
              sizing_contract: string; sample_needed: Record<string, number>;
              preregistered_forward_questions: string[] };
  launch: HLaunchState;
  decision_counts: Record<string, number>;
  maturity: HMaturity; evaluation: HEvaluation;
  position_rule: { watch_creates_position: boolean; reject_creates_position: boolean;
                   approve_creates_position: boolean; approve_creates_entry_candidate: boolean;
                   sizing_contract: string; why: string };
  price_store: { source: string; latest_session: string | null; sessions_observed: number };
  rows: HCohortRow[]; ledger_rows: number; snapshot_rows: number;
};

export type HIssuerView = HCohortRow & {
  available?: boolean; state?: string; reason?: string;
  thesis: { d3: Record<string, unknown> | null; d4: Record<string, unknown> | null;
            d5: Record<string, unknown> | null; d6: Record<string, unknown> | null };
  decision_history: Array<Record<string, unknown>>;
  launch_snapshot: Record<string, unknown>;
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
  forward: (id: string = STRATEGY_H) =>
    apiFetch<HForwardView>(`/api/v1/strategies/${encodeURIComponent(id)}/forward`),
  forwardIssuer: (ticker: string, id: string = STRATEGY_H) =>
    apiFetch<HIssuerView>(`/api/v1/strategies/${encodeURIComponent(id)}/forward/${encodeURIComponent(ticker)}`),
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
