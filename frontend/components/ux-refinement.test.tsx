import React from "react";
import { readFileSync } from "node:fs";
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ConvertedKrw, EmptyState, PageHeader } from "./ui";

const source = (path: string) => readFileSync(path, "utf8");

describe("Stage 9.6 UX policy", () => {
  it("keeps the four Stage 9.10 top-level navigation labels", () => {
    const shell = source("components/app-shell.tsx");
    ["트레이딩", "종목 분석", "전략 성과", "시스템"].forEach(label => expect(shell).toContain(label));
    expect(shell).not.toContain('label: "대시보드"');
  });
  it("redirects the dashboard route and removes the research onboarding guide", () => {
    expect(source("app/page.tsx")).toContain('redirect("/trading")');
    expect(source("app/research/page.tsx")).not.toContain("1. 프롬프트 복사");
    expect(source("app/research/page.tsx")).not.toContain("4. 승인 / 거절");
  });
  it("uses compact trading empty states", () => {
    render(<EmptyState title="주문 내역이 없습니다."/>);
    expect(screen.getByText("주문 내역이 없습니다.").parentElement).toHaveAttribute("data-empty-size", "compact");
    expect(source("app/trading/page.tsx")).toContain("청산 내역이 없습니다.");
  });
  it("only renders secondary KRW when an explicit converted amount is supplied", () => {
    const { rerender } = render(<ConvertedKrw/>);
    expect(screen.queryByText(/환산 약/)).not.toBeInTheDocument();
    rerender(<ConvertedKrw amount="76204000"/>);
    expect(screen.getByText("환산 약 ₩76,204,000")).toHaveClass("text-muted");
  });
  it("documents every Shadow variant and the control strategy", () => {
    const shadow = source("app/shadow/page.tsx") + source("components/shadow-performance.tsx") + source("lib/shadow-performance.ts");
    ["당일 청산 · ATR 1.5", "Day2 허용 · ATR 1.0", "Day2 허용 · ATR 1.5", "Day2 허용 · ATR 2.0", "당일 청산 · 구조 손절", "기준 전략"].forEach(label => expect(shadow).toContain(label));
  });
  it("keeps minimal runtime status, failures, controls, and Kill Switch confirmation", () => {
    const runtime = source("app/runtime/page.tsx");
    ["시스템 상태", "현재 상태", "운영 모드", "핵심 구성요소", "시세 데이터", "주문 실행", "상태 대조", "시스템 신호", "현재 장애", "시스템 제어", "안전 모드", "정상 복구", "자동 매매 중지", "비상 중지", "2단계 확인", "understood"].forEach(label => expect(runtime).toContain(label));
    expect(runtime).not.toContain('label="보유 포지션"');
    expect(runtime).not.toContain('label="미체결 주문"');
    expect(runtime).not.toContain('label="Heartbeat"');
    expect(runtime).toContain("allFailures.filter(f=>!f.resolved)");
    expect(runtime).toContain("전체 기록 보기");
    expect(runtime).toContain("api.resolveFailure(id)");
    expect(runtime).toContain('api.runtimeAction(action,reason)');
    expect(runtime).toContain("api.reconcile()");
    expect(runtime).toContain("api.recover()");
    expect(runtime).toContain("api.killSwitch(reason)");
  });
  it("shows compact connection and system information", () => {
    const settings = source("app/settings/page.tsx");
    ["연결 상태", "키움증권", "시장 데이터", "매매 모드", "시스템 정보", "환경", "USB Internal V1", "상세 버전 보기"].forEach(label => expect(settings).toContain(label));
    ["지원 기능", "보안 안내", "capabilityNames"].forEach(label => expect(settings).not.toContain(label));
    expect(settings).toContain("showVersions&&");
    ["scanner_version", "research_prompt_version", "research_schema_version", "evidence_version", "risk_version", "execution_version", "strategy_version", "shadow_variant_version", "operations_version"].forEach(value => expect(settings).toContain(value));
    expect(settings).toContain("c.market_data.kiwoom");
    expect(settings).toContain("c.market_data.fake||c.market_data.replay");
    expect(settings).toContain("formatBrokerMode(s.broker_mode)");
  });
  it("renders the Korean page header", () => {
    render(<PageHeader title="운영 대시보드" description="오늘의 운영 상태를 확인합니다."/>);
    expect(screen.getByRole("heading", { name: "운영 대시보드" })).toBeInTheDocument();
  });
});

describe("Stage 9.13.3 analysis workflow policy", () => {
  it("keeps candidates Quant-first and uses compact metadata", () => {
    const candidates = source("app/candidates/page.tsx");
    ["Quant 순위", "Quant 점수", "RVOL", "상대강도", "거래대금", "모멘텀", "상태"].forEach(label => expect(candidates).toContain(label));
    ["multiple(c.raw_metrics.rvol)", "signedPercent(c.raw_metrics.relative_strength)", "compactUsd(c.raw_metrics.dollar_volume)", "signedPercent(c.raw_metrics.momentum)"].forEach(call => expect(candidates).toContain(call));
    expect(candidates).toContain("기준 거래일");
    expect(candidates).toContain("const showCompany=s.top8.some");
    expect(candidates).not.toContain('<MetricCard label="거래일"');
    expect(candidates).not.toContain("<th>GPT 순위</th>");
    expect(candidates).not.toContain("<th>근거 점수</th>");
    expect(candidates).toContain("미리보기");
    expect(candidates).toContain("GPT 프롬프트 복사");
  });

  it("keeps research result-first with an optional re-import area", () => {
    const research = source("app/research/page.tsx");
    ["GPT 순위", "Quant 순위", "GPT 종합", "재료", "펀더멘털", "모멘텀", "위험", "근거 점수", "작업", "최종 결정", "상세보기"].forEach(label => expect(research).toContain(label));
    expect(research).toContain("showImport && importArea");
    expect(research).toContain("분석 결과 입력");
    expect(research).not.toContain("GPT 프롬프트 복사");
    expect(research).not.toContain("분석 결과 다시 불러오기");
    expect(research).toContain("noAnalysis");
    expect(research).toContain("setShowImport(false)");
    expect(research).not.toContain('<MetricCard label="분석 종목"');
    expect(research).not.toContain("GPT 분석 및 투자 승인");
    expect(research).toContain('api.decide(result.data.analysis.id, symbol, decision)');
    expect(research).not.toContain('onClick={() => void decide(candidate.symbol');
    expect(research).toContain("error.status === 409");
    expect(research).toContain("api.researchDetail");
    expect(research).toContain("api.importResearch(raw)");
  });
});
