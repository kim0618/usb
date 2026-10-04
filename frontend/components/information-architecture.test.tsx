import React from "react";
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { StrategyTabs, TradingTabs, strategyTabs } from "./section-tabs";
import { OPERATING_ROUTES, operatingTabs } from "../lib/strategies";
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
  it("keeps six top-level menus and routes both trading screens to 트레이딩", () => {
    expect(navigationItems.map(item => item.label)).toEqual(["대시보드", "트레이딩", "종목 분석", "전략", "선물", "시스템"]);
    const group = (route: string) => navigationItems.find(item => isNavigationActive(route, item))?.label;
    expect(group("/dashboard")).toBe("대시보드");
    expect(group("/trading")).toBe("트레이딩");
    expect(group("/trading-b")).toBe("트레이딩");
    expect(group("/shadow")).toBe("전략");
    expect(group("/strategy-b")).toBe("전략");
    expect(group("/strategy-compare")).toBe("전략");
    expect(group("/strategy-history")).toBe("전략");
    expect(group("/crypto-paper")).toBe("선물");
  });

  it("derives the operating tabs from the registry and keeps the result tabs fixed", () => {
    const registry = [
      { strategy_id: "STRATEGY_A", display_name: "Strategy A", variant_label: "기존 전략", enabled: true, mode: "SIMULATION_PAPER" },
      { strategy_id: "STRATEGY_E_MAX_V1", display_name: "Strategy E", variant_label: "E-MAX V1", enabled: true, mode: "SIMULATION_PAPER" },
      { strategy_id: "STRATEGY_B", display_name: "Strategy B", variant_label: "실시간 모멘텀", enabled: false, mode: "RESEARCH_CLOSED" },
    ] as never;
    // Labels are the registry's own display_name + variant; no strategy name is typed into the tab code.
    expect(operatingTabs(registry)).toEqual([
      { strategy_id: "STRATEGY_A", href: "/trading", label: "Strategy A · 기존 전략" },
      { strategy_id: "STRATEGY_E_MAX_V1", href: "/strategy-e", label: "Strategy E · E-MAX V1" },
    ]);
    // H-V2-D7: Strategy H operates too, as a forward shadow rather than an order-placing paper
    // strategy, so the filter is the set of paper modes and H gets a tab from the registry.
    expect(operatingTabs([...registry as never[],
      { strategy_id: "STRATEGY_H_V2", display_name: "Strategy H", variant_label: "Forward Shadow",
        enabled: true, mode: "FORWARD_SHADOW" }] as never)).toContainEqual(
      { strategy_id: "STRATEGY_H_V2", href: "/strategy-h", label: "Strategy H · Forward Shadow" });
    expect(Object.keys(OPERATING_ROUTES))
      .toEqual(["STRATEGY_A", "STRATEGY_E_MAX_V1", "STRATEGY_H_V2"]);   // B is not operating
    // A's exit-rule experiment is not a tab: its variants are also called A to E, which collides
    // with Strategy A and Strategy E, and the server's shadow_trades table is empty. The route
    // survives and the research history links to it.
    // The strategy group is one screen now, so it has no tab bar: `strategyTabs` is empty and
    // `StrategyTabs` renders nothing. The analysis routes keep working, they are just not tabs.
    expect(strategyTabs).toEqual([]);

    expect(source("lib/strategies.ts")).not.toMatch(/전략 [AE] ·/);
  });

  it("renders the operating tabs the registry returns, with the current route active", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true, status: 200, json: async () => [
      { strategy_id: "STRATEGY_A", display_name: "Strategy A", variant_label: "기존 전략", version: "V0", enabled: true,
        mode: "SIMULATION_PAPER", market_data_source: "KIWOOM", runtime_status: "RUNNING",
        paper_status: "PAPER", evidence_status: null, session: null },
      { strategy_id: "STRATEGY_E_MAX_V1", display_name: "Strategy E", variant_label: "E-MAX V1", version: "E-MAX V1", enabled: true,
        mode: "SIMULATION_PAPER", market_data_source: "KIWOOM", runtime_status: "COMPLETE",
        paper_status: "PROVISIONAL_RVOL_BOOTSTRAP", evidence_status: "PROVISIONAL_RVOL_BOOTSTRAP", session: null },
      { strategy_id: "STRATEGY_B", display_name: "Strategy B", version: "E1-A", enabled: false,
        mode: "RESEARCH_CLOSED", market_data_source: "KIWOOM", runtime_status: "NOT_RUNNING",
        paper_status: null, evidence_status: null, session: null },
    ] }) as Response));
    pathname = "/strategy-e";
    render(<TradingTabs/>);
    expect(await screen.findByRole("link", { name: "Strategy E · E-MAX V1" })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("navigation", { name: "트레이딩 화면" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Strategy A · 기존 전략" })).toHaveAttribute("href", "/trading");
    expect(screen.queryByRole("link", { name: /Strategy B|전략 B/ })).not.toBeInTheDocument();   // closed: hidden
    pathname = "/trading-b";
  });
  it("gives the strategy group no tab bar: it is a single screen", () => {
    pathname = "/daily";
    render(<StrategyTabs/>);
    // the landmark stays so the markup does not shift, but it carries no link at all
    expect(screen.getByRole("navigation", { name: "전략 화면" })).toBeInTheDocument();
    expect(screen.queryAllByRole("link")).toHaveLength(0);
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

describe("Closed Strategy B screens", () => {
  // B closed on 2026-09-23. Its old routes stay reachable by URL but render only a CLOSED notice:
  // no mock figure, no scanner, no position, so B can never read as a running strategy. B's
  // components, mocks and research code stay in the repository untouched.
  const tradingB = () => source("app/trading-b/page.tsx");
  const performanceB = () => source("app/strategy-b/page.tsx");

  it("render the closed notice and nothing from the B mock module", () => {
    [tradingB(), performanceB()].forEach(page => {
      expect(page).toContain("<ClosedStrategyNotice");
      expect(page).not.toMatch(/strategy-b-source|mocks\/|StrategyB(Scanner|Positions|Trades|Performance|EquityCurve)/);
    });
  });

  it("titles each screen with the registry name and CLOSED", () => {
    [tradingB(), performanceB()].forEach(page => expect(page).toContain("`${row.display_name} · CLOSED`"));
  });

  it("keeps B's research-era components in the repository", () => {
    ["components/strategy-b-scanner.tsx", "components/strategy-b-performance.tsx", "lib/strategy-b.ts"]
      .forEach(path => expect(() => statSync(path)).not.toThrow());
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
    // Every operating and result screen now reads the live API; none carries a mock marker.
    ["app/dashboard/page.tsx", "app/trading-b/page.tsx", "app/strategy-b/page.tsx", "app/strategy-compare/page.tsx",
     "app/strategy-history/page.tsx"].forEach(path => expect(source(path), path).not.toContain("MockBadge"));
  });
});
