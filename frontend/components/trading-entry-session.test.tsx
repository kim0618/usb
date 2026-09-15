import React from "react";
import { readFileSync } from "node:fs";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Dashboard, EntryBoard, EntryBoardStatus, TradingOverview } from "@/types/api";

vi.mock("next/navigation", () => ({ usePathname: () => "/trading" }));
const tradingApi = vi.fn();
const entryBoardApi = vi.fn();
const researchApi = vi.fn();
vi.mock("@/lib/api", () => ({
  api: {
    trading: () => tradingApi(), dashboard: async () => ({ trading: { strategy_performance_valid_from: null } } as unknown as Dashboard),
    entryBoard: () => entryBoardApi(), research: () => researchApi(),
    orders: async () => [], fills: async () => [], trades: async () => [], dailyPerformance: async () => [],
  },
  ApiError: class ApiError extends Error { constructor(public status: number) { super("api"); } },
}));

import TradingPage from "@/app/trading/page";

/** Production shape: the 09/14 ScannerRun exists, 09/11 Analysis #6 is the previous run's. */
const board = (status: EntryBoardStatus, extra: Partial<EntryBoard> = {}): EntryBoard => ({
  status, scanner_run_id: 8, analysis_session_date: "2026-09-14", entry_session_date: "2026-09-15",
  analysis_id: null, analysis_at: null,
  thresholds: { strategy_version: "strategy_v0", premarket_gap_min_pct: "0.02", premarket_gap_max_pct: "0.15", premarket_volume_ratio_min: "0.05" },
  candidates: [], ...extra,
});

const overview = (): TradingOverview => ({
  broker_mode: "SIMULATION", availability: "AVAILABLE",
  account: { currency: "USD", equity: "10000", cash: "10000", invested_notional: "0", unrealized_pnl: "0", realized_pnl: "12", today_pnl: "12" },
  open_positions: [], open_orders: [], strategy_states: [],
  entry_capacity: { entry_session_date: "2026-09-15", new_entries_used: 0, max_new_entries: 3, open_positions_used: 0, max_open_positions: 3, pending_entries: 0, blocked_reason: null },
});

const view = async () => (await screen.findByRole("heading", { name: "진입 평가" })).closest("section") as HTMLElement;
const PREVIOUS_ANALYSIS_SYMBOLS = ["SPCX", "ORCL", "AMD", "AAPL", "META"];

beforeEach(() => {
  vi.clearAllMocks();
  tradingApi.mockResolvedValue(overview());
  entryBoardApi.mockResolvedValue(board("NO_ACTIVE_ANALYSIS"));
});
afterEach(cleanup);

describe("current ScannerRun authority on the Trading screen", () => {
  it("shows no entry candidate while the current run has no GPT analysis", async () => {
    render(<TradingPage/>);
    const section = await view();
    await waitFor(() => expect(within(section).getByText("현재 ScannerRun의 GPT 분석이 아직 없습니다.")).toBeInTheDocument());
    expect(within(section).getByText(/오늘 진입 후보가 아직 준비되지 않았습니다/)).toBeInTheDocument();
    expect(within(section).getByText(/09\/14 \(월\) 분석 기준/)).toBeInTheDocument();
    expect(section.textContent).not.toContain("Analysis #");
    PREVIOUS_ANALYSIS_SYMBOLS.forEach(symbol => expect(section.textContent).not.toContain(symbol));
    expect(within(section).queryAllByRole("listitem")).toHaveLength(0);
  });

  it("never consults /research/latest for entry candidates", async () => {
    render(<TradingPage/>);
    await view();
    await waitFor(() => expect(entryBoardApi).toHaveBeenCalled());
    expect(researchApi).not.toHaveBeenCalled();
  });

  it.each<[EntryBoardStatus, string]>([
    ["NO_SCANNER_RUN", "오늘 스캐너 실행 결과가 없습니다."],
    ["SCANNER_RUN_OUTDATED", "오늘 스캐너 실행 결과가 없습니다."],
    ["NO_ACTIVE_ANALYSIS", "현재 ScannerRun의 GPT 분석이 아직 없습니다."],
    ["NO_APPROVALS", "현재 분석에서 채택된 진입 후보가 없습니다."],
  ])("separates the %s empty state", async (status, title) => {
    entryBoardApi.mockResolvedValue(board(status, status === "NO_SCANNER_RUN" ? { scanner_run_id: null, analysis_session_date: null, entry_session_date: null } : status === "NO_APPROVALS" ? { analysis_id: 9 } : {}));
    render(<TradingPage/>);
    const section = await view();
    await waitFor(() => expect(within(section).getByText(title)).toBeInTheDocument());
    expect(section.querySelectorAll("[data-entry-board]")).toHaveLength(1);
  });

  it("explains an outdated run with its own passed entry session", async () => {
    entryBoardApi.mockResolvedValue(board("SCANNER_RUN_OUTDATED", { analysis_session_date: "2026-09-11", entry_session_date: "2026-09-14" }));
    render(<TradingPage/>);
    const section = await view();
    await waitFor(() => expect(within(section).getByText("마지막 스캐너 실행은 09/11 (금) 기준이며, 그 진입 세션(09/14 (월))은 이미 지났습니다.")).toBeInTheDocument());
  });

  it("labels the current analysis, entry session and capacity once candidates are ready", async () => {
    entryBoardApi.mockResolvedValue(board("READY", { analysis_id: 7, candidates: [{ rank: 1, symbol: "GOOGL", scanner_candidate_id: 1, exchange: "NASDAQ", state_conflict: false, state: null, premarket: null }] }));
    render(<TradingPage/>);
    const section = await view();
    await waitFor(() => expect(within(section).getByText("09/14 (월) 분석 기준 · Analysis #7 · 진입 세션 09/15 (화)")).toBeInTheDocument());
    expect(within(section).getByText("신규 진입 0/3 · 보유 0/3")).toBeInTheDocument();
    expect(within(section).getByRole("link", { name: "분석 보기" })).toHaveAttribute("href", "/research");
  });
});

describe("Trading layout", () => {
  it("removes the current-session P&L panel and gives the board the full width", async () => {
    render(<TradingPage/>);
    const section = await view();
    expect(screen.queryByText("현재 세션 손익 상세")).toBeNull();
    expect(screen.queryByText("실현 손익")).toBeNull();
    expect(section.parentElement?.className ?? "").not.toContain("xl:grid-cols-2");
    ["총 자산", "투자 중", "보유 현금", "평가 손익", "직전 거래일 손익"].forEach(label => expect(screen.getAllByText(new RegExp(label)).length).toBeGreaterThan(0));
  });

  it("keeps the daily P&L section below the entry board", async () => {
    render(<TradingPage/>);
    const section = await view();
    const daily = screen.getByRole("heading", { name: "일별 손익" });
    expect(section.compareDocumentPosition(daily) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("no longer carries the card-per-candidate UI", () => {
    const page = readFileSync("app/trading/page.tsx", "utf8");
    ["EntryStatusCard", "현재 세션 손익 상세", "pnl-metric-grid", "승인 · 정규장 진입 평가 대기"].forEach(token => expect(page).not.toContain(token));
  });
});
