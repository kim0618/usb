import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { ChartSection, CANDLES_15S_POLL_MS } from "@/components/crypto-terminal-layout";
import {
  VISIBLE_15S_BARS, candles15sNote, candles15sToChart, cryptoApi, isWhitespace, offscreenOverlays, visible15sBars,
} from "@/lib/crypto-paper";
import type { Candle15s, Candles15sResponse, ChartOverlay, CryptoState } from "@/lib/crypto-paper";

// The chart stub reports a visible price range, the way the real chart does after each draw.
vi.mock("@/components/crypto-candle-chart", () => ({
  CandleChart: ({ candles, onPriceRange }: { candles: unknown[]; onPriceRange?: (r: { from: number; to: number }) => void }) => {
    React.useEffect(() => { onPriceRange?.({ from: 84_300, to: 84_500 }); }, [onPriceRange]);
    return React.createElement("div", { "data-testid": "candle-chart-stub", "data-count": candles.length });
  },
}));

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

const c15 = (start: number): Candle15s => ({ start_ms: start, end_ms: start + 15_000, open: "1", high: "2",
  low: "0.5", close: "1.5", volume: "3", trade_count: 4, confirmed: true, partial: false });

describe("15 s data for the chart", () => {
  it("keeps empty 15 s windows on the time axis as whitespace, never as a candle", () => {
    const points = candles15sToChart([c15(0), c15(45_000)]);
    expect(points.map(p => p.time)).toEqual([0, 15, 30, 45]);
    expect(points.map(p => isWhitespace(p))).toEqual([false, true, true, false]);
    expect(points.filter(p => !isWhitespace(p))).toHaveLength(2);
  });

  it("separates a quiet market from a connection problem", () => {
    expect(candles15sNote("CONNECTED", 40, 1)).toMatchObject({ warn: false });
    expect(candles15sNote("CONNECTED_WAITING_FOR_TRADE", 40, 1)).toEqual(
      expect.objectContaining({ warn: false, text: expect.stringContaining("체결 대기") }));
    expect(candles15sNote("RECONNECTING", 40, 1)).toEqual(
      expect.objectContaining({ warn: true, text: expect.stringContaining("재연결 중") }));
    expect(candles15sNote("DISCONNECTED", 40, 1).text).toContain("연결 끊김 · 주문·체결과 무관");
    expect(candles15sNote("STALE", 40, 1).warn).toBe(true);
    expect(candles15sNote("CONNECTED", 2, 1).text).toContain("기록 수집 중");
  });

  it("shows fewer, wider candles by default: about 45 on a phone and 80 on a desktop", () => {
    expect(visible15sBars(340)).toBe(VISIBLE_15S_BARS.narrow);
    expect(visible15sBars(722)).toBe(VISIBLE_15S_BARS.wide);
    expect(VISIBLE_15S_BARS.narrow).toBeLessThan(VISIBLE_15S_BARS.wide);
  });

  it("names overlay lines that are outside the price range on screen", () => {
    const overlays: ChartOverlay[] = [
      { id: "mark", price: 84_400, label: "Mark", kind: "MARK" },
      { id: "entry", price: 84_038.2, label: "진입", kind: "ENTRY" },
      { id: "liq", price: 92_138, label: "청산", kind: "LIQUIDATION" },
    ];
    const off = offscreenOverlays(overlays, { from: 84_300, to: 84_500 });
    expect(off.map(o => [o.id, o.direction])).toEqual([["entry", "DOWN"], ["liq", "UP"]]);
    expect(offscreenOverlays(overlays, null)).toEqual([]);
  });

  it("re-reads the open candle twice a second", () => {
    expect(CANDLES_15S_POLL_MS).toBe(500);
  });
});

const state = (): CryptoState => ({
  run_id: "p", engine_version: "d4.1", leverage: "10", server_time_ms: 1_000_500, started_at_ms: 1, ledger_event_count: 1,
  input_record_count: 1, liquidation_count: 0, funding_grid_mismatches: 0, starting_capital_krw: "1",
  state: { mode: "MANUAL", modes: ["MANUAL"], can_open_new_position: true, new_entry_blocked_reason: null,
    auto_available: false, auto_unavailable_reason: "AUTO_NOT_READY" },
  quote: { ts_ms: 1, best_bid: "84400.0", best_ask: "84400.1", spread: "0.1", mid: "84400.05", mark_price: "84400.0",
    last_price: "84400.0", index_price: "84400.0", funding_rate: "0.0001", next_funding_time_ms: 1,
    bid_depth_top5: "1", ask_depth_top5: "1" },
  account: { starting_capital_usdt: "1", wallet_balance: "1", available_balance: "1", used_margin: "1", realized_pnl: "0",
    unrealized_pnl: "0", equity: "1", cumulative_fees: "0", cumulative_funding_paid: "0", position_side: "SHORT",
    position_qty: "0.921", position_signed_qty: "-0.921", avg_entry: "84038.2", leverage: "10", entry_notional: "0",
    mark_notional: "0", maintenance_margin: "0", margin_ratio: "29", liquidation_price: "92138.0", risk_tier: 1,
    capital_base_usdt: "1", reset_count: 0, last_reset_ts_ms: null },
  krw: null, fees: { version: "v", taker_rate: "0", maker_rate: "0", source: "s", effective_date: "d", basis: "b" },
  fx: { krw_per_usdt: "1", source: "s", asof_utc: "t" }, slippage: { model: "NONE", bps: "0" },
  feed: { connected: true, connects: 1, reconnects: 0, book_gaps: 0, book_resyncs: 0, malformed: 0, messages: 1,
    last_message_ms: 1_000_400, last_error: null, book_ready: true }, recovery: null,
});
const reply = (status: Candles15sResponse["status"], n = 10): Candles15sResponse => ({
  timeframe: "15s", status, candles: Array.from({ length: n }, (_, k) => c15(k * 15_000)), current: null,
  server_time_ms: 1, coverage_from_ms: 1_000_000,
});

describe("15 s section", () => {
  it("shows a quiet market as neutral, not as a warning", async () => {
    vi.spyOn(cryptoApi, "candles15s").mockResolvedValue(reply("CONNECTED_WAITING_FOR_TRADE"));
    render(<ChartSection state={state()} bars={[]} timeframe="15s" onTimeframe={() => {}} />);
    await waitFor(() => expect(screen.getByTestId("candles-15s-note")).toHaveTextContent("체결 대기"));
    expect(screen.getByTestId("candles-15s-note").className).toContain("text-muted");
  });

  it("warns only on a real disconnect", async () => {
    vi.spyOn(cryptoApi, "candles15s").mockResolvedValue(reply("DISCONNECTED"));
    render(<ChartSection state={state()} bars={[]} timeframe="15s" onTimeframe={() => {}} />);
    await waitFor(() => expect(screen.getByTestId("candles-15s-note")).toHaveTextContent("연결 끊김"));
    expect(screen.getByTestId("candles-15s-note").className).toContain("text-warning");
  });

  it("says where an entry line off the chart is, instead of stretching the scale to it", async () => {
    vi.spyOn(cryptoApi, "candles15s").mockResolvedValue(reply("CONNECTED"));
    render(<ChartSection state={state()} bars={[]} timeframe="15s" onTimeframe={() => {}} />);
    await waitFor(() => expect(screen.getByTestId("offscreen-overlays")).toHaveTextContent("진입 84,038.2 ↓ 화면 밖"));
    expect(screen.getByTestId("offscreen-overlays")).toHaveTextContent("↑ 화면 밖");
  });
});

describe("15 s price scale floor", () => {
  it("keeps a few flat candles from filling the whole pane, and leaves real moves alone", async () => {
    const { widenPriceRange } = await vi.importActual<typeof import("@/components/crypto-candle-chart")>("@/components/crypto-candle-chart");
    expect(widenPriceRange({ priceRange: { minValue: 84_363.4, maxValue: 84_363.5 } }, 10))
      .toEqual({ priceRange: { minValue: 84_358.45, maxValue: 84_368.45 } });
    const wide = { priceRange: { minValue: 84_300, maxValue: 84_330 } };
    expect(widenPriceRange(wide, 10)).toBe(wide);
    expect(widenPriceRange({ priceRange: { minValue: 1, maxValue: 1.1 } }, 0)).toEqual({ priceRange: { minValue: 1, maxValue: 1.1 } });
    expect(widenPriceRange(null, 10)).toBeNull();
  });
});
