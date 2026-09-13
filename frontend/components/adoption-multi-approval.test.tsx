import React from "react";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { AdoptionItem, AdoptionResponse, ResearchDetail } from "@/types/api";

vi.mock("next/navigation", () => ({ usePathname: () => "/adoption" }));
const adoptionApi = vi.fn();
const detailApi = vi.fn();
vi.mock("@/lib/api", () => ({
  api: { adoption: () => adoptionApi(), researchDetail: (id: number, symbol: string) => detailApi(id, symbol) },
  ApiError: class ApiError extends Error { constructor(public status: number) { super("api"); } },
}));

import AdoptionPage from "@/app/adoption/page";

const item = (rank: number, symbol: string, approved: boolean): AdoptionItem => ({
  scanner_candidate_id: rank, symbol, quant_rank: rank, gpt_rank: rank, quant_score: 1,
  overall_score: 80, catalyst_score: 80, fundamental_score: 80, momentum_score: 80, risk_score: 60,
  evidence_confidence: 60, catalyst_duration: "ONE_TO_TWO_DAYS", stop_profile: "NORMAL",
  trailing_profile: "NORMAL", overnight_suitability: "MEDIUM", company_summary: `${symbol} 회사 설명`,
  catalyst_summary: "촉매", risk_summary: "위험", invalidation_summary: "무효화", unknown_fields: [],
  human_decision: approved ? { symbol, decision: "APPROVE", note: null, decided_at: "2026-09-13T00:00:00Z" } : null,
  company_name: `${symbol} INC`, exchange: "NASDAQ", industry: null, market_cap: null, previous_open: null,
  previous_high: null, previous_low: null, previous_close: null, previous_return_pct: null, previous_volume: null,
  rvol: 1.5, analysis_id: 7, scanner_run_id: 2, recommendation_rank: rank, evidence_score: 60,
  relative_strength: null, momentum: null, classification: "ADOPTION_CANDIDATE", rank_delta: 0,
  rank_delta_label: "유지", rank_direction: "UNCHANGED", strengths: [], warnings: [], rank_explanation: "설명.",
});

const RESPONSE: AdoptionResponse = {
  filter_version: "adoption_filter_v0", analysis_id: 7, scanner_run_id: 2, trading_date: "2026-09-11",
  counts: { adoption_candidate: 4, review_required: 0, excluded: 0 },
  items: [item(1, "AAA", true), item(2, "BBB", true), item(3, "CCC", true), item(4, "DDD", false)],
};

const detail = (symbol: string): ResearchDetail => ({
  ...RESPONSE.items.find(row => row.symbol === symbol)!,
  sources: [], premarket: { price: null, return_pct: null, observed_at: null, reason: "NOT_FETCHED" },
  postmarket: { price: null, return_pct: null, observed_at: null, reason: "NOT_FETCHED" },
  market_cap_reason: "UNIT_UNCONFIRMED", industry_reason: "NOT_AVAILABLE_FROM_PROVIDER",
});

describe("approval count is not capped", () => {
  beforeEach(() => {
    adoptionApi.mockReset(); detailApi.mockReset();
    adoptionApi.mockResolvedValue(RESPONSE); detailApi.mockImplementation(async (_id: number, symbol: string) => detail(symbol));
  });
  afterEach(cleanup);

  it("keeps APPROVE available for a fourth symbol after three approvals", async () => {
    render(<AdoptionPage/>);
    await waitFor(() => expect(screen.getByText(/최종 채택 3개/)).toBeInTheDocument());
    expect(screen.getByText(/하루 최대 3종목입니다/)).toBeInTheDocument();
    await act(async () => { fireEvent.click(screen.getAllByRole("button", { name: "상세보기" })[3]); });
    await waitFor(() => expect(detailApi).toHaveBeenCalledWith(7, "DDD"));
    await waitFor(() => expect(screen.getByRole("button", { name: "채택" })).toBeEnabled());
  });
});
