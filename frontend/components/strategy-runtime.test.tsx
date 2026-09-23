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

describe("operating tabs come from the registry", () => {
  const rows = [
    { ...aRow },
    { ...eRow },
    { strategy_id: "STRATEGY_B", display_name: "Strategy B", version: "E1-A", enabled: false,
      mode: "RESEARCH_CLOSED", market_data_source: "KIWOOM", runtime_status: "NOT_RUNNING",
      paper_status: null, evidence_status: null, session: null },
  ];

  it("shows only the enabled paper strategies and never invents one while loading", async () => {
    const { TradingTabs } = await import("./section-tabs");
    const { operatingTabs } = await import("../lib/strategies");
    expect(operatingTabs(rows).map(tab => tab.href)).toEqual(["/trading", "/strategy-e"]);
    vi.stubGlobal("fetch", vi.fn(async () => new Promise(() => {})));       // never resolves
    render(<TradingTabs/>);
    expect(screen.getByRole("navigation", { name: "트레이딩 화면" })).toBeInTheDocument();
    expect(screen.queryAllByRole("link")).toHaveLength(0);                  // no placeholder tab
  });

  it("keeps the closed strategy's own screen reachable by URL", () => {
    expect(() => readFileSync("app/trading-b/page.tsx", "utf8")).not.toThrow();
    expect(() => readFileSync("app/strategy-b/page.tsx", "utf8")).not.toThrow();
  });

  it("puts the E operating tab on the Strategy E screen", () => {
    const page = readFileSync("app/strategy-e/page.tsx", "utf8");
    expect(page).toContain("<TradingTabs/>");
  });

  it("leaves Strategy A's screen and its data calls untouched", () => {
    const trading = readFileSync("app/trading/page.tsx", "utf8");
    expect(trading).toContain("<TradingTabs/>");
    ["api.trading", "api.entryBoard", "계좌 요약", "현재 보유 종목"].forEach(m => expect(trading).toContain(m));
  });
});

describe("summary cards are the same five on A and E", () => {
  const order = ["총 자산", "투자 중", "보유 현금", "누적 수익", "직전 거래일 손익"];
  const positions = (source: string) => order.map(label => source.indexOf(label));

  it("keeps the same card order on both screens and drops 평가 손익 from the summary", () => {
    for (const path of ["app/trading/page.tsx", "app/strategy-e/page.tsx"]) {
      const source = readFileSync(path, "utf8");
      const at = positions(source);
      expect(at.every(index => index >= 0), path).toBe(true);
      expect(at, path).toEqual([...at].sort((a, b) => a - b));       // declared in the required order
      expect(source, path).not.toContain('label="평가 손익"');
      expect(source, path).toContain("CumulativePerformance");
    }
  });

  it("keeps the per-position unrealized column on Strategy A's holdings table", () => {
    const trading = readFileSync("app/trading/page.tsx", "utf8");
    expect(trading).toContain("pnlTone(position.unrealized_pnl)");
    expect(trading).toContain("signedDecimal(position.unrealized_pnl)");
  });

  it("reuses the existing PnL tone classes and adds none", async () => {
    const source = readFileSync("components/daily-performance.tsx", "utf8");
    const classes = new Set((source.match(/text-(danger|success|foreground-secondary|muted)/g) || []));
    expect([...classes].sort()).toEqual(["text-danger", "text-foreground-secondary", "text-muted", "text-success"]);
    expect(source).not.toMatch(/text-(profit|loss|gain|positive|negative)/);
  });
});

describe("cumulative return value", () => {
  it("shows the backend cumulative with its own return percentage", async () => {
    const { CumulativePerformance } = await import("./daily-performance");
    render(<CumulativePerformance pnl="80.478546" baseline="7428.92"/>);
    const value = screen.getByText(/^\+\$80\.47/);
    expect(value).toBeInTheDocument();
    expect(screen.getByText("+1.08%")).toBeInTheDocument();
    expect(value.className).toContain("text-success");
  });

  it("uses the loss tone below zero and the neutral tone at zero", async () => {
    const { CumulativePerformance } = await import("./daily-performance");
    const { unmount } = render(<CumulativePerformance pnl="-12.5" baseline="1000"/>);
    expect(screen.getByText(/^-\$12\.5/).className).toContain("text-danger");
    expect(screen.getByText("-1.25%")).toBeInTheDocument();
    unmount();
    render(<CumulativePerformance pnl="0" baseline="1000"/>);
    expect(screen.getByText(/^\+\$0\.00/).className).toContain("text-foreground-secondary");
  });

  it("shows a dash and a reason instead of a manufactured zero", async () => {
    const { CumulativePerformance, SessionPerformance } = await import("./daily-performance");
    const { unmount } = render(<CumulativePerformance pnl={null} baseline="10000" note="PROVISIONAL · RVOL 부트스트랩 중"/>);
    expect(screen.getByText(/^-/)).toBeInTheDocument();
    expect(screen.getByText("PROVISIONAL · RVOL 부트스트랩 중")).toBeInTheDocument();
    expect(screen.queryByText(/\$0(\.00)?$/)).not.toBeInTheDocument();
    unmount();
    render(<SessionPerformance session={null} pnl={null}/>);
    expect(screen.getByText("-")).toBeInTheDocument();
  });

  it("never adds the provisional and official books together", () => {
    const page = readFileSync("app/strategy-e/page.tsx", "utf8");
    expect(page).toContain("account.books?.[account.evidence_status");     // the active book only
    expect(page).toContain("PROVISIONAL과 OFFICIAL 누적은 합산하지 않습니다");
    expect(page).not.toMatch(/books\[[^\]]*PROVISIONAL[^\]]*\][^\n]*\+/);
  });
});

describe("money is rendered the way Strategy A renders it", () => {
  it("shows USD and KRW on every dashboard money field of Strategy A", async () => {
    const { formatKrw, usdToDisplayKrw } = await import("../lib/format");
    render(<StrategyCard bundle={bundle()}/>);
    const card = screen.getByText("Strategy A").closest("[data-strategy]") as HTMLElement;
    expect(within(card).getByText("$10,000.00")).toBeInTheDocument();          // 초기 자본 USD
    expect(within(card).getByText(formatKrw(usdToDisplayKrw("10000")))).toBeInTheDocument();
    expect(within(card).getByText("$10,240.55")).toBeInTheDocument();          // 현재 자산 USD
    expect(within(card).getByText(formatKrw(usdToDisplayKrw("10240.55")))).toBeInTheDocument();
    expect(within(card).getByText("+$120.25")).toBeInTheDocument();            // 오늘 손익 USD
    expect(within(card).getAllByText(/^\+₩/).length).toBeGreaterThan(1);       // 손익의 KRW 보조선
    expect(within(card).getByText("누적 수익")).toBeInTheDocument();            // 상세 화면과 같은 용어
  });

  it("uses Strategy A's own fixed rate, never a number typed into a component", async () => {
    const { FX_CONFIG } = await import("../lib/fx");
    const { formatKrw, usdToDisplayKrw } = await import("../lib/format");
    render(<StrategyCard bundle={bundle()}/>);
    const card = screen.getByText("Strategy A").closest("[data-strategy]") as HTMLElement;
    expect(within(card).getByText(formatKrw(10000 * FX_CONFIG.usdKrw))).toBeInTheDocument();
    for (const path of ["components/strategy-runtime.tsx", "app/strategy-e/page.tsx",
                        "components/daily-performance.tsx"]) {
      expect(readFileSync(path, "utf8"), path).not.toMatch(/1,?346(\.\d+)?/);
    }
  });

  it("colours a gain, a loss and a flat day with the existing tone classes", () => {
    const gain = bundle();
    const { unmount } = render(<StrategyCard bundle={gain}/>);
    expect(screen.getByText("+$120.25").closest("span")?.className).toContain("text-success");
    unmount();
    const loss = bundle({ account: { ...bundle().account, today_pnl: "-45.10", total_pnl: "-90.20" } });
    const second = render(<StrategyCard bundle={loss}/>);
    expect(screen.getByText("-$45.10").closest("span")?.className).toContain("text-danger");
    second.unmount();
    const flat = bundle({ account: { ...bundle().account, today_pnl: "0", total_pnl: "0" } });
    render(<StrategyCard bundle={flat}/>);
    expect(screen.getAllByText("+$0.00")[0].closest("span")?.className).toContain("text-foreground-secondary");
  });

  it("keeps a dash for Strategy E while its book holds no session", () => {
    render(<StrategyCard bundle={eBundle()}/>);
    const card = screen.getByText("Strategy E-MAX V1").closest("[data-strategy]") as HTMLElement;
    expect(within(card).queryByText(/₩0$/)).not.toBeInTheDocument();           // no converted zero
    expect(within(card).queryByText("+$0.00")).not.toBeInTheDocument();
    expect(within(card).getAllByText("-").length).toBeGreaterThan(1);
    expect(within(card).getByText(/RVOL 부트스트랩 중/)).toBeInTheDocument();
  });

  it("shows USD and KRW for Strategy E once its own book has a session", async () => {
    const { formatKrw, usdToDisplayKrw } = await import("../lib/format");
    const settled = eBundle({ account: { ...eBundle().account, current_equity: "10120.00",
      today_pnl: "120.00", total_pnl: "130.00", empty_reason: null,
      books: { [PROVISIONAL]: { present: true, initial_equity: "10000", equity: "10120.00",
                                realized_pnl: "120.00", sessions: 1 },
               [OFFICIAL]: { present: false, initial_equity: null, equity: null,
                             realized_pnl: null, sessions: 0 } } } });
    render(<StrategyCard bundle={settled}/>);
    const card = screen.getByText("Strategy E-MAX V1").closest("[data-strategy]") as HTMLElement;
    expect(within(card).getByText("$10,120.00")).toBeInTheDocument();
    expect(within(card).getByText(formatKrw(usdToDisplayKrw("10120.00")))).toBeInTheDocument();
    expect(within(card).getByText("+$120.00")).toBeInTheDocument();            // 오늘 손익
    expect(within(card).getByText("+$130.00")).toBeInTheDocument();            // 누적 수익
    expect(within(card).getByText("+1.30%")).toBeInTheDocument();              // 130 / 10,000 baseline
    expect(within(card).getByText("+$130.00").className).toContain("text-success");
  });

  it("reads only the active book, so provisional and official never add up", () => {
    const both = eBundle({ account: { ...eBundle().account, evidence_status: OFFICIAL,
      current_equity: "9950.00", today_pnl: "-20.00", total_pnl: "-50.00", empty_reason: null,
      books: { [PROVISIONAL]: { present: true, initial_equity: "10000", equity: "10120.00",
                                realized_pnl: "120.00", sessions: 3 },
               [OFFICIAL]: { present: true, initial_equity: "10000", equity: "9950.00",
                             realized_pnl: "-50.00", sessions: 1 } } } });
    render(<StrategyCard bundle={both}/>);
    const card = screen.getByText("Strategy E-MAX V1").closest("[data-strategy]") as HTMLElement;
    expect(within(card).getByText("-$50.00")).toBeInTheDocument();
    expect(within(card).queryByText("+$70.00")).not.toBeInTheDocument();       // 120 - 50 never appears
    expect(within(card).queryByText("+$120.00")).not.toBeInTheDocument();
  });
});
