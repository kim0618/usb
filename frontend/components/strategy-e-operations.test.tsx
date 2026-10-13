import React from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { afterEach, describe, expect, it } from "vitest";
import { DailyPnl, Holdings, TodayDecision } from "./strategy-e-operations";
import { decisionOf, reasonLabel } from "../lib/strategy-e-decision";
import type { StrategyAccount, StrategyStatus } from "../lib/strategies";

afterEach(cleanup);

const status = (over: Record<string, unknown> = {}, detail: Record<string, unknown> = {}): StrategyStatus => ({
  strategy_id: "STRATEGY_E_MAX_V1", display_name: "Strategy E", version: "x", enabled: true, mode: "SIMULATION_VIRTUAL_ONLY",
  market_data_source: "KIWOOM", runtime_status: "COMPLETE", paper_status: null, evidence_status: null, session: null,
  available: true, detail, ...over,
} as StrategyStatus);
const account = (over: Partial<StrategyAccount> = {}): StrategyAccount => ({
  strategy_id: "STRATEGY_E_MAX_V1", available: true, currency: "USD", initial_equity: "10000", current_equity: "10000",
  today_pnl: null, total_pnl: null, open_positions: 0, closed_trades_today: 0, source: "E_RUNTIME_FILES", ...over,
});

describe("Strategy E decision wording", () => {
  it("names the entry condition from the existing H5 counts", () => {
    expect(decisionOf(status({}, { h5_true: 2, selected: ["NVDA", "AMD"] }), account()).condition).toBe("충족");
    expect(decisionOf(status({}, { h5_true: 0, h5_unknown: 3, h5_false: 5 }), account()).condition).toBe("판단 불가");
    expect(decisionOf(status({}, { h5_true: 0, h5_unknown: 0, h5_false: 5, canonical_universe: 5 }), account()).condition).toBe("미충족");
    const waiting = decisionOf(status({ runtime_status: "NO_SESSION_YET" }), account());
    expect(waiting).toMatchObject({ condition: "판단 불가", reason: "세션 대기" });
  });

  it("translates internal enums and never prints a raw one", () => {
    expect(reasonLabel("UNIVERSE_NOT_AVAILABLE")).toBe("대상 종목 데이터 없음");
    expect(reasonLabel("SOME_NEW_ENUM_VALUE")).toBe("상세 확인 필요");
    expect(reasonLabel(null)).toBeNull();
    const bad = decisionOf(status({}, { universe: { available: false, status: "UNIVERSE_NOT_AVAILABLE" } }), account());
    expect(bad.reason).toBe("대상 종목 데이터 없음");
  });

  it("says whether it entered", () => {
    expect(decisionOf(status({}, { selected: ["NVDA"] }), account({ open_positions: 1 })).entry).toBe("진입함");
    expect(decisionOf(status({}, { selected: [] }), account()).entry).toBe("진입 없음");
    expect(decisionOf(status({ runtime_status: "DECIDED" }, { selected: ["NVDA"] }), account()).entry).toBe("진입 대기");
  });
});

describe("Strategy E operating blocks", () => {
  it("renders one decision block in Korean with KST time and no raw state", () => {
    const { container } = render(<TodayDecision status={status({}, { h5_true: 1, selected: ["NVDA"], rvol_threshold: 3 })}
      account={account({ open_positions: 1 })} decidedAt="2026-10-13T13:25:00+00:00"/>);
    const text = container.textContent!;
    expect(text).toContain("충족");
    expect(text).toContain("NVDA");
    expect(text).toContain("10/13 22:25 KST");
    for (const raw of ["RVOL", "H5", "RUNTIME", "EVIDENCE", "CANONICAL", "digest"]) expect(text).not.toContain(raw);
  });

  it("uses the plain empty sentences", () => {
    render(<><Holdings positions={[]}/><DailyPnl daily={null} trades={[]}/></>);
    expect(screen.getByText("보유 중인 포지션이 없습니다.")).toBeInTheDocument();
    expect(screen.getByText("아직 일별 손익 기록이 없습니다.")).toBeInTheDocument();
  });

  it("lists daily PnL in USD with KRW and opens the trade list on demand", () => {
    const daily = { currency: "USD", paper_clock: {} as never, strategies: [], rows: [
      { session: "2026-10-12", total_pnl: "-52.11", trades: [], strategies: { STRATEGY_E_MAX_V1: { pnl: "-52.11", equity: "9947.89", trades: 1 } } }] };
    const { container } = render(<DailyPnl daily={daily} trades={[]}/>);
    expect(container.textContent).toContain("-$52.11");
    expect(container.textContent).toContain("$9,947.89");
    expect(screen.queryByText("청산된 거래 없음")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "거래 내역 보기" }));
    expect(screen.getByText("청산된 거래 없음")).toBeInTheDocument();
  });

  it("drops the research cards, universe panel, books and curve from the E screen", () => {
    const page = readFileSync("app/strategy-e/page.tsx", "utf8");
    for (const gone of ["StrategyEStatus", "BootstrapProgress", "UniversePanel", "EvidenceBooks", "EquitySparkline"]) {
      expect(page).not.toContain(gone);
    }
    const order = ["<AccountSummary", "<Holdings", "<TodayDecision", "<DailyPnl"].map(m => page.indexOf(m));
    expect(order).toEqual([...order].sort((a, b) => a - b));
    expect(order.every(i => i > 0)).toBe(true);
  });
});
