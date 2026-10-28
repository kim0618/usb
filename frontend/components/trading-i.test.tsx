import React from "react";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import TradingIPage from "../app/trading-i/page";
import { STRATEGY_I_TAB } from "../lib/strategy-i";

vi.mock("next/navigation", () => ({ usePathname: () => "/trading-i" }));
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

const registry = [{ strategy_id: "STRATEGY_E_MAX_V1", display_name: "Strategy E", variant_label: "E-MAX V1", version: "E-MAX V1", enabled: true,
  mode: "SIMULATION_PAPER", market_data_source: "KIWOOM", runtime_status: "COMPLETE", paper_status: "PAPER", evidence_status: null, session: null }];

export const RUNNING = {
  strategy_id: "STRATEGY_I_MOM12_2", display_name: "Strategy I · MOM12-2", status: "RUNNING", launched: true, orders: "NONE",
  initial_capital: "10000", target_holdings: 20, launch_signal: "2026-10-30", next_signal: "2026-11-30", reason: null,
  last_run: { as_of: "2026-11-03", completed_at: "2026-11-04T06:00:00+00:00", events: 7 },
  account: { as_of: "2026-11-03", nav: 10016.08, cash: 9016.08, positions_value: 1000, total_pnl: 16.08, total_return: 0.001608,
    day_pnl: 20, day_return: 0.002, day_session: "2026-11-03", holdings: 2, operating_sessions: 2 },
  positions: [{ ticker: "AAA", shares: 10, avg_cost: 50.2, last_close: 52, value: 520, weight: 0.0519, stale_price: false },
              { ticker: "BBB", shares: 4, avg_cost: 120.48, last_close: 120, value: 480, weight: 0.0479, stale_price: true }],
  rebalances: [{ k: 0, session: "2026-11-02", signal: "2026-10-30", holdings: 2, trade_count: 2, cash_pct: 90, nav_post: 9996.08, costs: 3.92, turnover_one_way: null }],
  trades: [{ session: "2026-11-02", k: 0, side: "BUY", ticker: "AAA", shares: 10, notional: 500, cost: 2, stale_last_close: false }],
  overlay: { virtual: true, affects_book: false, observations: 1, evaluated_count: 0,
    latest: { status: "NOT_EVALUATED", reason: "NO_FORWARD_PIT_SIC_SNAPSHOT", session: "2026-11-02", signal: "2026-10-30" } },
  flags: { continuity_unresolved: [{ type: "CONTINUITY_UNRESOLVED", session: "2026-11-02", ticker: "ZZZ", last_close_session: "2026-10-20", reason: "NO_PIT_EVIDENCE" }],
    continuity_linked: [], price_stop_held: [], stale_positions: ["BBB"], data_delay: { as_of: "2026-11-03", expected: "2026-11-03", delayed: false },
    pending_signals: [], refused_signals: [], error: null },
};

function stub(paper: unknown) {
  vi.stubGlobal("fetch", vi.fn(async (url: string) => ({ ok: true, status: 200,
    json: async () => (String(url).includes("/strategy-i/paper") ? paper : registry) }) as Response));
}

describe("Strategy I paper screen", () => {
  it("shows the state and no figure before launch", async () => {
    stub({ strategy_id: "STRATEGY_I_MOM12_2", display_name: "Strategy I · MOM12-2", status: "NOT_LAUNCHED", launched: false, orders: "NONE",
      reason: "Paper가 시작되지 않았습니다 (launch.json 없음).", target_holdings: 20 });
    render(<TradingIPage/>);
    expect(await screen.findByText("Paper가 아직 시작되지 않았습니다.")).toBeInTheDocument();
    expect(screen.queryByText("현재 NAV")).not.toBeInTheDocument();
    expect(screen.queryByText("누적 손익")).not.toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });

  it("renders the book from the API and keeps the overlay apart", async () => {
    stub(RUNNING);
    render(<TradingIPage/>);
    expect(await screen.findByText("현재 NAV")).toBeInTheDocument();
    expect(screen.getAllByText("AAA").length).toBe(2);                 // position row and trade row
    expect(screen.getByText("BBB · 가격 중단")).toBeInTheDocument();
    expect(screen.getByText("CIK 미해결 · V1 가격중단 매도")).toBeInTheDocument();
    expect(screen.getByText(/가상 관찰 · 장부와 무관/)).toBeInTheDocument();
    expect(screen.getByText(/전방 SIC 스냅샷 없음/)).toBeInTheDocument();
    expect(await screen.findByRole("link", { name: "Strategy I · MOM12-2" })).toHaveAttribute("aria-current", "page");
  });

  it("flags input blocks and errors", async () => {
    stub({ ...RUNNING, status: "INPUT_BLOCKED", reason: "grouped store not calendar-complete" });
    render(<TradingIPage/>);
    expect(await screen.findByText("입력 차단")).toBeInTheDocument();
    expect(screen.getByText("grouped store not calendar-complete").className).toContain("text-danger");
  });

  it("shows a zero-event run before the launch signal and a guard hold as normal states", async () => {
    stub({ ...RUNNING, status: "DATA_DELAYED", next_signal: "2026-10-30", account: null, positions: [], rebalances: [], trades: [],
      last_run: { as_of: "2026-10-20", completed_at: "2026-10-22T00:54:32+00:00", events: 0 },
      overlay: { virtual: true, affects_book: false, observations: 0, evaluated_count: 0, latest: null },
      flags: { ...RUNNING.flags, continuity_unresolved: [], stale_positions: [], data_delay: { as_of: "2026-10-20", expected: "2026-10-27", delayed: true } },
      guard: { latest: { decision: "RUN", at: "2026-10-28T06:52:58+00:00", guard: "V3.1", reasons: [] },
        last_skip: { decision: "SKIP", at: "2026-10-28T06:15:04+00:00", guard: "V3", reasons: ["IO_PSI_full_avg10:25.52"] } } });
    render(<TradingIPage/>);
    expect(await screen.findByText("시작 신호일(2026-10-30) 전입니다. 첫 리밸런싱 후 장부가 표시됩니다.")).toBeInTheDocument();
    expect(screen.getByText("데이터 지연: 마지막 평가 2026-10-20, 기대 2026-10-27")).toBeInTheDocument();
    const hold = document.querySelector("[data-i-guard-skip]");
    expect(hold?.textContent).toContain("실행을 보류했습니다 (호스트 부하 보호, 오류 아님): IO_PSI_full_avg10:25.52");
    expect(hold?.className).not.toContain("text-danger");
    expect(screen.queryByText("현재 NAV")).not.toBeInTheDocument();
    expect(screen.queryByText("아직 완료된 Paper 실행이 없습니다.")).not.toBeInTheDocument();
  });

  it("hides a guard skip that a completed run has superseded and keeps a non-failure reason neutral", async () => {
    stub({ ...RUNNING, reason: "참고 메모", guard: { latest: { decision: "RUN" }, last_skip: { decision: "SKIP", at: "2026-11-01T06:15:00+00:00", reasons: [] } } });
    render(<TradingIPage/>);
    expect(await screen.findByText("현재 NAV")).toBeInTheDocument();
    expect(document.querySelector("[data-i-guard-skip]")).toBeNull();
    expect(screen.getByText("참고 메모").className).not.toContain("text-danger");
  });

  it("keeps the I tab out of the registry", () => {
    expect(STRATEGY_I_TAB.href).toBe("/trading-i");
  });
});
