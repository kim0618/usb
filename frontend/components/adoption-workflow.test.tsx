import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { analysisTabs } from "./section-tabs";

const adoption = () => readFileSync("app/adoption/page.tsx", "utf8");
const research = () => readFileSync("app/research/page.tsx", "utf8");

describe("Stage 10B-4 adoption workflow", () => {
  it("adds the shared third analysis tab", () => {
    expect(analysisTabs.map(tab => tab.label)).toEqual(["후보 종목", "GPT 분석", "채택 후보"]);
  });
  it("keeps GPT analysis result-only with corrected safety polarity", () => {
    const page = research();
    expect(page).toContain("분석 상태"); expect(page).toContain("분석 완료");
    expect(page).toContain("높을수록 안전합니다");
    expect(page).not.toContain("상세보기"); expect(page).not.toContain("최종 결정");
  });
  it("renders classification, excluded toggle, rank delta and human review detail", () => {
    const page = adoption();
    ["채택 후보", "검토 필요", "제외", "제외 숨기기", "Quant → GPT", "순위 변화", "채택 이유", "순위 변화 이유", "위험 / 주의"].forEach(text => expect(page).toContain(text));
    expect(page).toContain("approved >= 2"); expect(page).toContain("최종 채택 {approved}/2");
    expect(page).toContain("table-wrap"); expect(page).toContain("Drawer");
  });
  it("keeps the final review free of report-only research sections", () => {
    const page = adoption();
    ["왜 후보인가", "왜 주의해야 하나", "핵심 촉매", "위험 요인", "무효화 조건", "근거 자료", "미확인 사항"].forEach(text => expect(page).not.toContain(text));
    ["invalidation_summary", "unknown_fields", "detail.sources"].forEach(field => expect(page).not.toContain(field));
    expect(page).toContain("detail.catalyst_summary"); expect(page).toContain("detail.risk_summary");
  });
  it("uses only semantic theme tokens and retains accessible interaction state", () => {
    const page = adoption();
    expect(page).not.toMatch(/(?:bg|text|border)-(?:red|green|amber|blue)-/);
    expect(page).toContain("aria-pressed={showExcluded}");
    expect(page).toMatch(/tone-text-\$\{/);
    expect(page).toContain("btn-action-secondary-compact");
  });
});
