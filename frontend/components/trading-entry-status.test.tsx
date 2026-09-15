import React from "react";
import { readFileSync } from "node:fs";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Dashboard, EntryBoard, EntryBoardCandidate, EntryBoardPremarket, EntryBoardState, EntryCapacity, TradingOverview } from "@/types/api";

vi.mock("next/navigation", () => ({ usePathname: () => "/trading" }));
const tradingApi = vi.fn();
const entryBoardApi = vi.fn();
vi.mock("@/lib/api", () => ({
  api: {
    trading: () => tradingApi(), dashboard: async () => ({ trading: { strategy_performance_valid_from: null } } as unknown as Dashboard),
    entryBoard: () => entryBoardApi(), orders: async () => [], fills: async () => [], trades: async () => [],
    dailyPerformance: async () => [],
  },
  ApiError: class ApiError extends Error { constructor(public status: number) { super("api"); } },
}));

import TradingPage from "@/app/trading/page";

const ENTRY_SESSION = "2026-09-15";

const state = (phase: string, phase_reason: string | null = null, extra: Partial<EntryBoardState> = {}): EntryBoardState => ({
  phase, phase_reason, entry_price: null, initial_stop: null, active_stop: null, signal_at: null,
  intended_execution_bar_at: null, updated_at: null, ...extra,
});

const premarket = (extra: Partial<EntryBoardPremarket> = {}): EntryBoardPremarket => ({
  previous_close: "150.27", reference_price: "151.85", gap_pct: "0.0105", premarket_volume: "100",
  historical_average_daily_volume: "10000", volume_ratio: "0.01", minute_bars_count: 300, premarket_bars_count: 120,
  first_timestamp: null, last_timestamp: null, invalid_field: null, ...extra,
});

const row = (symbol: string, rank: number | null, rowState: EntryBoardState | null = null, rowPremarket: EntryBoardPremarket | null = null): EntryBoardCandidate => ({
  rank, symbol, scanner_candidate_id: rank ?? 0, exchange: "NASDAQ", state_conflict: false, state: rowState, premarket: rowPremarket,
});

const board = (candidates: EntryBoardCandidate[]): EntryBoard => ({
  status: "READY", scanner_run_id: 7, analysis_session_date: "2026-09-14", entry_session_date: ENTRY_SESSION,
  analysis_id: 7, analysis_at: "2026-09-14T23:00:00Z",
  thresholds: { strategy_version: "strategy_v0", premarket_gap_min_pct: "0.02", premarket_gap_max_pct: "0.15", premarket_volume_ratio_min: "0.05" },
  candidates,
});

const overview = (extra: Partial<TradingOverview> = {}): TradingOverview => ({
  broker_mode: "SIMULATION", availability: "AVAILABLE",
  account: { currency: "USD", equity: "10000", cash: "10000", invested_notional: "0", unrealized_pnl: "0", realized_pnl: null, today_pnl: null },
  open_positions: [], open_orders: [], strategy_states: [], entry_capacity: null, ...extra,
});

const capacity = (blocked_reason: EntryCapacity["blocked_reason"], entry_session_date = ENTRY_SESSION): EntryCapacity => ({
  entry_session_date, new_entries_used: 3, max_new_entries: 3, open_positions_used: 1, max_open_positions: 3, pending_entries: 0, blocked_reason,
});

const view = async () => (await screen.findByRole("heading", { name: "진입 평가" })).closest("section") as HTMLElement;
const rows = (section: HTMLElement) => within(section).queryAllByRole("listitem");
const rowFor = (section: HTMLElement, symbol: string) => section.querySelector(`[data-entry-row="${symbol}"]`) as HTMLElement;
const chip = (section: HTMLElement, symbol: string) => rowFor(section, symbol).querySelector("[data-entry-group]") as HTMLElement;

/** The five-status reference day: every display group at once, in GPT rank order. */
const fiveStatusBoard = () => board([
  row("GOOGL", 1),
  row("META", 2, state("PREMARKET_REJECTED", "GAP_TOO_LOW"), premarket()),
  row("AMD", 3, state("OPENING_RANGE_BUILDING"), premarket({ gap_pct: "0.031", volume_ratio: "0.07" })),
  row("AAPL", 4, state("POSITION_OPEN", null, { entry_price: "230.10", initial_stop: "228.00" }), premarket({ gap_pct: "0.025" })),
  row("MSFT", 5, state("PREMARKET_REJECTED", "INVALID_PREMARKET_DATA"), premarket({ gap_pct: null, volume_ratio: null, invalid_field: "NO_PREMARKET_BARS" })),
]);

beforeEach(() => {
  vi.clearAllMocks();
  tradingApi.mockResolvedValue(overview({ open_positions: [{ symbol: "AAPL", currency: "USD", quantity: "10" }] }));
  entryBoardApi.mockResolvedValue(fiveStatusBoard());
});
afterEach(cleanup);

describe("entry status board layout", () => {
  it.each([1, 5, 8])("keeps one fixed panel with one row per candidate for %i candidates", async count => {
    entryBoardApi.mockResolvedValue(board(Array.from({ length: count }, (_, index) => row(`S${index + 1}`, index + 1))));
    render(<TradingPage/>);
    const section = await view();
    await waitFor(() => expect(rows(section)).toHaveLength(count));
    expect(section.querySelectorAll("[data-entry-board]")).toHaveLength(1);
    expect(within(section).getByRole("list", { name: "진입 후보" })).toBeInTheDocument();
    rows(section).forEach((item, index) => expect(item).toHaveTextContent(`#${index + 1}S${index + 1}`));
  });

  it("keeps rank, symbol and status on narrow screens and hides only secondary columns", async () => {
    render(<TradingPage/>);
    const section = await view();
    const button = await waitFor(() => within(rowFor(section, "META")).getByRole("button"));
    expect(button.className).toContain("grid-cols-[2.5rem_minmax(4.5rem,1fr)_auto]");
    expect(button.className).toContain("sm:grid-cols-[2.5rem_6rem_7.5rem_minmax(0,1fr)_auto]");
    const [rank, symbol, status, detail, value] = Array.from(button.children) as HTMLElement[];
    [rank, symbol, status].forEach(cell => expect(cell.className).not.toContain("hidden"));
    [detail, value].forEach(cell => expect(cell.className).toContain("hidden"));
  });
});

describe("display status groups", () => {
  it("maps each state to its group, label, icon and colour", async () => {
    render(<TradingPage/>);
    const section = await view();
    await waitFor(() => expect(rows(section)).toHaveLength(5));
    const expected: Array<[string, string, string, string, string]> = [
      ["GOOGL", "WAITING", "○대기", "tone-neutral", "프리마켓 평가 대기"],
      ["META", "EXCLUDED", "×제외", "tone-danger", "갭 상승폭 기준 미달"],
      ["AMD", "WAITING", "○대기", "tone-neutral", "Opening Range 대기"],
      ["AAPL", "BOUGHT", "●매수", "tone-success", "보유 중"],
      ["MSFT", "SYSTEM", "▲데이터 오류", "tone-warning", "프리마켓 데이터 부족/이상"],
    ];
    expected.forEach(([symbol, group, label, tone, detail]) => {
      const badge = chip(section, symbol);
      expect(badge.dataset.entryGroup).toBe(group);
      expect(badge).toHaveTextContent(label);
      expect(badge.className).toContain(tone);
      expect(within(rowFor(section, symbol)).getByRole("button")).toHaveTextContent(detail);
    });
  });

  it("shows the evaluating group in blue while a signal waits for its execution bar", async () => {
    entryBoardApi.mockResolvedValue(board([row("NVDA", 1, state("ENTRY_SIGNALLED", null, { entry_price: "180.5", signal_at: "2026-09-15T13:47:30Z", intended_execution_bar_at: "2026-09-15T13:48:00Z" }))]));
    render(<TradingPage/>);
    const section = await view();
    await waitFor(() => expect(chip(section, "NVDA").dataset.entryGroup).toBe("EVALUATING"));
    expect(chip(section, "NVDA")).toHaveTextContent("◐평가 중");
    expect(chip(section, "NVDA").className).toContain("tone-info");
    expect(rowFor(section, "NVDA")).toHaveTextContent("체결봉 대기");
    expect(rowFor(section, "NVDA")).toHaveTextContent("신호 $180.5");
  });

  it("never colours a normal strategy exclusion like a system failure", async () => {
    entryBoardApi.mockResolvedValue(board([
      row("A1", 1, state("NO_TRADE", "ENTRY_PRICE_ABOVE_CEILING")), row("A2", 2, state("PREMARKET_REJECTED", "LOW_PREMARKET_VOLUME")),
      row("B1", 3, state("NO_TRADE", "AUTH_FAILED")), row("B2", 4, state("NO_TRADE", "ENTRY_SIGNAL_STALE")),
      row("B3", 5, state("PREMARKET_REJECTED", "UNSUPPORTED_EXCHANGE")), row("B4", 6, state("NO_TRADE", "MARKET_DATA_UNAVAILABLE")),
    ]));
    render(<TradingPage/>);
    const section = await view();
    await waitFor(() => expect(rows(section)).toHaveLength(6));
    ["A1", "A2"].forEach(symbol => expect(chip(section, symbol).className).toContain("tone-danger"));
    ["B1", "B2", "B3", "B4"].forEach(symbol => expect(chip(section, symbol).className).toContain("tone-warning"));
  });

  it("labels unentered rows with the backend cap only for the cap's own entry session", async () => {
    tradingApi.mockResolvedValue(overview({ entry_capacity: capacity("DAILY_ENTRY_CAP_REACHED") }));
    render(<TradingPage/>);
    const section = await view();
    await waitFor(() => expect(within(rowFor(section, "GOOGL")).getByRole("button")).toHaveTextContent("일일 진입 한도 도달"));
    expect(within(section).getByText("신규 진입 3/3 · 보유 1/3")).toBeInTheDocument();
    expect(within(rowFor(section, "AAPL")).getByRole("button")).toHaveTextContent("보유 중");
    cleanup();
    tradingApi.mockResolvedValue(overview({ entry_capacity: capacity("DAILY_ENTRY_CAP_REACHED", "2026-09-14") }));
    render(<TradingPage/>);
    const other = await view();
    await waitFor(() => expect(within(rowFor(other, "GOOGL")).getByRole("button")).toHaveTextContent("프리마켓 평가 대기"));
  });

  it("never puts a raw enum on the row itself", async () => {
    render(<TradingPage/>);
    const section = await view();
    await waitFor(() => expect(rows(section)).toHaveLength(5));
    const visibleRowText = within(section).getAllByRole("button").map(button => button.textContent).join(" ");
    ["GAP_TOO_LOW", "PREMARKET_REJECTED", "INVALID_PREMARKET_DATA", "POSITION_OPEN", "OPENING_RANGE_BUILDING"].forEach(code =>
      expect(visibleRowText).not.toContain(code));
  });
});

describe("fixed rank order", () => {
  it("renders the backend order whatever the states are", async () => {
    render(<TradingPage/>);
    const section = await view();
    await waitFor(() => expect(rows(section)).toHaveLength(5));
    expect(rows(section).map(item => item.dataset.entryRow)).toEqual(["GOOGL", "META", "AMD", "AAPL", "MSFT"]);
    cleanup();
    entryBoardApi.mockResolvedValue(board([
      row("GOOGL", 1, state("PREMARKET_REJECTED", "GAP_TOO_LOW")), row("META", 2), row("AMD", 3, state("POSITION_OPEN", null, { entry_price: "1" })),
      row("AAPL", 4), row("MSFT", 5, state("NO_TRADE", "AUTH_FAILED")),
    ]));
    render(<TradingPage/>);
    const after = await view();
    await waitFor(() => expect(chip(after, "AMD").dataset.entryGroup).toBe("BOUGHT"));
    expect(rows(after).map(item => item.dataset.entryRow)).toEqual(["GOOGL", "META", "AMD", "AAPL", "MSFT"]);
  });
});

describe("hover and click detail", () => {
  it("describes each row with a hover tooltip of persisted values and the final reason", async () => {
    render(<TradingPage/>);
    const section = await view();
    const button = await waitFor(() => within(rowFor(section, "META")).getByRole("button"));
    const tooltip = document.getElementById(button.getAttribute("aria-describedby") as string) as HTMLElement;
    expect(tooltip).toHaveAttribute("role", "tooltip");
    expect(tooltip.className).toContain("md:group-hover:block");
    expect(tooltip.className).toContain("md:group-focus-within:block");
    ["META", "상태: 제외", "단계: 프리마켓 탈락", "전일 종가$150.27", "프리마켓 기준가$151.85", "Gap+1.05%", "필요 Gap+2.00% ~ +15.00%", "V1 거래량 비율1.00%", "V1 기준5.00% 이상", "최종 사유: 갭 상승폭 기준 미달", "GAP_TOO_LOW"]
      .forEach(text => expect(tooltip).toHaveTextContent(text));
    expect(tooltip).not.toHaveTextContent("신호가");
  });

  it("opens a detail drawer on click with real values and — for missing ones", async () => {
    render(<TradingPage/>);
    const section = await view();
    fireEvent.click(await waitFor(() => within(rowFor(section, "AAPL")).getByRole("button")));
    const dialog = screen.getByRole("dialog", { name: "AAPL · 진입 평가 상세" });
    expect(within(dialog).getByText("순위 #4")).toBeInTheDocument();
    expect(within(dialog).getByText("진입가(체결)").nextSibling).toHaveTextContent("$230.10");
    expect(within(dialog).getByText("초기 Stop").nextSibling).toHaveTextContent("$228.00");
    expect(within(dialog).getByText("보유 수량").nextSibling).toHaveTextContent("10주");
    expect(within(dialog).getByText("체결 예정 봉").nextSibling).toHaveTextContent("—");
    expect(within(dialog).getByText("V1 기준").nextSibling).toHaveTextContent("5.00% 이상");
    expect(dialog).not.toHaveTextContent("V2");
    fireEvent.click(within(dialog).getByRole("button", { name: "상세 닫기" }));
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("survives a row with every optional value missing", async () => {
    entryBoardApi.mockResolvedValue(board([{ rank: null, symbol: "ZZZ", scanner_candidate_id: 1, exchange: null, state_conflict: false,
      state: state("NO_TRADE", null), premarket: { ...premarket(), previous_close: null, reference_price: null, gap_pct: null, volume_ratio: null, premarket_bars_count: null } }]));
    tradingApi.mockResolvedValue(overview({ account: null }));
    render(<TradingPage/>);
    const section = await view();
    await waitFor(() => expect(rows(section)).toHaveLength(1));
    expect(rowFor(section, "ZZZ")).toHaveTextContent("—ZZZ");
    expect(chip(section, "ZZZ")).toHaveTextContent("×제외");
    expect(rowFor(section, "ZZZ")).toHaveTextContent("상세 사유 기록 없음");
    fireEvent.click(within(rowFor(section, "ZZZ")).getByRole("button"));
    const dialog = screen.getByRole("dialog", { name: "ZZZ · 진입 평가 상세" });
    expect(within(dialog).getByText("전일 종가").nextSibling).toHaveTextContent("—");
  });

  it("reports a state owned by another candidate as a data problem", async () => {
    entryBoardApi.mockResolvedValue(board([{ ...row("DUP", 1), state_conflict: true }]));
    render(<TradingPage/>);
    const section = await view();
    await waitFor(() => expect(chip(section, "DUP").dataset.entryGroup).toBe("SYSTEM"));
    expect(rowFor(section, "DUP")).toHaveTextContent("다른 후보의 상태가 이미 기록됨");
  });
});

describe("read-only presentation", () => {
  it("reads the entry board API and never recomputes premarket conditions or reads research", () => {
    const page = readFileSync("app/trading/page.tsx", "utf8");
    const component = readFileSync("components/entry-status-board.tsx", "utf8");
    expect(page).toContain("useApi(api.entryBoard");
    expect(page).not.toContain("api.research");
    [page, component].forEach(source => ["getMinuteBars", "kiwoom", "Kiwoom", "api.research"].forEach(token => expect(source).not.toContain(token)));
  });
});
