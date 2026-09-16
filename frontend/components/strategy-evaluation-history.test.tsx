import React from "react";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { StrategyEvaluationHistory } from "./strategy-evaluation-history";
import { api } from "@/lib/api";
import type { StrategyEvaluationCandidate, StrategyEvaluationDetail, StrategyEvaluationSummary } from "@/types/api";

const summary = (over: Partial<StrategyEvaluationSummary> = {}): StrategyEvaluationSummary => ({
  trading_date: "2026-09-15", source: "EVALUATION", approved_count: 5, premarket_pass_count: 1,
  gap_rejected_count: 3, volume_rejected_count: 1, or_ready_count: 1, or_rejected_count: 0,
  signal_count: 0, execution_rejected_count: 0, no_entry_signal_count: 1,
  premarket_rejected_count: 4, filled_count: 0, data_error_count: 0, incomplete_count: 0,
  trade_count: 0, open_trade_count: 0, realized_pnl: "0", total_r: "0", result: "NO_TRADE_DAY",
  ...over,
});

const candidate = (over: Partial<StrategyEvaluationCandidate> = {}): StrategyEvaluationCandidate => ({
  source: "EVALUATION", rank: 1, symbol: "AAPL", exchange: "NASDAQ", scanner_candidate_id: 1,
  final_status: "PREMARKET_REJECTED", final_reason: "GAP_TOO_LOW", final_detail: null,
  last_phase: "PREMARKET_REJECTED", premarket_passed: false, opening_range_ready: null,
  entry_signalled: false, entry_filled: false, previous_close: "100", premarket_reference_price: "100.82",
  gap_pct: "0.0082", gap_min: "0.02", gap_max: "0.15", premarket_volume: "50000",
  historical_average_daily_volume: "1000000", v1_volume_ratio: "0.05", v1_volume_min: "0.05",
  thresholds_source: "STORED", opening_range_high: null, opening_range_low: null,
  signal_price: null, initial_stop: null, signal_at: null, intended_entry_bar_at: null,
  fill_price: null, fill_quantity: null, order_id: null, last_progress_reason: null,
  last_error_code: null, error_tick_count: 0, strategy_version: "strategy_v0",
  evaluated_at: null, finalized_at: null, trade_status: null, realized_pnl: null, net_r: null,
  exit_reason: null, ...over,
});

const detail = (candidates: StrategyEvaluationCandidate[],
                over: Partial<StrategyEvaluationDetail> = {}): StrategyEvaluationDetail => ({
  trading_date: "2026-09-15", source: "EVALUATION", summary: summary(), candidates, ...over,
});

const click = async (element: HTMLElement) => { await act(async () => { fireEvent.click(element); }); };

describe("strategy evaluation history", () => {
  beforeEach(() => vi.restoreAllMocks());
  afterEach(cleanup);

  it("shows a zero-trade day as a normal row with its funnel counts", async () => {
    vi.spyOn(api, "strategyEvaluations").mockResolvedValue([summary()]);
    render(<StrategyEvaluationHistory/>);

    const row = await screen.findByRole("row", { name: /09\/15/ });
    const cells = within(row).getAllByRole("cell").map(cell => cell.textContent);
    expect(cells).toEqual(["▸09/15 (화)", "5", "1", "1", "1", "0", "0", "매수 없음", "+$0.00"]);
  });

  it("opens a day and shows each candidate's reason, observed value, and threshold", async () => {
    vi.spyOn(api, "strategyEvaluations").mockResolvedValue([summary()]);
    vi.spyOn(api, "strategyEvaluationDetail").mockResolvedValue(detail([
      candidate(),
      candidate({ rank: 2, symbol: "AMD", scanner_candidate_id: 2,
        final_reason: "LOW_PREMARKET_VOLUME", gap_pct: "0.03", v1_volume_ratio: "0.0294" }),
    ]));
    render(<StrategyEvaluationHistory/>);
    await click(await screen.findByRole("button", { name: /09\/15/ }));

    await waitFor(() => expect(screen.getByText("AAPL")).toBeInTheDocument());
    const first = screen.getByText("AAPL").closest("tr") as HTMLElement;
    expect(within(first).getByText("제외")).toBeInTheDocument();
    expect(within(first).getByText("갭 상승폭 기준 미달")).toBeInTheDocument();
    expect(within(first).getByText("GAP_TOO_LOW")).toBeInTheDocument();
    expect(within(first).getByText("+0.82%")).toBeInTheDocument();
    expect(within(first).getByText("+2.00% ~ +15.00%")).toBeInTheDocument();
    const second = screen.getByText("AMD").closest("tr") as HTMLElement;
    expect(within(second).getByText("프리마켓 거래량 부족")).toBeInTheDocument();
    expect(within(second).getByText("2.94%")).toBeInTheDocument();
    expect(within(second).getByText("5.00% 이상")).toBeInTheDocument();
  });

  it("separates a buy, a data error, and an unrecognised reason from a normal exclusion", async () => {
    vi.spyOn(api, "strategyEvaluations").mockResolvedValue([summary({ filled_count: 1, result: "TRADED" })]);
    vi.spyOn(api, "strategyEvaluationDetail").mockResolvedValue(detail([
      candidate({ symbol: "ORCL", final_status: "TRADED", final_reason: "ABOVE_VWAP_AND_OR_BREAK",
        entry_filled: true, fill_price: "102.4", fill_quantity: "12" }),
      candidate({ rank: 2, symbol: "MU", scanner_candidate_id: 2, final_status: "DATA_ERROR",
        final_reason: "MARKET_DATA_UNAVAILABLE", error_tick_count: 4,
        last_error_code: "MARKET_DATA_UNAVAILABLE" }),
      candidate({ rank: 3, symbol: "NEW", scanner_candidate_id: 3, final_status: "UNKNOWN",
        final_reason: "SOMETHING_NEW" }),
      candidate({ rank: 4, symbol: "OLD", scanner_candidate_id: 4, final_status: "INCOMPLETE",
        final_reason: "INCOMPLETE_LEGACY" }),
    ]));
    render(<StrategyEvaluationHistory/>);
    await click(await screen.findByRole("button", { name: /09\/15/ }));

    await waitFor(() => expect(screen.getByText("ORCL")).toBeInTheDocument());
    const tone = (symbol: string) => (screen.getByText(symbol).closest("tr") as HTMLElement)
      .querySelector("[data-evaluation-status]")?.className;
    expect(tone("ORCL")).toContain("tone-success");
    expect(tone("MU")).toContain("tone-warning");
    expect(tone("NEW")).toContain("tone-warning");
    expect(tone("OLD")).toContain("tone-neutral");
    const unknown = screen.getByText("NEW").closest("tr") as HTMLElement;
    // An unrecognised code is flagged for a human, never shown as a strategy verdict.
    expect(within(unknown).getByText("확인 필요")).toBeInTheDocument();
    expect(within(unknown).getByText("기록된 사유 확인 필요")).toBeInTheDocument();
    expect(within(unknown).getByText("SOMETHING_NEW")).toBeInTheDocument();
    expect(within(screen.getByText("OLD").closest("tr") as HTMLElement)
      .getByText("과거 상세 사유 미기록")).toBeInTheDocument();
  });

  it("labels a legacy session and never shows a value it did not store", async () => {
    vi.spyOn(api, "strategyEvaluations").mockResolvedValue([summary({ source: "LEGACY_STATE" })]);
    vi.spyOn(api, "strategyEvaluationDetail").mockResolvedValue(detail([
      candidate({ source: "LEGACY_STATE", final_status: "INCOMPLETE",
        final_reason: "INCOMPLETE_LEGACY", final_detail: null, gap_pct: null, gap_min: null,
        gap_max: null, v1_volume_ratio: null, v1_volume_min: null, thresholds_source: null }),
    ], { source: "LEGACY_STATE" }));
    render(<StrategyEvaluationHistory/>);
    await click(await screen.findByRole("button", { name: /09\/15/ }));

    await waitFor(() => expect(screen.getByText(/평가 기록 도입 이전/)).toBeInTheDocument());
    const row = screen.getByText("AAPL").closest("tr") as HTMLElement;
    expect(within(row).queryByText("Gap")).toBeNull();
    expect(within(row).queryByText("Gap 기준")).toBeNull();
  });

  it("states an empty history instead of showing nothing", async () => {
    vi.spyOn(api, "strategyEvaluations").mockResolvedValue([]);
    render(<StrategyEvaluationHistory/>);
    expect(await screen.findByText("아직 평가된 진입 세션이 없습니다.")).toBeInTheDocument();
  });

  it("reports a failed history request without hiding the section", async () => {
    vi.spyOn(api, "strategyEvaluations").mockRejectedValue(new Error("down"));
    render(<StrategyEvaluationHistory/>);
    expect(await screen.findByRole("alert")).toHaveTextContent("전략 평가 이력 조회 실패");
  });
});
