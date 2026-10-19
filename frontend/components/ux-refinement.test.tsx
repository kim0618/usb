import React from "react";
import { readFileSync } from "node:fs";
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ConvertedKrw, EmptyState, InfoTooltip, PageHeader } from "./ui";

const source = (path: string) => readFileSync(path, "utf8");

describe("Stage 9.6 UX policy", () => {
  it("keeps the four confirmed top-level navigation labels", () => {
    const shell = source("components/app-shell.tsx");
    ["대시보드", "트레이딩", 'label: "선물"', "시스템"].forEach(label => expect(shell).toContain(label));
    expect(shell).not.toContain('label: "종목 분석"');
    expect(shell).not.toContain('label: "전략"');
    expect(shell).not.toContain('label: "전략 성과"');
  });
  it("redirects the root route to the dashboard", () => {
    expect(source("app/page.tsx")).toContain('redirect("/dashboard")');
  });
  it("uses compact empty states", () => {
    render(<EmptyState title="주문 내역이 없습니다."/>);
    expect(screen.getByText("주문 내역이 없습니다.").parentElement).toHaveAttribute("data-empty-size", "compact");
  });
  it("only renders secondary KRW when an explicit converted amount is supplied", () => {
    const { rerender } = render(<ConvertedKrw/>);
    expect(screen.queryByText(/환산 약/)).not.toBeInTheDocument();
    rerender(<ConvertedKrw amount="76204000"/>);
    expect(screen.getByText("환산 약 ₩76,204,000")).toHaveClass("text-muted");
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
  it("provides focusable, described help for table headings", () => {
    render(<InfoTooltip label="종합 점수" text="점수 설명"/>);
    const trigger = screen.getByRole("button", { name: "종합 점수 설명" });
    expect(trigger).toHaveAttribute("aria-describedby", "tooltip-종합 점수");
    expect(screen.getByRole("tooltip")).toHaveTextContent("점수 설명");
  });
});
