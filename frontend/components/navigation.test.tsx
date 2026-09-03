import React from "react";
import { readFileSync } from "node:fs";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AnalysisTabs, SectionTabs, SystemTabs, isRouteActive } from "./section-tabs";
import { isNavigationActive, navigationItems } from "./app-shell";

let pathname = "/candidates";
vi.mock("next/navigation", () => ({ usePathname: () => pathname }));

const source = (path: string) => readFileSync(path, "utf8");

describe("Stage 9.10 navigation", () => {
  beforeEach(() => { pathname = "/candidates"; });
  afterEach(() => cleanup());

  it("defines exactly four top-level menu entries in trading-first order", () => {
    expect(navigationItems.map(item => item.label)).toEqual(["트레이딩", "종목 분석", "전략 성과", "시스템"]);
    expect(navigationItems).toHaveLength(4);
    expect(navigationItems.some(item => item.label === "대시보드")).toBe(false);
    const shell = source("components/app-shell.tsx");
    ["후보 종목", "GPT 분석", "설정"].forEach(label => expect(shell).not.toContain(`label: "${label}"`));
  });

  it.each([
    ["/trading", "트레이딩"],
    ["/candidates", "종목 분석"],
    ["/candidates/NVDA", "종목 분석"],
    ["/research", "종목 분석"],
    ["/shadow", "전략 성과"],
    ["/runtime", "시스템"],
    ["/settings", "시스템"],
  ])("maps %s to the %s group", (route, label) => {
    const item = navigationItems.find(candidate => candidate.label === label);
    expect(item).toBeDefined();
    expect(isNavigationActive(route, item!)).toBe(true);
    navigationItems.filter(candidate => candidate !== item).forEach(candidate => expect(isNavigationActive(route, candidate)).toBe(false));
  });

  it("matches tab routes by exact path and nested prefix", () => {
    expect(isRouteActive("/research", "/research")).toBe(true);
    expect(isRouteActive("/research/NVDA", "/research")).toBe(true);
    expect(isRouteActive("/research-old", "/research")).toBe(false);
  });

  it("renders analysis tabs as links with the current route", () => {
    render(<AnalysisTabs/>);
    expect(screen.getByRole("navigation", { name: "분석 화면" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "후보 종목" })).toHaveAttribute("href", "/candidates");
    expect(screen.getByRole("link", { name: "후보 종목" })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("link", { name: "GPT 분석" })).toHaveAttribute("href", "/research");
  });

  it("shares compact blue analysis navigation across both active routes", () => {
    const first = render(<AnalysisTabs/>);
    expect(screen.getByRole("link", { name: "후보 종목" })).toHaveClass("h-9", "border-primary", "bg-primary-soft", "text-primary");
    expect(screen.getByRole("link", { name: "GPT 분석" })).toHaveClass("border-line", "bg-surface", "text-foreground-secondary");
    first.unmount();
    pathname = "/research";
    render(<AnalysisTabs/>);
    expect(screen.getByRole("link", { name: "GPT 분석" })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("link", { name: "GPT 분석" })).toHaveClass("border-primary", "bg-primary-soft", "text-primary");
    expect(source("components/section-tabs.tsx")).toContain("focus-visible:ring-2");
  });

  it("renders system tabs and activates settings", () => {
    pathname = "/settings";
    render(<SystemTabs/>);
    expect(screen.getByRole("link", { name: "시스템 상태" })).toHaveAttribute("href", "/runtime");
    expect(screen.getByRole("link", { name: "설정" })).toHaveAttribute("aria-current", "page");
  });

  it("uses a semantic navigation label supplied by the caller", () => {
    render(<SectionTabs label="테스트 그룹" tabs={[{ href: "/one", label: "하나" }]}/>);
    expect(screen.getByRole("navigation", { name: "테스트 그룹" })).toBeInTheDocument();
  });

  it("keeps all seven route pages and grouped tabs in place", () => {
    ["page.tsx", "candidates/page.tsx", "research/page.tsx", "trading/page.tsx", "shadow/page.tsx", "runtime/page.tsx", "settings/page.tsx"].forEach(path => expect(() => source(`app/${path}`)).not.toThrow());
    expect(source("app/candidates/page.tsx")).toContain("<AnalysisTabs/>");
    expect(source("app/research/page.tsx")).toContain("<AnalysisTabs/>");
    expect(source("app/runtime/page.tsx")).toContain("<SystemTabs/>");
    expect(source("app/settings/page.tsx")).toContain("<SystemTabs/>");
  });

  it("redirects the root route and sends the logo to trading", () => {
    expect(source("app/page.tsx")).toContain('redirect("/trading")');
    const shell = source("components/app-shell.tsx");
    expect(shell).toContain('href="/trading" aria-label="USB Trading System"');
  });

  it("uses the official vector brand with a theme-aware wordmark", () => {
    const shell = source("components/app-shell.tsx");
    expect(shell).toContain('src="/brand/usb-symbol.svg" alt="" aria-hidden="true" width={28} height={28}');
    expect(shell).toContain('tracking-[0.12em] text-foreground">USB</span>');
    expect(shell).not.toContain('src="/logo.png"');

    const symbol = source("public/brand/usb-symbol.svg");
    const dark = source("public/brand/usb-logo-dark.svg");
    const light = source("public/brand/usb-logo-light.svg");
    expect(symbol).toContain('viewBox="0 0 32 32"');
    expect(dark).toContain('fill="#F5F5F5"');
    expect(light).toContain('fill="#0F172A"');
    expect(dark.match(/<path/g)?.slice(0, 3)).toEqual(symbol.match(/<path/g));
    expect(source("app/layout.tsx")).toContain('icon: "/brand/usb-symbol.svg"');
  });

  it("renders the portfolio-centered Korean trading hierarchy", () => {
    const trading = source("app/trading/page.tsx");
    ["계좌 요약", "총 자산", "투자 중", "보유 현금", "평가 손익", "오늘 손익", "현재 보유 종목", "진입 대기"].forEach(label => expect(trading).toContain(label));
    ["오늘의 운영 요약", "미국 시장", "오늘 승인 종목", "매매 현황", "시스템 보기 →"].forEach(label => expect(trading).not.toContain(label));
    expect(trading).toContain('human_decision?.decision === "APPROVE"');
    expect(trading).toContain(".slice(0, 2)");
  });
});
