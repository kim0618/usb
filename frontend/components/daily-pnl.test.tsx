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
    { strategy_id: "STRATEGY_A", display_name: "Strategy A", short_name: "A" },
    { strategy_id: "STRATEGY_E_MAX_V1", display_name: "Strategy E", short_name: "E" },
    { strategy_id: "STRATEGY_H_V2", display_name: "Strategy H", short_name: "H" },
  ],
  ...over,
});

describe("daily pnl", () => {
  it("gives every operating strategy a column, booked or not, plus a total", () => {
    render(<DailyPnl view={view()}/>);
    const columns = Array.from(document.querySelectorAll("th[data-column]"))
      .map(n => (n as HTMLElement).dataset.column);
    // H has booked nothing yet and still gets a column: the table's shape must not depend on
    // when a strategy happens to make its first trade.
    expect(columns).toEqual(["STRATEGY_A", "STRATEGY_E_MAX_V1", "STRATEGY_H_V2"]);
    expect(screen.getByRole("columnheader", { name: "합계" })).toBeTruthy();
  });

  it("shows a strategy with no record that session as '-', on every row", () => {
    render(<DailyPnl view={view()}/>);
    const cells = Array.from(document.querySelectorAll("[data-cell=STRATEGY_H_V2]"))
      .map(n => n.textContent!.trim());
    expect(cells).toHaveLength(view().rows.length);
    expect(cells.every(c => c === "-")).toBe(true);   // never $0.00
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

  it("carries no explanatory prose: the table is the whole statement", () => {
    render(<DailyPnl view={view()}/>);
    expect(document.querySelector("[data-excluded-before-start]")).toBeNull();
    expect(document.body.textContent).not.toContain("단순 합");
    expect(document.body.textContent).not.toContain("회계 방식이 달라");
    expect(document.body.textContent).not.toContain("WATCH 6");
  });

  it("says so when no session has been recorded", () => {
    render(<DailyPnl view={view({ rows: [] })}/>);
    expect(screen.getByText("기록된 세션이 없습니다.")).toBeTruthy();
  });
});
