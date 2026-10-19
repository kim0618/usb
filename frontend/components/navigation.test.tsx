import React from "react";
import { readFileSync } from "node:fs";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { SectionTabs, SystemTabs, isRouteActive } from "./section-tabs";
import { isNavigationActive, navigationItems } from "./app-shell";

let pathname = "/dashboard";
vi.mock("next/navigation", () => ({ usePathname: () => pathname }));

const source = (path: string) => readFileSync(path, "utf8");

describe("Stage 9.10 navigation", () => {
  beforeEach(() => { pathname = "/dashboard"; });
  afterEach(() => cleanup());

  it("defines exactly four top-level menu entries, dashboard first and 선물 before 시스템", () => {
    // The 전략 menu was removed: its screens are reached from the dashboard and from Trading H.
    // 종목 분석 (Strategy A's scanner, GPT analysis and adoption) left with archived Strategy A.
    // 시스템 stays last.
    expect(navigationItems.map(item => item.label)).toEqual(["대시보드", "트레이딩", "선물", "시스템"]);
    expect(navigationItems).toHaveLength(4);
    expect(navigationItems.some(item => item.label === "전략")).toBe(false);
    expect(navigationItems.some(item => item.href === "/strategy-b" || item.href === "/trading-b")).toBe(false);
    const shell = source("components/app-shell.tsx");
    ["후보 종목", "GPT 분석", "설정"].forEach(label => expect(shell).not.toContain(`label: "${label}"`));
  });

  it.each([
    ["/dashboard", "대시보드"],
    ["/strategy-e", "트레이딩"],
    ["/strategy-b", "대시보드"],
    ["/daily", "대시보드"],
    ["/strategy-compare", "대시보드"],
    ["/strategy-history", "대시보드"],
    ["/strategy-h", "트레이딩"],
    ["/trading-h", "트레이딩"],
    ["/crypto-paper", "선물"],
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

  it("no menu entry or active path points at a removed Strategy A screen", () => {
    const removed = ["/trading", "/candidates", "/research", "/adoption", "/shadow"];
    navigationItems.forEach(item => {
      expect(removed).not.toContain(item.href);
      item.activePaths.forEach(path => expect(removed).not.toContain(path));
    });
    removed.forEach(route => expect(() => source(`app${route}/page.tsx`)).toThrow());
  });

  it("styles the active section tab compactly and keeps the focus ring", () => {
    pathname = "/runtime";
    render(<SystemTabs/>);
    expect(screen.getByRole("link", { name: "시스템 상태" })).toHaveClass("h-9", "border-primary", "bg-primary-soft", "text-primary");
    expect(screen.getByRole("link", { name: "설정" })).toHaveClass("border-line", "bg-surface", "text-foreground-secondary");
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

  it("keeps every route page and grouped tabs in place", () => {
    ["page.tsx", "dashboard/page.tsx", "strategy-b/page.tsx", "strategy-compare/page.tsx", "runtime/page.tsx", "settings/page.tsx"].forEach(path => expect(() => source(`app/${path}`)).not.toThrow());
    ["strategy-b", "strategy-compare"].forEach(route => expect(source(`app/${route}/page.tsx`)).toContain("<StrategyTabs"));
    expect(source("app/runtime/page.tsx")).toContain("<SystemTabs/>");
    expect(source("app/settings/page.tsx")).toContain("<SystemTabs/>");
  });

  it("lands the root on the dashboard", () => {
    expect(source("app/page.tsx")).toContain('redirect("/dashboard")');
    expect(source("app/page.tsx")).not.toContain('redirect("/trading")');
    const shell = source("components/app-shell.tsx");
    expect(shell).toContain('href="/dashboard" aria-label="USB Trading System"');
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

  it("shows no Strategy A broker badge in the header and no sidebar runtime footer", () => {
    const shell = source("components/app-shell.tsx");
    expect(shell).not.toContain("Backend 연결");
    expect(shell).not.toContain('className="absolute bottom-0');
    expect(shell).not.toContain("broker_mode");
  });

  it("renders calendar-authoritative market sessions with explicit active and closed states", () => {
    const shell = source("components/app-shell.tsx");
    expect(shell).toContain("marketSessionSchedule(market.trading_date, market.market_open, market.market_close)");
    expect(shell).toContain("market?.is_trading_day && market.session === session.key");
    expect(shell).toContain("· 현재");
    expect(shell).toContain('market && !market.is_trading_day ? "휴장" : formatMarketSession');
  });

  it("uses one ordered operations row on desktop and allows responsive wrapping below it", () => {
    const shell = source("components/app-shell.tsx");
    expect(shell).toContain('sticky top-0 z-30 border-b border-line bg-header');
    expect(shell).toContain('xl:flex-nowrap');
    expect(shell).not.toContain('border-t border-line');
    expect(shell).toContain('grid w-full shrink-0 grid-cols-3 gap-1.5 sm:flex sm:w-auto');
    expect(shell).toContain('aria-label="미국 시장 세션"');
    expect(shell.indexOf("현재시각 :")).toBeLessThan(shell.indexOf("기준거래일 :"));
    expect(shell.indexOf("기준거래일 :")).toBeLessThan(shell.indexOf('aria-label="미국 시장 세션"'));
  });

  it("takes the trading date from the market calendar, not from Strategy A's scanner or paper account", () => {
    const shell = source("components/app-shell.tsx");
    expect(shell).toContain("const contextTradingDate = market?.trading_date");
    expect(shell).toContain("현재시각 :");
    expect(shell).toContain("기준거래일 :");
    expect(shell).toContain("tradingDate(contextTradingDate)");
    expect(shell).not.toContain("scanner.trading_date");
    expect(shell).not.toContain("paper_started_at");
    expect(shell).not.toContain("가상매매 시작 :");
    expect(shell).not.toContain("Paper 시작");
    expect(shell).not.toContain("2026-09-08");
  });

  it("removes the product copy from the operations header without changing sidebar branding", () => {
    const shell = source("components/app-shell.tsx");
    expect(shell).not.toContain("미국주식 트레이딩 시스템");
    expect(shell).toContain('aria-label="USB Trading System"');
    expect(shell).toContain('tracking-[0.12em] text-foreground">USB</span>');
  });
});
