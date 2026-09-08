const display = (value: string | null | undefined, labels: Readonly<Record<string, string>>, empty = "-") => {
  if (value == null || value === "") return empty;
  return labels[value] ?? value;
};

export const formatDecisionStatus = (value?: string | null) => display(value, {
  APPROVE: "채택", REJECT: "거절", UNDECIDED: "미결정",
}, "미결정");

export const formatProfile = (value?: string | null) => display(value, {
  TIGHT: "타이트", NORMAL: "보통", WIDE: "넓음", UNKNOWN: "확인 불가",
  LOW: "낮음", MEDIUM: "보통", HIGH: "높음",
  INTRADAY: "당일", ONE_TO_TWO_DAYS: "1~2일", MULTI_DAY: "여러 날",
});

export const formatOrderSide = (value?: string | null) => display(value, { BUY: "매수", SELL: "매도" });
export const formatOrderStatus = (value?: string | null) => display(value, {
  PENDING: "주문 대기", PARTIALLY_FILLED: "부분 체결", FILLED: "체결 완료",
  CANCELLED: "주문 취소", REJECTED: "주문 거절",
});
export const formatExecutionBroker = (value?: string | null) => display(value, {
  SIM: "Simulation (SIM)",
});
export const formatOrderRejectionReason = (value?: string | null) => display(value, {
  NO_NEXT_BAR: "다음 체결 가능 시세 없음",
});
export const formatMarketSession = (value?: string | null) => display(value, {
  PREMARKET: "프리마켓", REGULAR: "정규장", POSTMARKET: "애프터마켓", CLOSED: "거래 종료",
});
/** Backend missing-reason codes for extended-hours quotes; no price is ever synthesized. */
export const formatQuoteMissingReason = (value?: string | null) => display(value, {
  NOT_FETCHED: "데이터 없음", NOT_AVAILABLE_FROM_PROVIDER: "데이터 없음",
  INSUFFICIENT_HISTORY: "데이터 없음", OUTSIDE_SESSION: "데이터 없음",
  PROVIDER_ERROR: "제공자 오류",
}, "데이터 없음");
export const formatRuntimeMode = (value?: string | null) => display(value, {
  NORMAL: "정상", SAFE_MODE: "안전 모드", HALTED: "중지",
});
export const formatRuntimeHealth = (healthy: boolean) => healthy ? "정상" : "확인 필요";
export const formatSeverity = (value?: string | null) => display(value, {
  WARNING: "경고", ERROR: "오류", CRITICAL: "심각",
});
export const formatResolved = (resolved: boolean) => resolved ? "해결됨" : "미해결";
export const formatBrokerMode = (value?: string | null) => display(value, {
  SIMULATION: "가상매매", PAPER: "모의투자", LIVE: "실전투자",
});
export const formatCapability = (enabled: boolean) => enabled ? "사용 가능" : "미지원";
export const formatConnection = (value?: string | null) => display(value, { CONNECTED: "연결됨", "NOT CONNECTED": "미연결" });

export const formatStrategyPhase = (value?: string | null) => display(value, {
  RESEARCH_READY: "분석 준비", HUMAN_REJECTED: "투자 거절", HUMAN_APPROVED: "투자 승인",
  PREMARKET_REJECTED: "프리마켓 탈락", PREMARKET_PASSED: "프리마켓 통과",
  OPENING_RANGE_BUILDING: "시초 범위 형성", WAITING_ENTRY: "진입 대기",
  ENTRY_SIGNALLED: "진입 신호", POSITION_OPEN: "보유 중", PYRAMID_ADDED: "추가매수 완료",
  OVERNIGHT_REVIEW: "익일 보유 검토", OVERNIGHT_HELD: "익일 보유", DAY2_ACTIVE: "2일차 보유",
  EXIT_SIGNALLED: "청산 신호", EXITED: "청산 완료", NO_TRADE: "미진입",
});

/** StrategyReason codes the premarket gate can record. Raw enums never reach the screen. */
export const formatStrategyPhaseReason = (value?: string | null) => display(value, {
  HUMAN_NOT_APPROVED: "사람 승인 없음",
  INVALID_PREMARKET_DATA: "프리마켓 데이터 부족/이상",
  RESEARCH_BLOCKED: "리서치 차단",
  NEGATIVE_CATALYST: "신규 악재 발생",
  GAP_TOO_LOW: "갭 상승폭 기준 미달",
  GAP_TOO_HIGH: "갭 상승폭 과다",
  LOW_PREMARKET_VOLUME: "프리마켓 거래량 부족",
}, "상세 사유 기록 없음");

export type SemanticTone = "info" | "success" | "warning" | "danger" | "indigo" | "neutral";
export type StrategyStatusDisplay = { label: string; tone: SemanticTone };

/** Display-only bands for the frozen quant_v0 score; never persisted or sent to the Backend. */
export const quantScoreLabel = (value: number): string => {
  if (value >= 0.6) return "강함";
  if (value >= 0.25) return "양호";
  if (value >= -0.1) return "보통";
  if (value >= -0.5) return "약함";
  return "매우 약함";
};

export const quantScoreTone = (value: number): SemanticTone => {
  if (value >= 0.25) return "info";
  if (value >= -0.1) return "neutral";
  return "danger";
};

/** Display-only activity bands for raw relative volume (RVOL). */
export const rvolLabel = (value: number): string => {
  if (value >= 2) return "매우 활발";
  if (value >= 1.5) return "활발";
  if (value >= 1) return "보통";
  return "낮음";
};

export const rvolTone = (value: number): SemanticTone => value >= 1.5 ? "success" : "neutral";

export const signedMetricTone = (value: number): SemanticTone => value > 0 ? "success" : value < 0 ? "danger" : "neutral";

const researchStrengthLabel = (value: number): string => {
  if (value >= 90) return "매우 강함";
  if (value >= 75) return "강함";
  if (value >= 60) return "양호";
  if (value >= 40) return "보통";
  return "약함";
};

const researchStrengthTone = (value: number): SemanticTone => {
  if (value >= 75) return "success";
  if (value >= 60) return "info";
  if (value >= 40) return "neutral";
  return "danger";
};

/** Display-only bands for frozen Research scores; raw values and scoring remain untouched. */
export const researchCatalystLabel = researchStrengthLabel;
export const researchCatalystTone = researchStrengthTone;
export const researchMomentumLabel = researchStrengthLabel;
export const researchMomentumTone = researchStrengthTone;

/** Backend contract: risk_score is a safety score; higher always means safer. */
export const researchRiskLabel = (value: number): string => {
  if (value >= 70) return "매우 안전";
  if (value >= 50) return "안전";
  if (value >= 30) return "보통";
  return "위험";
};
export const researchRiskTone = (value: number): SemanticTone => {
  if (value >= 70) return "success";
  if (value >= 50) return "info";
  if (value >= 30) return "neutral";
  return "danger";
};

export const researchOverallLabel = (value: number): string => {
  if (value >= 90) return "최상";
  if (value >= 80) return "매우 좋음";
  if (value >= 70) return "좋음";
  if (value >= 60) return "보통";
  return "낮음";
};
export const researchOverallTone = (value: number): SemanticTone => {
  if (value >= 80) return "success";
  if (value >= 70) return "info";
  if (value >= 60) return "neutral";
  return "danger";
};

export const evidenceScoreLabel = (value: number): string => value >= 70 ? "높음" : value >= 40 ? "보통" : "낮음";
export const evidenceScoreTone = (value: number): SemanticTone => value >= 70 ? "success" : value >= 40 ? "neutral" : "warning";

export const fundamentalLabel = researchStrengthLabel;
/** Fundamental strength is supporting context, so positive bands stay visually restrained. */
export const fundamentalTone = (value: number): SemanticTone => value < 40 ? "danger" : value >= 60 ? "info" : "neutral";

const strategyStatusTones: Readonly<Record<string, SemanticTone>> = {
  WAITING_ENTRY: "success",
  ENTRY_SIGNALLED: "success",
  POSITION_OPEN: "info",
  PYRAMID_WAITING: "warning",
  PYRAMID_ADDED: "indigo",
  OVERNIGHT_REVIEW: "warning",
  OVERNIGHT_HELD: "info",
  DAY2_ACTIVE: "info",
  EXIT_SIGNALLED: "warning",
  EXITED: "neutral",
  SAFE_MODE: "warning",
  BLOCKED: "warning",
  HALTED: "danger",
  ERROR: "danger",
  FORCE_STOPPED: "danger",
};

/** Keeps Backend strategy data untouched and adds display-only semantics. */
export const strategyStatusDisplay = (value: string): StrategyStatusDisplay => ({
  label: formatStrategyPhase(value),
  tone: strategyStatusTones[value] ?? "neutral",
});

export const formatShadowStatus = (value?: string | null) => display(value, {
  NO_TRADE: "미진입", OPEN: "보유 중", CLOSED: "청산 완료", UNFILLED: "미체결", REJECTED: "진입 거절",
});

export const formatTradeStatus = (value?: string | null) => display(value, {
  OPEN: "보유 중", CLOSED: "청산 완료",
});

const failureCodeDescriptions: Readonly<Record<string, string>> = {
  MARKET_DATA_STALE: "시세 데이터 지연", MARKET_DATA_UNAVAILABLE: "시세 데이터 사용 불가",
  MARKET_DATA_INVALID: "시세 데이터 오류", EXECUTION_UNAVAILABLE: "주문 실행 불가",
  EXECUTION_REJECTED: "주문 실행 거절", EXECUTION_TIMEOUT: "주문 실행 시간 초과",
  POSITION_MISMATCH: "보유 포지션 불일치", ORDER_MISMATCH: "주문 상태 불일치",
  STRATEGY_STATE_MISMATCH: "전략 상태 불일치", DATABASE_ERROR: "데이터베이스 오류",
  RUNTIME_INVARIANT_VIOLATION: "시스템 상태 오류", HEARTBEAT_MISSED: "시스템 신호 누락",
  STARTUP_RECONCILIATION_FAILED: "시작 상태 대조 실패", MANUAL_SAFE_MODE: "수동 안전 모드",
  MANUAL_HALT: "수동 자동매매 중지", KILL_SWITCH_ACTIVATED: "비상 중지 실행",
};
export const formatFailureCodeDescription = (value?: string | null) => value ? failureCodeDescriptions[value] ?? value : "";
