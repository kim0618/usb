import React from "react";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ToastProvider } from "@/components/toast";
import type { AdoptionResponse, Dashboard, EntryBoard, ResearchCurrentAuthority, TradingOverview } from "@/types/api";

// Strategy A same-session contract: a mover run of D is analysed on D and entered on D. The
// screens render the backend's own session dates; none of them derives an entry date.
vi.mock("next/navigation", () => ({ usePathname: () => "/adoption" }));
const adoptionApi = vi.fn(); const currentApi = vi.fn(); const entryBoardApi = vi.fn();
vi.mock("@/lib/api", () => ({
  api: {
    adoption: () => adoptionApi(), researchCurrent: () => currentApi(), researchDetail: vi.fn(), decide: vi.fn(),
    trading: async () => ({ broker_mode: "SIMULATION", availability: "AVAILABLE",
      account: { currency: "USD", equity: "10000", cash: "10000", invested_notional: "0", unrealized_pnl: "0", realized_pnl: null, today_pnl: null },
      open_positions: [], open_orders: [], strategy_states: [], entry_capacity: null } as TradingOverview),
    dashboard: async () => ({ trading: { strategy_performance_valid_from: null } } as unknown as Dashboard),
    entryBoard: () => entryBoardApi(), orders: async () => [], fills: async () => [], trades: async () => [], dailyPerformance: async () => [],
  },
  ApiError: class ApiError extends Error { constructor(public status: number) { super("api"); } },
}));

import AdoptionPage from "@/app/adoption/page";
import TradingPage from "@/app/trading/page";

const D = "2026-10-16";
const authority = (entry: string): ResearchCurrentAuthority => ({
  status: "ACTIVE", scanner_run_id: 39, trading_date: D, analysis_session_date: D, entry_session_date: entry,
  completed_at: "2026-10-16T13:27:00Z", active_analysis_id: 27, analysis_at: "2026-10-16T13:35:00Z", approved_count: 1,
} as ResearchCurrentAuthority);
const adoption: AdoptionResponse = { filter_version: "adoption_filter_v0", analysis_id: 27, scanner_run_id: 39, trading_date: D,
  counts: { adoption_candidate: 0, review_required: 0, excluded: 0 }, items: [] };
const board: EntryBoard = { status: "NO_APPROVALS", scanner_run_id: 39, analysis_session_date: D, entry_session_date: D,
  analysis_id: 27, analysis_at: "2026-10-16T13:35:00Z",
  thresholds: { strategy_version: "strategy_v0", premarket_gap_min_pct: "0.02", premarket_gap_max_pct: "0.15", premarket_volume_ratio_min: "0.05" },
  candidates: [] };

beforeEach(() => { vi.clearAllMocks(); adoptionApi.mockResolvedValue(adoption); entryBoardApi.mockResolvedValue(board); });
afterEach(cleanup);

describe("Strategy A same-session dates", () => {
  it("adoption names the backend's entry session, the mover run's own session D", async () => {
    currentApi.mockResolvedValue(authority(D));
    render(<ToastProvider><AdoptionPage/></ToastProvider>);
    expect(await screen.findByText(/10\/16 분석 기준/)).toBeInTheDocument();
    expect(await screen.findByText(/채택한 종목은 10\/16 진입 세션에서 시스템의 진입 평가 대상이 됩니다/)).toBeInTheDocument();
    expect(screen.queryByText(/다음 거래일/)).toBeNull();
  });

  it("adoption renders a legacy run's next-session entry exactly as the backend states it", async () => {
    currentApi.mockResolvedValue(authority("2026-10-19"));
    render(<ToastProvider><AdoptionPage/></ToastProvider>);
    expect(await screen.findByText(/채택한 종목은 10\/19 진입 세션에서/)).toBeInTheDocument();
  });

  it("the entry board shows D as both the analysis session and the entry session", async () => {
    render(<TradingPage/>);
    const heading = await screen.findByRole("heading", { name: "진입 평가" });
    const meta = heading.parentElement as HTMLElement;
    expect(meta).toHaveTextContent(/10\/16.*분석 기준/);
    expect(meta).toHaveTextContent(/진입 세션 10\/16/);
  });
});
