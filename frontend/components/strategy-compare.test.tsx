import React from "react";
import { readFileSync } from "node:fs";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { EquityCurve } from "./equity-curve";
import { StrategyCompareView } from "./strategy-compare";
import { mockComparison } from "../mocks/strategy-comparison";
import { strategyCompareSource } from "../lib/strategy-b-source";
import {
  axisTicks, comparisonMetrics, compactEquityUsd, dailyRows, equityRange, linePath, maxDrawdownPct, seriesPoints,
} from "../lib/strategy-compare";

const source = (path: string) => readFileSync(path, "utf8");
const view = () => render(<StrategyCompareView comparison={mockComparison}/>);

afterEach(cleanup);

describe("Strategy comparison mock integrity", () => {
  it("ends the equity curve on the stated returns", () => {
    const last = mockComparison.equity_curve[mockComparison.equity_curve.length - 1];
    expect(last.a_equity_krw).toBe(mockComparison.a.current_equity_krw);
    expect(last.b_equity_krw).toBe(mockComparison.b.current_equity_krw);
    expect((last.a_equity_krw / mockComparison.a.initial_capital_krw - 1) * 100).toBeCloseTo(mockComparison.a.return_pct, 2);
    expect((last.b_equity_krw / mockComparison.b.initial_capital_krw - 1) * 100).toBeCloseTo(mockComparison.b.return_pct, 2);
  });

  it("bottoms out at the stated max drawdown, so the chart and the KPI agree", () => {
    const withStart = (key: "a_equity_krw" | "b_equity_krw", initial: number) =>
      [initial, ...mockComparison.equity_curve.map(point => point[key])];
    expect(maxDrawdownPct(withStart("a_equity_krw", mockComparison.a.initial_capital_krw))).toBeCloseTo(mockComparison.a.max_drawdown_pct, 1);
    expect(maxDrawdownPct(withStart("b_equity_krw", mockComparison.b.initial_capital_krw))).toBeCloseTo(mockComparison.b.max_drawdown_pct, 1);
  });

  it("keeps net P&L consistent with equity and initial capital", () => {
    expect(mockComparison.a.current_equity_krw - mockComparison.a.initial_capital_krw).toBe(mockComparison.a.net_pnl_krw);
    expect(mockComparison.b.current_equity_krw - mockComparison.b.initial_capital_krw).toBe(mockComparison.b.net_pnl_krw);
  });

  it("keeps each same-symbol return consistent with its own entry and exit", () => {
    mockComparison.same_symbols.forEach(entry => {
      [entry.a, entry.b].forEach(side => {
        expect((side.exit_usd / side.entry_usd - 1) * 100).toBeCloseTo(side.return_pct, 1);
      });
    });
  });

  it("includes symbols where both strategies lost, not only winners", () => {
    expect(mockComparison.same_symbols.some(entry => entry.a.return_pct < 0 && entry.b.return_pct < 0)).toBe(true);
  });

  it("serves the comparison through the swappable source", async () => {
    await expect(strategyCompareSource.comparison()).resolves.toEqual(mockComparison);
    expect(source("components/strategy-compare.tsx")).not.toContain("@/mocks/");
  });
});

describe("Comparison chart geometry", () => {
  const range = equityRange(mockComparison.equity_curve, 10_000_000);

  it("pads the value window so no line touches the frame", () => {
    const values = mockComparison.equity_curve.flatMap(point => [point.a_equity_krw, point.b_equity_krw]);
    expect(range.min).toBeLessThan(Math.min(...values));
    expect(range.max).toBeGreaterThan(Math.max(...values));
  });

  it("maps a higher equity to a smaller y and spans the full width", () => {
    const points = seriesPoints(mockComparison.equity_curve, "b_equity_krw", range);
    expect(points).toHaveLength(mockComparison.equity_curve.length);
    expect(points[0].x).toBe(0);
    expect(points[points.length - 1].x).toBe(1000);
    const rising = seriesPoints([
      { date: "d1", a_equity_krw: 1, b_equity_krw: 100 },
      { date: "d2", a_equity_krw: 1, b_equity_krw: 200 },
    ], "b_equity_krw", { min: 0, max: 300 });
    expect(rising[1].y).toBeLessThan(rising[0].y);
  });

  it("builds one move-then-line path and four axis ticks", () => {
    expect(linePath([{ x: 0, y: 10 }, { x: 5, y: 20 }])).toBe("M0.00 10.00 L5.00 20.00");
    expect(axisTicks({ min: 0, max: 300 })).toEqual([300, 200, 100, 0]);
    expect(compactEquityUsd(10_742_000)).toBe("$8K");
    expect(compactEquityUsd(10_000_000)).toBe("$7.4K");
  });

  it("derives daily returns from the curve itself", () => {
    const rows = dailyRows(mockComparison.equity_curve, 10_000_000, 10_000_000);
    expect(rows).toHaveLength(mockComparison.equity_curve.length);
    expect(rows[0].a_return_pct).toBeCloseTo(0.8, 2);
    expect(rows[0].b_return_pct).toBeCloseTo(1.4, 2);
    expect(rows[1].a_return_pct).toBeCloseTo(-0.3, 2);
    expect(rows[2].b_return_pct).toBeCloseTo(-0.8, 2);
    expect(rows[0].a_pnl_krw).toBe(80_000);
  });
});

describe("Comparison screen", () => {
  it("states the common period and the shared initial capital", () => {
    view();
    expect(screen.getByText("COMMON PERIOD")).toBeInTheDocument();
    expect(screen.getByText("2026.09.01 ~ 2026.09.30")).toBeInTheDocument();
  });

  it("puts both strategies in one KPI table with the ten metrics", () => {
    view();
    const rows = comparisonMetrics(mockComparison);
    expect(rows.map(row => row.label)).toEqual(["초기 자본", "현재 자산", "수익률", "순손익", "매매 횟수", "승률", "Profit Factor", "기대값", "최대 낙폭", "평균 R"]);
    const kpi = screen.getByRole("table", { name: /핵심 지표/ });
    const returnRow = within(kpi).getByText("수익률").closest("tr")!;
    expect(within(returnRow).getByText("+3.80%")).toBeInTheDocument();
    expect(within(returnRow).getByText("+7.42%")).toBeInTheDocument();
  });

  it("never declares a winner or a better strategy", () => {
    const { container } = view();
    const text = container.textContent ?? "";
    ["Winner", "우승", "더 좋", "우월", "추천", "권장"].forEach(word => expect(text).not.toContain(word));
    ["Winner", "우월한", "더 좋은 전략"].forEach(word => {
      expect(source("components/strategy-compare.tsx")).not.toContain(word);
      expect(source("lib/strategy-compare.ts")).not.toContain(word);
    });
  });

  it("draws both equity curves on one frame with a legend and a baseline", () => {
    render(<EquityCurve curve={mockComparison.equity_curve} baseline={10_000_000} labelA="Strategy A" labelB="Strategy B"/>);
    expect(screen.getByText("Strategy A")).toBeInTheDocument();
    expect(screen.getByText("Strategy B")).toBeInTheDocument();
    expect(screen.getByText("초기 자본 $7,428.92 (≈ ₩10,000,000)")).toBeInTheDocument();
    expect(screen.getByRole("img", { name: /자산 곡선/ })).toBeInTheDocument();
  });

  it("shows a date, equity and return tooltip on hover", () => {
    const { container } = render(<EquityCurve curve={mockComparison.equity_curve} baseline={10_000_000} labelA="Strategy A" labelB="Strategy B"/>);
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
    fireEvent.mouseEnter(container.querySelector('[data-curve-hit="2026-09-03"]')!);
    const tooltip = screen.getByRole("status");
    expect(within(tooltip).getByText("09/03")).toBeInTheDocument();
    expect(within(tooltip).getByText("$7,548.01")).toBeInTheDocument();
    expect(within(tooltip).getByText("₩10,160,307")).toBeInTheDocument();
    expect(within(tooltip).getByText("+1.60%")).toBeInTheDocument();
  });

  it("keeps the daily comparison behind a toggle", () => {
    view();
    expect(screen.queryByRole("table", { name: /일별/ })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "일별 비교 보기" }));
    const table = screen.getByRole("table", { name: /일별 수익률과 손익/ });
    expect(within(table).getAllByRole("row")).toHaveLength(mockComparison.equity_curve.length + 1);
  });

  it("switches the same-symbol comparison with the symbol selector", () => {
    view();
    expect(screen.getByRole("button", { name: "NVTS" })).toHaveAttribute("aria-pressed", "true");
    const first = screen.getByRole("table", { name: /NVTS 종목의 전략별 매매 비교/ });
    expect(within(first).getByText("+11.96%")).toBeInTheDocument();
    expect(within(first).getByText("+14.02%")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "RGTI" }));
    const second = screen.getByRole("table", { name: /RGTI 종목의 전략별 매매 비교/ });
    expect(within(second).getByText("-6.15%")).toBeInTheDocument();
  });

  it("marks the comparison screen with one small badge and keeps it off the Backend", () => {
    const page = source("app/strategy-compare/page.tsx");
    expect(page).toContain("STRATEGY_B_MOCK ? <MockBadge/>");
    expect(page).toContain("strategyCompareSource");
    expect(page).not.toContain("@/lib/api");
  });

  it("trims the screen chrome to a title, one line and the badge", () => {
    const page = source("app/strategy-compare/page.tsx");
    expect(page).not.toContain("eyebrow=");
    expect(page).not.toContain("STRATEGY COMPARISON");
    expect(page).not.toContain("MockDataNotice");
    expect(page).toContain('description="동일 기간 · 동일 초기 자본 기준 성과 비교"');
  });
});
