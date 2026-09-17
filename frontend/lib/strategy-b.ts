import type { SemanticTone } from "@/lib/display";
import type {
  CandidateState, ExitReason, MomentumCandidate, PositionState, SetupType,
  StrategyAccount, StrategyPosition, StrategyRunState, StrategyTrade, TradeResult,
} from "@/types/strategy-b";

/** The scanner lifecycle in order. The candidate detail timeline walks this list. */
export const CANDIDATE_LIFECYCLE: readonly CandidateState[] = [
  "DETECTED", "QUALIFIED", "WATCHING", "SETUP_READY", "ENTRY_SIGNALLED", "ENTERED",
];

/** Display-only semantics for one scanner state. The raw code stays available for the
 *  detail panel, the same way the entry board shows its reason codes. */
export const CANDIDATE_STATE_META: Readonly<Record<CandidateState, { label: string; tone: SemanticTone; icon: string }>> = {
  DETECTED: { label: "포착", tone: "neutral", icon: "○" },
  QUALIFIED: { label: "조건 충족", tone: "info", icon: "◔" },
  WATCHING: { label: "감시 중", tone: "info", icon: "◐" },
  SETUP_READY: { label: "진입 준비", tone: "warning", icon: "◑" },
  ENTRY_SIGNALLED: { label: "진입 신호", tone: "success", icon: "▲" },
  ENTERED: { label: "진입 완료", tone: "indigo", icon: "●" },
  REJECTED: { label: "제외", tone: "danger", icon: "×" },
  EXPIRED: { label: "기회 소멸", tone: "neutral", icon: "—" },
};

/** V1 trades two setups. A third pattern needs the engine before it needs a label. */
export const SETUP_META: Readonly<Record<SetupType, { label: string }>> = {
  HOD_BREAKOUT: { label: "당일 고가 돌파" },
  FIRST_PULLBACK: { label: "첫 눌림목" },
};

export const EXIT_REASON_LABELS: Readonly<Record<ExitReason, string>> = {
  HARD_STOP: "손절 청산",
  PARTIAL_TRAIL: "분할 익절 + 트레일",
  TRAILING_STOP: "트레일링 청산",
  TIME_STOP: "시간 청산",
  EOD_EXIT: "장 마감 청산",
};

export const POSITION_STATE_LABELS: Readonly<Record<PositionState, string>> = {
  OPEN: "보유 중", SCALING_OUT: "분할 청산 중", CLOSING: "청산 진행",
};

export const RUN_STATE_LABELS: Readonly<Record<StrategyRunState, string>> = {
  RUNNING: "가동 중", PAUSED: "일시 정지", STOPPED: "정지",
};

export const TRADE_RESULT_META: Readonly<Record<TradeResult, { label: string; tone: SemanticTone }>> = {
  WIN: { label: "이익", tone: "success" },
  LOSS: { label: "손실", tone: "danger" },
  BREAKEVEN: { label: "본전", tone: "neutral" },
};

/** Why a candidate left the pool. An unmapped code is shown as-is, never hidden. */
const DROP_REASON_LABELS: Readonly<Record<string, string>> = {
  SPREAD_TOO_WIDE: "호가 스프레드 과다",
  SETUP_WINDOW_EXPIRED: "셋업 유효 시간 초과",
  RVOL_COLLAPSED: "거래량 강도 소멸",
  DOLLAR_VOLUME_TOO_LOW: "거래대금 부족",
};

export const dropReasonLabel = (code: string | null | undefined): string =>
  code == null ? "-" : DROP_REASON_LABELS[code] ?? code;

export const setupLabel = (setup: SetupType | null | undefined): string =>
  setup == null ? "-" : SETUP_META[setup].label;

export const stateMeta = (state: CandidateState) => CANDIDATE_STATE_META[state];

/** Values arriving in percent units (7.42 means +7.42%), unlike lib/format's fraction helpers. */
export const signedPct = (value: number | null | undefined, digits = 2): string =>
  value == null ? "-" : `${value > 0 ? "+" : ""}${value.toFixed(digits)}%`;

export const plainPct = (value: number | null | undefined, digits = 2): string =>
  value == null ? "-" : `${value.toFixed(digits)}%`;

export const signedR = (value: number | null | undefined): string =>
  value == null ? "-" : `${value > 0 ? "+" : ""}${value.toFixed(2)}R`;

export const rvolText = (value: number | null | undefined): string =>
  value == null ? "-" : value.toFixed(1);

/** Holding time as the desk reads it: minutes, then hours and minutes past an hour. */
export const holdingTime = (minutes: number | null | undefined): string => {
  if (minutes == null) return "-";
  if (minutes < 60) return `${minutes}분`;
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  return rest === 0 ? `${hours}시간` : `${hours}시간 ${rest}분`;
};

export const numericTone = (value: number): SemanticTone =>
  value > 0 ? "success" : value < 0 ? "danger" : "neutral";

export const numericToneClass = (value: number): string =>
  value > 0 ? "text-success" : value < 0 ? "text-danger" : "text-foreground-secondary";

/** Display-only score bands, in the spirit of lib/display's research bands. */
export const scoreTone = (value: number): SemanticTone =>
  value >= 85 ? "success" : value >= 70 ? "info" : value >= 55 ? "neutral" : "warning";

/** The only sortable scanner columns. Everything else keeps the scanner's own order. */
export type ScannerSortKey = "score" | "change_1m_pct" | "change_3m_pct" | "change_5m_pct" | "rvol";
export type SortDirection = "asc" | "desc";

export const SCANNER_SORT_KEYS: ReadonlyArray<{ key: ScannerSortKey; label: string }> = [
  { key: "score", label: "Score" },
  { key: "change_1m_pct", label: "1m" },
  { key: "change_3m_pct", label: "3m" },
  { key: "change_5m_pct", label: "5m" },
  { key: "rvol", label: "RVOL" },
];

export interface ScannerFilter {
  query: string;
  state: CandidateState | "ALL";
  setup: SetupType | "ALL";
}

export const EMPTY_SCANNER_FILTER: ScannerFilter = { query: "", state: "ALL", setup: "ALL" };

/** Symbol search, state and setup filters, then the chosen sort. Pure and order-stable:
 *  equal values keep the scanner's own order so rows do not jump between renders. */
export function visibleCandidates(
  rows: readonly MomentumCandidate[],
  filter: ScannerFilter,
  sort: { key: ScannerSortKey; direction: SortDirection } | null,
): MomentumCandidate[] {
  const query = filter.query.trim().toUpperCase();
  const filtered = rows.filter(row =>
    (query === "" || row.symbol.toUpperCase().includes(query))
    && (filter.state === "ALL" || row.state === filter.state)
    && (filter.setup === "ALL" || row.setup === filter.setup));
  if (!sort) return filtered;
  return filtered
    .map((row, index) => ({ row, index }))
    .sort((left, right) => {
      const delta = left.row[sort.key] - right.row[sort.key];
      if (delta !== 0) return sort.direction === "asc" ? delta : -delta;
      return left.index - right.index;
    })
    .map(entry => entry.row);
}

/** Which lifecycle steps a candidate has actually reached, with the recorded timestamps.
 *  A step without a timestamp is pending, never back-filled. */
export function lifecycleSteps(candidate: MomentumCandidate): Array<{ state: CandidateState; at: string | null; reached: boolean }> {
  const timestamps: Readonly<Record<string, string | null>> = {
    DETECTED: candidate.detected_at, QUALIFIED: candidate.qualified_at, WATCHING: candidate.watching_at,
    SETUP_READY: candidate.setup_ready_at, ENTRY_SIGNALLED: candidate.signal_at, ENTERED: candidate.entered_at,
  };
  return CANDIDATE_LIFECYCLE.map(state => ({ state, at: timestamps[state] ?? null, reached: timestamps[state] != null }));
}

/** The account view: what is invested, what is left, what today produced. Derived from the
 *  same positions and trades the screen lists, so the cards cannot disagree with the tables. */
export function strategyAccount(equityKrw: number, positions: readonly StrategyPosition[], trades: readonly StrategyTrade[]): StrategyAccount {
  const invested = positions.reduce((total, position) => total + position.market_value_krw, 0);
  return {
    equity_krw: equityKrw,
    invested_krw: invested,
    cash_krw: equityKrw - invested,
    unrealized_pnl_krw: positions.reduce((total, position) => total + position.pnl_krw, 0),
    realized_pnl_krw: trades.reduce((total, trade) => total + trade.pnl_krw, 0),
  };
}

/** One setup's contribution, grouped from the closed trades themselves. */
export interface SetupBreakdown { setup: SetupType; trades: number; wins: number; net_pnl_krw: number; average_r: number }

export function setupBreakdown(trades: readonly StrategyTrade[]): SetupBreakdown[] {
  return (Object.keys(SETUP_META) as SetupType[]).map(setup => {
    const rows = trades.filter(trade => trade.setup === setup);
    return {
      setup,
      trades: rows.length,
      wins: rows.filter(trade => trade.result === "WIN").length,
      net_pnl_krw: rows.reduce((total, trade) => total + trade.pnl_krw, 0),
      average_r: rows.length === 0 ? 0 : rows.reduce((total, trade) => total + trade.r_multiple, 0) / rows.length,
    };
  }).filter(row => row.trades > 0);
}

/** Return and R recomputed from the stored prices, so a position row can never show a
 *  percentage that disagrees with its own entry, price and stop. */
export const derivedReturnPct = (entry: number, current: number): number => (current / entry - 1) * 100;
export const derivedR = (entry: number, current: number, stop: number): number =>
  entry === stop ? 0 : (current - entry) / (entry - stop);
