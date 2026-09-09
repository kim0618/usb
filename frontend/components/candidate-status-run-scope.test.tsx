import React from "react";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Candidate, ResearchAnalysis, ResearchCandidate, ScannerSnapshot } from "@/types/api";

vi.mock("next/navigation", () => ({ usePathname: () => "/candidates" }));
const scannerApi = vi.fn();
const researchApi = vi.fn();
vi.mock("@/lib/api", () => ({
  api: { scanner: () => scannerApi(), research: () => researchApi(), prompt: async () => ({ prompt: "", prompt_version: "1" }) },
  ApiError: class ApiError extends Error { constructor(public status: number) { super("api"); } },
}));

import CandidatesPage from "@/app/candidates/page";

/** Production shape: run 1 = 09/04 (analysed, NVDA approved), run 2 = 09/08 (not analysed). */
const RUN1 = 1, RUN2 = 2;
const TOP8 = ["INTC", "AMD", "MU", "TSLA", "META", "SPCX", "NVDA", "AVGO"];

const candidate = (symbol: string, rank: number): Candidate => ({
  symbol, rank, quant_score: 0.5, latest_close: 100, latest_volume: 1_000_000,
  company_name: null, is_top8: true,
  raw_metrics: { momentum: 0.01, rvol: 1.2, relative_strength: 0.01, dollar_volume: 1e8 },
  normalized_metrics: {}, contributions: {},
} as unknown as Candidate);

const snapshot = (runId: number, tradingDate: string, symbols: string[]): ScannerSnapshot => ({
  run: {
    id: runId, trading_date: tradingDate, started_at: `${tradingDate}T20:00:00Z`,
    completed_at: `${tradingDate}T20:00:01Z`, status: "COMPLETED", provider: "KIWOOM_REAL",
    score_version: "quant_v0", universe_count: 10, excluded_count: 1,
    candidate_count: symbols.length, top8_count: symbols.length,
  },
  candidate_pool_count: symbols.length,
  candidates: symbols.map((s, i) => candidate(s, i + 1)),
  top8: symbols.map((s, i) => candidate(s, i + 1)),
});

const researchCandidate = (symbol: string, decision: "APPROVE" | "REJECT" | null): ResearchCandidate => ({
  scanner_candidate_id: 1, symbol, quant_rank: 1, gpt_rank: 1, quant_score: 1, overall_score: 80,
  catalyst_score: 80, fundamental_score: 80, momentum_score: 80, risk_score: 60,
  evidence_confidence: 60, catalyst_duration: "ONE_TO_TWO_DAYS", stop_profile: "NORMAL",
  trailing_profile: "NORMAL", overnight_suitability: "MEDIUM", company_summary: "",
  catalyst_summary: "", risk_summary: "", invalidation_summary: "", unknown_fields: [],
  human_decision: decision === null ? null : { symbol, decision, note: null, decided_at: "2026-09-04T20:00:00Z" },
});

const analysis = (runId: number, tradingDate: string, candidates: ResearchCandidate[]): ResearchAnalysis => ({
  analysis: {
    id: runId === RUN1 ? 2 : 9, scanner_run_id: runId, trading_date: tradingDate,
    analysis_at: `${tradingDate}T20:00:00Z`, imported_at: `${tradingDate}T20:05:00Z`,
    provider: "gpt", model: "m", prompt_version: "1", schema_version: "1",
    evidence_version: "1", status: "IMPORTED",
  },
  candidates,
});

/** Run 1's analysis: TSLA/META analysed, NVDA approved, AAPL approved. */
const RUN1_ANALYSIS = analysis(RUN1, "2026-09-04", [
  researchCandidate("TSLA", null), researchCandidate("META", null),
  researchCandidate("NVDA", "APPROVE"), researchCandidate("AAPL", "APPROVE"),
]);

const statusOf = (symbol: string) => {
  const row = screen.getByText(symbol).closest("tr") as HTMLElement;
  return within(row).getAllByText(/분석 전|분석 완료|채택|거절/)[0].textContent;
};

beforeEach(() => {
  vi.clearAllMocks();
  scannerApi.mockResolvedValue(snapshot(RUN2, "2026-09-08", TOP8));
  researchApi.mockResolvedValue(RUN1_ANALYSIS);
});
afterEach(cleanup);

describe("candidate analysis status is scoped to the current ScannerRun", () => {
  it("shows every 09/08 Top8 symbol as 분석 전 while run 2 has no analysis", async () => {
    render(<CandidatesPage/>);
    await waitFor(() => expect(screen.getByText("INTC")).toBeInTheDocument());
    TOP8.forEach(symbol => expect(statusOf(symbol)).toBe("분석 전"));
  });

  it("does not leak run 1's NVDA APPROVE into run 2", async () => {
    render(<CandidatesPage/>);
    await waitFor(() => expect(screen.getByText("NVDA")).toBeInTheDocument());
    expect(statusOf("NVDA")).toBe("분석 전");
    expect(screen.queryByText("채택")).toBeNull();
  });

  it("does not leak run 1's analysed TSLA/META into run 2", async () => {
    render(<CandidatesPage/>);
    await waitFor(() => expect(screen.getByText("TSLA")).toBeInTheDocument());
    expect(statusOf("TSLA")).toBe("분석 전");
    expect(statusOf("META")).toBe("분석 전");
    expect(screen.queryByText("분석 완료")).toBeNull();
  });

  it("marks a symbol 분석 완료 once THIS run carries the analysis", async () => {
    researchApi.mockResolvedValue(analysis(RUN2, "2026-09-08", [
      researchCandidate("INTC", null), researchCandidate("AMD", null),
    ]));
    render(<CandidatesPage/>);
    await waitFor(() => expect(screen.getByText("INTC")).toBeInTheDocument());
    expect(statusOf("INTC")).toBe("분석 완료");
    expect(statusOf("AMD")).toBe("분석 완료");
    expect(statusOf("MU")).toBe("분석 전");
  });

  it("marks a symbol 채택 only from THIS run's HumanDecision", async () => {
    researchApi.mockResolvedValue(analysis(RUN2, "2026-09-08", [
      researchCandidate("INTC", "APPROVE"), researchCandidate("AMD", "REJECT"),
    ]));
    render(<CandidatesPage/>);
    await waitFor(() => expect(screen.getByText("INTC")).toBeInTheDocument());
    expect(statusOf("INTC")).toBe("채택");
    expect(statusOf("AMD")).toBe("거절");
    expect(statusOf("NVDA")).toBe("분석 전");
  });

  it("still shows status when scanner run and analysis run agree (no over-blocking)", async () => {
    scannerApi.mockResolvedValue(snapshot(RUN1, "2026-09-04", ["TSLA", "META", "NVDA"]));
    researchApi.mockResolvedValue(RUN1_ANALYSIS);
    render(<CandidatesPage/>);
    await waitFor(() => expect(screen.getByText("NVDA")).toBeInTheDocument());
    expect(statusOf("NVDA")).toBe("채택");
    expect(statusOf("TSLA")).toBe("분석 완료");
  });
});
