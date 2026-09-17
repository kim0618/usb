import React from "react";
import { readFileSync } from "node:fs";
import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import DashboardPage from "../app/dashboard/page";
import { RecentActivity, StrategyCards, TodaySummary } from "./dashboard-summary";
import { strategyTabs } from "./section-tabs";
import { navigationItems } from "./app-shell";
import { DASHBOARD_MOCK, dashboardSource, recentEvents, strategyBEvents } from "../lib/dashboard-source";
import { mockStrategyAEvents, mockStrategyAToday } from "../mocks/dashboard";
import { mockCandidates, mockPositions, mockSummary } from "../mocks/strategy-b";
import { mockTrades } from "../mocks/strategy-b-trades";
import { mockComparison } from "../mocks/strategy-comparison";
import type { DashboardSnapshot } from "../types/dashboard";

const source = (path: string) => readFileSync(path, "utf8");

let snapshot: DashboardSnapshot;
beforeEach(async () => { snapshot = await dashboardSource.snapshot(); });
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

describe("Dashboard composition", () => {
  it("takes every Strategy B figure from the Strategy B mock, not a new constant", () => {
    expect(snapshot.b.current_equity_krw).toBe(mockSummary.current_equity_krw);
    expect(snapshot.b.total_return_pct).toBe(mockSummary.total_return_pct);
    expect(snapshot.b.today_pnl_krw).toBe(mockSummary.today_pnl_krw);
    expect(snapshot.b.open_positions).toBe(mockPositions.length);
    expect(snapshot.b.closed_trades_today).toBe(mockTrades.length);
    expect(snapshot.b.name).toBe(mockSummary.name);
  });

  it("takes Strategy A's period figures from the comparison mock", () => {
    expect(snapshot.a.current_equity_krw).toBe(mockComparison.a.current_equity_krw);
    expect(snapshot.a.total_return_pct).toBe(mockComparison.a.return_pct);
    expect(snapshot.a.initial_capital_krw).toBe(mockComparison.a.initial_capital_krw);
    expect(snapshot.a.name).toBe(mockComparison.a.label);
    expect(snapshot.a.today_pnl_krw).toBe(mockStrategyAToday.today_pnl_krw);
    expect(snapshot.equity_curve).toBe(mockComparison.equity_curve);
  });

  it("keeps the Strategy B mock and the comparison mock agreeing on B's equity", () => {
    expect(mockSummary.current_equity_krw).toBe(mockComparison.b.current_equity_krw);
    expect(mockSummary.total_return_pct).toBe(mockComparison.b.return_pct);
    expect(mockSummary.initial_capital_krw).toBe(mockComparison.b.initial_capital_krw);
  });

  it("derives Strategy B events from the scanner mock's own timestamps", () => {
    const events = strategyBEvents(mockCandidates);
    const entered = events.find(event => event.id === "B-NVTS-entered_at")!;
    expect(entered.at).toBe(mockCandidates[0].entered_at);
    expect(entered.message).toContain("진입 완료");
    expect(events.every(event => event.strategy === "B")).toBe(true);
    // No candidate step without a recorded timestamp becomes an event.
    expect(events.some(event => event.symbol === "SNTI" && event.id.endsWith("signal_at"))).toBe(false);
  });

  it("merges both strategies into one newest-first timeline without B crowding A out", () => {
    const merged = recentEvents([...mockStrategyAEvents, ...strategyBEvents(mockCandidates)], 3);
    expect([...merged].sort((left, right) => right.at.localeCompare(left.at))).toEqual(merged);
    expect(merged.filter(event => event.strategy === "A")).toHaveLength(3);
    expect(merged.filter(event => event.strategy === "B")).toHaveLength(3);
    // Strategy B alone produces more recent events than the whole list can hold.
    expect(strategyBEvents(mockCandidates).length).toBeGreaterThan(merged.length);
  });

  it("does not import a mock into a dashboard component", () => {
    expect(source("components/dashboard-summary.tsx")).not.toContain("@/mocks/");
    expect(source("app/dashboard/page.tsx")).not.toContain("@/mocks/");
  });
});

describe("Dashboard screen", () => {
  it("renders the page with both strategy cards once the snapshot resolves", async () => {
    render(<DashboardPage/>);
    // The loading branch renders its own header node, so wait for the data branch first.
    expect(await screen.findByText("STRATEGY A")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "대시보드" })).toBeInTheDocument();
    expect(screen.getByText("STRATEGY B")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "전략 자산 추이" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "오늘 요약" })).toBeInTheDocument();
  });

  it("does not repeat the header's market, system and broker badges", () => {
    render(<DashboardPage/>);
    ["시장 상태", "운영 모드", "확인 불가"].forEach(label => expect(screen.queryByText(label)).not.toBeInTheDocument());
  });

  it("shows each strategy's equity, return, today P&L and positions", () => {
    render(<StrategyCards snapshot={snapshot}/>);
    const cardA = screen.getByRole("region", { name: /STRATEGY A/ });
    expect(within(cardA).getByText("$7,711.22")).toBeInTheDocument();
    expect(within(cardA).getByText("≈ ₩10,380,000")).toBeInTheDocument();
    expect(within(cardA).getByText("+3.80%")).toBeInTheDocument();
    expect(within(cardA).getByText("+$60.92")).toBeInTheDocument();
    expect(within(cardA).getByText("≈ +₩82,000")).toBeInTheDocument();
    expect(within(cardA).getByText("1종목")).toBeInTheDocument();
    const cardB = screen.getByRole("region", { name: /STRATEGY B/ });
    expect(within(cardB).getByText("$7,980.15")).toBeInTheDocument();
    expect(within(cardB).getByText("≈ ₩10,742,000")).toBeInTheDocument();
    expect(within(cardB).getByText("+7.42%")).toBeInTheDocument();
    expect(within(cardB).getByText("+$136.69")).toBeInTheDocument();
    expect(within(cardB).getByText("≈ +₩184,000")).toBeInTheDocument();
    expect(within(cardB).getByText("2종목")).toBeInTheDocument();
  });

  it("links each card to its operating screen and the curve to the comparison", () => {
    render(<StrategyCards snapshot={snapshot}/>);
    expect(screen.getByRole("link", { name: "전략 A 보기" })).toHaveAttribute("href", "/trading");
    expect(screen.getByRole("link", { name: "전략 B 보기" })).toHaveAttribute("href", "/trading-b");
    expect(source("app/dashboard/page.tsx")).toContain('href="/strategy-compare" className="btn-action-secondary-compact"');
  });

  it("marks the demo state once, in the header, while both strategies are mock", () => {
    render(<StrategyCards snapshot={snapshot}/>);
    // Both sides are mock, so no card carries its own badge; the header says it once.
    expect(screen.queryByText("MOCK")).not.toBeInTheDocument();
    const page = source("app/dashboard/page.tsx");
    expect(page).toContain('<MockBadge label="DEMO DATA"/>');
    expect(page).toContain("전략 성과 수치는 화면 검토용 예시입니다.");
    expect(DASHBOARD_MOCK).toBe(true);
  });

  it("marks only the mock side once the two strategies differ", () => {
    render(<StrategyCards snapshot={{ ...snapshot, a: { ...snapshot.a, source: "LIVE" } }}/>);
    const cardB = screen.getByRole("region", { name: /STRATEGY B/ });
    expect(within(cardB).getByText("MOCK")).toBeInTheDocument();
    const cardA = screen.getByRole("region", { name: /STRATEGY A/ });
    expect(within(cardA).queryByText("MOCK")).not.toBeInTheDocument();
  });

  it("summarises today for both strategies side by side", () => {
    render(<TodaySummary snapshot={snapshot}/>);
    ["오늘 손익", "보유 포지션", "오늘 완료 거래"].forEach(label => expect(screen.getByText(label)).toBeInTheDocument());
    const trades = screen.getByText("오늘 완료 거래").closest<HTMLElement>("[data-today-row]")!;
    expect(within(trades).getByText("2건")).toBeInTheDocument();
    expect(within(trades).getByText(`${mockTrades.length}건`)).toBeInTheDocument();
  });

  it("lists recent events with time, strategy and symbol, capped at six", () => {
    render(<RecentActivity events={snapshot.events}/>);
    const items = screen.getAllByRole("listitem");
    expect(items.length).toBe(snapshot.events.length);
    expect(snapshot.events.length).toBeLessThanOrEqual(6);
    expect(within(items[0]).getByText(snapshot.events[0].symbol)).toBeInTheDocument();
  });

  it("never declares a winner between the two strategies", () => {
    const { container } = render(<StrategyCards snapshot={snapshot}/>);
    ["Winner", "우승", "우월", "더 좋", "추천"].forEach(word => expect(container.textContent).not.toContain(word));
    expect(source("components/dashboard-summary.tsx")).not.toContain("Winner");
  });

  it("keeps the dashboard a summary and leaves detailed statistics on the comparison screen", () => {
    const page = source("app/dashboard/page.tsx") + source("components/dashboard-summary.tsx");
    ["Profit Factor", "기대값", "최대 낙폭", "평균 R"].forEach(metric => expect(page).not.toContain(metric));
  });
});

describe("Strategy information architecture", () => {
  it("names the first strategy tab Strategy A and keeps its route", () => {
    expect(strategyTabs.map(tab => tab.label)).toEqual(["전략 A · 기존 전략", "전략 B · 실시간 모멘텀", "전략 A/B 비교"]);
    expect(strategyTabs.map(tab => tab.href)).toEqual(["/shadow", "/strategy-b", "/strategy-compare"]);
  });

  it("sends the dashboard cards to the operating screens, not the result screens", () => {
    expect(snapshot.a.href).toBe("/trading");
    expect(snapshot.b.href).toBe("/trading-b");
  });

  it("renames the sidebar group to 전략 and adds the dashboard entry", () => {
    expect(navigationItems.map(item => item.label)).toEqual(["대시보드", "트레이딩", "종목 분석", "전략", "시스템"]);
    const dashboard = navigationItems[0];
    expect(dashboard.href).toBe("/dashboard");
    expect(dashboard.activePaths).toEqual(["/dashboard"]);
  });
});
