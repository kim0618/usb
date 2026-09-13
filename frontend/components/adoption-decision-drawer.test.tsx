import React from "react";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { AdoptionItem, AdoptionResponse, Decision, ResearchDetail } from "@/types/api";

vi.mock("next/navigation", () => ({ usePathname: () => "/adoption" }));
const adoptionApi = vi.fn();
const detailApi = vi.fn();
const decideApi = vi.fn();
vi.mock("@/lib/api", () => ({
  api: {
    adoption: () => adoptionApi(), researchDetail: (id: number, symbol: string) => detailApi(id, symbol),
    decide: (id: number, symbol: string, decision: string) => decideApi(id, symbol, decision),
  },
  ApiError: class ApiError extends Error { constructor(public status: number) { super("api"); } },
}));

import AdoptionPage from "@/app/adoption/page";
import { ToastProvider } from "@/components/toast";
import { formatDecisionStatus } from "@/lib/display";

const item = (decision: Decision | null): AdoptionItem => ({
  scanner_candidate_id: 1, symbol: "AAA", quant_rank: 1, gpt_rank: 1, quant_score: 1,
  overall_score: 80, catalyst_score: 80, fundamental_score: 80, momentum_score: 80, risk_score: 60,
  evidence_confidence: 60, catalyst_duration: "ONE_TO_TWO_DAYS", stop_profile: "NORMAL",
  trailing_profile: "NORMAL", overnight_suitability: "MEDIUM", company_summary: "AAA 회사 설명",
  catalyst_summary: "촉매", risk_summary: "위험", invalidation_summary: "무효화", unknown_fields: [],
  human_decision: decision ? { symbol: "AAA", decision, note: null, decided_at: "2026-09-13T00:00:00Z" } : null,
  company_name: "AAA INC", exchange: "NASDAQ", industry: null, market_cap: null, previous_open: null,
  previous_high: null, previous_low: null, previous_close: null, previous_return_pct: null, previous_volume: null,
  rvol: 1.5, analysis_id: 7, scanner_run_id: 2, recommendation_rank: 1, evidence_score: 60,
  relative_strength: null, momentum: null, classification: "ADOPTION_CANDIDATE", rank_delta: 0,
  rank_delta_label: "유지", rank_direction: "UNCHANGED", strengths: [], warnings: [], rank_explanation: "설명.",
});
const response = (decision: Decision | null): AdoptionResponse => ({
  filter_version: "adoption_filter_v0", analysis_id: 7, scanner_run_id: 2, trading_date: "2026-09-11",
  counts: { adoption_candidate: 1, review_required: 0, excluded: 0 }, items: [item(decision)],
});
const detail = (): ResearchDetail => ({
  ...item(null), sources: [],
  premarket: { price: null, return_pct: null, observed_at: null, reason: "NOT_FETCHED" },
  postmarket: { price: null, return_pct: null, observed_at: null, reason: "NOT_FETCHED" },
  market_cap_reason: "UNIT_UNCONFIRMED", industry_reason: "NOT_AVAILABLE_FROM_PROVIDER",
});
const TITLE = "AAA · 채택 검토";
const click = async (element: HTMLElement) => { await act(async () => { fireEvent.click(element); }); };
const deferred = <T,>() => { let resolve!: (value: T) => void; let reject!: (error: unknown) => void; const promise = new Promise<T>((ok, fail) => { resolve = ok; reject = fail; }); return { promise, resolve, reject }; };

async function openDrawer() {
  render(<ToastProvider><AdoptionPage/></ToastProvider>);
  await waitFor(() => expect(screen.getByRole("table")).toBeInTheDocument());
  await click(screen.getByRole("button", { name: "상세보기" }));
  await waitFor(() => expect(screen.getByRole("dialog", { name: TITLE })).toBeInTheDocument());
}
const decisionCell = () => within(screen.getAllByRole("row")[1]);

describe("decision drawer closes only after a saved decision", () => {
  beforeEach(() => { adoptionApi.mockReset(); detailApi.mockReset(); decideApi.mockReset(); detailApi.mockResolvedValue(detail()); });
  afterEach(cleanup);

  it.each([["채택", "APPROVE"], ["거절", "REJECT"]] as const)("closes after a successful %s and shows the refreshed decision", async (label, decision) => {
    adoptionApi.mockResolvedValueOnce(response(null)).mockResolvedValue(response(decision));
    decideApi.mockResolvedValue({ approved_count: decision === "APPROVE" ? 1 : 0 });
    await openDrawer();
    await click(within(screen.getByRole("dialog")).getByRole("button", { name: label }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(decideApi).toHaveBeenCalledWith(7, "AAA", decision);
    expect(decisionCell().getByText(formatDecisionStatus(decision))).toBeInTheDocument();
  });

  it("keeps the drawer open and shows the error when saving fails", async () => {
    adoptionApi.mockResolvedValue(response(null));
    decideApi.mockRejectedValue(new Error("결정 저장 서버 오류"));
    await openDrawer();
    await click(within(screen.getByRole("dialog")).getByRole("button", { name: "채택" }));
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("결정 저장 서버 오류"));
    expect(screen.getByRole("dialog", { name: TITLE })).toBeInTheDocument();
    expect(decisionCell().getByText(formatDecisionStatus(null))).toBeInTheDocument();
  });

  it("sends one request for a double click while the save is pending", async () => {
    adoptionApi.mockResolvedValueOnce(response(null)).mockResolvedValue(response("APPROVE"));
    const pending = deferred<{ approved_count: number }>();
    decideApi.mockReturnValue(pending.promise);
    await openDrawer();
    const approve = within(screen.getByRole("dialog")).getByRole("button", { name: "채택" });
    await click(approve); await click(approve);
    expect(decideApi).toHaveBeenCalledTimes(1);
    expect(approve).toBeDisabled();
    await act(async () => { pending.resolve({ approved_count: 1 }); });
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });

  it("does not reopen or repopulate the drawer when a stale detail response arrives", async () => {
    adoptionApi.mockResolvedValueOnce(response(null)).mockResolvedValue(response("APPROVE"));
    const late = deferred<ResearchDetail>();
    detailApi.mockReturnValue(late.promise);
    decideApi.mockResolvedValue({ approved_count: 1 });
    await openDrawer();
    await click(within(screen.getByRole("dialog")).getByRole("button", { name: "채택" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    await act(async () => { late.resolve(detail()); });
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(screen.queryByText("AAA 회사 설명")).toBeNull();
  });
});
