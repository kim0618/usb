import React from "react";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ToastProvider } from "@/components/toast";
import type { ResearchAnalysis, ResearchHistoryItem } from "@/types/api";

vi.mock("next/navigation", () => ({ usePathname: () => "/research" }));
const researchApi = vi.fn(); const historyApi = vi.fn(); const activateApi = vi.fn(); const importApi = vi.fn(); const currentApi = vi.fn();
vi.mock("@/lib/api", () => ({
  api: {
    research: () => researchApi(), researchHistory: () => historyApi(), researchCurrent: () => currentApi(),
    activateResearch: (id: number) => activateApi(id), importResearch: (raw: string) => importApi(raw),
  },
  ApiError: class ApiError extends Error { constructor(public status: number, public code: string, message: string) { super(message); } },
}));

import ResearchPage from "@/app/research/page";
import { ApiError } from "@/lib/api";

const candidate = (symbol: string, rank: number, decision: "APPROVE" | null = null) => ({
  scanner_candidate_id: rank, symbol, quant_rank: rank, gpt_rank: rank, quant_score: 1,
  overall_score: 80, catalyst_score: 80, fundamental_score: 80, momentum_score: 80,
  risk_score: 60, evidence_confidence: 70, catalyst_duration: "ONE_TO_TWO_DAYS",
  stop_profile: "NORMAL", trailing_profile: "WIDE", overnight_suitability: "MEDIUM",
  company_summary: "", catalyst_summary: "", risk_summary: "", invalidation_summary: "",
  unknown_fields: [], human_decision: decision ? { symbol, decision, note: null,
    decided_at: "2026-09-12T00:00:00Z" } : null,
});

const analysis = (id: number): ResearchAnalysis => ({
  analysis: { id, scanner_run_id: 5, trading_date: "2026-09-11",
    analysis_at: `2026-09-${id === 5 ? "12" : "13"}T01:00:00Z`, imported_at: `2026-09-${id === 5 ? "12" : "13"}T02:00:00Z`,
    provider: "GPT", model: "m", prompt_version: "p", schema_version: "s",
    evidence_version: "e", status: "IMPORTED", is_active: true },
  candidates: [candidate("AMD", 1, "APPROVE"), candidate("SPCX", 2, "APPROVE")],
});

const item = (id: number, run: number, active: boolean, approved: string[]): ResearchHistoryItem => ({
  id, scanner_run_id: run, trading_date: run === 5 ? "2026-09-11" : "2026-09-10",
  analysis_at: "2026-09-12T01:00:00Z", imported_at: "2026-09-12T02:00:00Z",
  provider: "GPT", model: "m", status: "IMPORTED", is_active: active,
  candidate_count: 2, approved_symbols: approved,
  candidates: [{ symbol: id === 6 ? "ORCL" : "AMD", gpt_rank: 1 }, { symbol: "SPCX", gpt_rank: 2 }],
});

let current: ResearchAnalysis; let history: ResearchHistoryItem[];
beforeEach(() => {
  vi.clearAllMocks(); current = analysis(5);
  history = [item(6, 5, false, []), item(5, 5, true, ["AMD", "SPCX"]), item(2, 4, true, ["OLD"])];
  researchApi.mockImplementation(async () => current);
  historyApi.mockImplementation(async () => history);
  currentApi.mockImplementation(async () => ({ status: "READY", scanner_run_id: 5, trading_date: "2026-09-11", completed_at: null,
    active_analysis_id: current.analysis.id, analysis_at: null, approved_count: 2 }));
  activateApi.mockImplementation(async (id: number) => {
    current = analysis(id); history = history.map(row => row.scanner_run_id === 5 ? { ...row, is_active: row.id === id } : row);
    return { analysis_id: id, scanner_run_id: 5, active: true };
  });
  importApi.mockResolvedValue({ analysis_id: 6, active_analysis_id: 5, activated: false, candidate_count: 2 });
});
afterEach(cleanup);

const renderPage = () => render(<ToastProvider><ResearchPage/></ToastProvider>);
const rowFor = (id: number) => screen.getByText(`Analysis #${id}`).closest("div.flex") as HTMLElement;

describe("research active-analysis authority UX", () => {
  it("shows current-run active/inactive badges and keeps other dates collapsed", async () => {
    renderPage(); await screen.findByText("09/11 분석 이력");
    expect(within(rowFor(5)).getByText("이 ScannerRun의 현재 활성 분석")).toBeInTheDocument();
    expect(within(rowFor(6)).getByText("비활성 분석")).toBeInTheDocument();
    const other = screen.getByText("이전 분석 이력 1건").closest("details") as HTMLDetailsElement;
    expect(other.open).toBe(false); expect(within(other).getByText("2026-09-10 ScannerRun 분석")).toBeInTheDocument();
    // A previous run's active analysis is history: no green current badge, no activation.
    expect(within(rowFor(2)).getByText("당시 거래 기준")).toBeInTheDocument();
    expect(within(rowFor(2)).queryByText("이 ScannerRun의 현재 활성 분석")).toBeNull();
    expect(within(rowFor(2)).queryByText("이 분석 사용")).toBeNull();
  });

  it("shows an empty current analysis for a new run and keeps previous analyses as history only", async () => {
    researchApi.mockRejectedValue(new ApiError(404, "RESOURCE_NOT_FOUND", "Active research analysis not found"));
    currentApi.mockResolvedValue({ status: "NO_ACTIVE_ANALYSIS", scanner_run_id: 6, trading_date: "2026-09-14", completed_at: null,
      active_analysis_id: null, analysis_at: null, approved_count: 0 });
    history = [item(6, 5, true, ["SPCX", "ORCL"]), item(5, 5, false, [])];
    renderPage();
    expect(await screen.findByText("현재 ScannerRun에 대한 GPT 분석이 아직 없습니다.")).toBeInTheDocument();
    expect(screen.getByText("현재 기준 거래일: 09/14 · 현재 GPT 분석: 없음")).toBeInTheDocument();
    expect(screen.queryByText(/현재 거래 기준 · Analysis #/)).toBeNull();
    expect(screen.queryByText("이 ScannerRun의 현재 활성 분석")).toBeNull();
    expect(screen.getByText("이전 분석 이력 2건")).toBeInTheDocument();
    expect(within(rowFor(6)).getByText("당시 거래 기준")).toBeInTheDocument();
  });

  it("separates a missing ScannerRun from a missing analysis", async () => {
    researchApi.mockRejectedValue(new ApiError(404, "RESOURCE_NOT_FOUND", "Active research analysis not found"));
    currentApi.mockResolvedValue({ status: "NO_SCANNER_RUN", scanner_run_id: null, trading_date: null, completed_at: null,
      active_analysis_id: null, analysis_at: null, approved_count: 0 });
    history = [];
    renderPage();
    expect(await screen.findByText("오늘 스캐너 실행 결과가 없습니다.")).toBeInTheDocument();
    expect(screen.queryByText("현재 ScannerRun에 대한 GPT 분석이 아직 없습니다.")).toBeNull();
  });

  it("confirms with approved symbols and cancel performs no activation", async () => {
    history[0] = item(6, 5, false, ["ORCL", "SPCX"]); renderPage(); await screen.findByText("09/11 분석 이력");
    fireEvent.click(within(rowFor(6)).getByText("이 분석 사용"));
    const dialog = screen.getByRole("dialog", { name: "Analysis #6 거래 기준 변경" });
    expect(within(dialog).getByText("ORCL")).toBeInTheDocument(); expect(within(dialog).getByText("SPCX")).toBeInTheDocument();
    fireEvent.click(within(dialog).getByText("취소")); expect(activateApi).not.toHaveBeenCalled();
  });

  it("warns when activation would leave zero entry candidates", async () => {
    renderPage(); await screen.findByText("09/11 분석 이력"); fireEvent.click(within(rowFor(6)).getByText("이 분석 사용"));
    expect(screen.getByText("이 분석에는 승인된 종목이 없습니다. 활성화하면 현재 진입 후보가 0개가 됩니다.")).toBeInTheDocument();
  });

  it("activates once only after confirmation and refreshes the active id", async () => {
    renderPage(); await screen.findByText("09/11 분석 이력"); fireEvent.click(within(rowFor(6)).getByText("이 분석 사용"));
    fireEvent.click(screen.getByText("거래 기준으로 변경"));
    await waitFor(() => expect(activateApi).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(screen.getByText(/현재 거래 기준 · Analysis #6/)).toBeInTheDocument());
    expect(within(rowFor(6)).getByText("이 ScannerRun의 현재 활성 분석")).toBeInTheDocument();
  });

  it("reports that importing B preserved active A", async () => {
    renderPage(); await screen.findByText("09/11 분석 이력"); fireEvent.click(screen.getByText("분석 결과 입력"));
    fireEvent.change(screen.getByLabelText("GPT 분석 JSON"), { target: { value: "{}" } });
    fireEvent.click(screen.getByText("검증 후 적용"));
    expect(await screen.findByRole("status")).toHaveTextContent("새 분석 #6이 저장되었습니다. 현재 거래 기준은 Analysis #5입니다. 새 분석을 사용하려면 활성화하세요.");
  });
});
