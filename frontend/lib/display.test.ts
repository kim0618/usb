import { describe, expect, it } from "vitest";
import {
  formatBrokerMode, formatCapability, formatDecisionStatus, formatMarketSession,
  formatOrderSide, formatOrderStatus, formatResolved, formatRuntimeHealth,
  formatRuntimeMode, formatSeverity, formatShadowStatus, formatStrategyPhase, formatTradeStatus,
} from "./display";

describe("Korean display mappers", () => {
  it("maps decisions and missing decisions", () => {
    expect(formatDecisionStatus("APPROVE")).toBe("승인");
    expect(formatDecisionStatus("REJECT")).toBe("거절");
    expect(formatDecisionStatus()).toBe("미결정");
  });
  it("maps order directions and every current order status", () => {
    expect([formatOrderSide("BUY"), formatOrderSide("SELL")]).toEqual(["매수", "매도"]);
    expect(["PENDING", "PARTIALLY_FILLED", "FILLED", "CANCELLED", "REJECTED"].map(formatOrderStatus))
      .toEqual(["주문 대기", "부분 체결", "체결 완료", "주문 취소", "주문 거절"]);
  });
  it("maps market sessions, runtime mode, health, and severity", () => {
    expect(["PREMARKET", "REGULAR", "POSTMARKET"].map(formatMarketSession)).toEqual(["프리마켓", "정규장", "애프터마켓"]);
    expect(["NORMAL", "SAFE_MODE", "HALTED"].map(formatRuntimeMode)).toEqual(["정상", "안전 모드", "중지"]);
    expect([formatRuntimeHealth(true), formatRuntimeHealth(false)]).toEqual(["정상", "확인 필요"]);
    expect(["WARNING", "ERROR", "CRITICAL"].map(formatSeverity)).toEqual(["경고", "오류", "심각"]);
  });
  it("maps resolved, broker, capability, strategy, and shadow values", () => {
    expect([formatResolved(true), formatResolved(false)]).toEqual(["해결됨", "미해결"]);
    expect(["SIMULATION", "PAPER", "LIVE"].map(formatBrokerMode)).toEqual(["가상 매매", "모의투자", "실전투자"]);
    expect([formatCapability(true), formatCapability(false)]).toEqual(["사용 가능", "미지원"]);
    expect(formatStrategyPhase("POSITION_OPEN")).toBe("보유 중");
    expect(formatStrategyPhase("EXIT_SIGNALLED")).toBe("청산 신호");
    expect(formatStrategyPhase("OVERNIGHT_HELD")).toBe("익일 보유");
    expect(formatStrategyPhase("DAY2_ACTIVE")).toBe("2일차 보유");
    expect(formatStrategyPhase("EXITED")).toBe("청산 완료");
    expect(formatStrategyPhase("NO_TRADE")).toBe("미진입");
    expect(formatShadowStatus("REJECTED")).toBe("진입 거절");
    expect(["OPEN", "CLOSED"].map(formatTradeStatus)).toEqual(["보유 중", "청산 완료"]);
  });
  it("preserves an unknown future enum", () => {
    expect(formatOrderStatus("BROKER_PENDING_REVIEW")).toBe("BROKER_PENDING_REVIEW");
  });
});
