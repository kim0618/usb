import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const source = (path: string) => readFileSync(path, "utf8");

describe("Iteration 4 analysis UI polish", () => {
  it("keeps prompt copy primary and preview secondary on the candidate page", () => {
    const page = source("app/candidates/page.tsx");
    expect(page).toContain('className="btn-action-primary" disabled={busy}');
    expect(page).toContain("GPT 프롬프트 복사");
    expect(page).toContain('className="btn-action-secondary" disabled={busy}');
    expect(page).toContain(">미리보기</button>");
    expect(page).toContain('className="analysis-table analysis-table-candidates"');
  });

  it("uses a compact import panel with a primary validation action", () => {
    const page = source("app/research/page.tsx");
    expect(page).toContain('title="GPT 분석"');
    expect(page).toContain('className="input h-32');
    expect(page).toContain('className="btn-action-primary" disabled={busy || !raw.trim()}');
    expect(page).toContain("검증 후 적용");
    expect(page).toContain("아직 불러온 분석 결과가 없습니다.");
  });

  it("aligns adoption summary and table without changing decision controls", () => {
    const page = source("app/adoption/page.tsx");
    const decisions = source("components/research-decision.tsx");
    expect(page).toContain('<PageHeader title="채택 후보"');
    expect(page).toContain('className="analysis-table"');
    expect(page).toContain("최종 채택 {approved}/2");
    expect(decisions).toContain('className="btn-success-soft"');
    expect(decisions).toContain('className="btn-danger-soft"');
    expect(decisions).toContain('onDecide("APPROVE")');
    expect(decisions).toContain('onDecide("REJECT")');
  });

  it("scopes compact density to analysis tables", () => {
    const css = source("app/globals.css");
    expect(css).toContain(".analysis-table th");
    expect(css).toContain(".analysis-table td");
    expect(css).toContain("\nth { @apply border-b border-line");
  });
});
