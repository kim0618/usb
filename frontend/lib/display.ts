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
export const formatMarketSession = (value?: string | null) => display(value, {
  PREMARKET: "프리마켓", REGULAR: "정규장", POSTMARKET: "애프터마켓",
});
export const formatRuntimeMode = (value?: string | null) => display(value, {
  NORMAL: "정상", SAFE_MODE: "안전 모드", HALTED: "중지",
});
export const formatRuntimeHealth = (healthy: boolean) => healthy ? "정상" : "확인 필요";
export const formatSeverity = (value?: string | null) => display(value, {
  WARNING: "경고", ERROR: "오류", CRITICAL: "심각",
});
export const formatResolved = (resolved: boolean) => resolved ? "해결됨" : "미해결";
export const formatBrokerMode = (value?: string | null) => display(value, {
  SIMULATION: "가상 매매", PAPER: "모의투자", LIVE: "실전투자",
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

export type SemanticTone = "info" | "success" | "warning" | "danger" | "indigo" | "neutral";
export type StrategyStatusDisplay = { label: string; tone: SemanticTone };

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
