import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, renderHook, screen } from "@testing-library/react";
import { OrderTicket, TradeHistory, useLivePnl, LIVE_POLL_MS } from "@/components/crypto-paper-terminal";
import { LivePnlHeadline } from "@/components/crypto-terminal-layout";
import { costKrw, costUsdt, cryptoApi, krw, signedKrw, signedUsdt, usdt } from "@/lib/crypto-paper";
import type { CryptoState, LivePnl, OrderPreview, OrderPreviewSide, TradeRow } from "@/lib/crypto-paper";

afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.useRealTimers(); });

const state = (overrides: Partial<CryptoState> = {}): CryptoState => ({
  run_id: "paper-test", engine_version: "d4.1", leverage: "50", server_time_ms: 1_000_500,
  started_at_ms: 1_000_000, ledger_event_count: 9, input_record_count: 12, liquidation_count: 0,
  funding_grid_mismatches: 0, starting_capital_krw: "10000000",
  state: { mode: "MANUAL", modes: ["MANUAL", "AUTO", "AUTO_STOPPING", "EMERGENCY"],
    can_open_new_position: true, new_entry_blocked_reason: null, auto_available: false,
    auto_unavailable_reason: "AUTO_NOT_READY" },
  quote: { ts_ms: 1_000_000, best_bid: "84000.0", best_ask: "84000.1", spread: "0.1", mid: "84000.05",
    mark_price: "84000.05", last_price: "84000.0", index_price: "84010.2", funding_rate: "0.0001",
    next_funding_time_ms: 1_100_000, bid_depth_top5: "12", ask_depth_top5: "9" },
  account: { starting_capital_usdt: "7457", wallet_balance: "7457", available_balance: "7457",
    used_margin: "0", realized_pnl: "0", unrealized_pnl: "0", equity: "7457", cumulative_fees: "0",
    cumulative_funding_paid: "0", position_side: null, position_qty: "0", position_signed_qty: "0",
    avg_entry: null, leverage: null, entry_notional: "0", mark_notional: "0", maintenance_margin: "0",
    margin_ratio: null, liquidation_price: null, risk_tier: null, capital_base_usdt: "7457",
    reset_count: 0, last_reset_ts_ms: null },
  krw: { equity: "10000000", available_balance: "10000000", realized_pnl: "0", unrealized_pnl: "0",
    used_margin: "0", wallet_balance: "10000000" },
  fees: { version: "v", taker_rate: "0.00055", maker_rate: "0.0002", source: "s", effective_date: "d", basis: "b" },
  fx: { krw_per_usdt: "1341.00", source: "upbit", asof_utc: "t" }, slippage: { model: "NONE", bps: "0" },
  feed: { connected: true, connects: 1, reconnects: 0, book_gaps: 0, book_resyncs: 0, malformed: 0,
    messages: 1, last_message_ms: 1_000_400, last_error: null, book_ready: true },
  recovery: null, ...overrides,
});

const side = (s: "LONG" | "SHORT", overrides: Partial<OrderPreviewSide> = {}): OrderPreviewSide => ({
  side: s, feasible: true, qty: "1.000", leverage: "50", quote_ts_ms: 1_000_000,
  entry_fill_price: s === "LONG" ? "84000.1" : "84000.0", exit_fill_price: s === "LONG" ? "84000.0" : "84000.1",
  entry_fee: "46.2", exit_fee: "46.2", entry_slippage: "0.05", exit_slippage: "0.05",
  entry_slippage_pnl: "-0.05", exit_slippage_pnl: "-0.05", round_trip_cost: "92.5",
  immediate_round_trip_net: "-92.5", breakeven_mark_price: s === "LONG" ? "84092.6" : "83907.5",
  breakeven_move: "92.5", breakeven_move_pct: "0.0011", notional: "84000.1", required_margin: "1680",
  krw: { entry_fee: "61954", exit_fee: "61954", round_trip_cost: "124043", immediate_round_trip_net: "-124043",
    entry_slippage_pnl: "-67", exit_slippage_pnl: "-67" },
  ...overrides,
});

const preview = (overrides: Partial<OrderPreview> = {}): OrderPreview => ({
  run_id: "paper-test", server_time_ms: 1_000_500, feed_connected: true, feed_last_message_ms: 1_000_400,
  quote_ts_ms: 1_000_000,
  krw_per_usdt: "1341", sides: { LONG: side("LONG"), SHORT: side("SHORT") }, ...overrides,
});

const noop = () => {};

describe("pre-trade order preview", () => {
  it("shows the compact cost rows for both sides, won and breakeven, from the backend", () => {
    render(<OrderTicket state={state()} onAction={noop} busy={false} error={null} previewOverride={preview()} />);
    const panel = screen.getByTestId("order-preview");
    ["예상 진입 수수료", "예상 청산 수수료", "왕복 총비용", "손익분기"].forEach(label =>
      expect(panel).toHaveTextContent(label));
    expect(screen.getByTestId("preview-LONG-예상 진입 수수료")).toHaveTextContent("-61,954원");
    expect(screen.getByTestId("preview-SHORT-왕복 총비용")).toHaveTextContent("-124,043원");
    expect(screen.getByTestId("preview-LONG-손익분기")).toHaveTextContent("+0.11%");
    expect(panel).not.toHaveTextContent("손절");
    // Detail is closed on a phone and open on the wide layout.
    expect(screen.queryByTestId("order-preview-detail")).not.toBeInTheDocument();
  });

  it("opens the detail with fills, execution cost, breakeven price and the immediate net", () => {
    render(<OrderTicket state={state()} onAction={noop} busy={false} error={null} previewOverride={preview()} />);
    fireEvent.click(screen.getByTestId("order-preview-detail-toggle"));
    expect(screen.getByTestId("preview-detail-LONG-예상 진입 체결가")).toHaveTextContent("84,000.1");
    expect(screen.getByTestId("preview-detail-LONG-예상 청산 체결가")).toHaveTextContent("84,000.0");
    expect(screen.getByTestId("preview-detail-LONG-진입 체결비용")).toHaveTextContent("-67원");
    expect(screen.getByTestId("preview-detail-LONG-손익분기 가격 (Mark)")).toHaveTextContent("84,092.6");
    expect(screen.getByTestId("preview-detail-SHORT-즉시 청산 시 예상 순손익")).toHaveTextContent("-124,043원");
  });

  it("starts with the detail open on the wide layout", () => {
    render(<OrderTicket state={state()} onAction={noop} busy={false} error={null} wide previewOverride={preview()} />);
    expect(screen.getByTestId("order-preview-detail")).toBeInTheDocument();
  });

  it("keeps LONG/SHORT directly under the preview", () => {
    render(<OrderTicket state={state()} onAction={noop} busy={false} error={null} previewOverride={preview()} />);
    const panel = screen.getByTestId("order-preview");
    const long = screen.getByTestId("long-button");
    // Document order: preview before the buttons, nothing large in between.
    expect(panel.compareDocumentPosition(long) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("refuses to show numbers for a side the engine refused, with the safe size", () => {
    render(<OrderTicket state={state()} onAction={noop} busy={false} error={null} previewOverride={preview({
      sides: { LONG: side("LONG"), SHORT: { side: "SHORT", feasible: false, reject_stage: "EXIT",
        reject_code: "NO_LIQUIDITY", reject_message: "visible depth", safe_max_qty: "0.268" } } })} />);
    expect(screen.getByTestId("preview-SHORT-왕복 총비용")).toHaveTextContent("-");
    expect(screen.getByTestId("order-preview-reject-SHORT")).toHaveTextContent("호가 깊이 부족");
    expect(screen.getByTestId("order-preview-reject-SHORT")).toHaveTextContent("안전 최대 0.268 BTC");
  });

  it("judges age by the server's own receive time, not the exchange clock", () => {
    // The exchange timestamp is ahead of the server clock here; that must not read as fresh or stale.
    render(<OrderTicket state={state()} onAction={noop} busy={false} error={null}
      previewOverride={preview({ quote_ts_ms: 1_000_500 + 3_000 })} />);
    expect(screen.queryByTestId("order-preview-unavailable")).not.toBeInTheDocument();
    expect(screen.getByTestId("order-preview-age")).toHaveTextContent("0초 전");
  });

  it("withholds the preview on a stale book (5 s contract) and on a disconnected feed", () => {
    const { rerender } = render(<OrderTicket state={state()} onAction={noop} busy={false} error={null}
      previewOverride={preview({ feed_last_message_ms: 1_000_500 - 6_000 })} />);
    expect(screen.getByTestId("order-preview-unavailable")).toHaveTextContent("STALE");
    rerender(<OrderTicket state={state({ feed: { ...state().feed, connected: false } })} onAction={noop}
      busy={false} error={null} previewOverride={preview()} />);
    expect(screen.getByTestId("order-preview-unavailable")).toHaveTextContent("DISCONNECTED");
  });

  it("keeps HIGH RISK visible without opening the detail", () => {
    render(<OrderTicket state={state({ leverage: "50" })} sizing={{
      run_id: "p", leverage: "50", quote_ts_ms: 1, mark_price: "1", available_balance: "1",
      sides: { LONG: { side: "LONG", leverage: "50", max_qty: "1", max_feasible: true, reject_code: null,
        reject_message: null, instrument: { qty_step: "0.001", min_order_qty: "0.001", min_notional_value: "5", max_mkt_order_qty: "100" },
        presets: ["25%", "HALF", "75%", "MAX"].map(label => ({ label, fraction: "1", qty: "1.000", feasible: true,
          fill_price: "1", notional: "1", fee: "1", reserved_margin: "1", required_total: "1", liquidation_price: "1",
          margin_ratio: "1", risk_tier: 1, resulting_qty: "1", resulting_avg_entry: "1", available_after: "1",
          reject_code: null, reject_message: null, entry_feasible: true, exit_feasible: true, exit_fill_price: "1",
          exit_reject_code: null, exit_reject_message: null, quote_ts_ms: 1, best_bid: "1", best_ask: "1",
          mark_price: "1", bid_depth: "1", ask_depth: "1" })) } } }}
      onAction={noop} busy={false} error={null} previewOverride={preview()} />);
    fireEvent.click(screen.getByTestId("preset-MAX"));
    expect(screen.getByTestId("high-risk-badge")).toBeInTheDocument();
    expect(screen.getByTestId("long-button")).toBeEnabled(); // warned, not blocked
  });

  it("re-prices when the size, the mode or the leverage changes", async () => {
    vi.useFakeTimers();
    const calls: unknown[] = [];
    vi.spyOn(cryptoApi, "orderPreview").mockImplementation(async (_symbol, params) => { calls.push(params); return preview(); });
    const { rerender } = render(<OrderTicket state={state()} onAction={noop} busy={false} error={null} />);
    await act(async () => { await vi.advanceTimersByTimeAsync(200); });
    expect(calls.at(-1)).toEqual({ long_qty: "0.001", short_qty: "0.001" });

    fireEvent.change(screen.getByLabelText("주문 크기"), { target: { value: "0.250" } });
    await act(async () => { await vi.advanceTimersByTimeAsync(200); });
    expect(calls.at(-1)).toEqual({ long_qty: "0.250", short_qty: "0.250" });

    fireEvent.click(screen.getByRole("button", { name: "명목 (USDT)" }));
    await act(async () => { await vi.advanceTimersByTimeAsync(200); });
    expect(calls.at(-1)).toEqual({ notional_usdt: "0.250" });

    const before = calls.length;
    rerender(<OrderTicket state={state({ leverage: "20" })} onAction={noop} busy={false} error={null} />);
    await act(async () => { await vi.advanceTimersByTimeAsync(200); });
    expect(calls.length).toBe(before + 1);
  });

  it("does not fetch for the hidden twin", async () => {
    vi.useFakeTimers();
    const spy = vi.spyOn(cryptoApi, "orderPreview").mockResolvedValue(preview());
    render(<OrderTicket state={state()} onAction={noop} busy={false} error={null} previewEnabled={false} />);
    await act(async () => { await vi.advanceTimersByTimeAsync(500); });
    expect(spy).not.toHaveBeenCalled();
  });
});

// ------------------------------------------------------------------ live PnL

const live = (overrides: Partial<LivePnl> = {}): LivePnl => ({
  position_open: true, side: "LONG", qty: "1.419", leverage: "50", quote_ts_ms: 1_000_000,
  mark_price: "84072.8", unrealized_pnl: "9.75", unrealized_pct_of_margin: "0.0041",
  close_feasible: true, expected_position_net_if_closed: "-131.08",
  krw: { unrealized_pnl: "13073", expected_position_net_if_closed: "-175774" },
  server_time_ms: 1_000_300, feed_connected: true, feed_last_message_ms: 1_000_200, ...overrides,
});

describe("live PnL headline", () => {
  const holding = state({ account: { ...state().account!, position_side: "LONG", position_qty: "1.419",
    unrealized_pnl: "9.0", used_margin: "2385" }, krw: { ...state().krw!, unrealized_pnl: "12069" } });

  it("shows price PnL and the net if closed now side by side, never mixed", () => {
    render(<LivePnlHeadline state={holding} live={live()} />);
    expect(screen.getByTestId("live-unrealized")).toHaveTextContent("+13,073원");
    expect(screen.getByTestId("live-pnl")).toHaveTextContent("+0.41%");
    expect(screen.getByTestId("live-net-if-closed")).toHaveTextContent("-175,774원");
    expect(screen.getByTestId("live-source")).toHaveTextContent("실시간 호가 기준");
  });

  it("falls back to the 1 s figures and says so", () => {
    render(<LivePnlHeadline state={holding} live={null} />);
    expect(screen.getByTestId("live-unrealized")).toHaveTextContent("+12,069원");
    expect(screen.getByTestId("live-source")).toHaveTextContent("1초 갱신 기준");
  });

  it("withholds the net when the live tick is stale or the close cannot fill", () => {
    const { rerender } = render(<LivePnlHeadline state={holding} live={live({ server_time_ms: 1_000_200 + 6_000 })} />);
    expect(screen.getByTestId("live-net-unavailable")).toHaveTextContent("시세 지연");
    rerender(<LivePnlHeadline state={holding} live={live({ close_feasible: false })} />);
    expect(screen.getByTestId("live-net-unavailable")).toHaveTextContent("미리보기 불가");
  });

  it("polls fast only while enabled, sequentially", async () => {
    vi.useFakeTimers();
    let inflight = 0; let maxInflight = 0; let calls = 0;
    vi.spyOn(cryptoApi, "live").mockImplementation(async () => {
      calls++; inflight++; maxInflight = Math.max(maxInflight, inflight);
      await new Promise(resolve => setTimeout(resolve, 50));
      inflight--; return live();
    });
    const { result, rerender } = renderHook(({ on }) => useLivePnl(on), { initialProps: { on: true } });
    await act(async () => { await vi.advanceTimersByTimeAsync(LIVE_POLL_MS * 6); });
    expect(calls).toBeGreaterThanOrEqual(5);
    expect(maxInflight).toBe(1);
    expect(result.current?.position_open).toBe(true);
    const stopped = calls;
    rerender({ on: false });
    await act(async () => { await vi.advanceTimersByTimeAsync(LIVE_POLL_MS * 6); });
    expect(calls).toBeLessThanOrEqual(stopped + 1);
    expect(result.current).toBeNull();
  });
});

// ------------------------------------------------------------------ polish

describe("money formatting and mobile polish", () => {
  it("never prints a negative zero", () => {
    expect(signedKrw("-0.3")).toBe("0원");
    expect(krw("-0.4")).toBe("0원");
    expect(signedUsdt("-0.00001")).toBe("0.0000 USDT");
    expect(usdt("-0.001", 2)).toBe("0.00 USDT");
    expect(costKrw("0.2")).toBe("0원");
    expect(costUsdt("0.00001")).toBe("0.0000 USDT");
    expect(signedKrw("-1.6")).toBe("-2원");
    expect(costKrw("275000")).toBe("-275,000원");
  });

  it("keeps the cost-detail toggle on the same line as the trade number", () => {
    const row: TradeRow = { index: 1, side: "LONG", origin: "MANUAL", leverage: "50", opened_ts_ms: 1,
      closed_ts_ms: 2, hold_ms: 1, entry_price: "1", exit_price: "1", qty: "0.001", gross_pnl: "0",
      fees: "0", funding: "0", net_pnl: "0", max_adverse_excursion: "0", max_favourable_excursion: "0",
      liquidated: false, exits: 1, is_win: false };
    render(<TradeHistory trades={[row]} breakdowns={[{ index: 1, side: "LONG", closed_ts_ms: 2,
      gross_realized_pnl: "0", entry_fee: "0", exit_fee: "0", funding: "0", funding_pnl: "0",
      entry_slippage: "0", exit_slippage: "0", slippage: "0", slippage_pnl: "0", net_realized_pnl: "0",
      liquidated: false, exits: 1 }]} />);
    const cell = screen.getByTestId("trade-detail-toggle-1").closest("td")!;
    expect(cell.className).toContain("whitespace-nowrap");
  });
});
