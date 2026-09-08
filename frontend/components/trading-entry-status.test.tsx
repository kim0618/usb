import React from "react";
import { readFileSync } from "node:fs";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Dashboard, ResearchAnalysis, ResearchCandidate, StrategyState, TradingOverview } from "@/types/api";

vi.mock("next/navigation", () => ({ usePathname: () => "/trading" }));
const tradingApi = vi.fn();
const dashboardApi = vi.fn();
const researchApi = vi.fn();
vi.mock("@/lib/api", () => ({
  api: {
    trading: () => tradingApi(), dashboard: () => dashboardApi(), research: () => researchApi(),
    orders: async () => [], fills: async () => [], trades: async () => [],
    dailyPerformance: async () => [],
  },
  ApiError: class ApiError extends Error { constructor(public status: number) { super("api"); } },
}));

import TradingPage from "@/app/trading/page";

/** Production truth: 09/08 entry session consuming the 09/04 analysis. */
const ENTRY_SESSION = "2026-09-08";
const ANALYSIS_SESSION = "2026-09-04";

const candidate = (symbol: string): ResearchCandidate => ({
  scanner_candidate_id: 1, symbol, quant_rank: 1, gpt_rank: 1, quant_score: 1, overall_score: 80,
  catalyst_score: 80, fundamental_score: 80, momentum_score: 80, risk_score: 60,
  evidence_confidence: 60, catalyst_duration: "ONE_TO_TWO_DAYS", stop_profile: "NORMAL",
  trailing_profile: "NORMAL", overnight_suitability: "MEDIUM", company_summary: "",
  catalyst_summary: "", risk_summary: "", invalidation_summary: "", unknown_fields: [],
  human_decision: { symbol, decision: "APPROVE", note: null, decided_at: `${ANALYSIS_SESSION}T20:00:00Z` },
});

const state = (symbol: string, phase: string, phase_reason: string | null = null): StrategyState => ({
  symbol, trading_date: ENTRY_SESSION, book: "ACTUAL", variant: "ACTUAL", phase, phase_reason,
  entry_price: null, initial_stop: null, active_stop: null, highest_price: null,
  add_count: 0, holding_day: 0, overnight: false,
});

const research = (): ResearchAnalysis => ({
  analysis: {
    id: 2, scanner_run_id: 1, trading_date: ANALYSIS_SESSION, analysis_at: `${ANALYSIS_SESSION}T20:00:00Z`,
    imported_at: `${ANALYSIS_SESSION}T20:05:00Z`, provider: "gpt", model: "m", prompt_version: "1",
    schema_version: "1", evidence_version: "1", status: "IMPORTED",
  },
  candidates: [candidate("NVDA"), candidate("AAPL")],
});

const dashboard = () => ({
  system_time: `${ENTRY_SESSION}T14:00:00Z`,
  market: { trading_date: ENTRY_SESSION, session: "REGULAR", is_trading_day: true, market_open: null, market_close: null },
  runtime: { mode: "NORMAL", healthy: true, last_heartbeat_at: null, unresolved_failure_count: 0, last_failure: null },
  scanner: { latest_run_id: 1, trading_date: ANALYSIS_SESSION, completed_at: null, candidate_count: 8, top8_count: 8 },
  research: { latest_analysis_id: 2, analysis_at: null, approved_count: 2 },
  trading: { broker_mode: "SIMULATION", open_positions_count: 0, open_orders_count: 0, paper_started_at: null },
  shadow: { recent_result_count: 0 },
} as unknown as Dashboard);

const overview = (strategy_states: StrategyState[] = [], openSymbols: string[] = []): TradingOverview => ({
  broker_mode: "SIMULATION", availability: "OK",
  account: { currency: "USD", equity: "10000", cash: "10000", invested_notional: "0", unrealized_pnl: "0", realized_pnl: "0", today_pnl: "0" },
  open_positions: openSymbols.map(symbol => ({ symbol, currency: "USD", quantity: "1" })),
  open_orders: [], strategy_states,
});

const section = async () => (await screen.findByRole("heading", { name: "진입 평가" })).closest("section") as HTMLElement;

beforeEach(() => {
  vi.clearAllMocks();
  tradingApi.mockResolvedValue(overview());
  dashboardApi.mockResolvedValue(dashboard());
  researchApi.mockResolvedValue(research());
});
afterEach(cleanup);

describe("entry status reflects persisted StrategyState", () => {
  it("shows an approved symbol with no StrategyState as awaiting evaluation", async () => {
    render(<TradingPage/>);
    const view = await section();
    await waitFor(() => expect(within(view).getAllByText("승인 · 정규장 진입 평가 대기")).toHaveLength(2));
    expect(within(view).queryByText(/진입 제외/)).toBeNull();
  });

  it("shows a premarket rejection with its Korean reason instead of 진입 평가 대기", async () => {
    tradingApi.mockResolvedValue(overview([
      state("NVDA", "PREMARKET_REJECTED", "LOW_PREMARKET_VOLUME"),
      state("AAPL", "PREMARKET_REJECTED", "GAP_TOO_LOW"),
    ]));
    render(<TradingPage/>);
    const view = await section();
    await waitFor(() => expect(within(view).getAllByText("진입 제외 · 프리마켓 조건 미충족")).toHaveLength(2));
    expect(within(view).getByText("사유: 프리마켓 거래량 부족")).toBeInTheDocument();
    expect(within(view).getByText("사유: 갭 상승폭 기준 미달")).toBeInTheDocument();
    expect(within(view).queryByText("승인 · 정규장 진입 평가 대기")).toBeNull();
  });

  it("says no reason was recorded for rows written before the reason contract", async () => {
    tradingApi.mockResolvedValue(overview([state("NVDA", "PREMARKET_REJECTED", null)]));
    render(<TradingPage/>);
    const view = await section();
    await waitFor(() => expect(within(view).getByText("사유: 상세 사유 기록 없음")).toBeInTheDocument());
  });

  it.each([
    ["GAP_TOO_HIGH", "갭 상승폭 과다"],
    ["INVALID_PREMARKET_DATA", "프리마켓 데이터 부족/이상"],
    ["NEGATIVE_CATALYST", "신규 악재 발생"],
    ["RESEARCH_BLOCKED", "리서치 차단"],
    ["HUMAN_NOT_APPROVED", "사람 승인 없음"],
  ])("maps %s to its Korean label", async (reason, label) => {
    tradingApi.mockResolvedValue(overview([state("NVDA", "PREMARKET_REJECTED", reason)]));
    render(<TradingPage/>);
    const view = await section();
    await waitFor(() => expect(within(view).getByText(`사유: ${label}`)).toBeInTheDocument());
  });

  it("never renders a raw enum code on screen", async () => {
    tradingApi.mockResolvedValue(overview([state("NVDA", "PREMARKET_REJECTED", "LOW_PREMARKET_VOLUME")]));
    render(<TradingPage/>);
    const view = await section();
    await waitFor(() => expect(within(view).getByText(/사유:/)).toBeInTheDocument());
    expect(view.textContent).not.toContain("LOW_PREMARKET_VOLUME");
    expect(view.textContent).not.toContain("PREMARKET_REJECTED");
  });

  it("shows a neutral phase label for a non-terminal evaluation in progress", async () => {
    tradingApi.mockResolvedValue(overview([state("NVDA", "WAITING_ENTRY", null)]));
    render(<TradingPage/>);
    const view = await section();
    await waitFor(() => expect(within(view).getByText("진입 대기")).toBeInTheDocument());
    expect(within(view).queryByText("진입 제외 · 프리마켓 조건 미충족")).toBeNull();
  });

  it("keeps open-position exclusion and the analysis date label", async () => {
    tradingApi.mockResolvedValue(overview([state("NVDA", "PREMARKET_REJECTED", "GAP_TOO_LOW")], ["NVDA"]));
    render(<TradingPage/>);
    const view = await section();
    await waitFor(() => expect(within(view).getByText("09/04 (금) 분석 기준")).toBeInTheDocument());
    expect(within(view).queryByText("NVDA")).toBeNull();
    expect(within(view).getByText("AAPL")).toBeInTheDocument();
  });

  it("reads persisted state only and never recomputes premarket conditions", () => {
    const source = readFileSync("app/trading/page.tsx", "utf8");
    expect(source).toContain("o.strategy_states.find");
    ["gap_pct", "volume_ratio", "premarket_gap", "getMinuteBars", "kiwoom"].forEach(token =>
      expect(source).not.toContain(token));
  });
});
