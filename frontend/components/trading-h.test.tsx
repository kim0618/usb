import React from "react";
import { readFileSync } from "node:fs";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { byRecentDecision, decisionReason, horizonProgress, latestDecision, nextEvaluation } from "../lib/h-trading";
import { operatingTabs, OPERATING_ROUTES, STRATEGY_A, STRATEGY_E, STRATEGY_H } from "../lib/strategies";
import { navigationItems, isNavigationActive } from "./app-shell";
import TradingHPage from "../app/trading-h/page";

vi.mock("next/navigation", () => ({ usePathname: () => "/trading-h" }));
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

const row = (ticker: string, decision: string, time: string, over: Record<string, unknown> = {}) => ({
  strategy_id: STRATEGY_H, ticker, decision, decision_time: time, approve_blockers: [], reject_fired: [], watch_matched: [],
  forward: { "1D": { state: "MATURED", maturity_session: "2026-10-05" }, "5D": { state: "PENDING", maturity_session: "2026-10-09" },
             "21D": { state: "PENDING", maturity_session: "2026-11-02" } },
  checksums: { d5: "deadbeef" }, ...over,
});

const view = {
  strategy_id: STRATEGY_H, available: true,
  contract: { contract_id: "H_V2_D7_FORWARD_SHADOW_V1", d5_contract: "D5_D2R_V1", d6_contract: "D6", horizons: [1, 5, 21, 63] },
  launch: { status: "LAUNCHED", baseline_session: "2026-10-02", launched_at: "2026-10-04T08:34:30+00:00", decision_session: "2026-09-16", issuers: 3 },
  decision_counts: { APPROVE: 0, WATCH: 2, REJECT: 1 },
  maturity: { "1D": { matured: 3, pending: 0, incomplete: 0, no_baseline: 0, maturity_session: "2026-10-05" },
              "5D": { matured: 0, pending: 3, incomplete: 0, no_baseline: 0, maturity_session: "2026-10-09" } },
  evaluation: { state: "RUNNING", verdict: "INCONCLUSIVE", sample_reached: { "21D": false }, reasons: ["SAMPLE_NOT_REACHED"] },
  position_rule: {}, price_store: { source: "x", sessions_observed: 8 },
  rows: [
    row("AAA", "WATCH", "2026-10-04T08:34:30+00:00", { approve_blockers: ["x", "y"] }),
    row("BBB", "REJECT", "2026-10-04T08:34:30+00:00", { reject_fired: ["R1"] }),
    row("CCC", "WATCH", "2026-10-04T08:30:00+00:00"),
  ],
};

describe("Trading H routing", () => {
  it("puts H's operating tab on /trading-h and keeps the research screen on /strategy-h", () => {
    expect(OPERATING_ROUTES[STRATEGY_H].href).toBe("/trading-h");
    expect(OPERATING_ROUTES[STRATEGY_A].href).toBe("/trading");
    expect(OPERATING_ROUTES[STRATEGY_E].href).toBe("/strategy-e");
    expect(() => readFileSync("app/strategy-h/page.tsx", "utf8")).not.toThrow();
    const tabs = operatingTabs([STRATEGY_A, STRATEGY_E, STRATEGY_H].map(id => ({ strategy_id: id, display_name: id, version: "", enabled: true,
      mode: id === STRATEGY_H ? "FORWARD_SHADOW" : "SIMULATION_PAPER", market_data_source: "", runtime_status: null,
      paper_status: null, evidence_status: null, session: null })));
    expect(tabs.map(t => t.href)).toEqual(["/trading", "/strategy-e", "/trading-h"]);
  });

  it("keeps 트레이딩 active on all three tabs and on H's research detail", () => {
    const trading = navigationItems.find(i => i.label === "트레이딩")!;
    for (const path of ["/trading", "/strategy-e", "/trading-h", "/strategy-h"]) expect(isNavigationActive(path, trading)).toBe(true);
  });
});

describe("Trading H summary", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo) => {
      const path = String(input).replace(/^https?:\/\/[^/]+/, "");
      const body = path.endsWith("/forward") ? view : path.endsWith("/strategies")
        ? [{ strategy_id: STRATEGY_H, display_name: "Strategy H", variant_label: "Forward Shadow", version: "", enabled: true, mode: "FORWARD_SHADOW" }] : {};
      return { ok: true, status: 200, json: async () => body } as Response;
    }));
  });

  it("shows the five KPI cards, observed issuers, recent decisions and compact forward progress", async () => {
    const { container } = render(<TradingHPage/>);
    await screen.findByText("현재 관찰 종목");
    const text = container.textContent!;
    for (const label of ["관찰 종목", "WATCH", "REJECT", "APPROVE", "최근 결정", "현재 관찰 종목", "Forward 평가"]) expect(text).toContain(label);
    expect(container.querySelectorAll("section[aria-label='관찰 요약'] > *")).toHaveLength(5);
    expect(text).toContain("5D · 10/09");           // next evaluation per issuer
    expect(text).toContain("3 / 3");                 // matured / total
    expect(text).toContain("미래 데이터 관찰 중 · 실제 주문 없음");
    expect(screen.getByRole("link", { name: "연구 상세 보기" })).toHaveAttribute("href", "/strategy-h");
  });

  it("hides the research and contract internals and never renders INCONCLUSIVE as an error", async () => {
    const { container } = render(<TradingHPage/>);
    await screen.findByText("Forward 평가");
    const text = container.textContent!;
    for (const gone of ["H_V2_D7", "D5_D2R", "D5 Contract", "H Paper Positions", "baseline", "deadbeef", "INCONCLUSIVE",
                        "SAMPLE_NOT_REACHED", "R1", "LAUNCH", "연구는 종결"]) expect(text).not.toContain(gone);
    expect(container.querySelector("[data-h-forward] .tone-danger")).toBeNull();
    expect(text).toContain("정상");
  });
});

describe("Trading H wording helpers", () => {
  const rows = view.rows as never[];
  it("orders by decision time and reduces clauses to a sentence", () => {
    expect(byRecentDecision(rows).map(r => (r as { ticker: string }).ticker)).toEqual(["AAA", "BBB", "CCC"]);
    expect(latestDecision(rows)).toBe("WATCH");
    expect(decisionReason(rows[0])).toBe("승인 조건 2개가 아직 충족되지 않았습니다");
    expect(decisionReason(rows[1])).toBe("기각 조건 1개에 해당합니다");
    expect(nextEvaluation(rows[0])).toEqual({ horizon: "5D", session: "2026-10-09" });
    expect(horizonProgress(view.maturity as never)).toEqual([
      { horizon: "1D", matured: 3, pending: 0, total: 3 }, { horizon: "5D", matured: 0, pending: 3, total: 3 }]);
  });
});

describe("the retired 전략 menu's screens stay reachable", () => {
  it("links them from the dashboard and keeps every route file", async () => {
    const page = readFileSync("app/dashboard/page.tsx", "utf8");
    for (const href of ["/strategy-compare", "/daily", "/strategy-history"]) expect(page).toContain(`href: "${href}"`);
    for (const route of ["daily", "strategy-compare", "strategy-history", "shadow", "strategy-h", "strategy-b", "trading-b"]) {
      expect(() => readFileSync(`app/${route}/page.tsx`, "utf8"), route).not.toThrow();
    }
    expect(readFileSync("app/trading-h/page.tsx", "utf8")).toContain('href="/strategy-h"');
    expect(readFileSync("components/ae-operations.tsx", "utf8")).toContain('href="/shadow"');   // from the history screen
  });
});

describe("Trading H paper book", () => {
  const paper = {
    available: true, strategy_id: STRATEGY_H, paper: { mode: "SHADOW_PLUS_PAPER", launched: true, launched_at: "2026-10-14T00:00:00+00:00" },
    account: { launched: true, initial_equity: "10000", current_equity: "10120.5", cash: "6700", positions_value: "3420.5",
               unrealized_pnl: "90.5", realized_pnl: "30", total_pnl: "120.5", trades: 0, fills: 2, win_rate: null,
               pending_entries: [] },
    positions: [{ trade_id: "t", position_id: "STRATEGY_H_V2|2026-10-15|AAA", ticker: "AAA", status: "OPEN", position_status: "PARTIAL",
                  entry_session: "2026-10-15", entry_price: "10.015", current_shares: "166", initial_shares: "332",
                  tp1: "12", tp2: "15", bear: null, bear_rule: "NOT_EVALUATED", mark_price: "12.5",
                  realized_pnl: "30", unrealized_pnl: "90.5", net_pnl: null, exit_reason: null }],
    trades: [],
    fills: [{ event_id: "e2", ticker: "AAA", session: "2026-10-16", trade_action: "SELL_TP1", quantity: "166", fill_price: "11.982", raw_price: "12" },
            { event_id: "e1", ticker: "AAA", session: "2026-10-15", trade_action: "BUY", quantity: "332", fill_price: "10.015", raw_price: "10" }],
    autonomy: { counts: { QUEUED: 2, RUNNING: 0, BUDGET_DEFERRED: 1, FAILED: 0, COMPLETE: 0 }, authorized: false,
                timestamps: { last_auto_run_at: "2026-10-14T05:12:51+00:00", latest_price_session: "2026-10-13",
                              sec_refresh_at: "2026-10-14T05:12:40+00:00", research_queue_last_run_at: "2026-10-14T05:12:50+00:00",
                              research_last_run_at: "2026-10-14T05:12:51+00:00", next_auto_run_at: "2026-10-15T05:10:00+00:00" } },
  };
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo) => {
      const path = String(input).replace(/^https?:\/\/[^/]+/, "");
      const body = path.endsWith("/forward") ? view : path.endsWith("/paper") ? paper : path.endsWith("/strategies")
        ? [{ strategy_id: STRATEGY_H, display_name: "Strategy H", variant_label: "Paper + Forward Shadow", version: "", enabled: true, mode: "FORWARD_SHADOW" }] : {};
      return { ok: true, status: 200, json: async () => body } as Response;
    }));
  });

  it("shows the paper account, open positions with TP/Bear and recent fills, and labels the mode", async () => {
    const { container } = render(<TradingHPage/>);
    await screen.findByText("보유 포지션");
    const text = container.textContent!;
    for (const label of ["Strategy H · Paper + Forward Shadow", "가상 계좌 · 실제 주문 없음", "현재 자산", "누적 손익",
                         "보유 포지션", "최근 체결", "TP1 50% 매도", "평가 안 함", "Paper 조치", "관찰만"]) expect(text).toContain(label);
    expect(text).toContain("$10,120.50");
    expect(text).not.toContain("$0.00 Bear");
  });

  it("shows when H last ran by itself, from runtime timestamps, and what the research queue holds", async () => {
    const fetchMock = vi.mocked(fetch);
    const { container } = render(<TradingHPage/>);
    await screen.findByText("자동 운영");
    const text = container.textContent!;
    for (const label of ["마지막 자동 실행", "최신 가격 세션", "SEC 갱신", "리서치 큐 실행", "다음 자동 실행"]) expect(text).toContain(label);
    expect(text).toContain("2026-10-13");
    expect(container.querySelector("[data-h-auto-last]")!.textContent).not.toBe("-");
    expect(container.querySelector("[data-h-auto-next]")!.textContent).not.toBe("-");
    expect(container.querySelector("[data-h-auto-queue]")!.textContent).toContain("대기 2 · 실행 중 0 · 예산 이월 1 · 실패 0");
    expect(text).toContain("자동 리서치 승인 전");
    expect(text).toContain("보유 0종목");
    for (const call of fetchMock.mock.calls) expect(String(call[0])).toContain("/api/v1/strategies");
  });
});

