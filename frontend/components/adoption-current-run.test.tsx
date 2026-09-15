import React from "react";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ToastProvider } from "@/components/toast";
import type { AdoptionItem, AdoptionResponse, ResearchCurrentAuthority } from "@/types/api";

vi.mock("next/navigation", () => ({ usePathname: () => "/adoption" }));
const adoptionApi = vi.fn(); const currentApi = vi.fn();
vi.mock("@/lib/api", () => ({
  api: { adoption: () => adoptionApi(), researchCurrent: () => currentApi(), researchDetail: vi.fn(), decide: vi.fn() },
  ApiError: class ApiError extends Error { constructor(public status: number, public code: string, message: string) { super(message); } },
}));

import AdoptionPage from "@/app/adoption/page";
import { ApiError } from "@/lib/api";

const authority = (status: ResearchCurrentAuthority["status"], extra: Partial<ResearchCurrentAuthority> = {}): ResearchCurrentAuthority => ({
  status, scanner_run_id: 8, trading_date: "2026-09-14", completed_at: null, active_analysis_id: null, analysis_at: null, approved_count: 0, ...extra,
});

const item = (symbol: string, decision: "APPROVE" | "REJECT" | null): AdoptionItem => ({
  scanner_candidate_id: 1, symbol, quant_rank: 1, gpt_rank: 1, quant_score: 1, overall_score: 80, catalyst_score: 80,
  fundamental_score: 80, momentum_score: 80, risk_score: 60, evidence_confidence: 60, catalyst_duration: "ONE_TO_TWO_DAYS",
  stop_profile: "NORMAL", trailing_profile: "NORMAL", overnight_suitability: "MEDIUM", company_summary: "", catalyst_summary: "",
  risk_summary: "", invalidation_summary: "", unknown_fields: [],
  human_decision: decision ? { symbol, decision, note: null, decided_at: "2026-09-15T00:00:00Z" } : null,
  company_name: null, exchange: null, industry: null, market_cap: null, previous_open: null, previous_high: null, previous_low: null,
  previous_close: null, previous_return_pct: null, previous_volume: null, rvol: null, analysis_id: 7, scanner_run_id: 8,
  recommendation_rank: 1, evidence_score: 60, relative_strength: null, momentum: null, classification: "ADOPTION_CANDIDATE",
  rank_delta: 0, rank_delta_label: "유지", rank_direction: "UNCHANGED", strengths: [], warnings: [], rank_explanation: "",
});

const adoption = (items: AdoptionItem[]): AdoptionResponse => ({
  filter_version: "adoption_filter_v0", analysis_id: 7, scanner_run_id: 8, trading_date: "2026-09-14",
  counts: { adoption_candidate: items.length, review_required: 0, excluded: 0 }, items,
});

const renderPage = () => render(<ToastProvider><AdoptionPage/></ToastProvider>);

beforeEach(() => { vi.clearAllMocks(); });
afterEach(cleanup);

describe("adoption follows the current ScannerRun only", () => {
  it("shows no current adoption candidate when the current run has no analysis", async () => {
    adoptionApi.mockRejectedValue(new ApiError(404, "RESOURCE_NOT_FOUND", "Active research analysis not found"));
    currentApi.mockResolvedValue(authority("NO_ACTIVE_ANALYSIS"));
    renderPage();
    expect(await screen.findByText("현재 채택 후보가 없습니다.")).toBeInTheDocument();
    expect(await screen.findByText(/현재 ScannerRun\(09\/14\)의 GPT 분석이 아직 없습니다/)).toBeInTheDocument();
    expect(screen.queryByRole("table")).toBeNull();
  });

  it("separates a missing ScannerRun", async () => {
    adoptionApi.mockRejectedValue(new ApiError(404, "RESOURCE_NOT_FOUND", "Active research analysis not found"));
    currentApi.mockResolvedValue(authority("NO_SCANNER_RUN", { scanner_run_id: null, trading_date: null }));
    renderPage();
    expect(await screen.findByText("오늘 스캐너 실행 결과가 없습니다.")).toBeInTheDocument();
  });

  it("keeps the review list but says no decision exists yet for the current analysis", async () => {
    adoptionApi.mockResolvedValue(adoption([item("GOOGL", null), item("META", null)]));
    currentApi.mockResolvedValue(authority("NO_APPROVALS", { active_analysis_id: 7 }));
    renderPage();
    expect(await screen.findByText(/현재 분석에 대한 채택 결정이 아직 없습니다/)).toBeInTheDocument();
    expect(screen.getByText("현재 ScannerRun 분석 기준 · 09/14 분석 기준 · Analysis #7")).toBeInTheDocument();
    expect(screen.getByText("GOOGL")).toBeInTheDocument();
  });

  it("says no symbol was adopted when every decision is a rejection", async () => {
    adoptionApi.mockResolvedValue(adoption([item("GOOGL", "REJECT")]));
    currentApi.mockResolvedValue(authority("NO_APPROVALS", { active_analysis_id: 7 }));
    renderPage();
    expect(await screen.findByText("현재 분석에서 채택된 종목이 없습니다.")).toBeInTheDocument();
  });

  it("shows no notice once the current analysis has an APPROVE", async () => {
    adoptionApi.mockResolvedValue(adoption([item("GOOGL", "APPROVE")]));
    currentApi.mockResolvedValue(authority("READY", { active_analysis_id: 7, approved_count: 1 }));
    renderPage();
    await screen.findByText("GOOGL");
    expect(screen.queryByText(/채택 결정이 아직 없습니다/)).toBeNull();
    expect(screen.queryByText("현재 분석에서 채택된 종목이 없습니다.")).toBeNull();
  });
});
