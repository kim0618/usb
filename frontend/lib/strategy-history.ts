import { entryReasonLabel, fractionPercent, type EntryFact } from "@/lib/entry-board";
import type { SemanticTone } from "@/lib/display";
import { etTime, formatDecimalString, formatSignedUsd, formatUsd } from "@/lib/format";
import type { EvaluationStatus, StrategyEvaluationCandidate, StrategyEvaluationSummary } from "@/types/api";

/** How one finished outcome reads. The tones match the entry board's own: a normal
 *  exclusion is red, a buy green, a data fault amber, and an unresolved day grey. */
export const EVALUATION_STATUS_META: Readonly<Record<EvaluationStatus, { label: string; tone: SemanticTone; icon: string }>> = {
  TRADED: { label: "매수", tone: "success", icon: "●" },
  PREMARKET_REJECTED: { label: "제외", tone: "danger", icon: "×" },
  OR_REJECTED: { label: "제외", tone: "danger", icon: "×" },
  NO_ENTRY_SIGNAL: { label: "신호 없음", tone: "neutral", icon: "○" },
  EXECUTION_REJECTED: { label: "제외", tone: "danger", icon: "×" },
  DATA_ERROR: { label: "데이터 오류", tone: "warning", icon: "▲" },
  INCOMPLETE: { label: "기록 부족", tone: "neutral", icon: "?" },
  UNKNOWN: { label: "확인 필요", tone: "warning", icon: "▲" },
  IN_PROGRESS: { label: "평가 중", tone: "info", icon: "◐" },
};

/** Reason codes this history adds beyond the entry board's own set. */
const HISTORY_REASON_LABELS: Readonly<Record<string, string>> = {
  INCOMPLETE_LEGACY: "과거 상세 사유 미기록",
  EVALUATION_INCOMPLETE: "평가 도중 중단됨",
  NOT_EVALUATED: "평가 기록 없음",
  NO_DAILY_HISTORY: "일봉 이력 없음",
  NO_EXACT_PREVIOUS_CLOSE: "전일 종가 확인 불가",
  POSITION_ALREADY_OPEN: "이미 보유 중",
  DATE_OWNED_BY_OTHER_CANDIDATE: "다른 후보가 해당 일자 사용",
  DAILY_ENTRY_CAP_REACHED: "일일 신규 진입 한도 도달",
  OPEN_POSITION_CAP_REACHED: "동시 보유 한도 도달",
  ABOVE_VWAP_AND_OR_BREAK: "VWAP·시초 범위 돌파",
};

/** Korean label for a stored reason code; an unrecognised code is flagged, never shown raw. */
export const evaluationReasonLabel = (code: string | null | undefined): string =>
  code != null && code in HISTORY_REASON_LABELS ? HISTORY_REASON_LABELS[code] : entryReasonLabel(code);

export const statusMeta = (status: EvaluationStatus) =>
  EVALUATION_STATUS_META[status] ?? EVALUATION_STATUS_META.UNKNOWN;

const usd = (value: string | null) => value == null ? null : formatUsd(value);

/** The observed values and the thresholds they were judged against, as stored.
 *  A threshold the backend did not record is absent; nothing is recomputed here. */
export function candidateFacts(row: StrategyEvaluationCandidate): EntryFact[] {
  const gapMin = fractionPercent(row.gap_min, true);
  const gapMax = fractionPercent(row.gap_max, true);
  const facts: EntryFact[] = [
    ["Gap", fractionPercent(row.gap_pct, true)],
    ["Gap 기준", gapMin && gapMax ? `${gapMin} ~ ${gapMax}` : null],
    ["V1 거래량 비율", fractionPercent(row.v1_volume_ratio)],
    ["V1 기준", row.v1_volume_min ? `${fractionPercent(row.v1_volume_min)} 이상` : null],
    ["전일 종가", usd(row.previous_close)],
    ["프리마켓 기준가", usd(row.premarket_reference_price)],
    ["Opening Range", row.opening_range_high && row.opening_range_low
      ? `${formatUsd(row.opening_range_low)} ~ ${formatUsd(row.opening_range_high)}` : null],
    ["신호가", usd(row.signal_price)],
    ["초기 Stop", usd(row.initial_stop)],
    ["신호 시각", row.signal_at ? etTime(row.signal_at) : null],
    ["체결 예정 봉", row.intended_entry_bar_at ? etTime(row.intended_entry_bar_at) : null],
    ["체결가", usd(row.fill_price)],
    ["체결 수량", row.fill_quantity ? `${formatDecimalString(row.fill_quantity)}주` : null],
    ["실현 손익", row.realized_pnl == null ? null : formatSignedUsd(row.realized_pnl)],
    ["R 배수", row.net_r == null ? null : `${formatDecimalString(row.net_r)}R`],
    ["오류 발생 횟수", row.error_tick_count > 0 ? `${row.error_tick_count}회` : null],
    ["마지막 오류", row.last_error_code ? evaluationReasonLabel(row.last_error_code) : null],
  ];
  return facts.filter(([, value]) => value !== null);
}

/** The one-line explanation a row shows: the reason, then the detail that qualifies it. */
export function candidateDetail(row: StrategyEvaluationCandidate): string {
  const reason = evaluationReasonLabel(row.final_reason);
  if (row.final_status === "TRADED" || !row.final_detail) return reason;
  return `${reason} · ${evaluationReasonLabel(row.final_detail)}`;
}

/** A day's headline result; a session that traded nothing is a normal, stated outcome. */
export const summaryResultLabel = (summary: StrategyEvaluationSummary): string =>
  summary.result === "TRADED" ? `매수 ${summary.filled_count}건` : "매수 없음";

export const summaryTone = (summary: StrategyEvaluationSummary): SemanticTone =>
  summary.result === "TRADED" ? "success"
    : summary.data_error_count > 0 ? "warning"
      : summary.incomplete_count > 0 ? "neutral" : "danger";

/** A day's realized result. Null means some entry has not closed yet, not zero. */
export const summaryPnlLabel = (summary: StrategyEvaluationSummary): string =>
  summary.realized_pnl == null ? "정산 대기" : formatSignedUsd(summary.realized_pnl);
