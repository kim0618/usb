import { describe, expect, it } from "vitest";
import { ENTRY_GROUP_META, entryDisplay, entryReasonLabel, executionFacts, fractionPercent, premarketFacts, rowHeadline } from "@/lib/entry-board";
import type { EntryBoardCandidate, EntryBoardState } from "@/types/api";

const state = (phase: string, phase_reason: string | null = null, extra: Partial<EntryBoardState> = {}): EntryBoardState => ({
  phase, phase_reason, entry_price: null, initial_stop: null, active_stop: null, signal_at: null,
  intended_execution_bar_at: null, updated_at: null, ...extra,
});
const row = (rowState: EntryBoardState | null, extra: Partial<EntryBoardCandidate> = {}): EntryBoardCandidate => ({
  rank: 1, symbol: "AAA", scanner_candidate_id: 1, exchange: null, state_conflict: false, state: rowState, premarket: null, ...extra,
});
const group = (rowState: EntryBoardState | null) => entryDisplay(row(rowState)).group;

describe("entryDisplay phase mapping", () => {
  it.each<[string | null, string | null, string, string]>([
    [null, null, "WAITING", "프리마켓 평가 대기"],
    ["HUMAN_APPROVED", null, "WAITING", "프리마켓 평가 대기"],
    ["PREMARKET_PASSED", null, "WAITING", "Opening Range 대기"],
    ["OPENING_RANGE_BUILDING", null, "WAITING", "Opening Range 대기"],
    ["WAITING_ENTRY", null, "EVALUATING", "진입 신호 평가"],
    ["ENTRY_SIGNALLED", null, "EVALUATING", "체결봉 대기"],
    ["POSITION_OPEN", null, "BOUGHT", "보유 중"],
    ["PYRAMID_ADDED", null, "BOUGHT", "추가매수 완료"],
    ["EXITED", null, "BOUGHT", "청산 완료"],
    ["PREMARKET_REJECTED", "GAP_TOO_LOW", "EXCLUDED", "갭 상승폭 기준 미달"],
    ["PREMARKET_REJECTED", "LOW_PREMARKET_VOLUME", "EXCLUDED", "프리마켓 거래량 부족"],
    ["NO_TRADE", "ENTRY_PRICE_ABOVE_CEILING", "EXCLUDED", "체결가 상한 초과"],
    ["NO_TRADE", "ENTRY_DEADLINE_EXPIRED", "EXCLUDED", "진입 마감 시각 경과"],
    ["NO_TRADE", null, "EXCLUDED", "상세 사유 기록 없음"],
    ["PREMARKET_REJECTED", "INVALID_PREMARKET_DATA", "SYSTEM", "프리마켓 데이터 부족/이상"],
    ["NO_TRADE", "AUTH_FAILED", "SYSTEM", "시세 인증 실패"],
    ["NO_TRADE", "PROTECTION_UNAVAILABLE", "SYSTEM", "보호 주문 확인 불가"],
    ["NO_TRADE", "UNSUPPORTED_EXCHANGE", "SYSTEM", "지원하지 않는 거래소"],
    ["NO_TRADE", "MARKET_DATA_UNAVAILABLE", "SYSTEM", "시세 데이터 사용 불가"],
    ["NO_TRADE", "SOMETHING_NEW", "SYSTEM", "기록된 사유 확인 필요"],
    ["SOME_FUTURE_PHASE", null, "SYSTEM", "알 수 없는 전략 상태"],
  ])("%s / %s -> %s · %s", (phase, reason, expectedGroup, detail) => {
    const display = entryDisplay(row(phase === null ? null : state(phase, reason)));
    expect(display.group).toBe(expectedGroup);
    expect(display.detail).toBe(detail);
    expect(display.label).toBe(ENTRY_GROUP_META[display.group].label);
  });

  it("gives every group a text label and icon so colour is never the only signal", () => {
    Object.values(ENTRY_GROUP_META).forEach(meta => { expect(meta.label).not.toBe(""); expect(meta.icon).not.toBe(""); });
    expect(new Set(Object.values(ENTRY_GROUP_META).map(meta => meta.tone)).size).toBe(5);
    expect(ENTRY_GROUP_META.EXCLUDED.tone).toBe("danger");
    expect(ENTRY_GROUP_META.SYSTEM.tone).toBe("warning");
  });

  it("applies a capacity block only before entry", () => {
    expect(entryDisplay(row(null), "DAILY_ENTRY_CAP_REACHED")).toMatchObject({ group: "WAITING", detail: "일일 진입 한도 도달" });
    expect(entryDisplay(row(state("WAITING_ENTRY")), "OPEN_POSITION_CAP_REACHED").detail).toBe("보유 한도 도달");
    expect(entryDisplay(row(state("ENTRY_SIGNALLED")), "DAILY_ENTRY_CAP_REACHED").detail).toBe("체결봉 대기");
    expect(entryDisplay(row(state("POSITION_OPEN")), "DAILY_ENTRY_CAP_REACHED").group).toBe("BOUGHT");
    expect(entryDisplay(row(state("PREMARKET_REJECTED", "GAP_TOO_LOW")), "DAILY_ENTRY_CAP_REACHED").group).toBe("EXCLUDED");
  });

  it("treats a foreign state as a data problem", () => {
    expect(entryDisplay(row(null, { state_conflict: true })).group).toBe("SYSTEM");
    expect(group(null)).toBe("WAITING");
  });

  it("labels an empty or unknown reason without showing the code", () => {
    expect(entryReasonLabel(null)).toBe("상세 사유 기록 없음");
    expect(entryReasonLabel("")).toBe("상세 사유 기록 없음");
    expect(entryReasonLabel("NOT_A_REASON")).not.toContain("NOT_A_REASON");
  });
});

describe("facts shown on hover and in detail", () => {
  it("formats fraction strings as percentages and rejects non-numbers", () => {
    expect(fractionPercent("0.0105", true)).toBe("+1.05%");
    expect(fractionPercent("-0.0577", true)).toBe("-5.77%");
    expect(fractionPercent("0.05")).toBe("5.00%");
    [null, undefined, "", "abc"].forEach(value => expect(fractionPercent(value as string | null)).toBeNull());
  });

  it("returns null for every value the backend did not persist", () => {
    const empty = row(state("NO_TRADE"));
    expect(premarketFacts(empty, null).every(([, value]) => value === null)).toBe(true);
    expect(executionFacts(empty, null).every(([, value]) => value === null)).toBe(true);
    expect(rowHeadline(empty, null)).toBeNull();
  });

  it("names the stored price by phase: signal before a fill, fill once bought", () => {
    const signalled = row(state("ENTRY_SIGNALLED", null, { entry_price: "10.5" }));
    const bought = row(state("POSITION_OPEN", null, { entry_price: "10.5" }));
    expect(executionFacts(signalled, null)[0]).toEqual(["신호가", "$10.50"]);
    expect(executionFacts(bought, { symbol: "AAA", quantity: "7" })[0]).toEqual(["진입가(체결)", "$10.50"]);
    expect(rowHeadline(bought, { symbol: "AAA", quantity: "7" })).toBe("$10.50 · 7주");
    expect(rowHeadline(signalled, null)).toBe("신호 $10.50");
  });
});
