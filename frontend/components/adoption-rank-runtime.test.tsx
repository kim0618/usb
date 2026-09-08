import React from "react";
import { readFileSync } from "node:fs";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
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

const source = () => readFileSync("app/adoption/page.tsx", "utf8");

/** Run-2-equivalent adoption payload, already ordered by the backend `recommendation_rank`. */
const item = (
  recommendation_rank: number, symbol: string, quant_rank: number, gpt_rank: number,
  classification: AdoptionItem["classification"], momentum: number | null,
  strengths: string[] = ["촉매 98 매우 강함"], warnings: string[] = [],
): AdoptionItem => ({
  scanner_candidate_id: recommendation_rank, symbol, quant_rank, gpt_rank, quant_score: 1,
  overall_score: 80, catalyst_score: 80, fundamental_score: 80, momentum_score: 80, risk_score: 60,
  evidence_confidence: 60, catalyst_duration: "ONE_TO_TWO_DAYS", stop_profile: "NORMAL",
  trailing_profile: "NORMAL", overnight_suitability: "MEDIUM", company_summary: `${symbol} 회사 설명`,
  catalyst_summary: "촉매", risk_summary: "위험", invalidation_summary: "무효화", unknown_fields: [],
  human_decision: null, company_name: `${symbol} INC`, exchange: "NASDAQ", industry: null,
  market_cap: null, previous_open: null, previous_high: 384.04, previous_low: null,
  previous_close: 370.51, previous_return_pct: 0.038, previous_volume: null, rvol: 1.83,
  analysis_id: 7, scanner_run_id: 2, recommendation_rank, evidence_score: 60,
  relative_strength: 0.040942070617333126, momentum, classification,
  rank_delta: quant_rank - gpt_rank, rank_delta_label: quant_rank === gpt_rank ? "유지" : `↑${quant_rank - gpt_rank}`,
  rank_direction: quant_rank === gpt_rank ? "UNCHANGED" : "UP", strengths, warnings,
  rank_explanation: "설명.",
});

const RUN2: AdoptionResponse = {
  filter_version: "adoption_filter_v0", analysis_id: 7, scanner_run_id: 2, trading_date: "2026-09-03",
  counts: { adoption_candidate: 3, review_required: 2, excluded: 2 },
  items: [
    item(1, "TSLA", 1, 1, "ADOPTION_CANDIDATE", 0.15954683441304418,
      ["촉매 98 매우 강함", "모멘텀 98 매우 강함", "거래량 1.83배 활발"],
      ["근거 신뢰도 낮음 25", "안전도 주의 47", "Quant 하위권 #1/7"]),
    item(2, "NVDA", 5, 2, "ADOPTION_CANDIDATE", 0.049408648796748666, ["촉매 97 매우 강함"],
      ["Quant/GPT 순위 괴리 +3", "미확인 항목 3건", "재료 지속성 확인 불가", "촉매 출처 없음"]),
    item(3, "MSFT", 7, 4, "ADOPTION_CANDIDATE", 0.020665786420197563),
    item(4, "META", 4, 3, "REVIEW_REQUIRED", 0.042125784031191715),
    item(5, "AVGO", 3, 5, "REVIEW_REQUIRED", -0.14643396383436558),
    item(6, "SPCX", 2, 6, "EXCLUDED", 0.30873651235642185),
    item(7, "AAPL", 6, 7, "EXCLUDED", 0.05079863000544149),
  ],
};

const detail = (symbol: string): ResearchDetail => ({
  ...RUN2.items.find(row => row.symbol === symbol)!,
  sources: [], premarket: { price: null, return_pct: null, observed_at: null, reason: "NOT_AVAILABLE_FROM_PROVIDER" },
  postmarket: { price: null, return_pct: null, observed_at: null, reason: "NOT_AVAILABLE_FROM_PROVIDER" },
  market_cap_reason: "UNIT_UNCONFIRMED", industry_reason: "NOT_AVAILABLE_FROM_PROVIDER",
});

const bodyRows = () => within(screen.getAllByRole("rowgroup")[1]).getAllByRole("row");
const click = async (element: HTMLElement) => { await act(async () => { fireEvent.click(element); }); };

async function renderTable() {
  render(<AdoptionPage/>);
  await waitFor(() => expect(screen.getByRole("table")).toBeInTheDocument());
}

describe("Stage 10B-4.2 adoption rank runtime", () => {
  beforeEach(() => { adoptionApi.mockReset(); detailApi.mockReset(); adoptionApi.mockResolvedValue(RUN2); });
  afterEach(cleanup);

  it("labels the rank column 순위 and drops every 추천 prefix", async () => {
    await renderTable();
    expect(screen.getByRole("columnheader", { name: "순위" })).toBeInTheDocument();
    expect(screen.queryByRole("columnheader", { name: "추천 순위" })).not.toBeInTheDocument();
    const page = source();
    expect(page).not.toContain("추천 순위");
    expect(page).not.toContain("추천 #");
  });

  it("renders the backend recommendation_rank as #1..#7 instead of a bare #", async () => {
    await renderTable();
    expect(bodyRows().map(row => within(row).getAllByRole("cell")[0].textContent)).toEqual(["#1", "#2", "#3", "#4", "#5"]);
    await click(screen.getByRole("button", { name: "제외 2개 보기" }));
    expect(bodyRows().map(row => within(row).getAllByRole("cell")[0].textContent)).toEqual(["#1", "#2", "#3", "#4", "#5", "#6", "#7"]);
    expect(screen.queryByText("#", { exact: true })).not.toBeInTheDocument();
  });

  it("preserves the backend recommendation order and never re-sorts by GPT rank", async () => {
    await renderTable();
    const symbols = () => bodyRows().map(row => within(row).getAllByRole("cell")[2].textContent);
    expect(symbols()).toEqual(["TSLATSLA INC", "NVDANVDA INC", "MSFTMSFT INC", "METAMETA INC", "AVGOAVGO INC"]);
    await click(screen.getByRole("button", { name: "제외 2개 보기" }));
    expect(symbols().slice(5)).toEqual(["SPCXSPCX INC", "AAPLAAPL INC"]);
    expect(source()).not.toContain(".sort(");
  });

  it("counts hidden strengths and warnings as 외 n건 rather than +n", async () => {
    await renderTable();
    const tsla = within(bodyRows()[0]).getAllByRole("cell");
    expect(tsla[5]).toHaveTextContent("촉매 98 매우 강함 · 모멘텀 98 매우 강함 · 외 1건");
    expect(tsla[6]).toHaveTextContent("근거 신뢰도 낮음 25 · 안전도 주의 47 · 외 1건");
    expect(within(bodyRows()[1]).getAllByRole("cell")[6]).toHaveTextContent("Quant/GPT 순위 괴리 +3 · 미확인 항목 3건 · 외 2건");
    expect(screen.queryByText(/ \+1$/)).not.toBeInTheDocument();
    expect(screen.queryByText(/ \+2$/)).not.toBeInTheDocument();
  });

  it("shows the drawer rank and the persisted momentum for the selected symbol", async () => {
    detailApi.mockImplementation(async (_id: number, symbol: string) => detail(symbol));
    await renderTable();
    await click(within(bodyRows()[0]).getByRole("button", { name: "상세보기" }));
    const drawer = await screen.findByRole("dialog", { name: "TSLA · 채택 검토" });
    expect(within(drawer).getByText("순위 #1")).toBeInTheDocument();
    expect(within(drawer).queryByText(/추천/)).not.toBeInTheDocument();
    const momentum = within(drawer).getByText("최근 흐름").parentElement!;
    expect(momentum).toHaveTextContent("+16.0%");
    expect(momentum).not.toHaveTextContent("최근 흐름-");
    expect(within(momentum).getByText("+16.0%")).toHaveClass("tone-text-success");
  });

  it("opens the drawer shell immediately and issues one detail request per click", async () => {
    detailApi.mockReturnValue(new Promise(() => undefined));
    await renderTable();
    await click(within(bodyRows()[0]).getByRole("button", { name: "상세보기" }));
    expect(screen.getByRole("dialog", { name: "TSLA · 채택 검토" })).toBeInTheDocument();
    expect(detailApi).toHaveBeenCalledTimes(1);
    expect(detailApi).toHaveBeenCalledWith(7, "TSLA");
  });

  it("keeps the drawer rank aligned with the backend rank for every symbol", async () => {
    detailApi.mockImplementation(async (_id: number, symbol: string) => detail(symbol));
    await renderTable();
    for (const [index, expected] of [[1, "순위 #2"], [2, "순위 #3"]] as const) {
      await click(within(bodyRows()[index]).getByRole("button", { name: "상세보기" }));
      const drawer = await screen.findByRole("dialog");
      await waitFor(() => expect(within(drawer).getByText(expected)).toBeInTheDocument());
      await click(within(drawer).getByRole("button", { name: "상세 닫기" }));
    }
  });

  it("renders a missing rank as - instead of a bare # marker", () => {
    expect(source()).toContain('Number.isFinite(value) ? `#${value}` : "-"');
    expect(source()).not.toContain("index + 1");
  });
});
