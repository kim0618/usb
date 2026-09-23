import React from "react";
import { readFileSync } from "node:fs";
import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import DashboardPage from "../app/dashboard/page";
import {
  BootstrapProgress, EquitySparkline, EvidenceBooks, StrategyCard, StrategyCards, StrategyEStatus,
  StrategyPositions, StrategyTrades, UniversePanel, type StrategyBundle,
} from "./strategy-runtime";
import { OFFICIAL, PROVISIONAL, STRATEGY_A, STRATEGY_E, emptyLabel } from "../lib/strategies";

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

const aRow = {
  strategy_id: STRATEGY_A, display_name: "Strategy A", version: "V0", enabled: true,
  mode: "SIMULATION_PAPER", market_data_source: "KIWOOM", runtime_status: "RUNNING",
  paper_status: "PAPER", evidence_status: null, session: "2026-09-22",
};
const eRow = {
  strategy_id: STRATEGY_E, display_name: "Strategy E-MAX V1", version: "V1", enabled: true,
  mode: "SIMULATION_PAPER", market_data_source: "KIWOOM", runtime_status: "COMPLETE",
  paper_status: PROVISIONAL, evidence_status: PROVISIONAL, session: "2026-09-23",
};
const eDetail = {
  market_data_source: "KIWOOM", rvol_threshold: 3.0, rvol_window: 20, canonical_universe: 2561,
  rvol_ready: 28, rvol_missing: 826, market_data_unavailable: 1, sparse_no_premarket: 251,
  h5_true: 0, h5_false: 27, h5_unknown: 827, selected: [], no_decision_reason: null,
  bootstrap: { available: true, status: "RUNNING", symbols: 2561, ready: 28, partial: 0, zero: 2533,
               history_exhausted: 0, target_sessions: 20, minimum_sessions: 5, percent: 1.1,
               last_update: "2026-09-23T02:15:45+00:00", estimated_completion: null,
               estimate_note: "런타임이 신뢰할 수 있는 완료 예정 시각을 제공하지 않는다" },
  universe: { available: true, status: "READY", target_session: "2026-09-23", asof_session: "2026-09-22",
              symbol_count: 2561, source: "MASSIVE", digest: "abc123", identity: "ok" },
};
const bundle = (over: Partial<StrategyBundle> = {}): StrategyBundle => ({
  row: aRow, status: { ...aRow, available: true, detail: {} },
  account: { strategy_id: STRATEGY_A, available: true, currency: "USD", initial_equity: "10000",
             current_equity: "10240.55", today_pnl: "120.25", total_pnl: "240.55", open_positions: 1,
             closed_trades_today: 2, source: "PAPER_DB", empty_reason: null },
  equity: { strategy_id: STRATEGY_A, currency: "USD", source: "PAPER_DB", baseline: "10000",
            points: [{ date: "2026-09-19", equity: "10120.30" }, { date: "2026-09-22", equity: "10240.55" }] },
  ...over,
});
const eBundle = (over: Record<string, unknown> = {}): StrategyBundle => bundle({
  row: eRow,
  status: { ...eRow, available: true, detail: eDetail, last_update: "2026-09-23T13:29:00+00:00" },
  account: { strategy_id: STRATEGY_E, available: true, currency: "USD", initial_equity: "10000",
             current_equity: null, today_pnl: null, total_pnl: null, open_positions: 0,
             closed_trades_today: 0, source: "E_RUNTIME_FILES", evidence_status: PROVISIONAL,
             empty_reason: "RVOL 부트스트랩 중 (provisional)",
             books: { [PROVISIONAL]: { present: true, initial_equity: "10000", equity: "10000",
                                       realized_pnl: "0", sessions: 0 },
                      [OFFICIAL]: { present: false, initial_equity: null, equity: null,
                                    realized_pnl: null, sessions: 0 } } },
  equity: { strategy_id: STRATEGY_E, currency: "USD", source: "E_RUNTIME_FILES", baseline: "10000",
            points: [], current_book: PROVISIONAL,
            books: { [PROVISIONAL]: { points: [], baseline: "10000" },
                     [OFFICIAL]: { points: [], baseline: null } },
            note: "PROVISIONAL과 OFFICIAL은 각자의 장부이며 이어 붙이지 않는다" },
  ...over,
});

describe("strategy cards", () => {
  it("renders Strategy A with its own account figures", () => {
    render(<StrategyCard bundle={bundle()}/>);
    expect(screen.getByText("Strategy A")).toBeInTheDocument();
    expect(screen.getByText("$10,240.55")).toBeInTheDocument();
    expect(screen.getByText("+$120.25")).toBeInTheDocument();
    expect(screen.getByText("RUNNING")).toBeInTheDocument();
  });

  it("renders Strategy E with its evidence grade", () => {
    render(<StrategyCard bundle={eBundle()}/>);
    expect(screen.getByText("Strategy E-MAX V1")).toBeInTheDocument();
    expect(screen.getByText("PROVISIONAL PAPER")).toBeInTheDocument();
    expect(screen.getByText("COMPLETE")).toBeInTheDocument();
  });

  it("keeps A and E accounts apart and sums nothing", () => {
    render(<StrategyCards bundles={[bundle(), eBundle()]}/>);
    const a = screen.getByText("Strategy A").closest("[data-strategy]") as HTMLElement;
    const e = screen.getByText("Strategy E-MAX V1").closest("[data-strategy]") as HTMLElement;
    expect(a.dataset.strategy).toBe(STRATEGY_A);
    expect(e.dataset.strategy).toBe(STRATEGY_E);
    expect(within(a).getByText("$10,240.55")).toBeInTheDocument();
    expect(within(e).queryByText("$10,240.55")).not.toBeInTheDocument();
    expect(screen.queryByText("$20,481.10")).not.toBeInTheDocument();      // no combined total anywhere
  });

  it("is built from the registry, so a strategy appears by being listed", () => {
    render(<StrategyCards bundles={[eBundle()]}/>);
    expect(screen.queryByText("Strategy A")).not.toBeInTheDocument();
    expect(screen.getByText("Strategy E-MAX V1")).toBeInTheDocument();
  });

  it("says why there is no figure instead of printing a zero", () => {
    render(<StrategyCard bundle={eBundle()}/>);
    const card = screen.getByText("Strategy E-MAX V1").closest("[data-strategy]") as HTMLElement;
    expect(within(card).getByText(/RVOL 부트스트랩 중/)).toBeInTheDocument();
    expect(within(card).queryByText("$0.00")).not.toBeInTheDocument();
    expect(emptyLabel(eBundle().account)).toMatch(/PROVISIONAL/);
  });

  it("names the official empty state separately once the grade flips", () => {
    const official = eBundle().account;
    expect(emptyLabel({ ...official, evidence_status: OFFICIAL })).toBe("아직 official paper 거래 없음");
  });
});

describe("Strategy E status", () => {
  it("shows the frozen threshold, the counts and H5 unknown", () => {
    render(<StrategyEStatus status={eBundle().status}/>);
    expect(screen.getByText("3.0")).toBeInTheDocument();
    expect(screen.getByText("2,561")).toBeInTheDocument();
    expect(screen.getByText("827")).toBeInTheDocument();                   // H5 unknown
    expect(screen.getByText("826")).toBeInTheDocument();                   // RVOL missing
  });

  it("shows MARKET_DATA_UNAVAILABLE as its own count", () => {
    render(<StrategyEStatus status={eBundle().status}/>);
    const card = screen.getByText("Market data unavailable").closest("div")?.parentElement as HTMLElement;
    expect(within(card).getByText("1")).toBeInTheDocument();
  });

  it("renders a graceful state when the runtime has written nothing yet", () => {
    const empty = { ...eRow, available: true, runtime_status: "NO_SESSION_YET", evidence_status: null,
                    session: null, detail: {} };
    render(<StrategyEStatus status={empty}/>);
    expect(screen.getByText("NO_SESSION_YET")).toBeInTheDocument();
    expect(screen.getAllByText("-").length).toBeGreaterThan(3);
  });
});

describe("bootstrap progress", () => {
  it("shows collected over total and never invents an ETA", () => {
    render(<BootstrapProgress state={eDetail.bootstrap}/>);
    expect(screen.getByText("28 / 2,561 · 1.1%")).toBeInTheDocument();
    expect(screen.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "1");
    expect(screen.getByText(/완료 예정 시각을 제공하지 않는다/)).toBeInTheDocument();
  });

  it("degrades to an empty state without a collector report", () => {
    render(<BootstrapProgress state={{ available: false, status: "UNKNOWN", reason: "no coverage report" }}/>);
    expect(screen.getByText("RVOL 부트스트랩 상태 없음")).toBeInTheDocument();
  });
});

describe("universe", () => {
  it("marks a stale universe as NO_DECISION for E while A keeps running", () => {
    render(<UniversePanel universe={{ available: true, status: "UNIVERSE_NOT_AVAILABLE",
      target_session: "2026-09-22", asof_session: "2026-09-21", symbol_count: 2561, source: "MASSIVE",
      identity: "artifact targets 2026-09-22, not 2026-09-23" }}/>);
    expect(screen.getByText("UNIVERSE_NOT_AVAILABLE")).toBeInTheDocument();
    expect(screen.getByText(/A는 계속 실행/)).toBeInTheDocument();
  });
});

describe("positions and books", () => {
  it("shows the same symbol held by A and by E as two rows", () => {
    render(<StrategyPositions positions={[
      { strategy_id: STRATEGY_A, symbol: "NVDA", quantity: "10", average_price: "180.10",
        cost_basis: "1801.00", opened_at: null, source: "PAPER_DB" },
      { strategy_id: STRATEGY_E, symbol: "NVDA", quantity: "4", average_price: "181.20",
        cost_basis: "724.80", opened_at: null, evidence_status: PROVISIONAL, source: "E_RUNTIME_FILES" },
    ]}/>);
    expect(screen.getAllByText("NVDA")).toHaveLength(2);
    expect(document.querySelector('[data-position-key="STRATEGY_A/NVDA"]')).not.toBeNull();
    expect(document.querySelector('[data-position-key="STRATEGY_E_MAX_V1/NVDA"]')).not.toBeNull();
  });

  it("keeps provisional and official books separate", () => {
    render(<EvidenceBooks equity={eBundle().equity}/>);
    expect(document.querySelector(`[data-book="${PROVISIONAL}"]`)).not.toBeNull();
    expect(document.querySelector(`[data-book="${OFFICIAL}"]`)).not.toBeNull();
    expect(screen.getByText(/합산하지 않습니다|이어 붙이지 않는다/)).toBeInTheDocument();
    expect(screen.getByText("아직 official paper 거래 없음")).toBeInTheDocument();
  });

  it("does not draw an equity line from a single point", () => {
    render(<EquitySparkline equity={eBundle().equity}/>);
    expect(screen.getByText(/자산 곡선을 그릴 기록이 아직 없습니다/)).toBeInTheDocument();
  });

  it("says when there is no trade yet", () => {
    render(<StrategyTrades trades={[]}/>);
    expect(screen.getByText("청산된 거래 없음")).toBeInTheDocument();
  });
});

describe("dashboard route", () => {
  const payloads: Record<string, unknown> = {
    "/api/v1/strategies": [aRow, eRow],
    [`/api/v1/strategies/${STRATEGY_A}/status`]: bundle().status,
    [`/api/v1/strategies/${STRATEGY_A}/account`]: bundle().account,
    [`/api/v1/strategies/${STRATEGY_A}/equity`]: bundle().equity,
    [`/api/v1/strategies/${STRATEGY_E}/status`]: eBundle().status,
    [`/api/v1/strategies/${STRATEGY_E}/account`]: eBundle().account,
    [`/api/v1/strategies/${STRATEGY_E}/equity`]: eBundle().equity,
  };

  it("renders both strategies from the live API", async () => {
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo) => {
      const path = String(input).replace(/^https?:\/\/[^/]+/, "");
      const body = payloads[path];
      return { ok: body !== undefined, status: body === undefined ? 404 : 200,
               json: async () => body ?? { error: { message: path } } } as Response;
    }));
    render(<DashboardPage/>);
    expect(await screen.findAllByText("Strategy A")).not.toHaveLength(0);   // card + equity panel
    expect(screen.getAllByText("Strategy E-MAX V1").length).toBeGreaterThan(0);
    expect(document.querySelectorAll("[data-strategy]")).toHaveLength(2);
    expect(screen.getByRole("heading", { name: "대시보드" })).toBeInTheDocument();
  });

  it("has no mock import and no mock fallback left on the dashboard", () => {
    const source = readFileSync("app/dashboard/page.tsx", "utf8");
    expect(source).not.toMatch(/mocks\//);
    expect(source).not.toMatch(/MOCK/);
    expect(source).toMatch(/dashboardStrategies/);
  });

  it("keeps a route for Strategy E", () => {
    expect(() => readFileSync("app/strategy-e/page.tsx", "utf8")).not.toThrow();
  });
});
