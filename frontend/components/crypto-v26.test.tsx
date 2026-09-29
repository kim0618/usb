import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, renderHook, screen, waitFor } from "@testing-library/react";
import { ChartSection, MobilePositionCard, TimeframeTabs, use15sCandles, CANDLES_15S_POLL_MS } from "@/components/crypto-terminal-layout";
import { OrderTicket } from "@/components/crypto-paper-terminal";
import { candles15sToChart, cryptoApi, isTailUpdate } from "@/lib/crypto-paper";
import type { Candle15s, Candles15sResponse, CryptoState } from "@/lib/crypto-paper";

vi.mock("@/components/crypto-candle-chart", () => ({
  CandleChart: ({ candles, seconds, seriesKey }: { candles: unknown[]; seconds?: boolean; seriesKey?: string }) =>
    React.createElement("div", { "data-testid": "candle-chart-stub", "data-count": candles.length,
      "data-seconds": String(!!seconds), "data-key": seriesKey }),
}));

afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.useRealTimers(); });

const state = (overrides: Partial<CryptoState> = {}): CryptoState => ({
  run_id: "paper-test", engine_version: "d4.1", leverage: "10", server_time_ms: 1_000_500,
  started_at_ms: 1_000_000, ledger_event_count: 9, input_record_count: 12, liquidation_count: 0,
  funding_grid_mismatches: 0, starting_capital_krw: "10000000",
  state: { mode: "MANUAL", modes: ["MANUAL", "AUTO", "AUTO_STOPPING", "EMERGENCY"],
    can_open_new_position: true, new_entry_blocked_reason: null, auto_available: false,
    auto_unavailable_reason: "AUTO_NOT_READY" },
  quote: { ts_ms: 1_000_000, best_bid: "84000.0", best_ask: "84000.1", spread: "0.1", mid: "84000.05",
    mark_price: "84369.6", last_price: "84000.0", index_price: "84010.2", funding_rate: "0.0001",
    next_funding_time_ms: 1_100_000, bid_depth_top5: "12", ask_depth_top5: "9" },
  account: { starting_capital_usdt: "7457", wallet_balance: "7457", available_balance: "7457",
    used_margin: "7739.92", realized_pnl: "0", unrealized_pnl: "-305.2", equity: "7457", cumulative_fees: "0",
    cumulative_funding_paid: "0", position_side: "SHORT", position_qty: "0.921", position_signed_qty: "-0.921",
    avg_entry: "84038.2", leverage: "10", entry_notional: "0", mark_notional: "0", maintenance_margin: "0",
    margin_ratio: "28.99", liquidation_price: "92138.0", risk_tier: 1, capital_base_usdt: "7457",
    reset_count: 0, last_reset_ts_ms: null },
  krw: { equity: "10000000", available_balance: "10000000", realized_pnl: "0", unrealized_pnl: "-409299",
    used_margin: "0", wallet_balance: "10000000" },
  fees: { version: "v", taker_rate: "0.00055", maker_rate: "0.0002", source: "s", effective_date: "d", basis: "b" },
  fx: { krw_per_usdt: "1341.00", source: "upbit", asof_utc: "t" }, slippage: { model: "NONE", bps: "0" },
  feed: { connected: true, connects: 1, reconnects: 0, book_gaps: 0, book_resyncs: 0, malformed: 0,
    messages: 1, last_message_ms: 1_000_400, last_error: null, book_ready: true },
  recovery: null, ...overrides,
});
const flat = () => state({ account: { ...state().account!, position_side: null, position_qty: "0",
  position_signed_qty: "0", avg_entry: null, leverage: null, used_margin: "0", unrealized_pnl: "0" } });

describe("mobile position card", () => {
  it("shows what is held, both PnL figures, entry, mark and CLOSE before the chart", () => {
    render(<MobilePositionCard state={state()} openedMs={1_000_000 - 90_000} nowMs={1_000_000}
      onAction={() => {}} busy={false} />);
    const card = screen.getByTestId("mobile-position-card");
    expect(screen.getByTestId("mobile-position-side")).toHaveTextContent("SHORT");
    expect(card).toHaveTextContent("10x");
    expect(card).toHaveTextContent("0.921 BTC");
    expect(card).toHaveTextContent("1분 30초 보유");
    expect(screen.getByTestId("live-unrealized")).toHaveTextContent("-409,299원");
    expect(card).toHaveTextContent("청산 시 예상 순손익");
    expect(card).toHaveTextContent("84,038.2");
    expect(screen.getByTestId("mobile-close-button")).toBeEnabled();
    expect(screen.queryByTestId("mobile-position-detail")).not.toBeInTheDocument();
  });

  it("sends the same CLOSE order as the order panel, once", () => {
    const sent: unknown[] = [];
    const spy = vi.spyOn(cryptoApi, "order").mockResolvedValue({ state: state() });
    render(<MobilePositionCard state={state()} openedMs={null} nowMs={1} busy={false}
      onAction={run => { sent.push(run); void run(); }} />);
    fireEvent.click(screen.getByTestId("mobile-close-button"));
    expect(sent).toHaveLength(1);
    expect(spy).toHaveBeenCalledWith({ side: "SHORT", intent: "CLOSE", qty: "0.921" });
  });

  it("disables CLOSE while a request is in flight", () => {
    render(<MobilePositionCard state={state()} openedMs={null} nowMs={1} busy onAction={() => {}} />);
    expect(screen.getByTestId("mobile-close-button")).toBeDisabled();
  });

  it("folds liquidation, margin and the cost breakdown under the detail toggle", () => {
    render(<MobilePositionCard state={state()} openedMs={null} nowMs={1} onAction={() => {}} busy={false} />);
    fireEvent.click(screen.getByTestId("mobile-position-detail-toggle"));
    const detail = screen.getByTestId("mobile-position-detail");
    ["청산가", "사용 마진", "마진 비율"].forEach(label => expect(detail).toHaveTextContent(label));
    expect(detail).toHaveTextContent("92,138.0");
  });

  it("renders nothing when flat", () => {
    const { container } = render(<MobilePositionCard state={flat()} openedMs={null} nowMs={1} onAction={() => {}} busy={false} />);
    expect(container).toBeEmptyDOMElement();
  });
});

describe("compact order panel", () => {
  it("folds EMERGENCY to one line while flat and opens it on tap", () => {
    render(<OrderTicket state={flat()} onAction={() => {}} busy={false} error={null} previewEnabled={false} />);
    expect(screen.queryByTestId("emergency-button")).not.toBeInTheDocument();
    fireEvent.click(screen.getByTestId("emergency-toggle"));
    expect(screen.getByTestId("emergency-button")).toBeInTheDocument();
  });

  it("keeps EMERGENCY open while a position is held", () => {
    render(<OrderTicket state={state()} onAction={() => {}} busy={false} error={null} previewEnabled={false} />);
    expect(screen.getByTestId("emergency-button")).toBeInTheDocument();
    expect(screen.queryByTestId("emergency-toggle")).not.toBeInTheDocument();
  });
});

// ------------------------------------------------------------------ 15 s

const c15 = (start: number, confirmed = true, overrides: Partial<Candle15s> = {}): Candle15s => ({
  start_ms: start, end_ms: start + 15_000, open: "1", high: "2", low: "0.5", close: "1.5", volume: "3",
  trade_count: 4, confirmed, partial: false, ...overrides,
});
const body = (candles: Candle15s[], current: Candle15s | null, status: Candles15sResponse["status"] = "CONNECTED"): Candles15sResponse =>
  ({ timeframe: "15s", status, candles, current, server_time_ms: 1, coverage_from_ms: 1_000_000 });

describe("15 s candles", () => {
  it("offers 15s first in the timeframe row", () => {
    const seen: unknown[] = [];
    render(<TimeframeTabs value={1} onChange={next => seen.push(next)} />);
    const buttons = screen.getAllByRole("button");
    expect(buttons[0]).toHaveTextContent("15s");
    fireEvent.click(screen.getByTestId("timeframe-15s"));
    expect(seen).toEqual(["15s"]);
  });

  it("loads history once, then asks only for what is new, and keeps the open candle last", async () => {
    vi.useFakeTimers();
    const calls: (number | null | undefined)[] = [];
    const replies = [
      body([c15(0), c15(15_000)], c15(30_000, false)),
      body([c15(30_000)], c15(45_000, false, { close: "9" })),
    ];
    vi.spyOn(cryptoApi, "candles15s").mockImplementation(async since => { calls.push(since); return replies.shift() ?? body([], c15(45_000, false)); });
    const { result } = renderHook(() => use15sCandles(true));
    await act(async () => { await vi.advanceTimersByTimeAsync(10); });
    expect(calls[0]).toBeNull();
    expect(result.current.candles.map(c => c.time)).toEqual([0, 15, 30]);
    await act(async () => { await vi.advanceTimersByTimeAsync(CANDLES_15S_POLL_MS); });
    expect(calls[1]).toBe(15_000);
    expect(result.current.candles.map(c => c.time)).toEqual([0, 15, 30, 45]);
    const last = result.current.candles.at(-1) as { close: number; confirmed: boolean };
    expect(last.close).toBe(9);
    expect(last.confirmed).toBe(false);
  });

  it("does not poll while another timeframe is on screen", async () => {
    vi.useFakeTimers();
    const spy = vi.spyOn(cryptoApi, "candles15s").mockResolvedValue(body([], null));
    renderHook(() => use15sCandles(false));
    await act(async () => { await vi.advanceTimersByTimeAsync(CANDLES_15S_POLL_MS * 3); });
    expect(spy).not.toHaveBeenCalled();
  });

  it("falls back to an unavailable note when the trade stream is down, without touching trading", async () => {
    vi.useFakeTimers();
    vi.spyOn(cryptoApi, "candles15s").mockResolvedValue(body([], null, "DISCONNECTED"));
    render(<ChartSection state={flat()} bars={[]} timeframe="15s" onTimeframe={() => {}} />);
    await act(async () => { await vi.advanceTimersByTimeAsync(10); });
    expect(screen.getByTestId("candles-15s-note")).toHaveTextContent("연결 끊김");
    expect(screen.getByTestId("candles-15s-note")).toHaveTextContent("주문·체결과 무관");
  });

  it("feeds the chart 15 s candles with seconds on the axis and the mark overlay intact", async () => {
    // Real timers: the chart is a dynamic import and arrives on a later tick.
    vi.spyOn(cryptoApi, "candles15s").mockResolvedValue(body([c15(0), c15(15_000)], c15(30_000, false)));
    render(<ChartSection state={state()} bars={[]} timeframe="15s" onTimeframe={() => {}} />);
    await waitFor(() => expect(screen.getByTestId("candle-chart-stub")).toHaveAttribute("data-count", "3"));
    const chart = screen.getByTestId("candle-chart-stub");
    expect(chart).toHaveAttribute("data-seconds", "true");
    expect(chart).toHaveAttribute("data-key", "15s");
    expect(screen.getByTestId("candles-15s-note")).toHaveTextContent("15초봉 기록 수집 중");
  });

  it("converts backend rows for drawing only", () => {
    expect(candles15sToChart([c15(30_000)])[0]).toEqual({ time: 30, open: 1, high: 2, low: 0.5, close: 1.5, volume: 3, confirmed: true });
  });

  it("updates the chart incrementally only when the same series moved at its tail", () => {
    const prev = { key: "15s", times: [0, 15, 30] };
    expect(isTailUpdate(prev, "15s", [0, 15, 30])).toBe(true);          // open candle amended
    expect(isTailUpdate(prev, "15s", [0, 15, 30, 45])).toBe(true);      // one appended
    expect(isTailUpdate(prev, "1", [0, 15, 30, 45])).toBe(false);       // timeframe switched
    expect(isTailUpdate(prev, "15s", [15, 30, 45])).toBe(false);        // window shifted
    expect(isTailUpdate(null, "15s", [0])).toBe(false);                 // first draw
  });
});
