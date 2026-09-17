import React from "react";
import { readFileSync } from "node:fs";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { StrategyBTradingOverview } from "./strategy-b-overview";
import { StrategyBPositions, StrategyBTrades } from "./strategy-b-portfolio";
import { StrategyBPerformance, StrategyBSetupPerformance } from "./strategy-b-performance";
import { StrategyBScanner } from "./strategy-b-scanner";
import { MockBadge } from "./mock-badge";
import { mockCandidates, mockPositions, mockSummary } from "../mocks/strategy-b";
import { mockPerformance, mockTrades } from "../mocks/strategy-b-trades";
import { STRATEGY_B_MOCK, strategyBSource } from "../lib/strategy-b-source";
import {
  EMPTY_SCANNER_FILTER, SETUP_META, derivedR, derivedReturnPct, holdingTime, lifecycleSteps,
  setupBreakdown, signedPct, signedR, strategyAccount, visibleCandidates,
} from "../lib/strategy-b";

const source = (path: string) => readFileSync(path, "utf8");

afterEach(cleanup);

describe("Strategy B mock data integrity", () => {
  it("keeps every position consistent with its own entry, price and stop", () => {
    mockPositions.forEach(position => {
      expect(derivedReturnPct(position.entry_usd, position.current_usd)).toBeCloseTo(position.pnl_pct, 1);
      expect(derivedR(position.entry_usd, position.current_usd, position.stop_usd)).toBeCloseTo(position.r_multiple, 1);
    });
  });

  it("counts open positions and live candidates from the rows the screen shows", () => {
    const entered = mockCandidates.filter(candidate => candidate.state === "ENTERED").map(candidate => candidate.symbol);
    expect(entered).toEqual(mockPositions.map(position => position.symbol));
    expect(mockSummary.open_positions).toBe(mockPositions.length);
    expect(mockSummary.candidates).toBe(mockCandidates.filter(candidate => candidate.state !== "REJECTED" && candidate.state !== "EXPIRED").length);
    expect(mockSummary.candidates).toBe(7);
  });

  it("prices an open position at the scanner price of the same symbol", () => {
    mockPositions.forEach(position => {
      const row = mockCandidates.find(candidate => candidate.symbol === position.symbol);
      expect(row?.price_usd).toBe(position.current_usd);
    });
  });

  it("adds the day's realized and unrealized results up to the reported today P&L", () => {
    const realized = mockTrades.reduce((total, trade) => total + trade.pnl_krw, 0);
    const unrealized = mockPositions.reduce((total, position) => total + position.pnl_krw, 0);
    expect(realized + unrealized).toBe(mockSummary.today_pnl_krw);
  });

  it("shows losing trades, not only winners", () => {
    expect(mockTrades.some(trade => trade.result === "WIN")).toBe(true);
    expect(mockTrades.some(trade => trade.result === "LOSS")).toBe(true);
    mockTrades.forEach(trade => {
      expect(trade.result === "WIN" ? trade.pnl_krw > 0 : trade.pnl_krw < 0).toBe(true);
      expect(Math.sign(trade.r_multiple)).toBe(Math.sign(trade.pnl_krw));
    });
  });

  it("covers every scanner state so each badge is reachable on screen", () => {
    expect(new Set(mockCandidates.map(candidate => candidate.state)).size).toBe(8);
  });

  it("serves the mock through the swappable source, never from a component import", async () => {
    expect(STRATEGY_B_MOCK).toBe(true);
    await expect(strategyBSource.summary()).resolves.toEqual(mockSummary);
    await expect(strategyBSource.candidates()).resolves.toHaveLength(mockCandidates.length);
    ["strategy-b-overview", "strategy-b-scanner", "strategy-b-portfolio", "strategy-b-performance", "strategy-compare", "equity-curve"]
      .forEach(name => expect(source(`components/${name}.tsx`)).not.toContain("@/mocks/"));
  });
});

describe("Strategy B scanner behaviour", () => {
  const scanner = () => render(<StrategyBScanner candidates={mockCandidates}/>);

  it("sorts by score descending by default and re-sorts on a column click", () => {
    scanner();
    const scores = screen.getAllByRole("row").slice(1).map(row => Number(row.querySelectorAll("td")[8].textContent));
    expect(scores).toEqual([...scores].sort((left, right) => right - left));
    fireEvent.click(within(screen.getByRole("table")).getByRole("button", { name: /RVOL/ }));
    const rvols = screen.getAllByRole("row").slice(1).map(row => Number(row.querySelectorAll("td")[5].textContent));
    expect(rvols).toEqual([...rvols].sort((left, right) => right - left));
  });

  it("filters by symbol search, state and setup without touching the source rows", () => {
    scanner();
    fireEvent.change(screen.getByLabelText("종목 검색"), { target: { value: "nv" } });
    expect(screen.getAllByRole("row")).toHaveLength(2);
    expect(screen.getByText("NVTS")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("종목 검색"), { target: { value: "" } });
    fireEvent.change(screen.getByLabelText("상태 필터"), { target: { value: "ENTERED" } });
    expect(screen.getAllByRole("row")).toHaveLength(3);
    expect(mockCandidates).toHaveLength(9);
  });

  it("states an empty result instead of an empty table", () => {
    scanner();
    fireEvent.change(screen.getByLabelText("종목 검색"), { target: { value: "ZZZZ" } });
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
    expect(screen.getByText("조건에 맞는 후보가 없습니다.")).toBeInTheDocument();
  });

  it("opens a detail drawer with the lifecycle of the selected candidate", () => {
    scanner();
    fireEvent.click(screen.getByRole("button", { name: /^NVTS/ }));
    const drawer = screen.getByRole("dialog");
    expect(within(drawer).getAllByRole("heading", { name: /NVTS/ }).length).toBeGreaterThan(0);
    expect(within(drawer).getByText("VWAP 이격")).toBeInTheDocument();
    expect(within(drawer).getAllByText("진입 완료").length).toBeGreaterThan(0);
  });

  it("marks a lifecycle step pending when the scanner recorded no timestamp", () => {
    const watching = mockCandidates.find(candidate => candidate.state === "WATCHING")!;
    const steps = lifecycleSteps(watching);
    expect(steps.map(step => step.state)).toEqual(["DETECTED", "QUALIFIED", "WATCHING", "SETUP_READY", "ENTRY_SIGNALLED", "ENTERED"]);
    expect(steps.filter(step => step.reached)).toHaveLength(3);
  });

  it("keeps the scanner order stable for equal values", () => {
    const rows = [
      { ...mockCandidates[0], symbol: "AAA", score: 70 },
      { ...mockCandidates[1], symbol: "BBB", score: 70 },
    ];
    const sorted = visibleCandidates(rows, EMPTY_SCANNER_FILTER, { key: "score", direction: "desc" });
    expect(sorted.map(row => row.symbol)).toEqual(["AAA", "BBB"]);
  });
});

describe("Strategy B screen sections", () => {
  it("shows the trading account the way Strategy A's screen does, without period statistics", () => {
    const account = strategyAccount(mockSummary.current_equity_krw, mockPositions, mockTrades);
    render(<StrategyBTradingOverview summary={mockSummary} account={account}/>);
    ["현재 자산", "투자 중", "보유 현금", "평가 손익", "오늘 실현 손익"].forEach(label => expect(screen.getByText(label)).toBeInTheDocument());
    expect(screen.getByText("$7,980.15")).toBeInTheDocument();
    expect(screen.getByText("≈ ₩10,742,000")).toBeInTheDocument();
    expect(screen.getByText("+$91.97")).toBeInTheDocument();
    expect(screen.getByText("≈ +₩123,800")).toBeInTheDocument();
    expect(screen.getByText("+$44.72")).toBeInTheDocument();
    expect(screen.getByText("≈ +₩60,200")).toBeInTheDocument();
    expect(screen.getByText("가동 중")).toBeInTheDocument();
    expect(screen.queryByText("총 수익률")).not.toBeInTheDocument();
  });

  it("derives invested, cash and today's two P&L halves from the rows the screen lists", () => {
    const account = strategyAccount(mockSummary.current_equity_krw, mockPositions, mockTrades);
    expect(account.invested_krw).toBe(mockPositions.reduce((total, position) => total + position.market_value_krw, 0));
    expect(account.cash_krw).toBe(mockSummary.current_equity_krw - account.invested_krw);
    expect(account.unrealized_pnl_krw).toBe(123_800);
    expect(account.realized_pnl_krw).toBe(60_200);
    expect(account.realized_pnl_krw + account.unrealized_pnl_krw).toBe(mockSummary.today_pnl_krw);
    mockPositions.forEach(position => {
      expect(position.market_value_krw - position.cost_basis_krw).toBe(position.pnl_krw);
      expect((position.pnl_krw / position.cost_basis_krw) * 100).toBeCloseTo(position.pnl_pct, 1);
    });
  });

  it("renders positions with recomputed return and R", () => {
    render(<StrategyBPositions positions={mockPositions}/>);
    const row = screen.getByText("NVTS").closest("tr")!;
    expect(within(row).getByText("+7.72%")).toBeInTheDocument();
    expect(within(row).getByText("+1.41R")).toBeInTheDocument();
    expect(within(row).getByText("18분")).toBeInTheDocument();
  });

  it("renders closed trades with exit reasons and both outcomes", () => {
    render(<StrategyBTrades trades={mockTrades}/>);
    expect(screen.getAllByText("손절 청산")).toHaveLength(2);
    expect(screen.getByText("분할 익절 + 트레일")).toBeInTheDocument();
    expect(screen.getAllByText("이익")).toHaveLength(3);
    expect(screen.getAllByText("손실")).toHaveLength(3);
    expect(screen.getByText(/3승 3패/)).toBeInTheDocument();
  });

  it("renders the ten performance metrics", () => {
    render(<StrategyBPerformance performance={mockPerformance}/>);
    ["총 수익률", "순손익", "매매 횟수", "승률", "Profit Factor", "기대값", "최대 낙폭", "평균 R", "평균 이익", "평균 손실"]
      .forEach(label => expect(screen.getAllByText(new RegExp(`^${label}`)).length).toBeGreaterThan(0));
    expect(screen.getByText("1.79")).toBeInTheDocument();
    expect(screen.getByText("-6.2%")).toBeInTheDocument();
  });

  it("marks both Strategy B screens with one small badge, not a warning bar", () => {
    render(<MockBadge/>);
    expect(screen.getByText("MOCK")).toBeInTheDocument();
    ["app/strategy-b/page.tsx", "app/trading-b/page.tsx"].forEach(path => {
      const page = source(path);
      expect(page).toContain("STRATEGY_B_MOCK ? <MockBadge/>");
      expect(page).toContain("strategyBSource");
      expect(page).not.toContain("@/lib/api");
      expect(page).not.toContain("eyebrow=");
    });
  });

  it("formats percent, R and holding time the way the desk reads them", () => {
    expect(signedPct(7.42)).toBe("+7.42%");
    expect(signedPct(-6.2, 1)).toBe("-6.2%");
    expect(signedR(1.41)).toBe("+1.41R");
    expect(signedR(-0.7)).toBe("-0.70R");
    expect(holdingTime(18)).toBe("18분");
    expect(holdingTime(91)).toBe("1시간 31분");
    expect(holdingTime(120)).toBe("2시간");
  });

  it("keeps Strategy B inside the trading and strategy groups, never as a new top-level menu", async () => {
    const { navigationItems } = await import("./app-shell");
    expect(navigationItems).toHaveLength(5);
    expect(navigationItems.find(item => item.label === "트레이딩")!.activePaths).toEqual(["/trading", "/trading-b"]);
    expect(navigationItems.find(item => item.label === "전략")!.activePaths).toEqual(["/shadow", "/strategy-b", "/strategy-compare"]);
  });

  it("never reaches the Backend, Kiwoom, a socket, or an order path", () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    render(<StrategyBScanner candidates={mockCandidates}/>);
    expect(fetchSpy).not.toHaveBeenCalled();
    fetchSpy.mockRestore();
    const files = ["app/strategy-b/page.tsx", "app/trading-b/page.tsx", "app/strategy-compare/page.tsx",
      "components/strategy-b-scanner.tsx", "components/strategy-b-portfolio.tsx",
      "components/strategy-b-performance.tsx", "components/strategy-compare.tsx"];
    files.map(source).forEach(text => {
      ["new WebSocket(", "EventSource(", "apiFetch", "setInterval(", "api.orders", "api.trading"].forEach(forbidden => expect(text).not.toContain(forbidden));
    });
    // The source module names the future endpoints in a comment only; it imports no client.
    expect(source("lib/strategy-b-source.ts")).not.toContain('from "@/lib/api"');
  });
});
