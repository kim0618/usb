import React from "react";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { DailyPnl } from "./daily-pnl";
import type { DailyView } from "../lib/strategies";

afterEach(() => cleanup());

const view = (over: Partial<DailyView> = {}): DailyView => ({
  currency: "USD",
  paper_clock: { paper_evaluation_version: "AE_PAPER_V1", accounting_version: "V1",
                 official_paper_start: "2026-09-28", status: "STARTED",
                 gate_contract: "AE_PAPER_EVALUATION_GATE_V1" },
  rows: [
    { session: "2026-10-02", total_pnl: "0",
      strategies: { STRATEGY_A: { pnl: "0", equity: "7509.40", trades: 0 },
                    STRATEGY_E_MAX_V1: { pnl: "0", equity: "9787.61", trades: 0 } },
      trades: [] },
    { session: "2026-10-01", total_pnl: "-106.36",
      strategies: { STRATEGY_A: { pnl: "0", equity: "7509.40", trades: 0 },
                    STRATEGY_E_MAX_V1: { pnl: "-106.36", equity: "9787.61", trades: 1 } },
      trades: [{ strategy_id: "STRATEGY_E_MAX_V1", symbol: "MGM", entry_at: "2026-10-01T13:30:00Z",
                 entry_price: "33.93", exit_at: "2026-10-01T13:35:00Z", exit_price: "33.90",
                 qty: "197", net_pnl: "-106.36", costs: "16.69", exit_reason: "ON_TIME_0935_OPEN",
                 holding_seconds: 300, accounting_version: "V1", evaluation: "OFFICIAL" }] },
    // A reported nothing that session: "-" and not a zero
    { session: "2026-09-21", total_pnl: "72.36",
      strategies: { STRATEGY_A: { pnl: "72.36", equity: "7509.40", trades: 3 } },
      trades: [{ strategy_id: "STRATEGY_A", symbol: "INTC", entry_at: "2026-09-21T14:15:00Z",
                 entry_price: "31.42", exit_at: "2026-09-21T15:40:00Z", exit_price: "31.70",
                 qty: "100", net_pnl: "27.01", costs: "4.41", exit_reason: "TRAILING_STOP",
                 holding_seconds: 5100, accounting_version: "V0", evaluation: "LEGACY" }] },
  ],
  strategies: [
    { strategy_id: "STRATEGY_A", display_name: "Strategy A", short_name: "A", has_daily_pnl: true },
    { strategy_id: "STRATEGY_E_MAX_V1", display_name: "Strategy E", short_name: "E", has_daily_pnl: true },
    { strategy_id: "STRATEGY_H_V2", display_name: "Strategy H", short_name: "H", has_daily_pnl: false,
      reason: "H는 자본 장부가 없다. forward shadow는 결정을 관찰한다",
      decision_counts: { APPROVE: 0, WATCH: 6, REJECT: 2 },
      launch: { status: "LAUNCHED", launched_at: null, baseline_session: "2026-10-02",
                decision_session: "2026-09-16", issuers: 8 } },
  ],
  excluded_before_start: { STRATEGY_A: { sessions: 2, last_session: "2026-09-22" } },
  note: "일별 손익은 각 전략의 자체 장부에서 읽은 그대로이고, 합계는 단순 합입니다",
  ...over,
});

describe("daily pnl", () => {
  it("gives a column to each strategy that has a daily figure, and a total", () => {
    render(<DailyPnl view={view()}/>);
    const columns = Array.from(document.querySelectorAll("th[data-column]"))
      .map(n => (n as HTMLElement).dataset.column);
    expect(columns).toEqual(["STRATEGY_A", "STRATEGY_E_MAX_V1"]);
    expect(screen.getByRole("columnheader", { name: "합계" })).toBeTruthy();
  });

  it("lists sessions newest first with each strategy's own figure", () => {
    render(<DailyPnl view={view()}/>);
    const row = document.querySelector("[data-daily-row='2026-10-01']") as HTMLElement;
    expect(row.querySelector("[data-cell=STRATEGY_E_MAX_V1]")!.textContent).toContain("106.36");
    expect(row.querySelector("[data-cell=TOTAL]")!.textContent).toContain("106.36");
  });

  it("shows a session a strategy did not report as '-', never as a zero", () => {
    render(<DailyPnl view={view()}/>);
    const row = document.querySelector("[data-daily-row='2026-09-21']") as HTMLElement;
    const a = row.querySelector("[data-cell=STRATEGY_A]")!;
    const e = row.querySelector("[data-cell=STRATEGY_E_MAX_V1]")!;
    expect(a.textContent).toContain("72.36");
    expect(e.textContent!.trim()).toBe("-");        // no row that day, not a flat day
  });

  it("opens a session to that day's closed trades and closes it again", () => {
    render(<DailyPnl view={view()}/>);
    expect(document.querySelector("[data-daily-detail]")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /2026-10-01/ }));
    const detail = document.querySelector("[data-daily-detail='2026-10-01']") as HTMLElement;
    expect(within(detail).getByText("MGM")).toBeTruthy();
    expect(detail.textContent).toContain("ON_TIME_0935_OPEN");
    fireEvent.click(screen.getByRole("button", { name: /2026-10-01/ }));
    expect(document.querySelector("[data-daily-detail]")).toBeNull();
  });

  it("does not offer to open a session that had no trade", () => {
    render(<DailyPnl view={view()}/>);
    expect(screen.getByRole("button", { name: /2026-10-02/ })).toBeDisabled();
  });

  it("gives Strategy H no column, because a zero column would say it broke even every day", () => {
    render(<DailyPnl view={view()}/>);
    const columns = Array.from(document.querySelectorAll("th[data-column]"))
      .map(n => (n as HTMLElement).dataset.column);
    expect(columns).not.toContain("STRATEGY_H_V2");
    // H's own record lives under the strategy picker, not as a banner on this table.
    expect(document.body.textContent).not.toContain("WATCH 6");
  });

  it("says so when no session has been recorded", () => {
    render(<DailyPnl view={view({ rows: [] })}/>);
    expect(screen.getByText("기록된 세션이 없습니다.")).toBeTruthy();
  });

  it("explains a flat strategy whose own sessions predate the official clock", () => {
    render(<DailyPnl view={view()}/>);
    const note = document.querySelector("[data-excluded-before-start]") as HTMLElement;
    expect(note.textContent).toContain("A는 공식 시작 전에 움직인 세션이 2일");
    expect(note.textContent).toContain("2026-09-22");
    expect(note.textContent).toContain("회계 방식이 달라");
  });

  it("says nothing about excluded sessions when there are none", () => {
    render(<DailyPnl view={view({ excluded_before_start: {} })}/>);
    expect(document.querySelector("[data-excluded-before-start]")).toBeNull();
  });
});
