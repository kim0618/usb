import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const trading = readFileSync("app/trading/page.tsx", "utf8");
const types = readFileSync("types/api.ts", "utf8");

describe("Stage 9.11 portfolio-centered Trading UX", () => {
  it("renders the five account summary fields", () => ["총 자산", "투자 중", "보유 현금", "평가 손익", "오늘 손익"].forEach(value => expect(trading).toContain(value)));
  it("adds concise metadata and context to every account card", () => { ["계좌", "포지션", "주문 가능", "평가", "금일", "Broker account 기준", "보유 종목 기준", "Broker 주문 가능 기준", "실시간 평가 기준", "당일 체결 기준"].forEach(value => expect(trading).toContain(value)); });
  it("uses secondary blue action for analysis and preserves the selected history filter", () => { expect(trading).toContain('className="btn-action-secondary-compact">분석 보기</Link>'); expect(trading).toContain("btn-compact-active"); expect(trading).not.toContain("분석 확인 →"); });
  it("assigns a prioritized accent and decorative icon to every summary card", () => { ["primary", "indigo", "gold", "green", "bluegreen"].forEach(accent => expect(trading).toContain(`accent="${accent}"`)); ["wallet", "layers", "cash", "trend", "pulse"].forEach(icon => expect(trading).toContain(`type="${icon}"`)); });
  it("keeps unavailable account values null-safe", () => { expect(trading).toContain('accountValue(account, "equity")'); expect(trading).toContain('accountValue(account, "cash")'); expect(trading).toContain('accountValue(account, "invested_notional")'); });
  it("keeps the current-position empty state compact", () => expect(trading).toContain("보유 중인 포지션이 없습니다."));
  it("defines a broker-neutral position DTO path", () => { expect(types).toContain("interface TradingPosition"); expect(trading).toContain("o.open_positions.map"); });
  it("shows positive and negative pnl with text signs", () => { expect(trading).toContain("signedDecimal(position.unrealized_pnl)"); expect(trading).toContain('startsWith("-")'); });
  it("renders current stop and handles absence", () => { expect(trading).toContain("현재 Stop"); expect(trading).toContain('positionValue(position, "active_stop")'); });
  it("shows approved candidates as entry waiting", () => { expect(trading).toContain('label="진입 대기"'); expect(trading).toContain('decision === "APPROVE"'); });
  it("replaces separate history tables with one unified section", () => { expect(trading).toContain("매매 내역"); [">주문 내역</h2>", ">체결 내역</h2>", ">최근 종료 매매</h2>"].forEach(value => expect(trading).not.toContain(value)); });
  it("retains side and order-status display mappers", () => { expect(trading).toContain("formatOrderSide(order.side)"); expect(trading).toContain("formatOrderStatus(row.status)"); });
  it("does not infer account truth from seed execution history", () => { expect(trading).not.toContain("orders.data.reduce"); expect(trading).not.toContain("fills.data.reduce"); expect(trading).not.toContain("trades.data.reduce"); });
  it("polls frozen endpoints safely", () => { expect(trading).toContain("useApi(api.trading, 10000)"); expect(trading).toContain("useApi(api.dashboard, 20000)"); expect(trading).toContain("useApi(api.research, 20000)"); });
  it("does not duplicate the local page header or system warning", () => { expect(trading).not.toContain("매매 현황"); expect(trading).not.toContain("계좌 자산과 포지션을 먼저 보고"); expect(trading).not.toContain('href="/runtime"'); expect(trading).not.toContain("시스템 확인 필요"); expect(trading).not.toContain("formatBrokerMode"); });
  it("starts successful content with the account summary", () => expect(trading.indexOf('aria-label="계좌 요약"')).toBeLessThan(trading.indexOf("NO_ACTIVE_SIM_BROKER")));
  it("uses responsive summary and horizontally scrollable positions", () => { expect(trading).toContain("sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5"); expect(trading).toContain('className="table-wrap"'); });
  it("strengthens the positions table without changing its rows", () => { expect(trading).toContain("trading-positions-table"); expect(trading).toContain("o.open_positions.map"); });
  it("groups all four PnL metrics responsively inside one panel", () => { expect(trading).toContain("pnl-metric-grid"); expect(trading).toContain("grid-cols-2 sm:grid-cols-4"); ["실현 손익", "평가 손익", "비용", "순손익"].forEach(label => expect(trading).toContain(label)); });
  it("keeps zero and unavailable PnL neutral", () => { expect(trading).toContain("numericValue > 0"); expect(trading).toContain('"text-foreground-secondary"'); });
  it("keeps broker limitation compact", () => { expect(trading).toContain("py-2.5 text-xs"); expect(trading).toContain("저장된 주문·체결·매매 기록은 계속 제공합니다."); });
  it("does not present strategy persistence as broker positions", () => expect(trading).not.toContain("o.strategy_states.map"));
});
