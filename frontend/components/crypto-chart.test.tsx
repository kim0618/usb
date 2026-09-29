import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import {
  ChartSection, Disclosure, FeedDot, MarketHeader, PositionStrip, QuoteStrip, TimeframeTabs,
} from "@/components/crypto-terminal-layout";
import { PerformancePanel } from "@/components/crypto-paper-terminal";
import {
  aggregateCandles, describeReset, feedStatus, holdingDuration, positionOpenedMs,
  positionOverlays, positionReturnPct, resetScopeNote, volumeSeries,
} from "@/lib/crypto-paper";
import type { ChartBar, CryptoState, LedgerEvent, OpenPositionPnl } from "@/lib/crypto-paper";

// The chart itself is a canvas library loaded dynamically; these tests are about the data that
// reaches it and the chrome around it, so the component is stubbed.
vi.mock("@/components/crypto-candle-chart", () => ({
  CandleChart: ({ candles }: { candles: unknown[] }) =>
    React.createElement("div", { "data-testid": "candle-chart-stub", "data-count": candles.length }),
}));

afterEach(cleanup);

const bar = (startMs: number, o: string, h: string, l: string, c: string, v = "1",
             confirmed = true): ChartBar =>
  ({ start_ms: startMs, open: o, high: h, low: l, close: c, volume: v, confirmed });

const M = 60_000;

describe("candle aggregation", () => {
  it("passes 1m bars through as candles in seconds", () => {
    const candles = aggregateCandles([bar(0, "1", "3", "0.5", "2", "10")], 1);
    expect(candles).toEqual([{ time: 0, open: 1, high: 3, low: 0.5, close: 2, volume: 10, confirmed: true }]);
  });

  it("folds three minutes into one 3m candle, first open and last close", () => {
    const candles = aggregateCandles([
      bar(0 * M, "100", "110", "95", "105", "1"),
      bar(1 * M, "105", "130", "104", "120", "2"),
      bar(2 * M, "120", "125", "90", "99", "3"),
    ], 3);
    expect(candles).toHaveLength(1);
    expect(candles[0]).toMatchObject({ time: 0, open: 100, high: 130, low: 90, close: 99, volume: 6 });
  });

  it("aggregates 5m on wall-clock boundaries, not on where the data happens to start", () => {
    // Bars from 3m..7m must split 3,4 into the 0-5 bucket and 5,6,7 into the 5-10 bucket.
    const bars = [3, 4, 5, 6, 7].map(minute => bar(minute * M, "10", "10", "10", "10", "1"));
    const candles = aggregateCandles(bars, 5);
    expect(candles.map(candle => candle.time)).toEqual([0, 300]);
    expect(candles.map(candle => candle.volume)).toEqual([2, 3]);
  });

  it("aggregates 15m the same way", () => {
    const bars = Array.from({ length: 30 }, (_, minute) =>
      bar(minute * M, "1", "1", "1", "1", "1"));
    const candles = aggregateCandles(bars, 15);
    expect(candles.map(candle => candle.time)).toEqual([0, 900]);
    expect(candles.every(candle => candle.volume === 15)).toBe(true);
  });

  it("sums volume across the bucket", () => {
    const candles = aggregateCandles([
      bar(0, "1", "1", "1", "1", "1.5"), bar(M, "1", "1", "1", "1", "2.25"),
      bar(2 * M, "1", "1", "1", "1", "0.25"),
    ], 3);
    expect(candles[0].volume).toBeCloseTo(4);
  });

  it("marks a bucket unconfirmed while any minute inside it is still forming", () => {
    const candles = aggregateCandles([
      bar(0, "1", "1", "1", "1", "1", true),
      bar(M, "1", "1", "1", "1", "1", true),
      bar(2 * M, "1", "1", "1", "1", "1", false),
    ], 3);
    // Painting this as final would put a candle on screen that later changes shape.
    expect(candles[0].confirmed).toBe(false);
  });

  it("sorts out-of-order bars rather than trusting arrival order", () => {
    const candles = aggregateCandles([
      bar(2 * M, "120", "125", "90", "99"), bar(0, "100", "110", "95", "105"),
      bar(M, "105", "130", "104", "120"),
    ], 3);
    expect(candles[0]).toMatchObject({ open: 100, close: 99, high: 130, low: 90 });
  });

  it("skips bars whose prices are not numbers instead of poisoning the candle", () => {
    const candles = aggregateCandles([
      bar(0, "100", "110", "95", "105"), bar(M, "", "x", "", ""),
    ], 3);
    expect(candles[0]).toMatchObject({ open: 100, high: 110, low: 95, close: 105 });
  });

  it("returns nothing for no bars", () => {
    expect(aggregateCandles([], 5)).toEqual([]);
  });

  it("colours volume by the candle direction", () => {
    const candles = aggregateCandles([
      bar(0, "100", "110", "95", "105"), bar(5 * M, "105", "106", "90", "95"),
    ], 1);
    expect(volumeSeries(candles, "up", "down").map(row => row.color)).toEqual(["up", "down"]);
  });
});

// ------------------------------------------------------------------ fixtures

const baseState = (overrides: Partial<CryptoState> = {}): CryptoState => ({
  run_id: "paper-test", engine_version: "d4.1", leverage: "10", server_time_ms: 1_000_500,
  started_at_ms: 1_000_000, ledger_event_count: 9, input_record_count: 12, liquidation_count: 0,
  funding_grid_mismatches: 0, starting_capital_krw: "1000000",
  state: {
    mode: "MANUAL", modes: ["MANUAL", "AUTO", "AUTO_STOPPING", "EMERGENCY"],
    can_open_new_position: true, new_entry_blocked_reason: null, auto_available: false,
    auto_unavailable_reason: "AUTO_NOT_READY",
  },
  quote: {
    ts_ms: 1_000_000, best_bid: "86000.0", best_ask: "86000.1", spread: "0.1", mid: "86000.05",
    mark_price: "86000.05", last_price: "86000.0", index_price: "86010.2",
    funding_rate: "0.0001", next_funding_time_ms: 1_100_000,
    bid_depth_top5: "12.345", ask_depth_top5: "9.876",
  },
  account: {
    starting_capital_usdt: "744.047619", wallet_balance: "744.047619",
    available_balance: "744.047619", used_margin: "0", realized_pnl: "0", unrealized_pnl: "0",
    equity: "744.047619", cumulative_fees: "0", cumulative_funding_paid: "0",
    position_side: null, position_qty: "0", position_signed_qty: "0", avg_entry: null,
    leverage: null, entry_notional: "0", mark_notional: "0", maintenance_margin: "0",
    margin_ratio: null, liquidation_price: null, risk_tier: null,
    capital_base_usdt: "744.047619", reset_count: 0, last_reset_ts_ms: null,
  },
  krw: { equity: "1000000", available_balance: "1000000", realized_pnl: "0",
         unrealized_pnl: "0", used_margin: "0", wallet_balance: "1000000" },
  fees: { version: "v1", taker_rate: "0.00055", maker_rate: "0.0002", source: "test",
          effective_date: "2026-09-02", basis: "OFFICIAL_PUBLIC_VIP0_TIER_ASSUMED" },
  fx: { krw_per_usdt: "1341.00", source: "upbit", asof_utc: "2026-09-23T04:55:48Z" },
  slippage: { model: "NONE", bps: "0" },
  feed: { connected: true, connects: 1, reconnects: 0, book_gaps: 0, book_resyncs: 0,
          malformed: 0, messages: 4210, last_message_ms: 1_000_200, last_error: null,
          book_ready: true },
  recovery: null,
  ...overrides,
});

const holding = (side: "LONG" | "SHORT" = "LONG"): CryptoState => {
  const state = baseState();
  return { ...state, account: { ...state.account!, position_side: side, position_qty: "0.050",
    position_signed_qty: side === "LONG" ? "0.050" : "-0.050", avg_entry: "86000.0",
    leverage: "10", used_margin: "430.0", unrealized_pnl: "4.3", liquidation_price: "78100.0",
    margin_ratio: "3.10", risk_tier: 1, mark_notional: "4300.0", entry_notional: "4300.0" } };
};

const pnlPreview = (overrides: Partial<OpenPositionPnl> = {}): OpenPositionPnl => ({
  position_open: true, side: "LONG", qty: "0.500", leverage: "50", quote_ts_ms: 1_000_000,
  mark_price: "100008.0", unrealized_pnl: "3.95", entry_fee: "30.00003", partial_exit_fee: "0",
  partial_realized_pnl: "0", funding: "0", funding_pnl: "0", entry_slippage: "0.05",
  segment_realized_pnl: "-20", segment_net_pnl: "-24", close_feasible: true,
  close_reject_code: null, close_reject_message: null, expected_close_fill_price: "100008.0",
  expected_close_reference_price: "100008.0", expected_close_fee: "30.0024",
  expected_close_slippage: "0", expected_close_slippage_pnl: "0", expected_close_spread_cost: "0",
  expected_close_depth_cost: "0", expected_segment_net_if_closed: "-80.0",
  expected_position_net_if_closed: "-56.054",
  krw: { unrealized_pnl: "1100000", entry_fee: "275000", funding_pnl: "0", expected_close_fee: "275000",
    expected_close_slippage_pnl: "-110000", expected_position_net_if_closed: "440000",
    expected_segment_net_if_closed: "-107520" },
  ...overrides,
});

// ------------------------------------------------------------------ overlays

describe("chart overlays", () => {
  it("always draws the mark line", () => {
    expect(positionOverlays(baseState()).map(o => o.kind)).toEqual(["MARK"]);
  });

  it("adds entry and liquidation lines while a position is open", () => {
    const overlays = positionOverlays(holding());
    expect(overlays.map(o => o.kind)).toEqual(["MARK", "ENTRY", "LIQUIDATION"]);
    expect(overlays.find(o => o.kind === "ENTRY")!.price).toBe(86000);
    expect(overlays.find(o => o.kind === "LIQUIDATION")!.price).toBe(78100);
  });

  it("invents no target or stop line", () => {
    // There is no strategy in this system, so a level drawn for either would be fabricated, and
    // a fabricated line on a price chart reads exactly like a real one.
    const kinds = positionOverlays(holding()).map(o => o.kind);
    expect(kinds).not.toContain("TARGET");
    expect(kinds).not.toContain("STOP");
  });

  it("omits a liquidation line the account does not have", () => {
    const state = holding();
    state.account!.liquidation_price = null;
    expect(positionOverlays(state).map(o => o.kind)).toEqual(["MARK", "ENTRY"]);
  });
});

describe("position figures", () => {
  it("reports return against the margin actually committed, not the notional", () => {
    // 4.3 on 430 of margin is 1%, which is what a 10x operator feels; against notional it would
    // read 0.1% and understate the position tenfold.
    expect(positionReturnPct(holding())).toBeCloseTo(1);
  });

  it("has no return to report while flat", () => {
    expect(positionReturnPct(baseState())).toBeNull();
  });

  it("recovers the entry time from the ledger, newest event first", () => {
    const events = [
      { seq: 5, ts_ms: 5_000, event_type: "FILL" },
      { seq: 4, ts_ms: 4_000, event_type: "POSITION_OPEN" },
      { seq: 1, ts_ms: 1_000, event_type: "RUN_START" },
    ] as LedgerEvent[];
    expect(positionOpenedMs(events)).toBe(4_000);
  });

  it("reports no entry time once the position has been closed", () => {
    const events = [
      { seq: 6, ts_ms: 6_000, event_type: "POSITION_CLOSE" },
      { seq: 4, ts_ms: 4_000, event_type: "POSITION_OPEN" },
    ] as LedgerEvent[];
    expect(positionOpenedMs(events)).toBeNull();
  });

  it("formats holding time in the units a scalper reads", () => {
    expect(holdingDuration(0, 45_000)).toBe("45초");
    expect(holdingDuration(0, 125_000)).toBe("2분 5초");
    expect(holdingDuration(0, 7_300_000)).toBe("2시간 1분");
    expect(holdingDuration(null, 1)).toBeNull();
  });
});

// ------------------------------------------------------------------ feed status

describe("feed status", () => {
  it("is LIVE on a fresh tick", () => {
    expect(feedStatus(baseState())).toBe("LIVE");
  });

  it("is STALE when ticks stop but the socket is still up", () => {
    expect(feedStatus(baseState({ server_time_ms: 1_010_000 }))).toBe("STALE");
  });

  it("is DISCONNECTED when the socket is down", () => {
    const state = baseState();
    expect(feedStatus({ ...state, feed: { ...state.feed, connected: false } })).toBe("DISCONNECTED");
  });

  it("is DISCONNECTED when there is no quote at all", () => {
    expect(feedStatus(baseState({ quote: null }))).toBe("DISCONNECTED");
  });

  it("shows the status on screen", () => {
    render(<FeedDot status="STALE" />);
    expect(screen.getByTestId("feed-status")).toHaveAttribute("data-status", "STALE");
    expect(screen.getByTestId("feed-status")).toHaveTextContent("시세 지연");
  });
});

// ------------------------------------------------------------------ layout

describe("terminal layout", () => {
  it("puts the price and a compact account summary in the header", () => {
    render(<MarketHeader state={baseState()} performance={null} />);
    expect(screen.getByTestId("mark-price")).toHaveTextContent("86,000.1");
    const compact = screen.getByTestId("account-compact");
    ["자산", "주문가능", "미실현", "현재 손익"].forEach(label =>
      expect(compact).toHaveTextContent(label));
    // KRW leads because the account was funded in won.
    expect(compact).toHaveTextContent("1,000,000원");
  });

  it("offers 15s and the four minute timeframes", () => {
    render(<TimeframeTabs value={1} onChange={() => {}} />);
    ["15s", "1", "3", "5", "15"].forEach(minutes =>
      expect(screen.getByTestId(`timeframe-${minutes}`)).toBeInTheDocument());
    expect(screen.getByTestId("timeframe-1")).toHaveAttribute("aria-pressed", "true");
  });

  it("switches timeframe on click", () => {
    const seen: (number | string)[] = [];
    render(<TimeframeTabs value={1} onChange={next => seen.push(next)} />);
    fireEvent.click(screen.getByTestId("timeframe-15"));
    expect(seen).toEqual([15]);
  });

  it("keeps bid, ask and spread on one compact line", () => {
    render(<QuoteStrip state={baseState()} />);
    const strip = screen.getByTestId("quote-strip");
    expect(strip).toHaveTextContent("86,000.0");
    expect(strip).toHaveTextContent("86,000.1");
    expect(strip).toHaveTextContent("0.10");
  });

  it("feeds the chart candles built from the selected timeframe", async () => {
    // The chart is a dynamic import, so it arrives on a later tick than the rest of the section.
    const bars = Array.from({ length: 6 }, (_, minute) =>
      bar(minute * M, "1", "1", "1", "1"));
    const { rerender } = render(
      <ChartSection state={baseState()} bars={bars} timeframe={1} onTimeframe={() => {}} />);
    expect(await screen.findByTestId("candle-chart-stub")).toHaveAttribute("data-count", "6");
    rerender(<ChartSection state={baseState()} bars={bars} timeframe={3} onTimeframe={() => {}} />);
    expect(await screen.findByTestId("candle-chart-stub")).toHaveAttribute("data-count", "2");
  });

  it("says flat in one line instead of a large empty card", () => {
    render(<PositionStrip state={baseState()} openedMs={null} nowMs={1_000_500} />);
    expect(screen.getByTestId("position-strip")).toHaveTextContent("현재 포지션 없음");
    expect(screen.queryByTestId("position-side")).not.toBeInTheDocument();
  });

  it("expands with the numbers that matter once a position is open", () => {
    render(<PositionStrip state={holding("SHORT")} openedMs={1_000_000 - 90_000}
      nowMs={1_000_000} />);
    expect(screen.getByTestId("position-side")).toHaveTextContent("SHORT");
    expect(screen.getByTestId("position-upnl")).toHaveTextContent("+4.3");
    const strip = screen.getByTestId("position-strip");
    ["수량", "진입가", "Mark", "청산가", "사용 마진", "마진 비율"].forEach(label =>
      expect(strip).toHaveTextContent(label));
    expect(strip).toHaveTextContent("1분 30초 보유");
    // The headline shows both figures; without a live answer it falls back to the 1 s account.
    expect(screen.getByTestId("live-pnl")).toHaveTextContent("현재 포지션 손익");
    expect(screen.getByTestId("live-pnl")).toHaveTextContent("청산 시 예상 순손익");
    expect(screen.getByTestId("live-source")).toHaveTextContent("1초 갱신 기준");
  });

  it("separates price PnL from every cost, showing the engine's numbers verbatim", () => {
    render(<PositionStrip state={holding("LONG")} openedMs={null} nowMs={1_000_000}
      preview={pnlPreview()} />);
    const box = screen.getByTestId("position-pnl-breakdown");
    ["포지션 손익 (Mark 기준 미실현)", "진입 수수료 (확정)", "현재까지 Funding", "예상 청산 수수료",
      "예상 체결비용 (슬리피지·스프레드)"].forEach(label => expect(box).toHaveTextContent(label));
    // Won as sent by the backend, USDT beside it. Nothing is converted or negated here.
    expect(box).toHaveTextContent("+1,100,000원");
    expect(box).toHaveTextContent("-275,000원");
    expect(box).toHaveTextContent("-30.0024 USDT");
    expect(box).toHaveTextContent("-110,000원");
    // The totals are the backend's, not a sum done here.
    expect(screen.getByTestId("position-net-if-closed")).toHaveTextContent("+440,000원");
    expect(screen.getByTestId("position-net-if-closed")).toHaveTextContent("-56.0540 USDT");
    expect(screen.getByTestId("segment-net-if-closed")).toHaveTextContent("-80.0000 USDT");
    expect(box).toHaveTextContent("청산 시 현재 구간 총손익");
    expect(screen.getByTestId("preview-quote-age")).toHaveTextContent("현재 호가 기준 · 1초 전");
    expect(box).not.toHaveTextContent("부분청산");
  });

  it("says why the close preview is missing instead of showing a made-up total", () => {
    render(<PositionStrip state={holding("LONG")} openedMs={null} nowMs={1_000_000}
      preview={pnlPreview({ close_feasible: false, close_reject_code: "NO_LIQUIDITY",
        expected_close_fee: null, expected_position_net_if_closed: null,
        expected_segment_net_if_closed: null })} />);
    expect(screen.getByTestId("close-preview-unavailable"))
      .toHaveTextContent("미리보기 불가: 청산 가능한 호가 깊이 부족");
    expect(screen.queryByTestId("position-net-if-closed")).not.toBeInTheDocument();
    expect(screen.getByTestId("position-pnl-breakdown")).toHaveTextContent("+3.9500 USDT");
  });

  it("withholds the preview when its book is older than the 5 s STALE contract", () => {
    render(<PositionStrip state={holding("LONG")} openedMs={null} nowMs={1_000_000}
      preview={pnlPreview({ quote_ts_ms: 1_000_500 - 6_000 })} />);
    expect(screen.getByTestId("close-preview-unavailable")).toHaveTextContent("STALE market data");
    expect(screen.getByTestId("preview-quote-age")).toHaveTextContent("6초 전");
    expect(screen.queryByTestId("position-net-if-closed")).not.toBeInTheDocument();
    expect(screen.getByTestId("position-pnl-breakdown")).not.toHaveTextContent("예상 청산 수수료");
  });

  it("withholds the preview when the feed is disconnected", () => {
    const state = holding("LONG");
    render(<PositionStrip state={{ ...state, feed: { ...state.feed, connected: false } }} openedMs={null}
      nowMs={1_000_000} preview={pnlPreview()} />);
    expect(screen.getByTestId("close-preview-unavailable")).toHaveTextContent("DISCONNECTED");
  });

  it("shows no breakdown when the server has none (older deployment)", () => {
    render(<PositionStrip state={holding("LONG")} openedMs={null} nowMs={1_000_000} preview={null} />);
    expect(screen.queryByTestId("position-pnl-breakdown")).not.toBeInTheDocument();
  });

  it("keeps operational detail closed until it is asked for", () => {
    render(<Disclosure title="거래 기록" testId="disclosure-trades">
      <p>여기 내용</p>
    </Disclosure>);
    expect(screen.queryByText("여기 내용")).not.toBeInTheDocument();
    expect(screen.getByTestId("disclosure-trades-toggle")).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(screen.getByTestId("disclosure-trades-toggle"));
    expect(screen.getByText("여기 내용")).toBeInTheDocument();
  });
});

describe("account reset", () => {
  const noop = () => {};
  const withOpenPosition = (): CryptoState => {
    const state = baseState();
    return { ...state, account: { ...state.account!, position_side: "LONG",
      position_qty: "0.010", position_signed_qty: "0.010", avg_entry: "86000.0",
      leverage: "10", used_margin: "86.0", liquidation_price: "78000.0" } };
  };

  it("offers the reset button beside the balance", () => {
    render(<MarketHeader state={baseState()} performance={null} onAction={noop} busy={false} />);
    expect(screen.getByTestId("reset-button")).toBeEnabled();
  });

  it("stays out of the way when the screen has no action handler", () => {
    render(<MarketHeader state={baseState()} performance={null} />);
    expect(screen.queryByTestId("reset-control")).not.toBeInTheDocument();
  });

  it("asks before resetting and says what is kept", () => {
    render(<MarketHeader state={baseState()} performance={null} onAction={noop} busy={false} />);
    expect(screen.queryByTestId("reset-dialog")).not.toBeInTheDocument();
    fireEvent.click(screen.getByTestId("reset-button"));
    const dialog = screen.getByTestId("reset-dialog");
    expect(dialog).toHaveTextContent("10,000,000원");
    // The sentence that matters: this is not a delete.
    expect(dialog).toHaveTextContent("기존 거래 및 성과 기록은 삭제되지 않습니다");
    expect(screen.getByTestId("reset-cancel")).toBeInTheDocument();
    expect(screen.getByTestId("reset-confirm")).toBeInTheDocument();
  });

  it("does nothing on cancel", () => {
    const calls: unknown[] = [];
    render(<MarketHeader state={baseState()} performance={null} busy={false}
      onAction={run => calls.push(run)} />);
    fireEvent.click(screen.getByTestId("reset-button"));
    fireEvent.click(screen.getByTestId("reset-cancel"));
    expect(screen.queryByTestId("reset-dialog")).not.toBeInTheDocument();
    expect(calls).toHaveLength(0);
  });

  it("fires exactly one reset on confirm", () => {
    const calls: unknown[] = [];
    render(<MarketHeader state={baseState()} performance={null} busy={false}
      onAction={run => calls.push(run)} />);
    fireEvent.click(screen.getByTestId("reset-button"));
    fireEvent.click(screen.getByTestId("reset-confirm"));
    expect(calls).toHaveLength(1);
    expect(screen.queryByTestId("reset-dialog")).not.toBeInTheDocument();
  });

  it("is disabled with a reason while a position is open", () => {
    render(<MarketHeader state={withOpenPosition()} performance={null} onAction={noop}
      busy={false} />);
    expect(screen.getByTestId("reset-button")).toBeDisabled();
    expect(screen.getByTestId("reset-blocked-note"))
      .toHaveTextContent("포지션 청산 후 초기화 가능");
  });

  it("cannot be opened at all while a position is open", () => {
    render(<MarketHeader state={withOpenPosition()} performance={null} onAction={noop}
      busy={false} />);
    fireEvent.click(screen.getByTestId("reset-button"));
    expect(screen.queryByTestId("reset-dialog")).not.toBeInTheDocument();
  });

  it("notes how many times the account has been reset, and that records are kept", () => {
    const state = baseState();
    const reset = { ...state, account: { ...state.account!, reset_count: 2 } };
    render(<MarketHeader state={reset} performance={null} onAction={noop} busy={false} />);
    expect(screen.getByTestId("reset-count-note")).toHaveTextContent("초기화 2회");
    expect(screen.getByTestId("reset-count-note")).toHaveTextContent("기록 보존");
  });

  it("renders the reset as a before and after line in the ledger", () => {
    const line = describeReset({ seq: 40, ts_ms: 1_000, event_type: "ACCOUNT_RESET",
      before_equity_krw: "9431200", after_equity_krw: "10000000" } as never);
    expect(line).toBe("9,431,200원 → 10,000,000원");
  });
});

describe("current segment versus lifetime pnl on screen", () => {
  const noop = () => {};

  const perf = (overrides: Record<string, unknown> = {}) => ({
    trades: 4, wins: 2, losses: 2, win_rate: "0.5",
    gross_pnl: "-600", fees: "120", funding: "0", net_pnl: "-720",
    avg_win: "50", avg_loss: "-410", expectancy: "-180", profit_factor: "0.12",
    payoff_ratio: "0.12", max_drawdown: "800", max_drawdown_fraction: "0.1",
    longest_losing_streak: 2, hold_ms_median: 60_000, hold_ms_mean: 60_000,
    hold_ms_total: 240_000, max_adverse_excursion: "-9", max_favourable_excursion: "3",
    liquidations: 0, ending_equity: "7457", starting_capital: "745",
    krw: { net_pnl: "-965520" },
    capital_resets: 1,
    current_segment: {
      since_ts_ms: 1_000_400, reset_count: 1,
      starting_capital_usdt: "7457.12", starting_capital_krw: "10000000",
      current_segment_realized_pnl: "0", current_segment_fees: "0",
      current_segment_funding: "0", current_segment_net_pnl: "0",
      trades: 0, wins: 0, win_rate: null, max_drawdown: "0",
    },
    by_origin: {}, by_leverage: {}, by_side: {}, by_hour_utc: {},
    reconciliation: { gross_pnl_matches: true, fees_match: true, funding_matches: true },
    ...overrides,
  }) as never;

  it("shows zero in the header straight after a reset even though lifetime is negative", () => {
    render(<MarketHeader state={baseState()} performance={perf()} onAction={noop}
      busy={false} />);
    const compact = screen.getByTestId("account-compact");
    expect(compact).toHaveTextContent("현재 손익");
    // The whole bug: lifetime was -965,520원 and the header read as if the reset had not run.
    expect(compact).not.toHaveTextContent("-965,520원");
    expect(compact).toHaveTextContent("0원");
  });

  it("shows the new trade only, once the reset segment has one", () => {
    const after = perf({
      net_pnl: "-620",
      krw: { net_pnl: "-831420" },
      current_segment: {
        since_ts_ms: 1_000_400, reset_count: 1,
        starting_capital_usdt: "7457.12", starting_capital_krw: "10000000",
        current_segment_realized_pnl: "108", current_segment_fees: "8",
        current_segment_funding: "0", current_segment_net_pnl: "100",
        trades: 1, wins: 1, win_rate: "1", max_drawdown: "0",
      },
    });
    render(<MarketHeader state={baseState()} performance={after} onAction={noop} busy={false} />);
    const compact = screen.getByTestId("account-compact");
    expect(compact).toHaveTextContent("+100.0000 USDT");
    expect(compact).not.toHaveTextContent("-620");
  });

  it("says what the header figure is measured from", () => {
    expect(resetScopeNote(perf())).toBe("마지막 가상계좌 초기화 이후");
    expect(resetScopeNote({ current_segment: { reset_count: 0 } })).toBe("런 시작 이후");
    expect(resetScopeNote(null)).toBe("런 시작 이후");
  });

  it("falls back to the account figure before performance has arrived", () => {
    render(<MarketHeader state={baseState()} performance={null} onAction={noop} busy={false} />);
    expect(screen.getByTestId("account-compact")).toHaveTextContent("현재 손익");
  });

  it("shows both scopes in the performance detail, clearly separated", () => {
    render(<PerformancePanel performance={perf()} />);
    const segment = screen.getByTestId("current-segment-panel");
    expect(segment).toHaveTextContent("현재 구간");
    expect(segment).toHaveTextContent("초기화 1회차 이후");
    expect(segment).toHaveTextContent("초기화는 기록을 지우지 않습니다");
    // And the lifetime figure is named in full rather than just "순손익".
    expect(screen.getByTestId("performance-panel")).toHaveTextContent("전체 누적 손익");
  });

  it("omits the current-segment block entirely when there has been no reset", () => {
    const never = perf({ capital_resets: 0, current_segment: {
      since_ts_ms: 1_000_000, reset_count: 0, starting_capital_usdt: "745",
      starting_capital_krw: null, current_segment_realized_pnl: "-600",
      current_segment_fees: "120", current_segment_funding: "0",
      current_segment_net_pnl: "-720", trades: 4, wins: 2, win_rate: "0.5",
      max_drawdown: "800" } });
    render(<PerformancePanel performance={never} />);
    // With no reset the two scopes are the same run, so a second panel would only repeat it.
    expect(screen.queryByTestId("current-segment-panel")).not.toBeInTheDocument();
    expect(screen.getByTestId("performance-panel")).toHaveTextContent("전체 누적 손익");
  });
});
