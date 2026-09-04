import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { formatQuoteMissingReason } from "@/lib/display";

const adoption = () => readFileSync("app/adoption/page.tsx", "utf8");
const types = () => readFileSync("types/api.ts", "utf8");

describe("Stage 10B-4.1 adoption review refinement", () => {
  it("shows the backend recommendation rank in the table and the detail header", () => {
    const page = adoption();
    expect(page).toContain('<th className="whitespace-nowrap">순위</th>');
    expect(page).toContain("{rankLabel(item.recommendation_rank)}");
    expect(page).toContain("순위 {rankLabel(item.recommendation_rank)}");
    expect(types()).toContain("recommendation_rank: number");
  });

  it("never derives the recommendation order or any threshold in the frontend", () => {
    const page = adoption();
    expect(page).not.toContain(".sort(");
    expect(page).not.toMatch(/ADOPTION_CANDIDATE.*REVIEW_REQUIRED.*EXCLUDED.*indexOf/);
    expect(page).not.toMatch(/>= ?(?:70|75|60|40|30)\b/);
  });

  it("keeps the detail section order fixed for final human review", () => {
    const page = adoption();
    const order = ["순위 {rankLabel(item.recommendation_rank)}", '"회사 설명"', '"판단 요약"', '"단기 가격 상태"', '"채택 이유"', '"순위 변화 이유"', '"위험 / 주의"'].map(value => page.indexOf(value));
    expect(order.every(index => index >= 0)).toBe(true);
    expect(order).toEqual([...order].sort((left, right) => left - right));
  });

  it("hides the company summary section when research has no overview", () => {
    const page = adoption();
    expect(page).toContain("const company = detail.company_summary?.trim();");
    expect(page).toContain("{company && <Section title=\"회사 설명\"");
    expect(page).toContain("line-clamp-2");
  });

  it("renders short-term market state from persisted snapshot values only", () => {
    const page = adoption();
    ["전일 종가", "전일 고가", "전일 등락", "거래량 강도", "시장 대비", "최근 흐름", "프리마켓", "애프터마켓"].forEach(label => expect(page).toContain(`label="${label}"`));
    ["detail.previous_close", "detail.previous_high", "detail.previous_return_pct", "detail.rvol", "item.relative_strength", "item.momentum", "detail.premarket", "detail.postmarket"].forEach(field => expect(page).toContain(field));
  });

  it("never labels a stale snapshot close as a live current price", () => {
    const page = adoption();
    expect(page).not.toContain('label="현재가"');
    expect(page).not.toContain('label="마지막 관측 KST"');
    expect(page).not.toContain("latest_close");
    expect(page).toContain("실시간 현재가는 제공되지 않습니다");
  });

  it("maps missing extended-session coverage to typed reasons without synthesizing a price", () => {
    expect(formatQuoteMissingReason("NOT_AVAILABLE_FROM_PROVIDER")).toBe("데이터 없음");
    expect(formatQuoteMissingReason("INSUFFICIENT_HISTORY")).toBe("데이터 없음");
    expect(formatQuoteMissingReason("PROVIDER_ERROR")).toBe("제공자 오류");
    expect(formatQuoteMissingReason(null)).toBe("데이터 없음");
    expect(adoption()).toContain("quote.price == null");
  });

  it("merges catalyst and strengths into 채택 이유 and warnings into 위험 / 주의", () => {
    const page = adoption();
    const reason = page.indexOf('<Section title="채택 이유">');
    expect(page.indexOf("detail.catalyst_summary", reason)).toBeGreaterThan(reason);
    expect(page.indexOf("item.strengths", reason)).toBeGreaterThan(reason);
    const risk = page.indexOf('<Section title="위험 / 주의">');
    expect(page.indexOf("item.warnings", risk)).toBeGreaterThan(risk);
    expect(page.indexOf("detail.risk_summary", risk)).toBeGreaterThan(risk);
  });

  it("keeps the frozen decision contract and its non-purchase wording intact", () => {
    const page = adoption();
    expect(page).toContain("<ResearchDecisionControl");
    expect(page).toContain("approveDisabled={approved >= 2");
    expect(page).toContain("최대 2개 종목까지 채택할 수 있습니다.");
    expect(readFileSync("components/research-decision.tsx", "utf8")).toContain("채택은 즉시 매수가 아닙니다. Strategy/Risk 조건을 통과한 경우에만 SimulationBroker 진입 대상이 됩니다.");
  });

  it("stays responsive and theme neutral on narrow screens", () => {
    const page = adoption();
    expect(page).toContain("table-wrap");
    expect(page).toContain("hidden min-w-48 text-xs text-foreground-secondary lg:table-cell");
    expect(page).toContain("grid grid-cols-2 gap-3 sm:grid-cols-4");
    expect(page).not.toMatch(/#[0-9a-fA-F]{6}/);
  });
});
