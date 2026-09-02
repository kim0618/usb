import React from "react";
import { readFileSync } from "node:fs";
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { EmptyState, PageHeader } from "./ui";

const source = (path: string) => readFileSync(path, "utf8");

describe("Stage 9.6 UX policy", () => {
  it("keeps the five Stage 9.9 top-level navigation labels", () => {
    const shell = source("components/app-shell.tsx");
    ["대시보드", "분석", "매매", "전략 성과", "시스템"].forEach(label => expect(shell).toContain(label));
  });
  it("renders Korean dashboard and research workflow labels", () => {
    expect(source("app/page.tsx")).toContain("운영 대시보드");
    expect(source("app/research/page.tsx")).toContain("1. 프롬프트 복사");
    expect(source("app/research/page.tsx")).toContain("4. 승인 / 거절");
  });
  it("uses compact trading empty states", () => {
    render(<EmptyState title="주문 내역이 없습니다."/>);
    expect(screen.getByText("주문 내역이 없습니다.").parentElement).toHaveAttribute("data-empty-size", "compact");
    expect(source("app/trading/page.tsx")).toContain("주문 내역이 없습니다.");
  });
  it("documents every Shadow variant and CONTROL", () => {
    const shadow = source("app/shadow/page.tsx");
    ["당일 청산 · ATR 1.5", "Day2 허용 · ATR 1.0", "Day2 허용 · ATR 1.5", "Day2 허용 · ATR 2.0", "당일 청산 · 구조 손절", "기준 전략 · CONTROL"].forEach(label => expect(shadow).toContain(label));
  });
  it("groups runtime controls and keeps Kill Switch two-step confirmation", () => {
    const runtime = source("app/runtime/page.tsx");
    ["운영 제어", "복구 및 검증", "비상 중지 (Kill Switch)", "2단계 확인", "understood"].forEach(label => expect(runtime).toContain(label));
  });
  it("shows Korean capability labels", () => {
    const settings = source("app/settings/page.tsx");
    ["지원 기능", "시장 데이터", "브로커", "주요 기능", "키움증권", "보안 안내"].forEach(label => expect(settings).toContain(label));
  });
  it("renders the Korean page header", () => {
    render(<PageHeader title="운영 대시보드" description="오늘의 운영 상태를 확인합니다."/>);
    expect(screen.getByRole("heading", { name: "운영 대시보드" })).toBeInTheDocument();
  });
});
