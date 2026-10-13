/** The operator-facing reading of Strategy E's session record.
 *
 *  Pure labelling of fields the status endpoint already returns: no rule is re-run and no figure is
 *  recomputed. Raw enums, digests and artifact paths never reach the screen; a value this table does
 *  not know becomes a neutral "상세 확인 필요" rather than the raw string. */

import type { StrategyAccount, StrategyStatus } from "@/lib/strategies";

export type EntryCondition = "충족" | "미충족" | "판단 불가";

export type EDecision = {
  condition: EntryCondition;
  selected: string[] | null;
  entry: string;
  reason: string;
};

const REASON_LABELS: Readonly<Record<string, string>> = {
  NO_SESSION_YET: "세션 대기",
  UNIVERSE_NOT_AVAILABLE: "대상 종목 데이터 없음",
  NO_DECISION: "오늘은 결정이 내려지지 않았습니다",
  MARKET_DATA_UNAVAILABLE: "시장 데이터를 받지 못했습니다",
  NO_EXACT_0930_BAR: "09:30 시세가 없어 진입하지 않았습니다",
  ZERO_QUANTITY: "주문 가능 수량이 없습니다",
  RVOL_MISSING: "거래량 이력 부족",
};

/** A raw backend reason as a Korean sentence; unknown enums are never shown verbatim. */
export function reasonLabel(raw: string | null | undefined): string | null {
  if (!raw) return null;
  if (REASON_LABELS[raw]) return REASON_LABELS[raw];
  return /^[A-Z0-9_:.\-]+$/.test(raw) ? "상세 확인 필요" : raw.length > 60 ? "상세 확인 필요" : raw;
}

const FINISHED = new Set(["COMPLETE", "NO_DECISION", "ERROR"]);

export function decisionOf(status: StrategyStatus, account: StrategyAccount): EDecision {
  const d = status.detail || {};
  const runtime = status.runtime_status || "NO_SESSION_YET";
  const selected = d.selected ?? null;
  const trueCount = d.h5_true ?? 0;
  const unknownCount = d.h5_unknown ?? 0;
  const universeBad = d.universe != null && d.universe.status != null && d.universe.status !== "READY";

  let condition: EntryCondition;
  let reason: string;
  if (runtime === "NO_SESSION_YET") { condition = "판단 불가"; reason = REASON_LABELS.NO_SESSION_YET; }
  else if (universeBad) { condition = "판단 불가"; reason = REASON_LABELS.UNIVERSE_NOT_AVAILABLE; }
  else if ((selected?.length ?? 0) > 0 || trueCount > 0) { condition = "충족"; reason = "진입 조건을 충족한 종목이 있습니다"; }
  else if (unknownCount > 0) { condition = "판단 불가"; reason = `${unknownCount}개 종목은 신호를 판단할 수 없었습니다`; }
  else if (d.h5_false != null || d.canonical_universe != null) { condition = "미충족"; reason = "진입 조건을 충족한 종목이 없습니다"; }
  else { condition = "판단 불가"; reason = "결정 기록이 없습니다"; }

  const explicit = reasonLabel(d.no_decision_reason);
  if (explicit && condition !== "충족") reason = explicit;

  const entered = account.open_positions > 0 || (account.closed_trades_today ?? 0) > 0;
  const entry = entered ? "진입함" : FINISHED.has(runtime) ? "진입 없음"
    : (selected?.length ?? 0) > 0 ? "진입 대기" : "-";
  return { condition, selected, entry, reason };
}
