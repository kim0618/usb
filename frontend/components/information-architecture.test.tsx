import React from "react";
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { StrategyTabs, TradingTabs, strategyTabs, tradingTabs } from "./section-tabs";
import { isNavigationActive, navigationItems } from "./app-shell";
import { SETUP_META } from "../lib/strategy-b";
import { mockCandidates, mockPositions } from "../mocks/strategy-b";
import { mockTrades } from "../mocks/strategy-b-trades";

let pathname = "/trading-b";
vi.mock("next/navigation", () => ({ usePathname: () => pathname }));

const source = (path: string) => readFileSync(path, "utf8");
const projectFiles = (directory: string): string[] => readdirSync(directory, { withFileTypes: true }).flatMap(entry => {
  const path = join(directory, entry.name);
  if (entry.name === "node_modules" || entry.name.startsWith(".next")) return [];
  if (entry.isDirectory()) return projectFiles(path);
  return /\.(?:ts|tsx)$/.test(entry.name) && !entry.name.includes(".test.") ? [path] : [];
});

afterEach(() => cleanup());

describe("Final information architecture", () => {
  it("keeps five top-level menus and routes both trading screens to 트레이딩", () => {
    expect(navigationItems.map(item => item.label)).toEqual(["대시보드", "트레이딩", "종목 분석", "전략", "시스템"]);
    const group = (route: string) => navigationItems.find(item => isNavigationActive(route, item))?.label;
    expect(group("/dashboard")).toBe("대시보드");
    expect(group("/trading")).toBe("트레이딩");
    expect(group("/trading-b")).toBe("트레이딩");
    expect(group("/shadow")).toBe("전략");
    expect(group("/strategy-b")).toBe("전략");
    expect(group("/strategy-compare")).toBe("전략");
  });

  it("splits operating screens from result screens in the tab groups", () => {
    expect(tradingTabs).toEqual([
      { href: "/trading", label: "전략 A · 기존 전략" },
      { href: "/trading-b", label: "전략 B · 실시간 모멘텀" },
    ]);
    expect(strategyTabs).toEqual([
      { href: "/shadow", label: "전략 A · 기존 전략" },
      { href: "/strategy-b", label: "전략 B · 실시간 모멘텀" },
      { href: "/strategy-compare", label: "전략 A/B 비교" },
    ]);
  });

  it("renders the trading tabs with the current operating route active", () => {
    render(<TradingTabs/>);
    expect(screen.getByRole("navigation", { name: "트레이딩 화면" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "전략 A · 기존 전략" })).toHaveAttribute("href", "/trading");
    expect(screen.getByRole("link", { name: "전략 B · 실시간 모멘텀" })).toHaveAttribute("aria-current", "page");
  });

  it("renders the strategy tabs on the result routes", () => {
    pathname = "/strategy-b";
    render(<StrategyTabs/>);
    expect(screen.getByRole("navigation", { name: "전략 화면" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "전략 B · 실시간 모멘텀" })).toHaveAttribute("href", "/strategy-b");
    pathname = "/trading-b";
  });

  it("adds Strategy A's trading screen nothing but the tab row", () => {
    const trading = source("app/trading/page.tsx");
    expect(trading).toContain("<TradingTabs/>");
    // Strategy A keeps its own sections and its own data calls, untouched.
    ["계좌 요약", "현재 보유 종목", "<EntryStatusBoard", "api.trading", "api.entryBoard"].forEach(marker => expect(trading).toContain(marker));
    expect(trading).not.toContain("strategyBSource");
  });
});

describe("Strategy B screen responsibilities", () => {
  const tradingB = () => source("app/trading-b/page.tsx");
  const performanceB = () => source("app/strategy-b/page.tsx");

  it("puts the scanner, positions and today's trades on the trading screen", () => {
    ["<StrategyBScanner", "<StrategyBPositions", "<StrategyBTrades", "<StrategyBTradingOverview"].forEach(marker => expect(tradingB()).toContain(marker));
  });

  it("keeps period statistics off the trading screen", () => {
    const rendered = tradingB() + source("components/strategy-b-overview.tsx");
    ["Profit Factor", "최대 낙폭", "기대값", "평균 R", "총 수익률", "StrategyBPerformance", "StrategyBEquityCurve"]
      .forEach(metric => expect(rendered).not.toContain(metric));
  });

  it("keeps the scanner, positions and today's trades off the performance screen", () => {
    ["<StrategyBScanner", "<StrategyBPositions", "<StrategyBTrades", "실시간 스캐너"].forEach(marker => expect(performanceB()).not.toContain(marker));
  });

  it("puts the result statistics on the performance screen", () => {
    expect(performanceB()).toContain("<StrategyBPerformance");
    expect(performanceB()).toContain("<StrategyBEquityCurve");
    expect(performanceB()).toContain("<StrategyBSetupPerformance");
    expect(performanceB()).toContain("<StrategyBDailyPnl");
    const performance = source("components/strategy-b-performance.tsx");
    ["Profit Factor", "최대 낙폭", "기대값", "평균 R", "총 수익률"].forEach(metric => expect(performance).toContain(metric));
  });

  it("titles each screen for its role", () => {
    expect(tradingB()).toContain('title="전략 B · 실시간 모멘텀"');
    expect(performanceB()).toContain('title="전략 B · 성과"');
  });
});

describe("Strategy B V1 setups", () => {
  it("allows exactly the two V1 setups", () => {
    expect(Object.keys(SETUP_META)).toEqual(["HOD_BREAKOUT", "FIRST_PULLBACK"]);
    expect(Object.values(SETUP_META).map(meta => meta.label)).toEqual(["당일 고가 돌파", "첫 눌림목"]);
  });

  it("uses only those setups in every mock row", () => {
    const allowed = new Set(Object.keys(SETUP_META));
    mockCandidates.forEach(candidate => expect(candidate.setup === null || allowed.has(candidate.setup)).toBe(true));
    mockPositions.forEach(position => expect(allowed.has(position.setup)).toBe(true));
    mockTrades.forEach(trade => expect(allowed.has(trade.setup)).toBe(true));
  });

  it("leaves no VWAP reclaim setup anywhere in the frontend", () => {
    const files = ["app", "components", "lib", "mocks", "types"].flatMap(projectFiles);
    files.forEach(file => {
      const text = source(file);
      expect(text, file).not.toContain("VWAP_RECLAIM");
      expect(text, file).not.toContain("VWAP 회복");
    });
  });
});

describe("Screen chrome is not repeated", () => {
  it("drops the English eyebrow and the warning bar from the reorganised screens", () => {
    ["app/dashboard/page.tsx", "app/trading-b/page.tsx", "app/strategy-b/page.tsx", "app/strategy-compare/page.tsx"].forEach(path => {
      const page = source(path);
      expect(page, path).not.toContain("eyebrow=");
      expect(page, path).not.toContain("MockDataNotice");
    });
  });

  it("no longer repeats market, system and broker mode inside the dashboard", () => {
    const page = source("app/dashboard/page.tsx");
    expect(page).not.toContain("SystemStatusStrip");
    expect(page).not.toContain("api.dashboard");
    // The global header keeps showing them.
    expect(source("components/app-shell.tsx")).toContain("formatMarketSession");
  });

  it("keeps one mock marker per screen", () => {
    expect(() => statSync("components/mock-data-notice.tsx")).toThrow();
    const badge = source("components/mock-badge.tsx");
    expect(badge).toContain("MockBadge");
    ["app/dashboard/page.tsx", "app/trading-b/page.tsx", "app/strategy-b/page.tsx", "app/strategy-compare/page.tsx"]
      .forEach(path => expect(source(path).match(/<MockBadge/g)?.length ?? 0, path).toBe(1));
  });
});
