import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const trading = readFileSync("app/trading/page.tsx", "utf8");
const board = readFileSync("components/entry-status-board.tsx", "utf8");
const types = readFileSync("types/api.ts", "utf8");

describe("Stage 9.11 portfolio-centered Trading UX", () => {
  it("renders the five account summary fields", () => ["총 자산", "투자 중", "보유 현금", "평가 손익", "직전 거래일 손익"].forEach(value => expect(trading).toContain(value)));
  it("adds display-only KRW values to all monetary account cards", () => { expect(trading.match(/accountCardValue\(account,/g)).toHaveLength(4); expect(trading).toContain("PreviousSessionPerformance"); });
  it("keeps concise metadata while removing developer-facing card explanations", () => { ["계좌", "포지션", "주문 가능", "평가", "완료 세션"].forEach(value => expect(trading).toContain(value)); ["Broker account 기준", "보유 종목 기준", "Broker 주문 가능 기준", "실시간 평가 기준", "당일 체결 기준"].forEach(value => expect(trading).not.toContain(value)); });
  it("uses secondary blue action for analysis and preserves the selected history filter", () => { expect(board).toContain('className="btn-action-secondary-compact">분석 보기</Link>'); expect(trading).toContain("btn-compact-active"); expect(trading + board).not.toContain("분석 확인 →"); });
  it("assigns a prioritized accent and decorative icon to every summary card", () => { ["primary", "indigo", "gold", "green", "bluegreen"].forEach(accent => expect(trading).toContain(`accent="${accent}"`)); ["wallet", "layers", "cash", "trend", "pulse"].forEach(icon => expect(trading).toContain(`type="${icon}"`)); });
  it("keeps unavailable account values null-safe", () => { expect(trading).toContain("account?.[field]"); expect(trading).toContain('value == null ? "-"'); expect(trading).toContain("usdToDisplayKrw(value)"); });
  it("keeps the current-position empty state compact", () => expect(trading).toContain("보유 중인 포지션이 없습니다."));
  it("uses operator-friendly copy while the account connection is unavailable", () => { expect(trading).toContain("가상매매 계좌 연결 대기 중"); expect(trading).toContain("연결 후 실시간 자산 및 포지션이 표시됩니다."); ["Simulation Broker", "truth"].forEach(value => expect(trading).not.toContain(value)); });
  it("defines a broker-neutral position DTO path", () => { expect(types).toContain("interface TradingPosition"); expect(trading).toContain("o.open_positions.map"); });
  it("shows positive and negative pnl with text signs", () => { expect(trading).toContain("signedDecimal(position.unrealized_pnl)"); expect(trading).toContain('startsWith("-")'); });
  it("renders current stop and handles absence", () => { expect(trading).toContain("현재 Stop"); expect(trading).toContain('positionValue(position, "active_stop")'); });
  it("shows current-run approved candidates on the full-width entry status board", () => { expect(trading).toContain("<EntryStatusBoard"); expect(trading).not.toContain('decision === "APPROVE"'); expect(board).toContain('aria-label="진입 후보"'); });
  it("places the history control in the daily PnL header without a separate heading", () => { expect(trading).toContain('id="daily-performance-title"'); expect(trading).not.toContain('id="trading-history-title"'); expect(trading.indexOf('id="daily-performance-title"')).toBeLessThan(trading.indexOf("aria-expanded={showHistory}")); });
  it("keeps order, fill, and settlement history behind an explicit toggle", () => { expect(trading).toContain("거래 내역 보기"); expect(trading).toContain("aria-expanded={showHistory}"); expect(trading).toContain('aria-controls="trading-history-detail"'); ["TRADE", "ORDER", "FILL"].forEach(value => expect(trading).toContain(`value: "${value}"`)); });
  it("retains side and order-status display mappers", () => { expect(trading).toContain("formatOrderSide(order.side)"); expect(trading).toContain("formatOrderStatus(row.status)"); });
  it("projects the execution provider and null-safe rejection reason", () => { expect(types).toContain("broker_type: string"); expect(types).toContain("rejection_reason: string | null"); expect(trading).toContain("formatExecutionBroker(order.broker_type)"); expect(trading).toContain("formatOrderRejectionReason(order.rejection_reason)"); expect(trading).toContain("row.rawSource.rejection_reason &&"); });
  it("does not infer account truth from seed execution history", () => { expect(trading).not.toContain("orders.data.reduce"); expect(trading).not.toContain("fills.data.reduce"); expect(trading).not.toContain("trades.data.reduce"); });
  it("polls frozen endpoints safely", () => { expect(trading).toContain("useApi(api.trading, 10000)"); expect(trading).toContain("useApi(api.dashboard, 20000)"); expect(trading).toContain("useApi(api.entryBoard, 10000)"); expect(trading).not.toContain("api.research"); });
  it("does not duplicate the local page header or system warning", () => { expect(trading).not.toContain("매매 현황"); expect(trading).not.toContain("계좌 자산과 포지션을 먼저 보고"); expect(trading).not.toContain('href="/runtime"'); expect(trading).not.toContain("시스템 확인 필요"); expect(trading).not.toContain("formatBrokerMode"); });
  it("starts successful content with the account summary", () => expect(trading.indexOf('aria-label="계좌 요약"')).toBeLessThan(trading.indexOf("NO_ACTIVE_SIM_BROKER")));
  it("uses responsive summary and horizontally scrollable positions", () => { expect(trading).toContain("sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5"); expect(trading).toContain('className="table-wrap"'); });
  it("strengthens the positions table without changing its rows", () => { expect(trading).toContain("trading-positions-table"); expect(trading).toContain("o.open_positions.map"); });
  it("removes the large current-session PnL panel; 평가 손익 stays on the summary card", () => { ["pnl-metric-grid", "현재 세션 손익 상세", "순손익"].forEach(label => expect(trading).not.toContain(label)); expect(trading).toContain('label="평가 손익"'); });
  it("keeps durable daily performance below the entry board", () => { expect(trading).toContain("<DailyPerformanceTable"); expect(trading.indexOf("<EntryStatusBoard")).toBeLessThan(trading.indexOf('id="daily-performance-title"')); });
  it("keeps zero and unavailable PnL neutral", () => { expect(trading).toContain("numericValue > 0"); expect(trading).toContain('"text-foreground-secondary"'); });
  it("keeps the account connection message compact", () => { expect(trading).toContain("py-2.5 text-xs"); expect(trading).toContain("연결 후 실시간 자산 및 포지션이 표시됩니다."); });
  it("does not present strategy persistence as broker positions", () => expect(trading).not.toContain("o.strategy_states.map"));
});
