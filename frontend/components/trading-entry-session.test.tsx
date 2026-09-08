import React from "react";
import { readFileSync } from "node:fs";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Dashboard, ResearchAnalysis, ResearchCandidate, TradingOverview } from "@/types/api";

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

const source = () => readFileSync("app/trading/page.tsx", "utf8");

/** Production shape: the 09/08 entry session consumes the 09/04 analysis. */
const ENTRY_SESSION = "2026-09-08";
const ANALYSIS_SESSION = "2026-09-04";

const candidate = (symbol: string, decision: "APPROVE" | "REJECT" | null): ResearchCandidate => ({
  scanner_candidate_id: 1, symbol, quant_rank: 1, gpt_rank: 1, quant_score: 1, overall_score: 80,
  catalyst_score: 80, fundamental_score: 80, momentum_score: 80, risk_score: 60,
  evidence_confidence: 60, catalyst_duration: "ONE_TO_TWO_DAYS", stop_profile: "NORMAL",
  trailing_profile: "NORMAL", overnight_suitability: "MEDIUM", company_summary: "",
  catalyst_summary: "", risk_summary: "", invalidation_summary: "", unknown_fields: [],
  human_decision: decision === null
    ? null
    : { symbol, decision, note: null, decided_at: `${ANALYSIS_SESSION}T20:00:00Z` },
});

const research = (candidates: ResearchCandidate[], trading_date = ANALYSIS_SESSION): ResearchAnalysis => ({
  analysis: {
    id: 2, scanner_run_id: 1, trading_date, analysis_at: `${trading_date}T20:00:00Z`,
    imported_at: `${trading_date}T20:05:00Z`, provider: "gpt", model: "m", prompt_version: "1",
    schema_version: "1", evidence_version: "1", status: "IMPORTED",
  },
  candidates,
});

const dashboard = (trading_date = ENTRY_SESSION) => ({
  system_time: `${trading_date}T14:00:00Z`,
  market: { trading_date, session: "REGULAR", is_trading_day: true, market_open: null, market_close: null },
  runtime: { mode: "NORMAL", healthy: true, last_heartbeat_at: null, unresolved_failure_count: 0, last_failure: null },
  scanner: { latest_run_id: 1, trading_date: ANALYSIS_SESSION, completed_at: null, candidate_count: 8, top8_count: 8 },
  research: { latest_analysis_id: 2, analysis_at: null, approved_count: 2 },
  trading: { broker_mode: "SIMULATION", open_positions_count: 0, open_orders_count: 0, paper_started_at: null },
  shadow: { recent_result_count: 0 },
} as unknown as Dashboard);

const overview = (openSymbols: string[] = []): TradingOverview => ({
  broker_mode: "SIMULATION", availability: "OK",
  account: { currency: "USD", equity: "10000", cash: "10000", invested_notional: "0", unrealized_pnl: "0", realized_pnl: "0", today_pnl: "0" },
  open_positions: openSymbols.map(symbol => ({ symbol, currency: "USD", quantity: "1" })),
  open_orders: [], strategy_states: [],
});

const waitingSection = async () => {
  const heading = await screen.findByRole("heading", { name: "진입 평가" });
  return heading.closest("section") as HTMLElement;
};

beforeEach(() => {
  vi.clearAllMocks();
  tradingApi.mockResolvedValue(overview());
  dashboardApi.mockResolvedValue(dashboard());
  researchApi.mockResolvedValue(research([candidate("NVDA", "APPROVE"), candidate("AAPL", "APPROVE")]));
});
afterEach(cleanup);

describe("entry session consumes the previous session's analysis", () => {
  it("shows APPROVE candidates even though the analysis date is not the current session date", async () => {
    render(<TradingPage/>);
    const section = await waitingSection();
    await waitFor(() => expect(within(section).getByText("NVDA")).toBeInTheDocument());
    expect(within(section).getByText("AAPL")).toBeInTheDocument();
    expect(within(section).queryByText("승인 후 아직 진입하지 않은 종목이 없습니다.")).toBeNull();
  });

  it("labels which session's analysis the waiting list came from", async () => {
    render(<TradingPage/>);
    const section = await waitingSection();
    await waitFor(() => expect(within(section).getByText("09/04 (금) 분석 기준")).toBeInTheDocument());
  });

  it("never gates the list on research trading_date matching the current market date", () => {
    const trading = source();
    expect(trading).not.toContain("researchIsToday");
    expect(trading).not.toContain("research.data?.analysis.trading_date === dashboard.data.market.trading_date");
  });

  it("excludes symbols that already hold an open position", async () => {
    tradingApi.mockResolvedValue(overview(["NVDA"]));
    render(<TradingPage/>);
    const section = await waitingSection();
    await waitFor(() => expect(within(section).getByText("AAPL")).toBeInTheDocument());
    expect(within(section).queryByText("NVDA")).toBeNull();
  });

  it("keeps the two-card display cap and shows only approved candidates", async () => {
    researchApi.mockResolvedValue(research([
      candidate("NVDA", "APPROVE"), candidate("AAPL", "APPROVE"), candidate("TSLA", "APPROVE"),
      candidate("MSFT", "REJECT"), candidate("AMD", null),
    ]));
    render(<TradingPage/>);
    const section = await waitingSection();
    await waitFor(() => expect(within(section).getAllByText("승인 · 정규장 진입 평가 대기")).toHaveLength(2));
    ["MSFT", "AMD", "TSLA"].forEach(symbol => expect(within(section).queryByText(symbol)).toBeNull());
  });

  it("says an approval is still awaiting strategy and risk evaluation, never a planned buy", async () => {
    render(<TradingPage/>);
    const section = await waitingSection();
    await waitFor(() => expect(within(section).getAllByText("승인 · 정규장 진입 평가 대기")).toHaveLength(2));
    ["매수 예정", "매수 확정"].forEach(text => expect(source()).not.toContain(text));
  });

  it("keeps the empty state when no approval is outstanding", async () => {
    researchApi.mockResolvedValue(research([candidate("MSFT", "REJECT")]));
    render(<TradingPage/>);
    const section = await waitingSection();
    await waitFor(() =>
      expect(within(section).getByText("승인 후 아직 진입하지 않은 종목이 없습니다.")).toBeInTheDocument());
  });
});
