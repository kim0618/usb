import React from "react";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import DashboardPage from "../app/dashboard/page";
import { DashboardStrategyCards, DashboardStrategyICard, iDashboardSummary } from "./dashboard-strategy-card";
import type { IPaperView } from "../lib/strategy-i";

afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

/** GET /api/v1/strategy-i/paper as production answered it on 2026-10-28: launched, signal 10/30 not yet
 *  reached, as-of 10/20 behind the expected 10/27, the 15:15 KST firing held back by the guard. */
const prod = (): IPaperView => ({
  strategy_id: "STRATEGY_I_MOM12_2", display_name: "Strategy I · MOM12-2", orders: "NONE",
  initial_capital: "10000", target_holdings: 20, launched: true, launch_signal: "2026-10-30",
  launched_at: "2026-10-22T00:48:40.598753+00:00", status: "DATA_DELAYED", reason: null,
  last_run: { as_of: "2026-10-20", completed_at: "2026-10-22T00:54:32.008547+00:00", events: 0 },
  next_signal: "2026-10-30", account: null, positions: [], rebalances: [], trades: [],
  overlay: { virtual: true, affects_book: false, latest: null, evaluated_count: 0, observations: 0 },
  flags: { continuity_unresolved: [], price_stop_held: [], continuity_linked: [], stale_positions: [],
           data_delay: { as_of: "2026-10-20", expected: "2026-10-27", delayed: true },
           pending_signals: [], refused_signals: [], error: null },
  guard: {
    latest: { decision: "RUN", at: "2026-10-28T06:52:58.826637+00:00", guard: "V3.1", exit_code: 0, reasons: [] },
    last_skip: { decision: "SKIP", at: "2026-10-28T06:15:04.295707+00:00", guard: "V3", exit_code: 10,
                 reasons: ["MEMORY_PSI_full_avg10:6.92", "IO_PSI_full_avg10:25.52"] },
  },
});

const fields = (c: HTMLElement) => Object.fromEntries([...c.querySelectorAll("dl > div")].map(el => [
  el.querySelector("dt")!.textContent, el.querySelector("dd")!.textContent]));

describe("Strategy I dashboard card", () => {
  it("shows the pre-launch book as waiting, with no trade and a zero cumulative PnL", () => {
    const { container } = render(<DashboardStrategyICard view={prod()} href="/trading-i"/>);
    const card = container.querySelector("[data-dashboard-card]")!;
    expect(card.getAttribute("data-strategy")).toBe("STRATEGY_I_MOM12_2");
    expect(card.querySelector("[data-status]")!.getAttribute("data-status")).toBe("WAITING");
    expect(card.querySelector("[data-mode]")!.textContent).toBe("PAPER");
    expect(card.querySelector("[data-period]")!.textContent).toBe("시작 신호일 2026-10-30 전");
    const f = fields(container);
    expect(f["초기 자본"]).toContain("$10,000.00");
    expect(f["현재 자산"]).toBe("-");
    expect(f["최근 세션 손익"]).toBe("-");
    expect(f["공식 누적 손익"]).toBe("$0.00₩0");
    expect(f["공식 거래 수"]).toBe("0");
    expect(f["최근 결정"]).toBe("-");
    expect(card.querySelector("[data-last-run]")!.textContent).toBe("마지막 실행 10/22 09:54 KST");
    // same twelve-field frame as the registry cards
    expect([...container.querySelectorAll("dt")].map(dt => dt.textContent))
      .toEqual(["초기 자본", "현재 자산", "최근 세션 손익", "공식 누적 손익", "공식 거래 수", "최근 결정"]);
  });

  it("explains a guard SKIP in a neutral note and never as ISSUE or ERROR", () => {
    const { container } = render(<DashboardStrategyICard view={prod()}/>);
    const note = container.querySelector("[data-note]")!;
    expect(note.textContent).toBe("호스트 부하 보호로 실행 보류");
    expect(note.className).toContain("text-muted");
    expect(container.querySelector("[data-reason]")).toBeNull();
    expect(container.textContent).not.toMatch(/ISSUE|ERROR|오류/);
  });

  it("drops the guard note once a run completed after the skip, and shows the delay instead", () => {
    const v = prod();
    v.last_run = { ...v.last_run!, completed_at: "2026-10-28T07:00:00+00:00" };
    expect(iDashboardSummary(v).note).toBe("데이터 지연 · 마지막 평가 2026-10-20");
    v.flags!.data_delay.delayed = false;
    expect(iDashboardSummary(v).note).toBeNull();
  });

  it("links the title to /trading-i", () => {
    render(<DashboardStrategyICard view={prod()} href="/trading-i"/>);
    expect(screen.getByRole("link", { name: "Strategy I · MOM12-2" })).toHaveAttribute("href", "/trading-i");
  });

  it("folds every API status without inventing one", () => {
    const fold = (status: IPaperView["status"]) => iDashboardSummary({ ...prod(), status }).state;
    expect(fold("NOT_LAUNCHED")).toBe("WAITING");
    expect(fold("LAUNCHED_NO_RUN")).toBe("WAITING");
    expect(fold("DATA_DELAYED")).toBe("WAITING");
    expect(fold("RUNNING")).toBe("RUNNING");
    expect(fold("INPUT_BLOCKED")).toBe("ISSUE");
    expect(fold("ERROR")).toBe("ISSUE");
  });

  it("shows a failed run as ISSUE with the API's reason in the danger slot", () => {
    const { container } = render(<DashboardStrategyICard view={{ ...prod(), status: "ERROR", reason: "integrity check failed" }}/>);
    expect(container.querySelector("[data-status]")!.getAttribute("data-status")).toBe("ISSUE");
    expect(container.querySelector("[data-reason]")!.textContent).toBe("integrity check failed");
    expect(container.querySelector("[data-note]")).toBeNull();
  });

  it("shows a dash, not a zero, before any completed run", () => {
    const v: IPaperView = { strategy_id: "STRATEGY_I_MOM12_2", display_name: "Strategy I · MOM12-2", orders: "NONE",
      status: "NOT_LAUNCHED", launched: false, initial_capital: "10000", reason: "Paper가 시작되지 않았습니다 (launch.json 없음)." };
    const { container } = render(<DashboardStrategyICard view={v}/>);
    const f = fields(container);
    expect(f["공식 누적 손익"]).toBe("-");
    expect(f["공식 거래 수"]).toBe("-");
    expect(container.querySelector("[data-period]")!.textContent).toBe("미시작");
    expect(container.querySelector("[data-reason]")).toBeNull();
  });

  it("reads the account once launched, and sums trades from the uncapped rebalances", () => {
    const v = prod();
    v.status = "RUNNING"; v.guard = null; v.flags!.data_delay.delayed = false;
    v.last_run = { as_of: "2026-11-03", completed_at: "2026-11-04T07:15:00+00:00", events: 50 };
    v.account = { as_of: "2026-11-03", nav: 10123.45, cash: 50, positions_value: 10073.45, total_pnl: 123.45,
                  total_return: 0.0123, day_pnl: -20.5, day_return: -0.002, day_session: "2026-11-03", holdings: 20, operating_sessions: 2 };
    v.rebalances = [{ k: 1, session: "2026-11-02", signal: "2026-10-30", holdings: 20, trade_count: 20, cash_pct: 0.5,
                      nav_post: 9990, costs: 10, turnover_one_way: null }];
    v.trades = [];                                   // the trade list is capped server-side; the count does not use it
    const { container } = render(<DashboardStrategyICard view={v}/>);
    const f = fields(container);
    expect(container.querySelector("[data-status]")!.getAttribute("data-status")).toBe("RUNNING");
    expect(container.querySelector("[data-period]")!.textContent).toBe("운영 2세션");
    expect(f["현재 자산"]).toContain("$10,123.45");
    expect(f["최근 세션 손익"]).toContain("-$20.50");
    expect(f["공식 누적 손익"]).toContain("+$123.45");
    expect(f["공식 거래 수"]).toBe("20");
    expect(f["최근 결정"]).toBe("2026-10-30 신호 · 20종목");
  });

  it("falls back to an ISSUE card when the I API cannot be read", () => {
    const { container } = render(<DashboardStrategyICard view={null} error="HTTP 502"/>);
    expect(container.querySelector("[data-status]")!.getAttribute("data-status")).toBe("ISSUE");
    expect(container.querySelector("[data-reason]")!.textContent).toBe("HTTP 502");
    expect(Object.values(fields(container)).slice(0, 5)).toEqual(["-", "-", "-", "-", "-"]);
  });

  it("joins the existing two-column grid instead of a new layout", () => {
    const { container } = render(<DashboardStrategyCards bundles={[]}><DashboardStrategyICard view={prod()}/></DashboardStrategyCards>);
    const grid = container.querySelector("section[aria-label='전략 현황']")!;
    expect(grid.className).toBe("grid gap-4 lg:grid-cols-2");
    expect(grid.querySelectorAll("[data-dashboard-card]")).toHaveLength(1);
  });
});

describe("dashboard page with Strategy I", () => {
  const eRow = { strategy_id: "STRATEGY_E_MAX_V1", display_name: "Strategy E", version: "V1", enabled: true,
    mode: "SIMULATION_PAPER", market_data_source: "KIWOOM", runtime_status: "COMPLETE", paper_status: null,
    evidence_status: null, session: "2026-10-27" };
  const base: Record<string, unknown> = {
    "/api/v1/strategies": [eRow],
    "/api/v1/strategies/STRATEGY_E_MAX_V1/status": { ...eRow, available: true, detail: {} },
    "/api/v1/strategies/STRATEGY_E_MAX_V1/account": { strategy_id: "STRATEGY_E_MAX_V1", available: true, currency: "USD",
      initial_equity: "10000", current_equity: "9643.51", today_pnl: "-12", total_pnl: null, open_positions: 0,
      closed_trades_today: 0, source: "E" },
    "/api/v1/strategies/STRATEGY_E_MAX_V1/equity": { strategy_id: "STRATEGY_E_MAX_V1", currency: "USD", source: "E", baseline: "10000", points: [] },
    "/api/v1/strategies/cards": [{ strategy_id: "STRATEGY_E_MAX_V1", trades: 7, official: { trades: 7, net_pnl: "-356.49", accounting_versions: ["V1"] },
      na: {}, dashboard: { mode: "OFFICIAL PAPER", status: "WAITING", status_reason: null, operating_since: "2026-09-28",
        operating_day: 31, session_count: 7, session_kind: "공식", last_run_at: "2026-10-27T13:36:13+00:00", last_decision: "HRI, NVR, UNF" } }],
  };
  const stub = (payloads: Record<string, unknown>) => vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo) => {
    const path = String(input).replace(/^https?:\/\/[^/]+/, "");
    const body = payloads[path];
    return { ok: body !== undefined, status: body === undefined ? 502 : 200,
             json: async () => body ?? { error: { message: "HTTP 502" } } } as Response;
  }));

  it("puts Strategy I after the registry cards, linked to /trading-i", async () => {
    stub({ ...base, "/api/v1/strategy-i/paper": prod() });
    render(<DashboardPage/>);
    expect(await screen.findByText("Strategy I · MOM12-2")).toBeInTheDocument();
    expect([...document.querySelectorAll("[data-dashboard-card]")].map(c => c.getAttribute("data-strategy")))
      .toEqual(["STRATEGY_E_MAX_V1", "STRATEGY_I_MOM12_2"]);
    expect(screen.getByRole("link", { name: "Strategy I · MOM12-2" })).toHaveAttribute("href", "/trading-i");
    expect(screen.getByRole("link", { name: "Strategy E" })).toHaveAttribute("href", "/strategy-e");
  });

  it("keeps the registry cards when only the I API fails", async () => {
    stub(base);
    render(<DashboardPage/>);
    expect(await screen.findByText("Strategy I · MOM12-2")).toBeInTheDocument();
    const e = document.querySelector("[data-strategy='STRATEGY_E_MAX_V1']") as HTMLElement;
    expect(e.querySelector("[data-status]")!.getAttribute("data-status")).toBe("WAITING");
    expect(fields(e)["공식 누적 손익"]).toContain("-$356.49");
    const i = document.querySelector("[data-strategy='STRATEGY_I_MOM12_2']") as HTMLElement;
    expect(i.querySelector("[data-status]")!.getAttribute("data-status")).toBe("ISSUE");
  });
});
