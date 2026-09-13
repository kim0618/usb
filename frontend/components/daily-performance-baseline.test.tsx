import React from "react";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { DailyPerformanceTable, PreviousSessionPerformance } from "@/components/daily-performance";
import type { DailyPerformance } from "@/types/api";

const row = (trading_date: string, scope: DailyPerformance["performance_scope"], strategy: string | null, closing = "7428.92"): DailyPerformance => ({
  trading_date, opening_equity: "7428.92", closing_equity: closing, cash: closing, position_market_value: "0",
  daily_pnl: "0", daily_return: "0", cumulative_pnl: "0", performance_scope: scope, strategy_cumulative_pnl: strategy,
});

describe("post-fix strategy performance baseline", () => {
  afterEach(cleanup);

  it("keeps pre-fix rows as history but excludes them from strategy cumulative PnL", () => {
    render(<DailyPerformanceTable validFrom="2026-09-14" rows={[
      row("2026-09-15", "STRATEGY", "40.00", "7468.92"), row("2026-09-14", "STRATEGY", "12.50", "7441.42"),
      row("2026-09-11", "SYSTEM_VALIDATION_PRE_FIX", null), row("2026-09-08", "SYSTEM_VALIDATION_PRE_FIX", null),
    ]}/>);
    expect(screen.getByText(/정상 전략 성과 집계 시작: 2026-09-14/)).toBeInTheDocument();
    expect(screen.getAllByText("시스템 검증 기간 — 전략 성과 제외")).toHaveLength(2);
    ["09/15", "09/14", "09/11", "09/08"].forEach(day => expect(screen.getByText(day)).toBeInTheDocument());
    expect(screen.getByText("+$40.00")).toBeInTheDocument();
  });

  it("marks the previous-session card when it is a pre-fix validation day", () => {
    render(<PreviousSessionPerformance row={row("2026-09-11", "SYSTEM_VALIDATION_PRE_FIX", null)}/>);
    expect(screen.getByText(/시스템 검증 기간 \(성과 제외\)/)).toBeInTheDocument();
  });

  it("falls back to the legacy cumulative value when the API predates the scope", () => {
    const legacy = { ...row("2026-09-01", undefined, null), cumulative_pnl: "5.00" };
    render(<DailyPerformanceTable rows={[legacy]}/>);
    expect(screen.queryByText(/정상 전략 성과 집계 시작/)).toBeNull();
    expect(screen.getByText("+$5.00")).toBeInTheDocument();
  });

  it("tells the strategy comparison screen which validation period is excluded", async () => {
    const { readFileSync } = await import("node:fs");
    const page = readFileSync("app/shadow/page.tsx", "utf8");
    expect(page).toContain("summary.excluded_periods?.map");
    expect(page).toContain("전략 성과 집계에서 제외됩니다.");
  });
});
