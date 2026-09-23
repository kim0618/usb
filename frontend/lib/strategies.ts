/** The multi-strategy read layer: one shape per question, one row per strategy in the registry.
 *
 *  Screens are built from this registry rather than from hardcoded A/B branches, so a strategy that
 *  reaches paper later shows up by appearing in `GET /strategies`. Nothing here composes a figure
 *  across strategies: A and E keep separate accounts, positions, trades and equity, and Strategy E
 *  keeps its provisional and official books apart on top of that. */

import { apiFetch } from "@/lib/api";

export const STRATEGY_A = "STRATEGY_A";
export const STRATEGY_E = "STRATEGY_E_MAX_V1";
export const STRATEGY_B = "STRATEGY_B";
export const PROVISIONAL = "PROVISIONAL_RVOL_BOOTSTRAP";
export const OFFICIAL = "OFFICIAL_KIWOOM_PAPER";

export type StrategyRow = {
  strategy_id: string; display_name: string; version: string; enabled: boolean; mode: string;
  market_data_source: string; note?: string;
  runtime_status: string | null; paper_status: string | null; evidence_status: string | null;
  session: string | null;
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

/** Where each strategy's operating screen lives. The route and its label are a frontend concern;
 *  which of them is actually operating is the registry's answer, never a hardcoded pair here. */
export const OPERATING_ROUTES: Readonly<Record<string, { href: string; label: string }>> = {
  [STRATEGY_A]: { href: "/trading", label: "전략 A · 기존 전략" },
  [STRATEGY_E]: { href: "/strategy-e", label: "전략 E · E-MAX V1" },
  [STRATEGY_B]: { href: "/trading-b", label: "전략 B · 실시간 모멘텀" },
};

/** The operating tabs: registry rows that are enabled, run as paper, and have a screen.
 *  A closed research strategy keeps its route and its screens; it just stops being a tab. */
export function operatingTabs(rows: readonly StrategyRow[]): Array<{ href: string; label: string; strategy_id: string }> {
  return rows
    .filter(row => row.enabled && row.mode === "SIMULATION_PAPER" && OPERATING_ROUTES[row.strategy_id])
    .map(row => ({ strategy_id: row.strategy_id, ...OPERATING_ROUTES[row.strategy_id] }));
}

export const strategiesApi = {
  list: () => apiFetch<StrategyRow[]>("/api/v1/strategies"),
  status: (id: string) => apiFetch<StrategyStatus>(`/api/v1/strategies/${encodeURIComponent(id)}/status`),
  account: (id: string) => apiFetch<StrategyAccount>(`/api/v1/strategies/${encodeURIComponent(id)}/account`),
  positions: (id: string) => apiFetch<StrategyPosition[]>(`/api/v1/strategies/${encodeURIComponent(id)}/positions`),
  trades: (id: string, limit = 50) =>
    apiFetch<StrategyTrade[]>(`/api/v1/strategies/${encodeURIComponent(id)}/trades?limit=${limit}`),
  equity: (id: string) => apiFetch<StrategyEquity>(`/api/v1/strategies/${encodeURIComponent(id)}/equity`),
};

/** Everything the dashboard needs, fetched per strategy and never summed across them. */
export async function dashboardStrategies(): Promise<Array<{
  row: StrategyRow; status: StrategyStatus; account: StrategyAccount; equity: StrategyEquity;
}>> {
  const rows = (await strategiesApi.list()).filter(row => row.enabled);
  return Promise.all(rows.map(async row => ({
    row,
    status: await strategiesApi.status(row.strategy_id),
    account: await strategiesApi.account(row.strategy_id),
    equity: await strategiesApi.equity(row.strategy_id),
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
