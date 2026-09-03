import React from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { describe, expect, it, vi } from "vitest";
import { Drawer } from "./ui";

const research = () => readFileSync("app/research/page.tsx", "utf8");

describe("Stage 9.13.2 research detail drawer", () => {
  it("is a responsive dialog and closes from its button or Escape", () => {
    const onClose = vi.fn();
    const { rerender } = render(<Drawer open title="S04 · 종목 분석" onClose={onClose}>상세 내용</Drawer>);
    expect(screen.getByRole("dialog", { name: "S04 · 종목 분석" })).toHaveClass("w-full", "sm:max-w-[640px]");
    fireEvent.keyDown(document, { key: "Escape" });
    fireEvent.click(screen.getByRole("button", { name: "상세 닫기" }));
    expect(onClose).toHaveBeenCalledTimes(2);
    rerender(<Drawer open={false} title="S04 · 종목 분석" onClose={onClose}>상세 내용</Drawer>);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("keeps import validation labels and removes the old re-import wording", () => {
    const page = research();
    ["분석 결과 입력", "GPT 분석 JSON", "ChatGPT가 반환한 JSON을 붙여넣으세요.", "검증 후 적용", "api.importResearch(raw)"].forEach(value => expect(page).toContain(value));
    expect(page).not.toContain("분석 결과 다시 불러오기");
    expect(page).not.toContain("GPT 프롬프트 복사");
    expect(page).not.toContain("api.prompt()");
  });

  it("fetches candidate detail and the matching persisted quant snapshot", () => {
    const page = research();
    expect(page).toContain("api.researchDetail(result.data.analysis.id, symbol)");
    expect(page).toContain("api.scannerRun(result.data.analysis.scanner_run_id)");
    expect(page).toContain("setDetailTradingDate(scanner.run.trading_date)");
    expect(page).toContain("상세 분석 불러오는 중");
    expect(page).toContain("retry={() => selectedSymbol && void openDetail(selectedSymbol)}");
  });

  it("uses the drawer for decisions without duplicating the table score grids", () => {
    const page = research();
    ["회사 정보", "최근 주가 흐름", "주요 재료", "위험 요인", "주의 / 무효화 조건", "근거 자료", "전략 참고", "확인되지 않은 항목", "회사 정보 없음"].forEach(value => expect(page).toContain(value));
    ["company_summary", "catalyst_summary", "risk_summary", "invalidation_summary", "unknown_fields"].forEach(value => expect(page).toContain(value));
    expect(page).not.toContain('<Section title="Quant 분석"');
    expect(page).not.toContain('<Section title="GPT 평가"');
    expect(page).not.toContain("function ScoreGrid");
  });

  it("shows compact ranks and only available ScannerRun market facts", () => {
    const page = research();
    ["Quant {detail.quant_rank", "GPT #{detail.gpt_rank}", "기준 거래일", "전일 종가", "전일 거래량", "평균 대비 거래량", "raw_metrics.rvol"].forEach(value => expect(page).toContain(value));
    ["전일 등락", "전일 고가 / 저가", "애프터마켓", "프리마켓", "데이터 미제공"].forEach(value => expect(page).toContain(value));
    expect(page).toContain("quant?.latest_close");
    expect(page).toContain("quant?.latest_volume");
    expect(page).not.toContain("signedPercent(quant");
  });

  it("keeps safe sources, raw decisions, conflict UX, and refetch synchronization", () => {
    const page = research();
    expect(page).toContain('rel="noopener noreferrer"');
    expect(page).toContain('target="_blank"');
    expect(page).toContain('decision: "APPROVE" | "REJECT"');
    expect(page).toContain("evidence_confidence");
    expect(page).toContain("error.status === 409");
    expect(page).toContain("await result.refresh()");
    expect(page).toContain("setDetail(await api.researchDetail");
  });

  it("keeps table decisions read-only and routes final decisions through the drawer", () => {
    const page = research();
    expect(page).toContain("상세보기");
    expect(page).toContain("최종 결정");
    expect(page).toContain("<ResearchDecisionControl");
    expect(page).not.toContain('onClick={() => void decide(candidate.symbol');
  });
});
