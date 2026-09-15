import { formatEntryCapacityReason, formatStrategyPhase, type SemanticTone } from "@/lib/display";
import { etTime, formatDecimalString, formatUsd } from "@/lib/format";
import type { EntryBoardCandidate, EntryBoardThresholds, EntryCapacity, TradingPosition } from "@/types/api";

/** The five display groups of the entry board. Raw phases and reasons stay in hover/detail. */
export type EntryDisplayGroup = "WAITING" | "EVALUATING" | "BOUGHT" | "EXCLUDED" | "SYSTEM";

export const ENTRY_GROUP_META: Readonly<Record<EntryDisplayGroup, { label: string; icon: string; tone: SemanticTone }>> = {
  WAITING: { label: "대기", icon: "○", tone: "neutral" },
  EVALUATING: { label: "평가 중", icon: "◐", tone: "info" },
  BOUGHT: { label: "매수", icon: "●", tone: "success" },
  EXCLUDED: { label: "제외", icon: "×", tone: "danger" },
  SYSTEM: { label: "데이터 오류", icon: "▲", tone: "warning" },
};

/** Backend reason codes: normal Strategy/Risk rejections, i.e. the rule worked as designed. */
const STRATEGY_REJECTIONS = new Set([
  "HUMAN_NOT_APPROVED", "GAP_TOO_LOW", "GAP_TOO_HIGH", "LOW_PREMARKET_VOLUME", "NEGATIVE_CATALYST",
  "RESEARCH_BLOCKED", "ENTRY_DEADLINE_EXPIRED", "ENTRY_PRICE_ABOVE_CEILING", "ENTRY_RISK_REJECTED",
]);
/** Backend reason codes: data, provider or system failures that need an operator's check. */
const SYSTEM_FAILURES = new Set([
  "INVALID_PREMARKET_DATA", "INVALID_MARKET_DATA", "INSUFFICIENT_OPENING_RANGE", "ENTRY_SIGNAL_STALE",
  "ENTRY_SESSION_ENDED", "ENTRY_EXECUTION_REJECTED", "AUTH_FAILED", "PROTECTION_UNAVAILABLE",
  "UNSUPPORTED_EXCHANGE", "EXCHANGE_AUTHORITY_MISSING", "MARKET_DATA_UNAVAILABLE", "MARKET_DATA_STALE",
  "RATE_LIMITED", "INVALID_SYMBOL",
]);

const REASON_LABELS: Readonly<Record<string, string>> = {
  HUMAN_NOT_APPROVED: "사람 승인 없음", GAP_TOO_LOW: "갭 상승폭 기준 미달", GAP_TOO_HIGH: "갭 상승폭 과다",
  LOW_PREMARKET_VOLUME: "프리마켓 거래량 부족", NEGATIVE_CATALYST: "신규 악재 발생", RESEARCH_BLOCKED: "리서치 차단",
  ENTRY_DEADLINE_EXPIRED: "진입 마감 시각 경과", ENTRY_PRICE_ABOVE_CEILING: "체결가 상한 초과",
  ENTRY_RISK_REJECTED: "리스크 기준 거절", INVALID_PREMARKET_DATA: "프리마켓 데이터 부족/이상",
  INVALID_MARKET_DATA: "시세 데이터 이상", INSUFFICIENT_OPENING_RANGE: "시초 범위 데이터 부족",
  ENTRY_SIGNAL_STALE: "체결봉 처리 지연", ENTRY_SESSION_ENDED: "신호 세션 종료",
  ENTRY_EXECUTION_REJECTED: "주문 체결 거절", AUTH_FAILED: "시세 인증 실패",
  PROTECTION_UNAVAILABLE: "보호 주문 확인 불가", UNSUPPORTED_EXCHANGE: "지원하지 않는 거래소",
  EXCHANGE_AUTHORITY_MISSING: "거래소 정보 없음", MARKET_DATA_UNAVAILABLE: "시세 데이터 사용 불가",
  MARKET_DATA_STALE: "시세 데이터 지연", RATE_LIMITED: "시세 요청 한도 초과", INVALID_SYMBOL: "종목 코드 오류",
};

/** Korean label for a backend reason code; an unknown code never reaches the row as raw text. */
export const entryReasonLabel = (code: string | null | undefined): string =>
  code == null || code === "" ? "상세 사유 기록 없음" : REASON_LABELS[code] ?? "기록된 사유 확인 필요";

const WAITING_PHASES: Readonly<Record<string, string>> = {
  RESEARCH_READY: "프리마켓 평가 대기", HUMAN_APPROVED: "프리마켓 평가 대기",
  PREMARKET_PASSED: "Opening Range 대기", OPENING_RANGE_BUILDING: "Opening Range 대기",
};
const EVALUATING_PHASES: Readonly<Record<string, string>> = {
  WAITING_ENTRY: "진입 신호 평가", ENTRY_SIGNALLED: "체결봉 대기",
};
const BOUGHT_PHASES = new Set(["POSITION_OPEN", "PYRAMID_ADDED", "OVERNIGHT_REVIEW", "OVERNIGHT_HELD", "DAY2_ACTIVE", "EXIT_SIGNALLED", "EXITED"]);
const TERMINAL_REJECTIONS = new Set(["PREMARKET_REJECTED", "NO_TRADE", "HUMAN_REJECTED"]);
/** A full cap stops these before entry. ENTRY_SIGNALLED keeps its own phase: whether its order
 *  already holds a pending slot is not visible here, so no cap label is guessed for it. */
const CAPPABLE_PHASES = new Set(["RESEARCH_READY", "HUMAN_APPROVED", "PREMARKET_PASSED", "OPENING_RANGE_BUILDING", "WAITING_ENTRY"]);

export interface EntryDisplay { group: EntryDisplayGroup; label: string; icon: string; tone: SemanticTone; detail: string; reasonCode: string | null }

const shown = (group: EntryDisplayGroup, detail: string, reasonCode: string | null = null): EntryDisplay =>
  ({ group, ...ENTRY_GROUP_META[group], detail, reasonCode });

/** Maps persisted StrategyState (plus the backend capacity block) to one display group and phase. */
export function entryDisplay(row: EntryBoardCandidate, capacityReason: EntryCapacity["blocked_reason"] = null): EntryDisplay {
  if (row.state_conflict) return shown("SYSTEM", "다른 후보의 상태가 이미 기록됨");
  const state = row.state;
  const phase = state?.phase ?? null;
  if (capacityReason && (phase === null || CAPPABLE_PHASES.has(phase))) return shown("WAITING", formatEntryCapacityReason(capacityReason), capacityReason);
  if (phase === null) return shown("WAITING", "프리마켓 평가 대기");
  if (phase in WAITING_PHASES) return shown("WAITING", WAITING_PHASES[phase]);
  if (phase in EVALUATING_PHASES) return shown("EVALUATING", EVALUATING_PHASES[phase]);
  if (BOUGHT_PHASES.has(phase)) return shown("BOUGHT", formatStrategyPhase(phase));
  if (TERMINAL_REJECTIONS.has(phase)) {
    const reason = state?.phase_reason ?? null;
    if (reason !== null && SYSTEM_FAILURES.has(reason)) return shown("SYSTEM", entryReasonLabel(reason), reason);
    if (reason === null || STRATEGY_REJECTIONS.has(reason)) return shown("EXCLUDED", entryReasonLabel(reason), reason);
    return shown("SYSTEM", entryReasonLabel(reason), reason);
  }
  return shown("SYSTEM", "알 수 없는 전략 상태");
}

/** Fraction decimal string ("0.0105") to a percentage label; non-numeric or missing is null. */
export function fractionPercent(value: string | null | undefined, signed = false): string | null {
  if (value == null || value === "") return null;
  const amount = Number(value);
  if (!Number.isFinite(amount)) return null;
  const text = `${(amount * 100).toFixed(2)}%`;
  return signed && amount > 0 ? `+${text}` : text;
}

const usd = (value: string | null | undefined) => value == null ? null : formatUsd(value);
const at = (value: string | null | undefined) => value ? etTime(value) : null;

export type EntryFact = [label: string, value: string | null];

/** Premarket-gate facts exactly as persisted, next to the frozen thresholds. */
export function premarketFacts(row: EntryBoardCandidate, thresholds: EntryBoardThresholds | null): EntryFact[] {
  const premarket = row.premarket;
  const gapMin = fractionPercent(thresholds?.premarket_gap_min_pct, true);
  const gapMax = fractionPercent(thresholds?.premarket_gap_max_pct, true);
  return [
    ["전일 종가", usd(premarket?.previous_close)],
    ["프리마켓 기준가", usd(premarket?.reference_price)],
    ["Gap", fractionPercent(premarket?.gap_pct, true)],
    ["필요 Gap", gapMin && gapMax ? `${gapMin} ~ ${gapMax}` : null],
    ["V1 거래량 비율", fractionPercent(premarket?.volume_ratio)],
    ["V1 기준", thresholds ? `${fractionPercent(thresholds.premarket_volume_ratio_min)} 이상` : null],
  ];
}

/** Signal and fill facts. The signal price becomes the fill price once the state is bought. */
export function executionFacts(row: EntryBoardCandidate, position: TradingPosition | null): EntryFact[] {
  const state = row.state;
  const bought = state !== null && BOUGHT_PHASES.has(state.phase);
  return [
    [bought ? "진입가(체결)" : "신호가", usd(state?.entry_price)],
    ["초기 Stop", usd(state?.initial_stop)],
    ["신호 시각", at(state?.signal_at)],
    ["체결 예정 봉", at(state?.intended_execution_bar_at)],
    ["보유 수량", position?.quantity ? `${formatDecimalString(position.quantity)}주` : null],
  ];
}

/** The one short value a row shows next to its phase, or null when nothing was persisted. */
export function rowHeadline(row: EntryBoardCandidate, position: TradingPosition | null): string | null {
  const state = row.state;
  if (state && BOUGHT_PHASES.has(state.phase) && state.entry_price) {
    const quantity = position?.quantity ? ` · ${formatDecimalString(position.quantity)}주` : "";
    return `${formatUsd(state.entry_price)}${quantity}`;
  }
  if (state?.phase === "ENTRY_SIGNALLED" && state.entry_price) return `신호 ${formatUsd(state.entry_price)}`;
  const gap = fractionPercent(row.premarket?.gap_pct, true);
  return gap ? `Gap ${gap}` : null;
}
