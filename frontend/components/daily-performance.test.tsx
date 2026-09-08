import React from "react";
import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { DailyPerformanceTable, PreviousSessionPerformance } from "./daily-performance";
import type { DailyPerformance } from "@/types/api";

const row = (trading_date: string, daily_pnl: string, daily_return: string, closing_equity: string, cumulative_pnl = daily_pnl): DailyPerformance => ({
  trading_date, daily_pnl, daily_return, closing_equity, cumulative_pnl,
  opening_equity: "7428.92", cash: closing_equity, position_market_value: "0",
});

describe("actual daily performance table", () => {
  afterEach(cleanup);

  it("colors positive, negative, and zero PnL and returns semantically", () => {
    render(<DailyPerformanceTable rows={[
      row("2026-09-10", "10", "0.01", "7438.92"),
      row("2026-09-09", "-5", "-0.005", "7428.92"),
      row("2026-09-08", "0", "0", "7428.92"),
    ]}/>);
    const rows = screen.getAllByRole("row").slice(1);
    expect(within(rows[0]).getAllByText("+$10.00")[0].closest("td")).toHaveClass("text-success");
    expect(within(rows[0]).getByText("+1.00%")).toHaveClass("text-success");
    expect(within(rows[1]).getAllByText("-$5.00")[0].closest("td")).toHaveClass("text-danger");
    expect(within(rows[1]).getByText("-0.50%")).toHaveClass("text-danger");
    expect(within(rows[2]).getAllByText("+$0.00")[0].closest("td")).toHaveClass("text-foreground-secondary");
    expect(within(rows[2]).getByText("+0.00%")).toHaveClass("text-foreground-secondary");
  });

  it("shows fixed-rate KRW beneath USD while closing equity stays neutral", () => {
    render(<DailyPerformanceTable rows={[row("2026-09-08", "7428.92", "1", "7428.92")]}/>);
    expect(screen.getAllByText("+₩10,000,000").length).toBeGreaterThan(0);
    expect(screen.getByText("$7,428.92").closest("td")).toHaveClass("text-foreground");
    expect(screen.getByText("₩10,000,000")).toHaveClass("text-muted");
  });

  it("preserves API recent-first order without frontend sorting", () => {
    render(<DailyPerformanceTable rows={[row("2026-09-10", "1", "0", "1"), row("2026-09-08", "1", "0", "1")]}/>);
    expect(screen.getAllByRole("row").slice(1).map(item => within(item).getAllByRole("cell")[0].textContent)).toEqual(["09/10", "09/08"]);
  });

  it("renders the normal empty state", () => {
    render(<DailyPerformanceTable rows={[]}/>);
    expect(screen.getByText("아직 일별 손익 기록이 없습니다.")).toBeInTheDocument();
  });

  it("summarizes the exact latest API row with date, return, and fixed-rate KRW", () => {
    const rows = [row("2026-09-08", "82.10", "0.011", "7511.02")];
    render(<><PreviousSessionPerformance row={rows[0]}/><DailyPerformanceTable rows={rows}/></>);
    expect(screen.getAllByText("09/08")).toHaveLength(2);
    expect(screen.getAllByText("+$82.10")[0]).toHaveClass("text-success");
    expect(screen.getAllByText("+₩110,514")).toHaveLength(3);
    expect(screen.getAllByText("+1.10%")[0]).toHaveClass("text-success");
    expect(screen.getAllByText("+$82.10")).toHaveLength(3);
  });

  it.each([
    ["-5", "-0.005", "text-danger"],
    ["0", "0", "text-foreground-secondary"],
  ])("uses %s semantic tone in the previous-session card", (dailyPnl, dailyReturn, tone) => {
    render(<PreviousSessionPerformance row={row("2026-09-05", dailyPnl, dailyReturn, "7428.92")}/>);
    expect(screen.getByText(dailyPnl === "0" ? "+$0.00" : "-$5.00")).toHaveClass(tone);
  });

  it("shows a dash when no completed snapshot exists", () => {
    render(<PreviousSessionPerformance row={null}/>);
    expect(screen.getByText("-")).toHaveClass("text-foreground-secondary");
  });

  it("does not reset a Friday snapshot based on a later calendar date", () => {
    render(<PreviousSessionPerformance row={row("2026-09-04", "10", "0.001", "7438.92")}/>);
    expect(screen.getByText("09/04")).toBeInTheDocument();
    expect(screen.getByText("+$10.00")).toBeInTheDocument();
  });
});
